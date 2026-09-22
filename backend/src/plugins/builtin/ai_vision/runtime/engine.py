"""ONNX 推理引擎（任务 2.1）。

职责：
- 加载 YOLO 系 ONNX 模型（CPU），返回 :class:`ModelHandle`（LRU 缓存，
  按文件 mtime+size 失效，避免重复加载同一文件）；
- ``detect(frame_bgr, handle, threshold, classes_filter)`` 返回结构化
  :class:`Detection` 列表；
- 类别映射：模型输出类别 ID → 平台类别编码（category_map，如 COCO
  car/bus/truck → vehicle 聚合），由调用方在创建 handle 时传入
  （任务 2.2 的 manifest / models 表提供）。

设计原则：
- 本模块**延迟导入** onnxruntime / numpy / cv2（保证插件加载零重依赖）；
- 线程安全：onnxruntime ``InferenceSession.run`` 是线程安全的，
  ``detect`` 可被多个 worker 线程并发调用；
- 单帧延迟目标 ≤80ms（YOLO11n CPU）。

.. note::
    置信度过滤 / NMS / letterbox 逆变换在 ``yolo_postprocess.py`` 实现；
    本引擎负责「张量预处理 → 推理 → 解码回原图坐标」。
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.plugins.builtin.ai_vision.runtime.deps import (
    RuntimeNotInstalledError,
    ensure_infer_runtime,
)

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Detection:
    """单目标检测结果（原图坐标）。"""

    bbox: list[float]  # [x1, y1, x2, y2]
    conf: float
    cls_id: int
    cls_name: str  # 平台类别编码（如 person/vehicle/fire/smoke）


@dataclass(slots=True)
class PoseDetection:
    """单目标姿态检测结果（原图坐标）。

    keypoints: 17 项 COCO 关键点 ``[[x, y, conf], ...]``；
    conf < kpt_thr 的点坐标仍有效但可信度低，调用方自行过滤。
    """

    bbox: list[float]  # [x1, y1, x2, y2]
    conf: float
    cls_id: int
    cls_name: str
    keypoints: list[list[float]]  # (17, 3)


@dataclass(slots=True)
class ModelHandle:
    """已加载的 ONNX 模型（会话 + 元数据）。"""

    session: Any
    input_size: int
    # 模型输出类别 ID → 显示名（COCO 80 类时用官方名）
    names: dict[int, str]
    # 模型输出类别 ID → 平台类别编码（聚合映射，如 {2:"vehicle",5:"vehicle",7:"vehicle"}）
    category_map: dict[int, str]
    file_path: str
    mtime_ns: int
    size: int
    # pose 模型专属：关键点形状 (nk, kd)，从 ONNX 元数据 kpt_shape 读取；
    # 检测模型为默认 (17, 3) 但不会被使用（detect_pose 校验 task=="pose"）
    kpt_shape: tuple[int, int] = (17, 3)
    is_pose: bool = False


# ---------------------------------------------------------------------------
# 推理引擎
# ---------------------------------------------------------------------------


class InferenceEngine:
    """YOLO ONNX 推理引擎（进程内单例，线程安全）。

    LRU 缓存按 ``mtime + size`` 判定文件是否变化：文件被替换/重新导出
    后自动重新加载；``load()`` 传入不同路径即不同 key。
    """

    _instance: "InferenceEngine | None" = None
    _singleton_lock = threading.Lock()
    _cache_lock = threading.Lock()  # 保护 _cache / _cache_order

    def __init__(self) -> None:
        self._cache: dict[str, ModelHandle] = {}
        self._cache_order: list[str] = []
        self._cache_capacity = 4  # LRU 最多缓存 4 个模型

    # ── 单例 ─────────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "InferenceEngine":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 公共 API ──────────────────────────────────────────
    def load(
        self,
        model_path: str,
        names: dict[int, str] | None = None,
        category_map: dict[int, str] | None = None,
    ) -> ModelHandle:
        """加载（或复用缓存）模型。

        Args:
            model_path: ONNX 文件绝对路径。
            names: 模型类别 ID → 显示名（缺省则用 str(id)）。
            category_map: 模型类别 ID → 平台类别编码（缺省则映射到自身）。

        Returns:
            :class:`ModelHandle`。

        Raises:
            RuntimeNotInstalledError: L1 未安装。
            FileNotFoundError: 模型文件不存在。
        """
        ensure_infer_runtime()
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"模型文件不存在: {path}")

        stat = path.stat()
        key = str(path)

        with self._cache_lock:
            handle = self._cache.get(key)
            if handle and handle.mtime_ns == stat.st_mtime_ns and handle.size == stat.st_size:
                # 命中缓存：移动到 LRU 末尾
                self._cache_order.remove(key)
                self._cache_order.append(key)
                return handle

        # 未命中或文件已变化 → 重新加载
        handle = self._load_real(path, names or {}, category_map or {})
        with self._cache_lock:
            self._cache[key] = handle
            if key in self._cache_order:
                self._cache_order.remove(key)
            self._cache_order.append(key)
            while len(self._cache_order) > self._cache_capacity:
                old_key = self._cache_order.pop(0)
                self._cache.pop(old_key, None)
        return handle

    @property
    def cache_size(self) -> int:
        with self._cache_lock:
            return len(self._cache)

    def clear_cache(self) -> None:
        """清空模型缓存（卸载插件时调用）。"""
        with self._cache_lock:
            self._cache.clear()
            self._cache_order.clear()

    # ── 内部实现 ──────────────────────────────────────────
    def _load_real(
        self,
        path: Path,
        names: dict[int, str],
        category_map: dict[int, str],
    ) -> ModelHandle:
        """创建新会话并探测输入尺寸。"""
        import onnxruntime as ort

        sess_options = ort.SessionOptions()
        try:
            sess_options.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
        except Exception:  # noqa: BLE001
            pass
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        session = ort.InferenceSession(
            str(path),
            sess_options=sess_options,
            providers=["CPUExecutionProvider"],
        )
        stat = path.stat()

        # 输入尺寸：从模型输入张量推导
        input_size = 640
        try:
            shape = session.get_inputs()[0].shape
            if len(shape) == 4 and shape[2] and shape[3]:
                input_size = int(shape[2])
        except Exception:  # noqa: BLE001
            pass

        # pose 模型探测：ultralytics 导出时写入 task/kpt_shape 元数据；
        # 无元数据时按输出列数推断（4+nc+nk*kd，nk=17/kd=3 时列数 = 56+nc）
        kpt_shape = (17, 3)
        is_pose = False
        try:
            meta = session.get_modelmeta().custom_metadata_map or {}
            if str(meta.get("task", "")).lower() == "pose":
                is_pose = True
                ks_raw = meta.get("kpt_shape")
                if ks_raw:
                    import json as _json

                    ks = _json.loads(ks_raw)
                    if isinstance(ks, (list, tuple)) and len(ks) == 2:
                        kpt_shape = (int(ks[0]), int(ks[1]))
            if not is_pose:
                out_dim = int(session.get_outputs()[0].shape[1])
                if out_dim >= 4 + 17 * 3 + 1:  # 56 列起（nc≥1）→ 疑似 pose
                    remainder = out_dim - 4 - 1
                    if remainder % (17 * 3) == 0:
                        is_pose = True
                        kpt_shape = (17, 3)
        except Exception:  # noqa: BLE001
            pass

        # 缺省类别映射：id → 自身编码
        if not category_map:
            # 从 names 推导；无 names 时用 str(id)
            category_map = {cid: names.get(cid, str(cid)) for cid in names} or {}

        return ModelHandle(
            session=session,
            input_size=input_size,
            names=names or {},
            category_map=category_map,
            file_path=str(path),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            kpt_shape=kpt_shape,
            is_pose=is_pose,
        )

    # ── 推理 ──────────────────────────────────────────────
    def detect(
        self,
        frame_bgr: Any,
        handle: ModelHandle,
        threshold: float = 0.45,
        classes_filter: set[int] | None = None,
        iou_thr: float = 0.45,
    ) -> list[Detection]:
        """对单帧 BGR 图像执行检测。

        Args:
            frame_bgr: OpenCV BGR 图像（numpy uint8）。
            handle: :meth:`load` 返回的模型句柄。
            threshold: 置信度阈值。
            classes_filter: 仅保留这些模型类别 ID（None = 全部）。
            iou_thr: NMS IoU 阈值。

        Returns:
            :class:`Detection` 列表（按置信度降序，原图坐标）。

        Raises:
            RuntimeNotInstalledError: L1 未安装。
        """
        ensure_infer_runtime()
        import numpy as np

        from src.plugins.builtin.ai_vision.runtime.yolo_postprocess import (
            letterbox,
            postprocess,
        )

        h, w = frame_bgr.shape[:2]
        input_size = handle.input_size

        # 预处理：letterbox → BGR2RGB → HWC2CHW → /255
        img, ratio, pad, _ = letterbox(frame_bgr, (input_size, input_size))
        img = img[:, :, ::-1]  # BGR→RGB
        img = np.ascontiguousarray(img.transpose(2, 0, 1)).astype(np.float32) / 255.0
        tensor = img[None, ...]  # (1,3,H,W)

        # 推理
        session = handle.session
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: tensor})[0]

        # 后处理（NMS 前按类别过滤以省计算）
        dets = postprocess(
            output,
            orig_shape=(h, w),
            pad=pad,
            ratio=ratio,
            class_names=handle.names,
            conf_thr=threshold,
            iou_thr=iou_thr,
        )

        results: list[Detection] = []
        for d in dets:
            cid = d["cls_id"]
            if classes_filter is not None and cid not in classes_filter:
                continue
            results.append(
                Detection(
                    bbox=d["bbox"],
                    conf=d["conf"],
                    cls_id=cid,
                    cls_name=handle.category_map.get(cid, str(cid)),
                )
            )
        return results

    # ── 姿态推理 ────────────────────────────────────────────
    def detect_pose(
        self,
        frame_bgr: Any,
        handle: ModelHandle,
        threshold: float = 0.35,
        classes_filter: set[int] | None = None,
        iou_thr: float = 0.45,
    ) -> list[PoseDetection]:
        """对单帧 BGR 图像执行姿态检测（yolo11-pose 系 ONNX）。

        与 :meth:`detect` 相同的预处理/逆变换流程，额外解码 17 个
        关键点（原图坐标 + 每点置信度）。

        Args:
            frame_bgr: OpenCV BGR 图像（numpy uint8）。
            handle: :meth:`load` 返回的模型句柄（须为 pose 模型）。
            threshold: 目标置信度阈值（pose 建议 0.3~0.4）。
            classes_filter: 仅保留这些模型类别 ID（None = 全部）。
            iou_thr: NMS IoU 阈值。

        Returns:
            :class:`PoseDetection` 列表（按置信度降序，原图坐标）。

        Raises:
            ValueError: handle 不是 pose 模型。
        """
        ensure_infer_runtime()
        import numpy as np

        from src.plugins.builtin.ai_vision.runtime.yolo_postprocess import (
            letterbox,
            postprocess_pose,
        )

        if not handle.is_pose:
            raise ValueError(f"模型 {handle.file_path} 不是 pose 模型，无法执行姿态推理")

        h, w = frame_bgr.shape[:2]
        input_size = handle.input_size

        img, ratio, pad, _ = letterbox(frame_bgr, (input_size, input_size))
        img = img[:, :, ::-1]
        img = np.ascontiguousarray(img.transpose(2, 0, 1)).astype(np.float32) / 255.0
        tensor = img[None, ...]

        session = handle.session
        input_name = session.get_inputs()[0].name
        output = session.run(None, {input_name: tensor})[0]

        dets = postprocess_pose(
            output,
            orig_shape=(h, w),
            pad=pad,
            ratio=ratio,
            class_names=handle.names,
            conf_thr=threshold,
            iou_thr=iou_thr,
            kpt_shape=handle.kpt_shape,
        )

        results: list[PoseDetection] = []
        for d in dets:
            cid = d["cls_id"]
            if classes_filter is not None and cid not in classes_filter:
                continue
            results.append(
                PoseDetection(
                    bbox=d["bbox"],
                    conf=d["conf"],
                    cls_id=cid,
                    cls_name=handle.category_map.get(cid, str(cid)),
                    keypoints=d["keypoints"],
                )
            )
        return results


# 便捷函数
def get_engine() -> InferenceEngine:
    """获取推理引擎单例。"""
    return InferenceEngine.get_instance()