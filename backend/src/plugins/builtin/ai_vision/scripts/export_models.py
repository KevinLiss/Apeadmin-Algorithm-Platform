"""AI 视觉平台模型导出脚本（开发机用，非运行时）。

用途：把 ultralytics 权重（.pt）导出为平台可用的 ONNX 模型，
并输出/更新 manifest 所需的 SHA256、体积等元数据。

用法（需 ultralytics，即 L2 训练层已装或开发机独立 venv）::

    python scripts/export_models.py --pt path/to/model.pt --name my-model --classes person vehicle

常用示例::

    # 从官方 yolo11n.pt 导出人/车模型
    python scripts/export_models.py --pt yolo11n.pt --name yolo11n-coco

    # 从 fire-smoke best.pt 导出明火模型（保留 fire/smoke 两类，剔除垃圾类）
    python scripts/export_models.py --pt runs/detect/train/weights/best.pt --name fire-smoke-v1 --class-filter 0 2

说明：
- 导出参数固定：opset 17、imgsz 640、dynamic=False（平台按 640 固定输入设计）
- 导出后自动计算 SHA256 与文件大小，并打印追加到 manifest.json 的片段
- 该脚本是开发机工具，不进入插件运行时依赖
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def sha256_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def export_onnx(pt_path: Path, out_dir: Path) -> Path:
    """用 ultralytics API 导出 ONNX（imgsz 640 / opset 17 / 固定尺寸）。"""
    from ultralytics import YOLO

    model = YOLO(str(pt_path))
    export_path = model.export(
        format="onnx",
        imgsz=640,
        opset=17,
        dynamic=False,
        simplify=False,
    )
    src = Path(export_path)
    dst = out_dir / src.name
    shutil.copy2(src, dst)
    return dst


def _model_names(onnx_path: Path) -> dict[int, str]:
    """从 ONNX 元数据读取类别名（ultralytics 导出时写入 meta 的 names）。"""
    import onnxruntime as ort

    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    meta = sess.get_modelmeta()
    custom = meta.custom_metadata_map or {}
    names_raw = custom.get("names")
    if not names_raw:
        return {}
    try:
        names = json.loads(names_raw)
        return {int(k): str(v) for k, v in names.items()}
    except Exception:  # noqa: BLE001
        return {}


def build_manifest_entry(
    name: str,
    onnx_path: Path,
    class_filter: list[int] | None = None,
    category_map: dict[str, str] | None = None,
    license_note: str = "",
    version: str = "1.0.0",
) -> dict:
    """生成 manifest 条目（供手工合并到 manifest.json）。"""
    names = _model_names(onnx_path)
    # 类别映射：优先用外部传入；否则按 filter 或全部类别索引自映射
    if category_map is None:
        if class_filter is not None:
            category_map = {str(i): names.get(i, str(i)) for i in class_filter}
        else:
            category_map = {str(i): names.get(i, str(i)) for i in sorted(names)}
    if not category_map:
        category_map = {"0": "unknown"}
    return {
        "name": name,
        "file": onnx_path.name,
        "version": version,
        "sha256": sha256_of(onnx_path),
        "file_size": onnx_path.stat().st_size,
        "category_map": {str(k): v for k, v in category_map.items()},
        "input_size": 640,
        "quantized": False,
        "source": "builtin",
        "license_note": license_note,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 ultralytics 权重为平台 ONNX 模型")
    parser.add_argument("--pt", required=True, help="源 .pt 权重路径")
    parser.add_argument("--name", required=True, help="模型名（如 yolo11n-coco / fire-smoke-v1）")
    parser.add_argument("--out", default="backend/src/plugins/builtin/ai_vision/assets/models",
                        help="输出目录（默认插件 models 目录）")
    parser.add_argument("--class-filter", nargs="*", type=int, default=None,
                        help="只保留这些模型类别 ID（如 0 2 过滤掉垃圾类）")
    parser.add_argument("--category-map", default=None,
                        help="JSON 字符串：模型类别ID→平台类别编码映射（覆盖自动推导）")
    parser.add_argument("--license", default="", help="来源与许可说明（写入 manifest 条目）")
    args = parser.parse_args()

    pt_path = Path(args.pt)
    if not pt_path.exists():
        raise SystemExit(f"权重文件不存在: {pt_path}")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = export_onnx(pt_path, out_dir)
    print(f"[导出完成] {onnx_path}")

    category_map = None
    if args.category_map:
        try:
            category_map = json.loads(args.category_map)
        except json.JSONDecodeError:
            raise SystemExit(f"--category-map 不是合法 JSON: {args.category_map}")

    entry = build_manifest_entry(
        args.name,
        onnx_path,
        args.class_filter,
        category_map,
        args.license,
    )
    print("\n请将以下条目合并到 manifest.json 的 models 数组中：\n")
    print(json.dumps(entry, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()