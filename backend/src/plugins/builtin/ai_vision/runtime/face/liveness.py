# -*- coding: utf-8 -*-
"""人脸活体检测引擎（P4）：MiniFASNetV2-SE（INT8 ONNX，Apache-2.0）。

用法：:class:`FaceDetector` 在识别前对每张人脸调用 :meth:`score`，
低于阈值视为"翻拍攻击"（照片/屏幕），直接跳过识别——不跟踪、不告警、
不记录（策略：挡在门外，静默丢弃）。

预处理与输出语义（严格按上游 demo 移植）：
1. 人脸框按 ``1.5×`` 边长扩展取方框（越界反射 padding）；
2. letterbox 缩放到 128×128（BORDER_REFLECT_101），RGB，/255，CHW；
3. 输出 2 维 logits ``[real, spoof]``，真人概率
   ``p = sigmoid(real - spoof)``，``p >= threshold``（默认 0.5）判真人。

威胁模型与局限（重要，面向用户说明用）：
- **挡得住**：摄像头翻拍打印纸张（纸纹/边缘）、屏幕翻拍（摩尔纹/边框/
  刷新纹）——模型依赖拍摄载体的工件特征；
- **挡不住**：① 干净的数码图片文件直喂识别管线（非真实攻击路径——
  真实场景是摄像头对着纸/屏幕）；② 高清屏 + 优质摄像头的翻拍是业界
  公认难点，阈值可调但别指望 100%；
- 单帧约 8~14ms（i7-6700，INT8），可按事件规则开关。

模型文件缺失时 ``get_liveness_engine()`` 返回 None；调用方（detector）
采取 **fail-open + 告警日志**：不阻断识别主流程（与 FaceEngine 约定一致）。
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

_MODEL = (
    Path(__file__).resolve().parents[2] / "assets" / "models"
    / "face_antispoof_minifasnet_v2se.onnx"
)

_INPUT_SIZE = 128
_EXPANSION = 1.5  # 上游 demo 的 bbox_expansion_factor 默认值
DEFAULT_LIVENESS_THRESHOLD = 0.5


class LivenessEngine:
    """MiniFASNetV2-SE 活体检测（进程内可多实例；默认路径取单例）。"""

    def __init__(self, model_path: str | Path | None = None) -> None:
        import onnxruntime as ort

        path = str(model_path or _MODEL)
        if not Path(path).exists():
            raise FileNotFoundError(f"活体模型文件不存在: {path}")
        self._sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self._in_name = self._sess.get_inputs()[0].name

    def score(self, frame_bgr: np.ndarray, bbox: list[float] | tuple) -> float | None:
        """单人脸 → 真人概率（0~1）；裁剪/推理失败返回 None。

        Args:
            frame_bgr: OpenCV BGR 图像。
            bbox: 人脸框 [x1, y1, x2, y2]（原图像素坐标，YuNet 输出格式）。
        """
        import cv2

        x1, y1, x2, y2 = (float(v) for v in bbox)
        bw, bh = x2 - x1, y2 - y1
        if bw <= 0 or bh <= 0:
            return None
        img = frame_bgr[:, :, ::-1]  # BGR → RGB（上游 demo 口径）
        h, w = img.shape[:2]
        max_dim = max(bw, bh)
        cx, cy = x1 + bw / 2, y1 + bh / 2
        x = int(cx - max_dim * _EXPANSION / 2)
        y = int(cy - max_dim * _EXPANSION / 2)
        size = int(max_dim * _EXPANSION)
        crop = img[max(0, y):min(h, y + size), max(0, x):min(w, x + size)]
        if crop.size == 0:
            return None
        pad_t, pad_l = max(0, -y), max(0, -x)
        pad_b = max(0, (y + size) - h)
        pad_r = max(0, (x + size) - w)
        if pad_t or pad_b or pad_l or pad_r:
            crop = np.pad(
                crop, ((pad_t, pad_b), (pad_l, pad_r), (0, 0)), mode="reflect"
            )
        # letterbox 到 128×128（反射 padding，与上游一致）
        ratio = _INPUT_SIZE / max(crop.shape[:2])
        nw, nh = int(crop.shape[1] * ratio), int(crop.shape[0] * ratio)
        interp = cv2.INTER_LANCZOS4 if ratio > 1.0 else cv2.INTER_AREA
        small = cv2.resize(crop, (nw, nh), interpolation=interp)
        dw, dh = _INPUT_SIZE - nw, _INPUT_SIZE - nh
        if dw or dh:
            small = np.pad(
                small,
                ((dh // 2, dh - dh // 2), (dw // 2, dw - dw // 2), (0, 0)),
                mode="reflect",
            )
        blob = small.transpose(2, 0, 1).astype(np.float32)[None] / 255.0
        logits = self._sess.run([], {self._in_name: blob})[0][0]
        diff = float(logits[0]) - float(logits[1])
        return 1.0 / (1.0 + np.exp(-diff))


_inst: LivenessEngine | None = None
_lock = threading.Lock()


def get_liveness_engine() -> LivenessEngine | None:
    """进程级单例；模型缺失/加载失败返回 None（调用方 fail-open）。"""
    global _inst
    with _lock:
        if _inst is None:
            if not _MODEL.exists():
                return None
            try:
                _inst = LivenessEngine()
            except Exception:  # noqa: BLE001
                return None
        return _inst


def liveness_model_ready() -> bool:
    """活体模型文件是否就位（前端提示/运行环境自检用）。"""
    return _MODEL.exists()
