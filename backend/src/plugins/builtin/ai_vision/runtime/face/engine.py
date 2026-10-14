# -*- coding: utf-8 -*-
"""人脸识别引擎（人脸库登记 + worker 识别共用）。

模型：YuNet（人脸检测 + 5 关键点）+ SFace（128 维特征），均来自
opencv_zoo（MIT 许可，可商用）。运行方式为 OpenCV 官方 demo 用法：
``cv2.FaceDetectorYN`` / ``cv2.FaceRecognizerSF`` 直接加载 ONNX，
预处理 / 解码 / NMS / 5 点对齐全部由 OpenCV 内置处理，无需自行移植。

P0 标定结论（2026-10-14，i7-6700 实测）：
- 检测 score 阈值用 **0.6**：官方默认 0.9 在监控/压缩/远景场景漏检严重
  （测试图 8/11 被卡，实际分数 0.62~0.86）；
- SFace 余弦相似度阈值 **0.363**（官方值）：实测同人 min 0.634 /
  异人 max 0.209，分布完全分离，0.363 落在间隙内；
- 单帧耗时约 56ms（检测 45 + 对齐 0.2 + 特征 10），全速 18fps；
  按 1s 识别间隔可支撑 8 路。

模型文件缺失 / 加载失败时 ``get_face_engine()`` 返回 None，调用方降级
（API 返回 503、worker 跳过识别，均不阻断主流程）——与
``appearance.py`` 的外观特征引擎同一约定。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# 模型文件（assets/models/ 下，manifest.json 登记 + SHA256 校验）
_MODELS_DIR = Path(__file__).resolve().parents[2] / "assets" / "models"
_DET_MODEL = _MODELS_DIR / "face_detection_yunet_2023mar.onnx"
_REC_MODEL = _MODELS_DIR / "face_recognition_sface_2021dec.onnx"

# P0 结论：检测 score 阈值 0.6（官方 0.9 监控场景漏检严重）
DET_SCORE_THRESHOLD = 0.6
# SFace 官方余弦阈值（P0 实测落在同人/异人分布间隙内）
REC_COSINE_THRESHOLD = 0.363


@dataclass(slots=True)
class FaceDetection:
    """单张人脸（YuNet detect 输出解析，原图像素坐标）。"""

    bbox: list[float]  # [x1, y1, x2, y2]
    score: float
    landmarks: list[list[float]]  # (5,2)：右眼/左眼/鼻尖/右嘴角/左嘴角
    face_row: np.ndarray  # YuNet 原始 15 维行（alignCrop 直接用，勿改结构）

    @property
    def area(self) -> float:
        return max(0.0, self.bbox[2] - self.bbox[0]) * max(0.0, self.bbox[3] - self.bbox[1])


class FaceEngine:
    """YuNet 检测 + SFace 特征（进程内可多实例；默认路径取单例）。

    实例化参数允许调用方指定模型路径（如 worker 按事件绑定的模型记录
    解析路径），未指定时用 assets/models 下的内置文件。
    """

    def __init__(
        self,
        det_model_path: str | Path | None = None,
        rec_model_path: str | Path | None = None,
        score_threshold: float = DET_SCORE_THRESHOLD,
    ) -> None:
        import cv2 as cv

        det_path = str(det_model_path or _DET_MODEL)
        rec_path = str(rec_model_path or _REC_MODEL)
        for p in (det_path, rec_path):
            if not Path(p).exists():
                raise FileNotFoundError(f"人脸模型文件不存在: {p}")

        self._det = cv.FaceDetectorYN.create(
            model=det_path,
            config="",
            input_size=(320, 320),
            score_threshold=score_threshold,
            nms_threshold=0.3,
            top_k=5000,
            backend_id=cv.dnn.DNN_BACKEND_OPENCV,
            target_id=cv.dnn.DNN_TARGET_CPU,
        )
        self._rec = cv.FaceRecognizerSF.create(
            model=rec_path,
            config="",
            backend_id=cv.dnn.DNN_BACKEND_OPENCV,
            target_id=cv.dnn.DNN_TARGET_CPU,
        )

    # ── 检测 ────────────────────────────────────────────────
    def detect_faces(self, frame_bgr: np.ndarray) -> list[FaceDetection]:
        """检测帧内全部人脸（按框面积降序）。无人脸返回空列表。"""
        import cv2 as cv  # noqa: F401 — 延迟导入约定（本类 __init__ 已 import）

        h, w = frame_bgr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(frame_bgr)
        if faces is None or len(faces) == 0:
            return []
        out: list[FaceDetection] = []
        for row in faces:
            x, y, bw, bh = (float(v) for v in row[:4])
            out.append(
                FaceDetection(
                    bbox=[x, y, x + bw, y + bh],
                    score=float(row[14]),
                    landmarks=[[float(v) for v in p] for p in row[4:14].reshape(5, 2)],
                    face_row=np.asarray(row, dtype=np.float32),
                )
            )
        out.sort(key=lambda f: f.area, reverse=True)
        return out

    def detect_largest(self, frame_bgr: np.ndarray) -> FaceDetection | None:
        """仅取最大人脸（登记/特写场景常用）。"""
        faces = self.detect_faces(frame_bgr)
        return faces[0] if faces else None

    # ── 特征 ────────────────────────────────────────────────
    def embed(self, frame_bgr: np.ndarray, face: FaceDetection) -> np.ndarray | None:
        """对齐 + 提 128 维 L2 归一化特征；失败返回 None。"""
        aligned = self._rec.alignCrop(frame_bgr, face.face_row[:-1])
        if aligned is None or aligned.size == 0:
            return None
        feat = self._rec.feature(aligned).flatten()
        norm = float(np.linalg.norm(feat))
        if norm <= 0:
            return None
        return (feat / norm).astype(np.float32)

    def embed_frame(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, FaceDetection] | None:
        """检测最大人脸并提特征（登记一键用）；无人脸返回 None。"""
        face = self.detect_largest(frame_bgr)
        if face is None:
            return None
        vec = self.embed(frame_bgr, face)
        if vec is None:
            return None
        return vec, face


# ── 进程级单例（默认 assets/models 路径）────────────────────

_inst: FaceEngine | None = None
_lock = threading.Lock()


def get_face_engine() -> FaceEngine | None:
    """进程级单例；模型缺失/加载失败返回 None（调用方降级）。"""
    global _inst
    with _lock:
        if _inst is None:
            if not (_DET_MODEL.exists() and _REC_MODEL.exists()):
                return None
            try:
                _inst = FaceEngine()
            except Exception:  # noqa: BLE001
                return None
        return _inst


def face_models_ready() -> bool:
    """两个内置模型文件是否就位（运行环境自检/前端提示用）。"""
    return _DET_MODEL.exists() and _REC_MODEL.exists()
