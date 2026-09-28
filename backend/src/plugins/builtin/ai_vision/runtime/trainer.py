"""训练任务管理器：子进程执行 + 进度轮询 + 产物回流注册。

设计：
- **子进程训练**（``scripts/train_runner.py``）：ultralytics 全局状态/线程
  不污染主服务；cancel 直接 terminate 进程；主服务重启不丢任务记录
  （running 任务标记 failed-orphan，可重发）。
- **单实例互斥**：CPU 单机一次只跑一个训练（发起时校验）。
- **进度**：runner 逐 epoch 追加 JSONL（progress.jsonl），管理器 2s 轮询
  写库（progress/metrics）；前端轮询任务接口即可，无需 SSE。
- **回流**（B6）：训练成功 → best.pt 与导出的 ONNX 拷贝进
  ``assets/models/``，注册新 AIVisionModel（source=trained、parent=基座、
  category_map 按 names 顺序、status=candidate 待人工换绑）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from src.plugins.builtin.ai_vision.paths import datasets_dir, train_runs_dir
from src.plugins.builtin.ai_vision.runtime.datasets import build_dataset

# 产物模型目录：ai_vision/assets/models（与推理引擎加载路径一致）。
# 本文件在 ai_vision/runtime/ 下：parents[0]=runtime/ parents[1]=ai_vision/
# （自查：原 parents[2] 算到 builtin/，回流模型引擎找不到）
_MODELS_DIR = Path(__file__).resolve().parents[1] / "assets" / "models"


class TrainManager:
    """全局单例：管理训练子进程与进度回写。"""

    _instance: "TrainManager | None" = None
    _inst_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[int, subprocess.Popen] = {}
        self._threads: dict[int, threading.Thread] = {}
        self._cancelled: set[int] = set()  # 用户主动取消的 job（watch 不再覆盖错误信息）
        self._launch_at: dict[int, float] = {}  # job → 启动时刻（孤儿判定防误杀）
        self._launch_guard = threading.Lock()  # 互斥检查+占位原子锁（自查 P2-4）

    @classmethod
    def get(cls) -> "TrainManager":
        with cls._inst_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ── 状态查询 ────────────────────────────────────────
    def active_job_id(self) -> int | None:
        with self._lock:
            for jid, p in self._procs.items():
                if p.poll() is None:
                    return jid
        return None

    def try_reserve(self, job_id: int) -> bool:
        """原子互斥：有活进程或新鲜占位则拒绝；否则为 job 占位（自查 P2-4）。

        API 层在创建训练记录后、launch 前调用；占位堵住"检查互斥→真正
        拉起进程"之间的并发窗口（双击提交连发两个训练）。
        """
        with self._launch_guard:
            if self._reserve_busy():
                return False
            self._launch_at[job_id] = time.time()
            return True

    def release(self, job_id: int) -> None:
        """launch 失败（未拉起进程）时释放占位，免等 120s 陈旧过期。"""
        with self._launch_guard:
            self._launch_at.pop(job_id, None)

    def _reserve_busy(self) -> bool:
        """是否有任务处于互斥窗口内（launch 占位中或子进程运行中）。"""
        now = time.time()
        for jid, ts in self._launch_at.items():
            if now - ts > 120:
                continue  # 陈旧占位（launch 抛异常未清理）忽略
            proc = self._procs.get(jid)
            if proc is None or proc.poll() is None:
                return True
        return False

    @staticmethod
    def _kill_orphan_runners() -> int:
        """杀掉遗留的 train_runner 子进程（服务重启后旧进程可能仍在跑，自查 P1-2）。

        首选 psutil 按命令行匹配（跨平台、无 wmic 权限问题——wmic 在受限
        环境会 WinError 5）；psutil 不可用时回退 wmic/pkill。失败不阻塞启动。
        """
        killed = 0
        try:
            import psutil

            victims = []
            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmd = " ".join(p.info.get("cmdline") or [])
                    if "train_runner.py" in cmd and p.pid != os.getpid():
                        victims.append(p)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            # 连树杀：runner 的 dataloader worker 子进程命令行不含
            # train_runner.py，只杀父会留孤儿继续占 CPU
            for p in victims:
                try:
                    for c in p.children(recursive=True):
                        try:
                            c.kill()
                        except psutil.NoSuchProcess:
                            pass
                    p.kill()
                    killed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if killed:
                logger.info(f"[AIVision] killed {killed} orphan train_runner process(es)")
            return killed
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] psutil orphan cleanup failed, fallback: {exc}")
        try:
            if sys.platform == "win32":
                r = subprocess.run(
                    ["wmic", "process", "where", "CommandLine like '%train_runner.py%'",
                     "get", "ProcessId", "/format:list"],
                    capture_output=True, text=True, timeout=20,
                )
                my_pid = str(os.getpid())
                pids = set()
                for line in (r.stdout or "").splitlines():
                    line = line.strip()
                    if line.startswith("ProcessId="):
                        pid = line.split("=", 1)[1].strip()
                        if pid.isdigit() and pid != my_pid:
                            pids.add(pid)
                for pid in pids:
                    subprocess.run(
                        ["taskkill", "/PID", pid, "/T", "/F"],
                        capture_output=True, timeout=15,
                    )
                    killed += 1
            else:
                subprocess.run(["pkill", "-f", "train_runner.py"], capture_output=True, timeout=15)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] orphan runner cleanup skipped: {exc}")
        if killed:
            logger.info(f"[AIVision] killed orphan train_runner process(es): {killed}")
        return killed

    def recover_orphans(self) -> int:
        """服务启动时调用：杀掉遗留训练进程，DB 里 running 的孤儿任务标 failed。

        若不回收，互斥检查会认为"仍有训练在跑"而永久拒绝新任务（P1-4）；
        而旧版只改 DB 不杀进程——Windows 子进程不随父进程死，会后台偷占
        CPU/内存，与新训练打架（自查 P1-2）。
        """
        from src.plugins.builtin.ai_vision.models import AIVisionTraining
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        self._kill_orphan_runners()
        n = 0
        try:
            with sync_session() as db:
                rows = db.query(AIVisionTraining).filter(AIVisionTraining.status == "running").all()
                for job in rows:
                    # 防误杀：刚启动 60s 内的服务可能正有 launch 在途（register 早于
                    # 子进程拉起）；内存有活进程或占位新鲜的跳过
                    if self.active_job_id() == job.id:
                        continue
                    if time.time() - self._launch_at.get(job.id, 0) < 120:
                        continue
                    job.status = "failed"
                    job.metrics = json.dumps({"error": "服务重启，训练进程已中断（可重新发起）"}, ensure_ascii=False)
                    job.finished_at = _now()
                    n += 1
                if n:
                    db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] train orphan recovery failed: {exc}")
        if n:
            logger.info(f"[AIVision] recovered {n} orphan train job(s) → failed")
        return n

    # ── 发起训练 ────────────────────────────────────────
    def launch(
        self,
        job_id: int,
        base_pt: str,
        names: list[str],
        samples: list[Any],
        params: dict[str, Any],
    ) -> dict:
        """构建数据集并拉起训练子进程。返回数据集统计。"""
        # 互斥由 API 层 try_reserve 原子保证；此处仅兜底检查他任务
        other = self.active_job_id()
        if other is not None and other != job_id:
            raise RuntimeError("已有训练任务在运行（单机 CPU 互斥），请等待完成或取消")

        ds_dir = datasets_dir() / str(job_id)
        run_dir = train_runs_dir() / str(job_id)
        ds_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        stats = build_dataset(samples, names, ds_dir, val_split=float(params.get("val_split", 0.2)))
        if stats["train"] < 4:
            raise RuntimeError(f"训练集样本不足（{stats['train']} 张，至少 4 张已标注样本）")

        cfg = {
            "data_yaml": str(ds_dir / "data.yaml"),
            "pt": base_pt,
            "out_dir": str(run_dir),
            "epochs": int(params.get("epochs", 30)),
            "imgsz": int(params.get("imgsz", 416)),
            "batch": int(params.get("batch", 4)),
            "lr0": float(params.get("lr0", 0.01)),
            "progress_file": str(run_dir / "progress.jsonl"),
            "result_file": str(run_dir / "result.json"),
        }
        (run_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        runner = Path(__file__).resolve().parent.parent / "scripts" / "train_runner.py"
        log_fp = open(run_dir / "train.log", "ab", buffering=0)
        proc = subprocess.Popen(
            [sys.executable, str(runner), "--config", str(run_dir / "config.json")],
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            cwd=str(run_dir),
        )
        with self._lock:
            self._procs[job_id] = proc
        t = threading.Thread(target=self._watch, args=(job_id, run_dir, log_fp), daemon=True)
        with self._lock:
            self._threads[job_id] = t
        t.start()
        logger.info(f"[AIVision] train job {job_id} launched pid={proc.pid} train={stats['train']} val={stats['val']}")
        return stats

    def cancel(self, job_id: int) -> bool:
        with self._lock:
            proc = self._procs.get(job_id)
            if proc is not None and proc.poll() is None:
                # 先标记再杀：watch 轮询到"取消瞬间子进程恰好写完 result.json"时，
                # 依据标记保留"用户取消"、不覆盖成 completed（自查 P2-5）
                self._cancelled.add(job_id)
            else:
                return False
        self._kill_tree(proc)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc, force=True)
        logger.info(f"[AIVision] train job {job_id} cancelled")
        return True

    @staticmethod
    def _kill_tree(proc: subprocess.Popen, force: bool = False) -> None:
        """终止整个进程树——terminate() 只杀 runner 本身，ultralytics
        dataloader 子进程会残留占 CPU（自查 P2-8）。Windows 用 taskkill /T。"""
        if sys.platform == "win32":
            import subprocess as sp

            cmd = ["taskkill", "/PID", str(proc.pid), "/T"] + (["/F"] if force else ["/F"])
            try:
                sp.run(cmd, capture_output=True, timeout=15)
                return
            except Exception:  # noqa: BLE001
                pass
        if force:
            proc.kill()
        else:
            proc.terminate()

    # ── 进度轮询线程 ────────────────────────────────────
    def _watch(self, job_id: int, run_dir: Path, log_fp: Any) -> None:
        from src.plugins.builtin.ai_vision.models import AIVisionModel, AIVisionTraining
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        progress_file = run_dir / "progress.jsonl"
        result_file = run_dir / "result.json"
        try:
            while True:
                time.sleep(2)
                rec = self._tail_progress(progress_file)
                result = None
                if result_file.exists():
                    try:
                        result = json.loads(result_file.read_text(encoding="utf-8"))
                    except ValueError:
                        result = None
                with sync_session() as db:
                    job = db.get(AIVisionTraining, job_id)
                    if job is None:
                        break
                    with self._lock:
                        was_cancelled = job_id in self._cancelled
                    if rec and not was_cancelled:
                        job.progress = min(99, int(rec["epoch"] / max(1, rec["epochs"]) * 100))
                        job.metrics = json.dumps(rec, ensure_ascii=False)
                    if result is not None:
                        if was_cancelled:
                            # 取消与"恰好完成"竞态：用户意图优先，保留 API 层已写的
                            # "用户取消"，不覆盖成 completed（自查 P2-5）
                            db.commit()
                            break
                        if result.get("ok"):
                            out_id = self._register_output(db, job, result)
                            job.output_model_id = out_id
                            job.status = "completed"
                            job.progress = 100
                        else:
                            job.status = "failed"
                            job.metrics = json.dumps({"error": result.get("error", "")[-500:]}, ensure_ascii=False)
                        job.finished_at = _now()
                        db.commit()
                        break
                    # 进程已退出但无 result（崩溃/被杀）
                    with self._lock:
                        proc = self._procs.get(job_id)
                    if proc is not None and proc.poll() is not None and result is None:
                        if not was_cancelled:  # 用户取消时 API 已写"用户取消"，不覆盖（P2-7）
                            job.status = "failed"
                            job.metrics = json.dumps({"error": f"训练进程退出 code={proc.poll()}"}, ensure_ascii=False)
                            job.finished_at = _now()
                        db.commit()
                        break
                    db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] train watch loop error job={job_id}: {exc}")
            try:
                with sync_session() as db:
                    job = db.get(AIVisionTraining, job_id)
                    if job and job.status == "running":
                        job.status = "failed"
                        job.metrics = json.dumps({"error": str(exc)[:500]}, ensure_ascii=False)
                        db.commit()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                log_fp.close()
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._procs.pop(job_id, None)
                self._threads.pop(job_id, None)
            # 清理占位与取消标记，防无界增长（自查 P2-6）；
            # _cancelled 延迟 60s 再清——cancel() 后 watch 还要读它做覆盖保护
            with self._launch_guard:
                self._launch_at.pop(job_id, None)
            threading.Timer(60, lambda: self._cancelled.discard(job_id)).start()

    @staticmethod
    def _tail_progress(p: Path) -> dict | None:
        if not p.exists():
            return None
        try:
            lines = p.read_text(encoding="utf-8").strip().splitlines()
            return json.loads(lines[-1]) if lines else None
        except (ValueError, OSError):
            return None

    # ── 产物回流注册（B6）──────────────────────────────
    @staticmethod
    def _register_output(db: Any, job: Any, result: dict) -> int | None:
        import hashlib
        import shutil

        from sqlalchemy import select

        from src.plugins.builtin.ai_vision.models import AIVisionModel

        base = db.get(AIVisionModel, job.base_model_id) if job.base_model_id else None
        onnx_src = result.get("onnx")
        pt_src = result.get("pt")
        if not onnx_src or not Path(onnx_src).exists():
            return None
        _MODELS_DIR.mkdir(parents=True, exist_ok=True)
        base_name = (base.name if base else "model")
        # 自定义产物名优先（params.output_name，发起训练时填写）；
        # 只留安全字符；重名自动追加 -2/-3 序号（默认名 基座-ftN 同理递增）
        try:
            custom = str(json.loads(job.params or "{}").get("output_name", "")).strip()
            custom = re.sub(r"[^\w\u4e00-\u9fff.\-]", "", custom)[:60]
        except (ValueError, TypeError):
            custom = ""
        stem = custom or f"{base_name}-ft"
        seq = 1
        while True:
            new_name = f"{stem}{seq}" if not custom else (stem if seq == 1 else f"{stem}-{seq}")
            # 名称占用 = DB 有记录 或 磁盘有孤儿文件（删模型时文件删除失败残留）
            # ——否则同名 copy2 会静默覆盖那个文件（自查 P2-7）
            occupied = db.execute(
                select(AIVisionModel).where(AIVisionModel.name == new_name)
            ).scalars().first() is not None
            if (_MODELS_DIR / f"{new_name}.onnx").exists() or (_MODELS_DIR / f"{new_name}.pt").exists():
                occupied = True
            if not occupied:
                break
            seq += 1
        onnx_dst = _MODELS_DIR / f"{new_name}.onnx"
        shutil.copy2(onnx_src, onnx_dst)
        pt_dst = None
        if pt_src and Path(pt_src).exists():
            pt_dst = _MODELS_DIR / f"{new_name}.pt"
            shutil.copy2(pt_src, pt_dst)
        sha = hashlib.sha256(onnx_dst.read_bytes()).hexdigest()
        # 训练备注透传到模型说明：产物入库后可追溯"这次训练是为什么"
        try:
            train_note = str(json.loads(job.params or "{}").get("note", "")).strip()[:200]
        except (ValueError, TypeError):
            train_note = ""
        model = AIVisionModel(
            name=new_name,
            file_path=f"assets/models/{new_name}.onnx",
            sha256=sha,
            file_size=onnx_dst.stat().st_size,
            category_map=base.category_map if base else "{}",
            input_size=base.input_size if base else 640,
            source="trained",
            parent_model_id=job.base_model_id,
            license_note=(
                f"平台训练产物（job {job.id}，需人工验收后到事件换绑）"
                + (f"｜备注：{train_note}" if train_note else "")
            ),
            version="1.0.0",
            pt_path=f"assets/models/{new_name}.pt" if pt_dst else "",
        )
        db.add(model)
        db.flush()
        logger.info(f"[AIVision] trained model registered: {new_name} id={model.id}")
        return model.id


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def get_train_manager() -> TrainManager:
    return TrainManager.get()
