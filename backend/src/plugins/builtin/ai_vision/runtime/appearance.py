"""外观特征引擎（人岗验证）：yolo11n-cls ONNX → 人体裁剪图 1000 维 L2 归一化向量。

用法：worker 的 IdentityDetector 对 ROI 内最大人框裁剪（带 padding）后
调用 ``embed_crop`` 提特征，与员工档案（ai_vision_staff.embedding）做
余弦相似度比对。ImageNet 分类骨干对衣着颜色/纹理/体态敏感，适合
"当班着装登记"式的人岗验证；同款工装的区分力有限，建议按班次登记。

模型文件缺失时 ``get_appearance_engine()`` 返回 None，调用方降级跳过
（不阻断 worker 主循环）。
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

_MODEL = Path(__file__).resolve().parents[1] / "assets" / "models" / "yolo11n-cls.onnx"


class AppearanceEngine:
    """轻量外观嵌入：224×224 输入，单次约 7ms（CPU）。"""

    def __init__(self) -> None:
        import onnxruntime as ort

        self._sess = ort.InferenceSession(str(_MODEL), providers=["CPUExecutionProvider"])
        self._in_name = self._sess.get_inputs()[0].name

    def embed_crop(self, frame: np.ndarray, bbox: tuple | list, pad: float = 0.15) -> np.ndarray | None:
        """人体框（像素坐标）裁剪 + padding → 归一化特征向量；裁剪无效返回 None。"""
        import cv2

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in bbox)
        bw, bh = x2 - x1, y2 - y1
        ix1 = max(0, int(x1 - bw * pad))
        iy1 = max(0, int(y1 - bh * pad))
        ix2 = min(w, int(x2 + bw * pad))
        iy2 = min(h, int(y2 + bh * pad))
        crop = frame[iy1:iy2, ix1:ix2]
        if crop.size == 0 or crop.shape[0] < 8 or crop.shape[1] < 8:
            return None
        img = cv2.resize(crop, (224, 224))
        blob = img[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self._sess.run(None, {self._in_name: blob})[0][0]
        norm = float(np.linalg.norm(out))
        if norm <= 0:
            return None
        return (out / norm).astype(np.float32)


_inst: AppearanceEngine | None = None
_lock = threading.Lock()


def get_appearance_engine() -> AppearanceEngine | None:
    """进程级单例；模型缺失/加载失败返回 None（调用方降级）。"""
    global _inst
    with _lock:
        if _inst is None:
            if not _MODEL.exists():
                return None
            try:
                _inst = AppearanceEngine()
            except Exception:  # noqa: BLE001
                return None
        return _inst
