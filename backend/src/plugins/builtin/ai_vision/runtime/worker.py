"""StreamWorker 推理流水线（任务 2.3）。

职责（一个摄像头 = 一个 StreamWorker 线程）：
1. ``FrameGrabber`` 循环：cv2.VideoCapture 抓帧，按该摄像头全部事件
   的最高分析 fps 节流（避免重复解码）；
2. 推理分发：一帧 → 该摄像头全部启用事件的模型推理（模型去重后逐个
   ``InferenceEngine.detect``，同一模型只推理一次，结果按事件过滤类别）；
3. ``PostProcessor``：ROI 多边形过滤 / 最小尺寸过滤 / 置信度过滤；
4. ``EventDetector``（一期简化版）：连续 N 帧命中投票 + 冷却期；
5. 告警落库：``ai_vision_alarms`` + 抓拍图
   ``uploads/ai_vision/snapshots/{camera_id}/{ts}.jpg``（cv2 画框 + 标签）。

设计原则：
- **线程内独立 DB session**（不能跨线程复用 async session）：worker 内
  用 ``SessionLocal()`` 同步连接写库，避免阻塞事件循环；
- 推理引擎线程安全（onnxruntime run 可并发），多个事件共享同一模型句柄；
- 抓帧失败 → 指数退避（1s/2s/4s…上限 60s）重连；连续 5 分钟失败 →
  事件 ``CAMERA_OFFLINE`` + 任务状态 ``degraded``；
- 一期 EventDetector 按状态机抽象（``feed(detections, ts) -> bool``），
  二期替换为完整时序实现（见任务 4.1）。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from src.plugins.builtin.ai_vision.runtime.deps import ensure_infer_runtime
from src.plugins.builtin.ai_vision.runtime.engine import InferenceEngine, get_engine
from src.plugins.builtin.ai_vision.runtime.events import (
    ALARM_RAISED,
    CAMERA_OFFLINE,
    CAMERA_RECOVERED,
    WORKER_ERROR,
    WORKER_STARTED,
    WORKER_STOPPED,
    RuntimeEvent,
)

# 断线重连参数
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 60.0
_OFFLINE_AFTER_SECONDS = 5 * 60  # 连续 5 分钟失败 → offline


@dataclass(slots=True)
class TaskBinding:
    """一个运行中任务在 worker 内的绑定信息。"""

    task_id: int
    event_id: int
    camera_id: int
    category_codes: list[str]
    rule: dict[str, Any]
    model_ids: list[int]
    analyze_fps: int = 2
    status: str = "running"
    detector: "EventDetector | None" = None  # 每个任务一个判定器实例


@dataclass(slots=True)
class PostProcessor:
    """检测结果过滤（ROI / 最小尺寸 / 置信度）。"""

    roi: list[list[float]] = field(default_factory=list)  # [[x,y],...] 空=全图
    min_size: float = 20.0  # 最小检测框边长（像素）
    threshold: float = 0.45

    def filter(self, dets: list[Any]) -> list[Any]:
        """按 ROI / 最小尺寸过滤。置信度过滤已在引擎内完成。"""
        out = []
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            w, h = x2 - x1, y2 - y1
            if w < self.min_size or h < self.min_size:
                continue
            if self.roi and not self._in_roi((x1 + x2) / 2, (y1 + y2) / 2):
                continue
            out.append(d)
        return out

    def _in_roi(self, cx: float, cy: float) -> bool:
        """点在 ROI 多边形内（含边界）。"""
        import cv2

        poly = np.array([(float(p[0]), float(p[1])) for p in self.roi], dtype=np.float32)
        if len(poly) < 3:
            return True  # 无效多边形视为全图
        return cv2.pointPolygonTest(poly, (cx, cy), False) >= 0


class EventDetector:
    """一期简化判定器：连续 N 帧命中投票 + 冷却期。

    接口按状态机抽象（``feed(detections, ts) -> bool``），二期（任务 4.1）
    替换为 SlidingWindowVoter + DurationTimer + CooldownGate 完整实现。

    规则参数：
    - vote_seconds: 投票窗口（秒）；0 = 单帧命中即告警
    - cooldown: 告警冷却（秒）
    - fps: 分析帧率（用于计算 N = ceil(fps × vote_seconds)）
    """

    def __init__(self, rule: dict[str, Any], fps: int) -> None:
        self.threshold = float(rule.get("threshold", 0.45))
        self.vote_seconds = int(rule.get("vote_seconds", 0))
        self.cooldown = int(rule.get("cooldown", 60))
        self.fps = max(1, int(fps))
        self._hit_count = 0
        self._last_alarm_at = 0.0
        self._vote_n = max(1, int(self.fps * self.vote_seconds)) if self.vote_seconds > 0 else 1

    def feed(self, detections: list[Any], ts: float) -> bool:
        """喂入一帧检测结果，返回是否触发告警（达到投票阈值且在冷却期外）。"""
        hit = len(detections) > 0
        if hit:
            self._hit_count += 1
        else:
            self._hit_count = 0

        if self._hit_count < self._vote_n:
            return False

        # 冷却期检查
        if ts - self._last_alarm_at < self.cooldown:
            return False

        self._last_alarm_at = ts
        self._hit_count = 0
        return True


@dataclass(slots=True)
class WorkerStats:
    """worker 运行指标（供 /tasks/{id}/stats）。"""

    frames_read: int = 0
    frames_processed: int = 0
    fps_actual: float = 0.0
    inference_ms: float = 0.0
    drop_rate: float = 0.0
    last_alarm_at: float = 0.0


class StreamWorker:
    """单视频源推理 worker（线程）。

    启动后持续抓帧 → 推理 → 检测器判定 → 告警落库，直到 ``stop()`` 被调用。

    源类型（``source_type``）：
    - ``camera``：RTSP 实时流，断线指数退避重连；
    - ``video``：本地视频文件（测试/演示），播完自动回绕循环播放。
    """

    def __init__(
        self,
        camera_id: int,
        rtsp_url: str,
        source_type: str = "camera",
        engine: InferenceEngine | None = None,
        event_listener: Any | None = None,
        uploads_dir: str | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.source_type = source_type if source_type in ("camera", "video") else "camera"
        self.engine = engine or get_engine()
        self.event_listener = event_listener
        # uploads 根目录：默认 backend/uploads/（统一由 paths.py 管理）
        if uploads_dir is not None:
            self.uploads_dir = uploads_dir
        else:
            from src.plugins.builtin.ai_vision.paths import UPLOADS_ROOT

            self.uploads_dir = str(UPLOADS_ROOT)

        self._tasks: dict[int, TaskBinding] = {}
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._lock = threading.RLock()
        # 模型句柄缓存：model_id → (handle, resolved_path, cat_map_json)
        # 热路径优化：_process_frame 每帧调用 _load_model，避免每帧开
        # SQLite session 查库；引擎层已按 mtime+size 失效模型文件缓存，
        # 此处缓存的是「DB 记录 → 路径/类别映射」的解析结果。
        # 记录变更（重新导出/换文件）通过 _refresh_model_handles() 失效。
        self._model_cache: dict[int, tuple[Any, str, str]] = {}
        self.stats = WorkerStats()
        self._worker_id = f"cam-{camera_id}"
        self._video_fps = 0.0  # 视频源原始帧率（用于实时播放节流）

        # 断线重连状态
        self._reconnect_delay = _RECONNECT_BASE
        self._offline_since: float | None = None
        self._state = "unknown"  # unknown/online/offline
        self._last_frame: Any = None  # 最近一次成功帧（供抓拍图使用）
        self._last_frame_ts = 0.0

    # ── 任务绑定 ─────────────────────────────────────────────
    def add_task(self, task: TaskBinding) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def remove_task(self, task_id: int) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def snapshot_tasks(self) -> list[TaskBinding]:
        with self._lock:
            return list(self._tasks.values())

    # ── 启停 ─────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"stream-{self.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def state(self) -> str:
        return self._state

    # ── 主循环 ───────────────────────────────────────────────
    def _run(self) -> None:
        self._emit(WORKER_STARTED, {"camera_id": self.camera_id})
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[AIVision] worker {self._worker_id} error: {exc}")
            self._emit(WORKER_ERROR, {"error": str(exc)})
        finally:
            self._emit(WORKER_STOPPED, {"camera_id": self.camera_id})

    def _loop(self) -> None:
        ensure_infer_runtime()
        import cv2

        cap = None
        frames_read = 0
        frames_processed = 0
        # 节流计时（上次处理帧的时刻）与统计窗口起点分离：
        # window_start 若兼任节流基准，会在每次处理后重置，
        # 导致 1 秒统计窗口永远凑不满，stats 恒为 0。
        last_process_at = time.monotonic()
        stats_window_start = time.monotonic()
        window_frames = 0
        window_processed = 0
        target_fps = self._max_fps()
        last_target = target_fps
        min_interval = 1.0 / max(1, target_fps)
        video_read_interval = 0.0  # 视频源实时播放节奏（打开 capture 后更新）
        last_read_at = 0.0  # 视频源实时播放节奏基准

        while not self._stop_flag.is_set():
            # 重新打开连接（首次或断线重连）
            if cap is None or not cap.isOpened():
                cap = self._open_capture(cv2)
                if cap is None:
                    self._mark_read_failure()
                    delay = self._reconnect_delay
                    self._reconnect_delay = min(self._reconnect_delay * 2, _RECONNECT_MAX)
                    if self._stop_flag.wait(timeout=delay):
                        break
                    continue
                # 打开成功后按视频原始帧率设置实时播放间隔
                if self.source_type == "video" and self._video_fps > 0:
                    video_read_interval = 1.0 / self._video_fps

            # 视频源节流：按原始帧率实时播放，否则 read() 全速快进、CPU 空转
            if self.source_type == "video" and self._video_fps > 0:
                now = time.monotonic()
                wait = video_read_interval - (now - last_read_at)
                if wait > 0:
                    if self._stop_flag.wait(timeout=wait):
                        break
                    continue
                last_read_at = now

            ok, frame = cap.read()
            if not ok or frame is None:
                # 视频文件源：播到末尾 → 回绕循环（测试/演示友好）
                if self.source_type == "video":
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                # RTSP 源：视为断线，走重连
                self._mark_read_failure()
                cap.release()
                cap = None
                continue

            frames_read += 1
            window_frames += 1
            self._mark_read_ok()
            self._last_frame = frame
            self._last_frame_ts = time.time()

            # 帧率节流：按当前最大任务 fps 计算最小间隔
            target_fps = self._max_fps()
            if target_fps != last_target:
                min_interval = 1.0 / max(1, target_fps)
                last_target = target_fps
            now = time.monotonic()
            if now - last_process_at < min_interval:
                continue  # 节流：跳过本帧
            last_process_at = now

            frames_processed += 1
            window_processed += 1
            self._process_frame(frame)

            # 统计（1 秒窗口，独立于节流计时）
            elapsed = now - stats_window_start
            if elapsed >= 1.0:
                self.stats.frames_read = frames_read
                self.stats.frames_processed = frames_processed
                self.stats.fps_actual = round(window_processed / elapsed, 2)
                self.stats.drop_rate = round(
                    max(0.0, 1.0 - (window_processed / max(1, window_frames))), 4
                )
                window_frames = 0
                window_processed = 0
                stats_window_start = now

        if cap is not None:
            cap.release()

    def _process_frame(self, frame: Any) -> None:
        """推理分发：模型去重 → 逐事件判定 → 告警落库。"""
        tasks = self.snapshot_tasks()
        if not tasks:
            return

        # 模型去重：model_id → ModelHandle（同一摄像头多事件共享一个推理）
        handles: dict[int, Any] = {}
        for task in tasks:
            for mid in task.model_ids:
                if mid not in handles:
                    handles[mid] = self._load_model(mid)

        for task in tasks:
            try:
                self._process_event(task, frame, handles)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AIVision] task {task.task_id} frame error: {exc}")

    def _process_event(self, task: TaskBinding, frame: Any, handles: dict[int, Any]) -> None:
        """单事件处理：收集所有绑定模型的检测结果 → 过滤 → 判定 → 告警。"""
        rule = task.rule or {}
        threshold = float(rule.get("threshold", 0.45))
        pp = PostProcessor(
            roi=rule.get("roi", []),
            min_size=float(rule.get("min_size", 20)),
            threshold=threshold,
        )
        if task.detector is None:
            task.detector = EventDetector(rule, task.analyze_fps)

        # 收集该事件关心的所有检测
        all_dets: list[Any] = []
        for mid in task.model_ids:
            handle = handles.get(mid)
            if handle is None:
                continue
            cat_filter = {cid for cid, cat in handle.category_map.items() if cat in set(task.category_codes)}
            if not cat_filter:
                continue
            try:
                dets = self.engine.detect(frame, handle, threshold=threshold, classes_filter=cat_filter)
                all_dets.extend(dets)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AIVision] detect failed (task {task.task_id}): {exc}")

        all_dets = pp.filter(all_dets)

        if task.detector.feed(all_dets, time.time()):
            self._raise_alarm(task, all_dets)

    def _raise_alarm(self, task: TaskBinding, dets: list[Any]) -> None:
        """告警落库 + 抓拍图 + 事件回调。"""
        try:
            snap = self._save_snapshot(task, dets)
            alarm = {
                "task_id": task.task_id,
                "event_id": task.event_id,
                "camera_id": self.camera_id,
                "category_code": task.category_codes[0] if task.category_codes else "",
                "confidence": round(max(d.conf for d in dets), 4) if dets else 0.0,
                "snapshot_path": snap,
                "level": "warning",
                "status": "pending",
                "note": "",
            }
            self._db_insert_alarm(alarm)
            self.stats.last_alarm_at = time.time()
            self._emit(ALARM_RAISED, alarm)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] alarm save failed: {exc}")

    def _save_snapshot(self, task: TaskBinding, dets: list[Any]) -> str:
        """画框 + 标签 → JPEG（quality 85，最多 top-3）→ 返回绝对路径。"""
        import cv2

        frame = self._last_frame
        if frame is None:
            return ""
        img = frame.copy()
        top = sorted(dets, key=lambda d: d.conf, reverse=True)[:3]
        for d in top:
            x1, y1, x2, y2 = [int(v) for v in d.bbox]
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 165, 255), 2)
            label = f"{d.cls_name} {d.conf:.2f}"
            cv2.putText(img, label, (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

        snap_dir = Path(self.uploads_dir) / "ai_vision" / "snapshots" / str(self.camera_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = snap_dir / f"{ts}_{task.event_id}.jpg"
        cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return str(path)

    def _db_insert_alarm(self, alarm: dict) -> None:
        """独立同步 Session 写库（不阻塞事件循环）。"""
        from src.plugins.builtin.ai_vision.models import AIVisionAlarm
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        with sync_session() as db:
            db.add(AIVisionAlarm(**alarm))
            db.commit()

    # ── 模型加载 ─────────────────────────────────────────────
    def _load_model(self, model_id: int) -> Any | None:
        """按模型记录加载 ONNX（带进程内缓存，命中则复用句柄）。

        热路径优化：先查 worker 级 ``_model_cache``（记录解析结果），
        命中则直接走 engine.load（其内部按 mtime+size 判定文件变化，
        未变化时 O(1) 返回缓存句柄）；仅首次（或缓存失效后）查 DB。
        """
        with self._lock:
            cached = self._model_cache.get(model_id)
        if cached is not None:
            handle, _, _ = cached
            return handle

        from src.plugins.builtin.ai_vision.models import AIVisionModel
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        try:
            with sync_session() as db:
                rec = db.get(AIVisionModel, model_id)
                if not rec:
                    return None
                handle = self._load_model_from_record(rec)
                if handle is not None:
                    with self._lock:
                        self._model_cache[model_id] = (
                            handle,
                            rec.file_path,
                            rec.category_map or "{}",
                        )
                return handle
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] 模型加载失败 model#{model_id}: {exc}")
            return None

    def _refresh_model_handles(self) -> None:
        """清空 worker 级模型记录缓存（模型记录变更 / 重新导出后调用）。

        engine 层缓存按文件 mtime+size 自动失效，无需手动清理；
        此处仅清理「DB 记录 → 路径/类别映射」的解析缓存。
        """
        with self._lock:
            self._model_cache.clear()

    def _load_model_from_record(self, rec: Any) -> Any | None:
        """按 ORM 记录解析路径与类别映射并加载（不查库）。"""
        rel = rec.file_path  # 形如 assets/models/xxx.onnx
        # 解析路径：优先绝对路径；相对路径从插件根解析
        model_path = Path(rel)
        if not model_path.is_absolute():
            plugin_root = Path(__file__).resolve().parent.parent  # ai_vision/
            candidate = plugin_root / rel
            if candidate.exists():
                model_path = candidate
            else:
                # 退回到 backend 运行目录
                candidate2 = Path.cwd() / rel
                if candidate2.exists():
                    model_path = candidate2
                else:
                    logger.error(f"[AIVision] 模型文件不存在: {rel}")
                    return None
        cat_map = json.loads(rec.category_map or "{}")
        cat_map_int = {int(k): v for k, v in cat_map.items()}
        return self.engine.load(str(model_path), category_map=cat_map_int)

    # ── 抓帧辅助 ─────────────────────────────────────────────
    def _open_capture(self, cv2: Any) -> Any | None:
        """按源类型打开 VideoCapture，失败返回 None。

        - camera：RTSP 流（FFMPEG 后端 + 打开超时）
        - video：本地视频文件（路径必须存在且非目录）
        """
        if self.source_type == "video":
            path = Path(self.rtsp_url)
            if not path.is_file():
                logger.error(f"[AIVision] 视频文件不存在: {self.rtsp_url}")
                return None
            cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                logger.error(f"[AIVision] 视频文件无法打开（格式不支持）: {self.rtsp_url}")
                return None
            fps = cap.get(cv2.CAP_PROP_FPS)
            # 视频文件 read() 全速返回，记录原始帧率供主循环按实时节奏播放
            self._video_fps = float(fps) if fps and fps > 0 else 25.0
            return cap

        # camera：RTSP
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        return cap

    def _mark_read_ok(self) -> None:
        if self._state in ("offline", "unknown"):
            if self._state == "offline":
                self._emit(CAMERA_RECOVERED, {"camera_id": self.camera_id})
            self._state = "online"
        self._offline_since = None
        self._reconnect_delay = _RECONNECT_BASE

    def _mark_read_failure(self) -> None:
        if self._offline_since is None:
            self._offline_since = time.time()
            self._state = "offline"
            self._emit(CAMERA_OFFLINE, {"camera_id": self.camera_id})
        elif time.time() - self._offline_since > _OFFLINE_AFTER_SECONDS:
            self._state = "offline"  # 保持离线（已发过事件）

    def _max_fps(self) -> int:
        """该摄像头全部任务的最大分析 fps。"""
        tasks = self.snapshot_tasks()
        if not tasks:
            return 2
        return max((t.analyze_fps for t in tasks), default=2)

    def _emit(self, etype: str, payload: dict) -> None:
        if self.event_listener:
            try:
                self.event_listener(RuntimeEvent(type=etype, worker_id=self._worker_id, payload=payload))
            except Exception:  # noqa: BLE001
                pass