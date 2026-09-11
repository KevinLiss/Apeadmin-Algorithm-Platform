"""RTSP 抓流工具：连通性测试 + 快照抓取。

设计原则（见任务 1.4）：
- 本模块**延迟导入** opencv，调用前先 ``ensure_infer_runtime()``，
  未安装 L1 时抛 :class:`RuntimeNotInstalledError`，由 API 层转为
  409 响应并提示「请先安装运行环境」。
- 所有耗时操作（open / read / encode）在线程中执行（asyncio.to_thread），
  避免阻塞事件循环；单次测试默认 5 秒超时。
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

from src.plugins.builtin.ai_vision.runtime.deps import (
    RuntimeNotInstalledError,
    ensure_infer_runtime,
)


def _do_test(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """同步执行 RTSP 连通性测试（在 to_thread 中调用）。

    返回：:
        {
          "ok": True/False,
          "latency_ms": int,
          "snapshot_base64": str | None,   # JPEG base64，成功时
          "width": int, "height": int,
          "error": str | None,
        }
    """
    import cv2

    result: dict[str, Any] = {
        "ok": False,
        "latency_ms": 0,
        "snapshot_base64": None,
        "width": 0,
        "height": 0,
        "error": None,
    }
    cap = None
    try:
        start = time.perf_counter()
        # 打开流（FFMPEG 后端；设置打开超时毫秒）
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
        if not cap.isOpened():
            result["error"] = "无法打开视频流（RTSP 地址不可达或格式不支持）"
            return result

        # 读一帧（首次读帧也可能阻塞，由调用方整体超时保护）
        ok, frame = cap.read()
        if not ok or frame is None:
            result["error"] = "视频流已连接但无法读取画面（可能为纯音频流或权限受限）"
            return result

        latency = int((time.perf_counter() - start) * 1000)
        h, w = frame.shape[:2]

        # JPEG 编码 → base64
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            result["error"] = "快照编码失败"
            return result
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")

        result.update(
            ok=True,
            latency_ms=latency,
            snapshot_base64=b64,
            width=w,
            height=h,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"测试异常: {exc}"
        return result
    finally:
        if cap is not None:
            cap.release()


async def test_rtsp(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """异步 RTSP 连通性测试（to_thread 执行，不阻塞事件循环）。

    未安装 L1 时抛 :class:`RuntimeNotInstalledError`，由 API 层转为
    409 响应并提示「请先安装运行环境」。

    注意：cv2 的 ``CAP_PROP_OPEN_TIMEOUT_MSEC`` 对部分 RTSP 源不生效，
    这里用 ``asyncio.wait_for`` 做整体超时兜底；超时后返回失败结果
    （底层线程无法强制终止，属已知限制，测试接口短时使用可接受）。
    """
    ensure_infer_runtime()

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_do_test, url, timeout),
            timeout=timeout + 2.0,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "latency_ms": int(timeout * 1000),
            "snapshot_base64": None,
            "width": 0,
            "height": 0,
            "error": f"测试超时（>{timeout:.0f} 秒无响应）",
        }