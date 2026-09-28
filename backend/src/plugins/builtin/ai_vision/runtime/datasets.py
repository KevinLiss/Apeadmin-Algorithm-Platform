"""YOLO 数据集构建：样本库已标注样本 → 训练目录结构。

产出（out_dir）::

    images/train/*.jpg   labels/train/*.txt
    images/val/*.jpg     labels/val/*.txt
    data.yaml            # path/train/val/nc/names

标注格式（sample.label_data）::

    {"boxes": [{"class_name": "<平台类别code>", "x": cx, "y": cy, "w": w, "h": h}, ...]}

坐标为 YOLO 归一化（中心点+宽高）。导出时 class_name（code）按调用方
给定的 ``names`` 顺序映射为整数索引——**顺序必须与基座模型一致**，
由调用方（train API）校验后传入。
"""
from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any

# 划分用固定种子：同批样本多次导出 train/val 归属稳定，便于复现
_SPLIT_SEED = 20260928


def build_dataset(
    samples: list[Any],
    names: list[str],
    out_dir: Path,
    val_split: float = 0.2,
) -> dict:
    """把已标注样本写入 YOLO 目录。

    samples: ORM 对象列表（需有 id/file_path/label_data/width/height）。
    names:   类别 code 顺序表（索引即 YOLO class id）。
    返回统计 {total, train, val, skipped, per_class}。
    """
    idx_of = {code: i for i, code in enumerate(names)}
    for sub in ("images/train", "images/val", "labels/train", "labels/val"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    labeled = [s for s in samples if (s.label_status == "labeled" and s.label_data)]
    rng = random.Random(_SPLIT_SEED)
    rng.shuffle(labeled)
    n_val = int(round(len(labeled) * val_split)) if len(labeled) > 2 else 0
    val_ids = {s.id for s in labeled[:n_val]}

    stats = {"total": len(labeled), "train": 0, "val": 0, "skipped": 0, "per_class": {c: 0 for c in names}}
    for s in labeled:
        try:
            boxes = json.loads(s.label_data).get("boxes", [])
        except (ValueError, TypeError):
            stats["skipped"] += 1
            continue
        lines = []
        ok = True
        for b in boxes:
            ci = idx_of.get(b.get("class_name"))
            if ci is None:  # 标注类别不在本次 names 内 → 丢弃该框
                continue
            lines.append(f"{ci} {b['x']:.6f} {b['y']:.6f} {b['w']:.6f} {b['h']:.6f}")
            stats["per_class"][b["class_name"]] += 1
        if not lines:
            stats["skipped"] += 1
            continue
        split = "val" if s.id in val_ids else "train"
        src = Path(s.file_path)
        if not src.exists():
            stats["skipped"] += 1
            continue
        stem = f"s{s.id}"
        shutil.copy2(src, out_dir / "images" / split / f"{stem}{src.suffix.lower()}")
        (out_dir / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        stats[split] += 1

    yaml_lines = [
        f"path: {out_dir.as_posix()}",
        "train: images/train",
        "val: images/val",
        "",
        f"nc: {len(names)}",
        "names:",
        *[f"  - {n}" for n in names],
    ]
    (out_dir / "data.yaml").write_text("\n".join(yaml_lines) + "\n", encoding="utf-8")
    return stats
