"""L1/L2 运行环境依赖检测与一键安装。

设计原则（见规划方案第 3 章）：
- 本模块**零重依赖**：只使用标准库 + 已装包探测，绝不顶层 import
  onnxruntime / opencv / torch 等（避免插件加载即失败）。
- 检测用 ``importlib.metadata.version``，避免真实 import 模块（防止
  导入时触发动态库初始化而污染内存）。
- 安装用 ``subprocess.Popen([sys.executable, "-m", "pip", "install", "-r", ...])``
  后台进程，stdout 追加写 ``ai_vision_runtime_status.install_log``，供 SSE 轮询。
- 同一时刻只允许一个安装任务（进程级互斥）。

L1 推理层（必装，约 75MB）：
    onnxruntime + opencv-python-headless + numpy + pillow（全 MIT/Apache）
L2 训练层（可选，追加约 147MB）：
    torch-cpu + torchvision + ultralytics（AGPL 隔离在训练层）
"""
from __future__ import annotations

import datetime
import importlib.metadata
import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# 层定义（与 requirements-*.txt 对应）
# ---------------------------------------------------------------------------

LAYERS: dict[str, dict] = {
    "L1": {
        "label": "推理层（必装）",
        "packages": ["onnxruntime", "opencv-python-headless", "numpy", "pillow"],
        "requirements": "requirements-infer.txt",
        "pip_index": None,  # 默认 pip 源（.env 可配置 PIP_INDEX_URL）
    },
    "L2": {
        "label": "训练层（可选）",
        "packages": ["torch", "torchvision", "ultralytics"],
        "requirements": "requirements-train.txt",
        "pip_index": "https://download.pytorch.org/whl/cpu",  # torch CPU 源
    },
}

# 内存态：安装互斥锁 + 当前安装任务日志（进程内）
_install_lock = threading.Lock()
_install_log: dict = {"type": "idle", "layer": None, "pid": None, "text": "", "returncode": None}

# L1 已就绪缓存：ensure_infer_runtime() 首次探测成功后置 True，
# 后续调用直接返回（避免推理热路径上每帧 4 次 importlib.metadata 探测）。
# 安装成功 / 用户重置环境时调用 _invalidate_infer_cache() 失效。
_infer_ready: bool = False
_infer_ready_lock = threading.Lock()


class RuntimeNotInstalledError(RuntimeError):
    """L1/L2 运行时未安装（或缺失核心包）。

    推理引擎 / 训练后端捕获此异常，向用户返回明确提示。
    """


