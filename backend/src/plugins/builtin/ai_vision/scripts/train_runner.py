"""训练子进程执行器（由 runtime/trainer.py 以独立进程拉起）。

用法::

    python train_runner.py --config <job_config.json>

config JSON::

    {"data_yaml": ..., "pt": ..., "out_dir": ..., "epochs": n, "imgsz": n,
     "batch": n, "lr0": f, "progress_file": ..., "result_file": ...}

行为：
- 逐 epoch 回调把 {epoch, epochs, box_loss, cls_loss, mAP50, mAP50_95}
  追加写 progress_file（JSONL），供主进程轮询写库/SSE；
- 结束（成功/失败）写 result_file：{ok, error?, onnx?, pt?, metrics?}；
- stdout 全量重定向由父进程接管为训练日志。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))

    progress_file = Path(cfg["progress_file"])
    result_file = Path(cfg["result_file"])
    out_dir = Path(cfg["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(rec: dict) -> None:
        with progress_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    try:
        from ultralytics import YOLO

        model = YOLO(cfg["pt"])
        state = {"last": {}}

        def on_epoch_end(trainer) -> None:  # noqa: ANN001
            # 兼容不同 ultralytics 版本：loss_items 可能是 tensor / dict / None
            def _loss(idx: int, key: str) -> float:
                li = getattr(trainer, "loss_items", None)
                if li is None:
                    return 0.0
                try:
                    if isinstance(li, dict):
                        return float(li.get(key, 0.0) or 0.0)
                    return float(li[idx])
                except Exception:  # noqa: BLE001
                    return 0.0

            def _metric(key: str) -> float:
                m = getattr(trainer, "metrics", None)
                if m is None:
                    return 0.0
                try:
                    rd = getattr(m, "results_dict", None) or {}
                    return float(rd.get(key, 0.0) or 0.0)
                except Exception:  # noqa: BLE001
                    return 0.0

            rec = {
                "epoch": int(getattr(trainer, "epoch", 0)) + 1,
                "epochs": int(cfg["epochs"]),
                "box_loss": round(_loss(0, "box_loss"), 4),
                "cls_loss": round(_loss(1, "cls_loss"), 4),
                "mAP50": round(_metric("metrics/mAP50(B)"), 4),
                "mAP50_95": round(_metric("metrics/mAP50-95(B)"), 4),
            }
            state["last"] = rec
            emit(rec)

        model.add_callback("on_train_epoch_end", on_epoch_end)
        res = model.train(
            data=cfg["data_yaml"],
            epochs=int(cfg["epochs"]),
            imgsz=int(cfg["imgsz"]),
            batch=int(cfg["batch"]),
            lr0=float(cfg["lr0"]),
            project=str(out_dir),
            name="run",
            exist_ok=False,
            device="cpu",
            workers=2,
            verbose=True,
            plots=False,
        )
        best_pt = out_dir / "run" / "weights" / "best.pt"
        if not best_pt.exists():
            # 训练未产出权重（极少见）：明确失败，不让上层误判完成
            result_file.write_text(json.dumps({
                "ok": False, "error": f"训练结束但未产出 best.pt（{best_pt}）",
            }, ensure_ascii=False), encoding="utf-8")
            return 1
        # 导出 ONNX（推理层 L1 只认 onnx）——失败视为整体失败：
        # 否则任务标"已完成"但模型库什么都没有（自查 P1-1）
        onnx_path = None
        export_err = ""
        try:
            exp = YOLO(str(best_pt))
            exported = exp.export(format="onnx", imgsz=int(cfg["imgsz"]), simplify=True)
            onnx_path = str(exported)
        except Exception as exc:  # noqa: BLE001
            export_err = str(exc)
            print(f"[runner] onnx export failed: {exc}", file=sys.stderr)
        if not onnx_path or not Path(onnx_path).exists():
            result_file.write_text(json.dumps({
                "ok": False,
                "error": f"ONNX 导出失败: {export_err or '产物路径无效'}（.pt 已保留于 {best_pt}，可手动导出）",
                "pt": str(best_pt),
                "metrics": state["last"],
            }, ensure_ascii=False), encoding="utf-8")
            return 1
        result_file.write_text(json.dumps({
            "ok": True,
            "pt": str(best_pt),
            "onnx": onnx_path,
            "metrics": state["last"],
            "results": {k: v for k, v in (res.results_dict if hasattr(res, "results_dict") else {}).items()},
        }, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception:  # noqa: BLE001
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        result_file.write_text(json.dumps({"ok": False, "error": err[-2000:]}, ensure_ascii=False), encoding="utf-8")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
