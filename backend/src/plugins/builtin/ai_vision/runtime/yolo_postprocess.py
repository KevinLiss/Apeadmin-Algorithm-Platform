"""YOLO ONNX 输出解码（letterbox 逆变换 + 纯 numpy NMS）。

针对 YOLO11 系列导出 ONNX 的标准输出张量 ``(1, 4+nc, 8400)``：
    4 = cx, cy, w, h（输入图 640×640 坐标），后续 nc 为各类别得分。

解码流程（与 ultralytics 官方推理对齐）：
    1. 转置 ``(8400, 4+nc)``
    2. 置信度过滤（阈值默认 0.45，通过 ``max_thr`` 参数传入）
    3. 纯 numpy 实现的 IoU NMS（按分数降序贪心抑制）
    4. 坐标从 letterbox 输入图逆变换回原图

设计原则：
- **纯 numpy**：不依赖 opencv 做 NMS（保持轻量、可单测）；
- 支持任意 ``4+nc`` 输出（不写死 84）；YOLO11n 是 84，yolo11s/m/l/x 依次增大；
- 若未来遇到非 8400 的锚点数（不同导出/输入尺寸），自动从张量形状推导。

本模块只做纯张量数学，**不 import onnxruntime / cv2**（可被单测独立加载）。
"""
from __future__ import annotations

import numpy as np


def letterbox(
    img: np.ndarray,
    new_shape: tuple[int, int] = (640, 640),
    color: tuple[int, int, int] = (114, 114, 114),
) -> tuple[np.ndarray, float, tuple[int, int], tuple[int, int]]:
    """等比缩放 + 居中填充到目标尺寸（与 ultralytics 一致）。

    返回::
        (padded_img, ratio, (pad_w, pad_h), (orig_h, orig_w))

    ``ratio`` 为 ``new / old`` 的缩放系数；``pad_w/pad_h`` 为单侧填充像素。
    逆变换用：``orig_x = (x - pad_w) / ratio``。
    """
    import cv2

    h, w = img.shape[:2]
    target_h, target_w = new_shape
    r = min(target_h / h, target_w / w)
    new_w, new_h = round(w * r), round(h * r)
    dw, dh = target_w - new_w, target_h - new_h
    pad_w, pad_h = dw / 2, dh / 2

    if (new_w, new_h) != (w, h):
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    top, bottom = int(round(pad_h - 0.1)), int(round(pad_h + 0.1))
    left, right = int(round(pad_w - 0.1)), int(round(pad_w + 0.1))
    img = cv2.copyMakeBorder(
        img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color
    )
    return img, r, (pad_w, pad_h), (h, w)


def nms(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_thr: float = 0.45,
) -> np.ndarray:
    """纯 numpy 实现的 IoU NMS（按分数降序贪心）。

    Args:
        boxes: (N, 4) float，x1/y1/x2/y2 绝对坐标。
        scores: (N,) float，与 boxes 一一对应（已含类别内最大分）。
        iou_thr: IoU 阈值，默认 0.45。

    Returns:
        保留框的索引数组（按分数降序）。
    """
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        # 与当前框计算 IoU
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / (area_i + area_r - inter + 1e-9)
        order = rest[iou <= iou_thr]
    return np.asarray(keep, dtype=np.int64)


def postprocess(
    pred: np.ndarray,
    orig_shape: tuple[int, int],
    pad: tuple[float, float],
    ratio: float,
    class_names: dict[int, str],
    conf_thr: float = 0.45,
    iou_thr: float = 0.45,
    max_det: int = 300,
) -> list[dict]:
    """YOLO 输出张量 → 原图坐标检测结果列表。

    Args:
        pred: ONNX 输出 ``(1, 4+nc, N)`` 或 ``(4+nc, N)``。
        orig_shape: 原图 (H, W)。
        ratio / pad: ``letterbox()`` 返回值。
        class_names: 类别名列表（索引对应模型输出类别 ID）。
        conf_thr / iou_thr: 置信度 / NMS 阈值。
        max_det: 最多保留框数。

    Returns:
        每个元素::
            {
                "bbox": [x1, y1, x2, y2],   # 原图绝对坐标
                "conf": float,
                "cls_id": int,
                "cls_name": str,
            }
        按置信度降序。
    """
    pred = np.asarray(pred)
    if pred.ndim == 3:
        pred = pred[0]  # (4+nc, N)
    pred = pred.T  # (N, 4+nc)
    n_boxes, dim = pred.shape
    if dim < 5:
        raise ValueError(f"YOLO 输出列数异常: {dim}（应 ≥5）")
    nc = dim - 4
    pad_w, pad_h = pad

    cls_scores = pred[:, 4:]
    cls_ids = cls_scores.argmax(axis=1)
    scores = cls_scores[np.arange(n_boxes), cls_ids]

    mask = scores >= conf_thr
    if not mask.any():
        return []
    boxes_p = pred[mask, :4]
    scores_p = scores[mask]
    cls_ids_p = cls_ids[mask]

    # 置信度过滤后再做类别分组 NMS（避免不同类别互相抑制）
    results: list[dict] = []
    for cid in np.unique(cls_ids_p):
        sel = cls_ids_p == cid
        boxes_c = boxes_p[sel]
        scores_c = scores_p[sel]
        xyxy = _decode_c2xyxy(boxes_c)
        keep = nms(xyxy, scores_c, iou_thr)
        for idx in keep:
            x1, y1, x2, y2 = xyxy[int(idx)]
            # letterbox 逆变换回原图
            x1 = (x1 - pad_w) / ratio
            y1 = (y1 - pad_h) / ratio
            x2 = (x2 - pad_w) / ratio
            y2 = (y2 - pad_h) / ratio
            results.append(
                {
                    "cls_id": int(cid),
                    "cls_name": class_names.get(int(cid), str(int(cid))),
                    "conf": float(scores_c[idx]),
                    "bbox": [float(x1), float(y1), float(x2), float(y2)],
                }
            )

    results.sort(key=lambda d: d["conf"], reverse=True)
    return results[:max_det]