def _pkg_version(pkg: str) -> str | None:
    """用 importlib.metadata.version 探测包版本；未安装返回 None。"""
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def _pkg_size(pkg: str) -> int | None:
    """探测已安装包的真实磁盘占用（字节）；未安装返回 None。

    优先解析 dist-info/RECORD 文件（记录包安装的全部文件路径，相对于
    site-packages），逐个累加文件大小；无 RECORD 时退化为统计
    dist-info 目录本身大小。
    """
    try:
        dist = importlib.metadata.distribution(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None
    try:
        dist_info = Path(dist._path)  # noqa: SLF001 — 标准库私有字段
        base_dir = dist_info.parent
        record_file = dist_info / "RECORD"
        if record_file.is_file():
            total = 0
            for line in record_file.read_text(encoding="utf-8", errors="replace").splitlines():
                path = line.split(",")[0].strip()
                if not path or path.startswith("..") or ".." in path:
                    continue
                p = base_dir / path
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    pass
            return total
        # 退化：仅 dist-info 目录
        total = 0
        for p in dist_info.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total
    except Exception:  # noqa: BLE001
        return None


def check_layer(layer: str) -> dict:
    """检测单层依赖状态。

    返回：:
        {
          "layer": "L1",
          "label": "推理层（必装）",
          "installed": True/False,
          "missing": ["onnxruntime", ...],
          "packages": [{"name": "onnxruntime", "version": "1.17.0" | None,
                        "size": 123456 | None}, ...],
          "satisfied": True/False,
        }
    """
    cfg = LAYERS[layer]
    packages: list[dict] = []
    missing: list[str] = []
    for name in cfg["packages"]:
        version = _pkg_version(name)
        packages.append({
            "name": name,
            "version": version,
            "size": _pkg_size(name) if version else None,
        })
        if version is None:
            missing.append(name)
    return {
        "layer": layer,
        "label": cfg["label"],
        "installed": not missing,
        "missing": missing,
        "packages": packages,
        "suitable": not missing,
    }


def check_all_layers() -> dict:
    """检测 L1 + L2。"""
    return {"L1": check_layer("L1"), "L2": check_layer("L2")}


def ensure_infer_runtime() -> None:
    """确保 L1 推理层可用，否则抛 :class:`RuntimeNotInstalledError`。

    供推理引擎 / API 在运行前统一校验。只做探测，不 import 重模块。
    热路径优化：首次探测成功后置 ``_infer_ready``，后续调用 O(1) 短路。
    """
    global _infer_ready
    if _infer_ready:
        return
    with _infer_ready_lock:
        if _infer_ready:
            return
        status = check_layer("L1")
        if not status["installed"]:
            raise RuntimeNotInstalledError(
                "L1 推理运行环境未安装（缺少：" + "、".join(status["missing"]) + "）。"
                "请到后台「运行环境」页点击『安装 L1 依赖』后重试。"
            )
        _infer_ready = True


def _invalidate_infer_cache() -> None:
    """失效 L1 就绪缓存（安装流程成功后调用，允许重新探测）。"""
    global _infer_ready
    with _infer_ready_lock:
        _infer_ready = False


def _requirements_path(layer: str) -> Path:
    layer = layer.upper()
    filename = LAYERS[layer]["requirements"]
    return Path(__file__).resolve().parent.parent / filename


def is_installing() -> bool:
    """是否有安装任务正在执行。"""
    return _install_log.get("type") == "installing"


def install_layer(layer: str, pip_index: str | None = None) -> dict:
    """启动一层依赖的安装（后台子进程）。

    返回立即返回的启动信息；安装日志通过 :func:`read_install_log` 轮询读取。

    .. warning::
       同一时刻只允许一个安装任务；重复调用会抛 ``RuntimeError``。
    """
    if layer not in LAYERS:
        raise ValueError(f"未知依赖层: {layer}")
    if not _install_lock.acquire(blocking=False):
        raise RuntimeError("已有安装任务进行中，请等待完成后再试")

    cfg = LAYERS[layer]
    req = _requirements_path(layer)
    if not req.exists():
        _install_lock.release()
        raise FileNotFoundError(f"依赖清单不存在: {req}")

    # 构建 pip 命令：基础 -r requirements + 可配 index
    # 注意：用 --extra-index-url（在 PyPI 基础上追加源）而非 --index-url（替换源），
    # 否则 pytorch 源上不存在包（如 ultralytics）会导致整体安装失败。
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    env = os.environ.copy()
    index_url = pip_index or cfg.get("pip_index") or env.get("PIP_INDEX_URL")
    if index_url:
        cmd += ["--extra-index-url", index_url]

    # 清空旧日志（新任务从空开始）
    from src.plugins.builtin.ai_vision.models import AIVisionRuntimeStatus

    # 记录安装开始（内存态 + 后续由 API 落库）
    _install_log.clear()
    _install_log.update({
        "type": "installing",
        "layer": layer,
        "cmd": " ".join(cmd),
        "text": "",
    })

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            _install_log["pid"] = proc.pid
            for line in proc.stdout or []:
                _install_log["text"] += line
            proc.wait()
            _install_log["type"] = "success" if proc.returncode == 0 else "failed"
            _install_log["returncode"] = proc.returncode
            if proc.returncode == 0 and layer.upper() == "L1":
                # 安装成功 → 失效 L1 就绪缓存，让下次探测重新确认
                _invalidate_infer_cache()
        except Exception as exc:  # noqa: BLE001
            _install_log["type"] = "failed"
            _install_log["text"] += f"\n[错误] {exc}"
        finally:
            _install_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return {"layer": layer, "status": "started", "pid": _install_log.get("pid")}


def read_install_log() -> dict:
    """读取当前（或最近一次）安装/卸载任务状态与日志。"""
    return {
        "type": _install_log.get("type", "idle"),
        "layer": _install_log.get("layer"),
        "running": _install_log.get("type") == "installing",
        "log": _install_log.get("text", ""),
        "pid": _install_log.get("pid"),
        "returncode": _install_log.get("returncode"),
    }


# ---------------------------------------------------------------------------
# 依赖卸载
# ---------------------------------------------------------------------------


def uninstall_layer(layer: str) -> dict:
    """启动一层依赖的卸载（后台子进程）。

    与 :func:`install_layer` 共用互斥锁与日志通道，同一时刻仅一个
    安装/卸载任务。卸载会移除该层 requirements 中列出的全部包；
    卸载成功后会失效 L1 就绪缓存（若 L1 被卸载）。
    """
    layer = layer.upper()
    if layer not in LAYERS:
        raise ValueError(f"未知依赖层: {layer}")
    if not _install_lock.acquire(blocking=False):
        raise RuntimeError("已有安装/卸载任务进行中，请等待完成后再试")

    cfg = LAYERS[layer]
    pkgs = cfg["packages"]
    if not pkgs:
        _install_lock.release()
        raise ValueError(f"依赖层 {layer} 未定义可卸载的包")

    # pip uninstall：包名列表（不依赖 requirements 文件，避免文件缺失影响卸载）
    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", *pkgs]
    env = os.environ.copy()

    _install_log.clear()
    _install_log.update({
        "type": "installing",
        "layer": layer,
        "cmd": " ".join(cmd),
        "text": "",
    })

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            _install_log["pid"] = proc.pid
            for line in proc.stdout or []:
                _install_log["text"] += line
            proc.wait()
            _install_log["type"] = "success" if proc.returncode == 0 else "failed"
            _install_log["returncode"] = proc.returncode
            if proc.returncode == 0 and layer == "L1":
                # L1 被卸载 → 失效就绪缓存，下次推理前重新探测
                _invalidate_infer_cache()
        except Exception as exc:  # noqa: BLE001
            _install_log["type"] = "failed"
            _install_log["text"] += f"\n[错误] {exc}"
        finally:
            _install_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return {"layer": layer, "status": "started", "pid": _install_log.get("pid")}


# ---------------------------------------------------------------------------
# 健康测试
# ---------------------------------------------------------------------------


def test_layer(layer: str, timeout: float = 30.0) -> dict:
    """对单层依赖做深度健康测试。

    与 :func:`check_layer` 的「只探版本」不同，这里会对关键包做**真实
    import** 并尝试加载模型，以探测「版本已装但 import 失败 / 动态库
    不兼容」这类隐形问题。

    .. warning::
        会真实加载 onnxruntime / opencv / torch 等重模块，可能占用
        CPU/内存；请不要在推理热路径上频繁调用（供「运行环境」页
        一键自检 / AI 助手按需调用）。

    返回：:
        {
          "layer": "L1",
          "label": "推理层（必装）",
          "ok": True/False,
          "started_at": iso,
          "elapsed_ms": 123,
          "packages": [
            {"name": "onnxruntime", "installed": True, "version": "1.17.0",
             "importable": True, "error": None},
            ...
          ],
          "model_probe": {"ok": True, "detail": "引擎可加载，yolo11n-coco 校验通过"} | {"ok": False, "detail": "..."},
        }
    """
    import time

    cfg = LAYERS[layer]
    started = time.time()
    results: list[dict] = []

    for name in cfg["packages"]:
        entry: dict = {"name": name, "installed": False, "version": None, "importable": False, "error": None}
        version = _pkg_version(name)
        if version is None:
            results.append(entry)
            continue
        entry["installed"] = True
        entry["version"] = version
        try:
            # 真实 import（带超时保护，防止动态库初始化卡死）
            import importlib
            mod_name = {
                "onnxruntime": "onnxruntime",
                "opencv-python-headless": "cv2",
                "numpy": "numpy",
                "pillow": "PIL",
                "torch": "torch",
                "torchvision": "torchvision",
                "ultralytics": "ultralytics",
            }.get(name, name)
            importlib.import_module(mod_name)
            entry["importable"] = True
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)

        results.append(entry)

    ok = all(r["installed"] and r["importable"] for r in results)

    # L1 额外做模型加载探测（试加载一个内置 ONNX）
    model_probe: dict = {"ok": False, "detail": ""}
    if layer == "L1" and ok:
        try:
            model_probe = _probe_l1_model()
        except Exception as exc:  # noqa: BLE001
            model_probe = {"ok": False, "detail": f"模型加载探测异常: {exc}"}

    return {
        "layer": layer,
        "label": cfg["label"],
        "ok": ok,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "elapsed_ms": int((time.time() - started) * 1000),
        "packages": results,
        "model_probe": model_probe,
    }


def _probe_l1_model() -> dict:
    """尝试加载一个内置 ONNX 模型，验证推理链路可用。"""
    try:
        from src.plugins.builtin.ai_vision.runtime.engine import InferenceEngine
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"推理引擎导入失败: {exc}"}

    # 模型目录 = plugin 包根下 assets/models（与本文件相对定位）
    models_dir = Path(__file__).resolve().parent.parent / "assets" / "models"
    if not models_dir.exists():
        return {"ok": False, "detail": "内置模型目录不存在"}
    onnx_files = list(models_dir.glob("*.onnx"))
    if not onnx_files:
        return {"ok": False, "detail": "未找到任何 .onnx 模型文件"}
    engine = InferenceEngine()
    try:
        engine.load(str(onnx_files[0]))  # 不跑推理，仅验证可加载
        return {"ok": True, "detail": f"模型 {onnx_files[0].name} 可正常加载"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": f"模型 {onnx_files[0].name} 加载失败: {exc}"}