def postprocess_pose(
    pred: np.ndarray,
    orig_shape: tuple[int, int],
    pad: tuple[float, float],
    ratio: float,
    class_names: dict[int, str],
    conf_thr: float = 0.35,
    iou_thr: float = 0.45,
    kpt_shape: tuple[int, int] = (17, 3),
    max_det: int = 50,
) -> list[dict]:
    """YOLO-pose 输出张量 → 原图坐标检测框 + 关键点列表。

    输出张量形状 ``(1, 4+nc+nk*kd, N)``（yolo11n-pose 为 ``(1, 56, 8400)``）：
    4 = cx,cy,w,h（letterbox 输入图坐标），nc = 类别得分，
    nk*kd = 17 关键点 × (x, y, conf)。

    Returns:
        每个元素::

            {
                "bbox": [x1, y1, x2, y2],      # 原图绝对坐标
                "conf": float,
                "cls_id": int,
                "cls_name": str,
                "keypoints": [[x, y, conf], ...],  # nk 项，原图绝对坐标
            }

        按置信度降序，最多 max_det 个。
    """
    pred = np.asarray(pred)
    if pred.ndim == 3:
        pred = pred[0]
    pred = pred.T  # (N, 4+nc+nk*kd)
    n, dim = pred.shape
    nk, kd = int(kpt_shape[0]), int(kpt_shape[1])
    nc = dim - 4 - nk * kd
    if nc < 1:
        raise ValueError(f"pose 输出列数异常: {dim}（nk={nk}, kd={kd}, 应 ≥ 4+nk*kd+1）")
    pad_w, pad_h = pad

    cls_scores = pred[:, 4 : 4 + nc]
    cls_ids = cls_scores.argmax(axis=1)
    scores = cls_scores[np.arange(n), cls_ids]

    mask = scores >= conf_thr
    if not mask.any():
        return []

    boxes_p = pred[mask, :4]
    scores_p = scores[mask]
    cls_ids_p = cls_ids[mask]
    kpts_p = pred[mask, 4 + nc :].reshape(-1, nk, kd)

    results: list[dict] = []
    for cid in np.unique(cls_ids_p):
        sel = cls_ids_p == cid
        xyxy = _decode_c2xyxy(boxes_p[sel])
        keep = nms(xyxy, scores_p[sel], iou_thr)
        kpts_c = kpts_p[sel]
        for idx in keep:
            i = int(idx)
            x1, y1, x2, y2 = xyxy[i]
            # letterbox 逆变换回原图（框 + 关键点同一变换）
            x1 = (x1 - pad_w) / ratio
            y1 = (y1 - pad_h) / ratio
            x2 = (x2 - pad_w) / ratio
            y2 = (y2 - pad_h) / ratio
            kpts = kpts_c[i].astype(np.float32, copy=True)
            kpts[:, 0] = (kpts[:, 0] - pad_w) / ratio
            kpts[:, 1] = (kpts[:, 1] - pad_h) / ratio
            # 关键点置信度：多数导出版本已 sigmoid；防御性归一到 0~1
            kc = kpts[:, 2]
            if kc.size and float(kc.max()) > 1.5:
                kc = 1.0 / (1.0 + np.exp(-kc))
            kpts[:, 2] = np.clip(kc, 0.0, 1.0)
            results.append(
                {
                    "cls_id": int(cid),
                    "cls_name": class_names.get(int(cid), str(int(cid))),
                    "conf": float(scores_p[sel][i]),
                    "bbox": [float(x1), float(y1), float(x2), float(y2)],
                    "keypoints": kpts.tolist(),
                }
            )

    results.sort(key=lambda d: d["conf"], reverse=True)
    return results[:max_det]


def _decode_c2xyxy(boxes: np.ndarray) -> np.ndarray:
    """(N,4) cxcywh → (N,4) x1y1x2y2。"""
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return out