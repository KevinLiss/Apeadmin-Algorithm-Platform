"""样本库 API（任务 2.5 一期最小版）。

路由前缀：``/ai-vision/samples``（挂载后为 ``/api/v1/ai-vision/samples``）。
权限：``ai_vision:sample:list/create/delete``。

实现要点（任务 2.5）：
- GET /samples：分页查询（按类别/来源/标注状态过滤）
- POST /samples/upload：批量上传图片（multipart，一期简单版）
- DELETE /samples/{id}：删除样本（软删记录 + 物理删图）
"""
import json
import shutil
import time

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionSample
from src.plugins.builtin.ai_vision.paths import samples_dir
from src.plugins.builtin.ai_vision.schemas import SampleLabelUpdate, SampleOut

router = APIRouter(prefix="/samples", tags=["AI 视觉平台-样本库"])

# 样本图目录（backend/uploads/ai_vision/samples，统一由 paths.py 管理）
_SAMPLES_DIR = samples_dir()

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def imread_any(path: str):
    """cv2.imread 在 Windows 下读不了中文路径（分组目录如"公开泳池俯视"），
    统一走 np.fromfile + imdecode（外部导入样本的预标注/尺寸解析都用它）。"""
    import cv2
    import numpy as np

    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def _parse_ids(body: dict) -> list[int]:
    """批量接口 sample_ids 统一校验：非数字元素报 400 而非 500（自查 P2-9）。"""
    raw = body.get("sample_ids")
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=400, detail="sample_ids 不能为空")
    try:
        return [int(x) for x in raw]
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="sample_ids 必须是整数数组") from None


def _parse_out(item: AIVisionSample) -> dict:
    return SampleOut.model_validate(item).model_dump()


@router.get("")
async def list_samples(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category_code: str = Query(default="", max_length=50),
    source: str = Query(default="", max_length=20),
    label_status: str = Query(default="", max_length=20),
    folder: str = Query(default="", max_length=100, description="分组文件夹（__none__=未分组）"),
    event_id: int | None = Query(default=None, ge=1),
):
    """分页查询样本。"""
    stmt = select(AIVisionSample)
    if category_code:
        stmt = stmt.where(AIVisionSample.category_code == category_code)
    if source:
        stmt = stmt.where(AIVisionSample.source == source)
    if label_status:
        stmt = stmt.where(AIVisionSample.label_status == label_status)
    if folder == "__none__":
        stmt = stmt.where(AIVisionSample.folder == "")
    elif folder:
        stmt = stmt.where(AIVisionSample.folder == folder)
    if event_id:
        stmt = stmt.where(AIVisionSample.related_event_id == event_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(AIVisionSample.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
    })


@router.get("/suggestions")
async def training_suggestions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:list"))],
    limit: int = Query(default=20, ge=1, le=100),
):
    """主动学习建议清单（智能建议卡片数据源）。

    两类建议（都排除已转过样本的告警——按 file_path 里 alarm_{id}_ 前缀去重）：
    - negative：误报告警未转样本 → 建议转负样本（skipped 背景图压误报）
    - positive：已确认(critical/高置信)告警未转样本 → 建议转样本后标注
    """
    from src.plugins.builtin.ai_vision.models import AIVisionAlarm

    # 已转样本的告警 id（file_path 含 alarm_{id}_ 视为已转）
    sample_paths = (await db.execute(
        select(AIVisionSample.file_path)
    )).scalars().all()
    converted = set()
    for p in sample_paths:
        name = (p or "").split("\\")[-1].split("/")[-1]
        if name.startswith("alarm_"):
            try:
                converted.add(int(name.split("_")[1]))
            except (ValueError, IndexError):
                pass

    async def _pick(cond):
        stmt = select(AIVisionAlarm).where(cond)
        rows = (await db.execute(stmt)).scalars().all()
        # 仅推荐有"干净原图"的告警：旧告警只有烧录了检测框/置信度的画框图，
        # 转样本（无论正负）都会把红框像素喂给模型污染训练（二轮自检 P3-2）
        rows = [r for r in rows if r.id not in converted and getattr(r, "clean_snapshot_path", "")]
        rows.sort(key=lambda r: r.confidence, reverse=True)
        return rows[:limit]

    fp = await _pick(AIVisionAlarm.status == "false_positive")
    pos = await _pick(AIVisionAlarm.status == "acknowledged")
    return success_response(data={
        "negative": [{"alarm_id": a.id, "camera_id": a.camera_id, "event_id": a.event_id,
                      "category_code": a.category_code, "confidence": a.confidence,
                      "snapshot": a.snapshot_path, "note": a.note} for a in fp],
        "positive": [{"alarm_id": a.id, "camera_id": a.camera_id, "event_id": a.event_id,
                      "category_code": a.category_code, "confidence": a.confidence,
                      "snapshot": a.snapshot_path} for a in pos],
    })


@router.post("/upload")
async def upload_samples(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:create"))],
    category_code: str = Query(default="", max_length=50, description="关联类别编码"),
    folder: str = Query(default="", max_length=100, description="分组文件夹"),
    event_id: int | None = Query(default=None, ge=1, description="关联事件 ID"),
    files: list[UploadFile] = File(default=...),
):
    """批量上传样本图片（一期简单版：直接落盘 samples/upload/ 目录）。"""
    if not files:
        raise HTTPException(status_code=400, detail="未收到文件")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="单次最多上传 50 张")

    upload_dir = _SAMPLES_DIR / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved: list[dict] = []
    errors: list[str] = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in _ALLOWED_EXT:
            errors.append(f"{f.filename}: 不支持的文件类型 {ext}")
            continue
        # 防路径穿越：只用文件名
        safe_name = Path(f.filename or "sample.jpg").name
        dest = upload_dir / f"{user.id}_{int(time.time() * 1000)}_{safe_name}"
        try:
            content = await f.read()
            if not content:
                errors.append(f"{f.filename}: 空文件")
                continue
            dest.write_bytes(content)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{f.filename}: {exc}")
            continue

        # 解析尺寸
        width = height = 0
        try:
            import cv2

            img = imread_any(str(dest))
            if img is not None:
                height, width = img.shape[:2]
        except Exception:  # noqa: BLE001
            pass

        sample = AIVisionSample(
            file_path=str(dest),
            source="upload",
            label_status="unlabeled",
            label_data="{}",
            category_code=category_code,
            folder=folder.strip()[:100],
            related_event_id=event_id,
            width=width,
            height=height,
        )
        db.add(sample)
        saved.append(sample)

    if saved:
        await db.commit()
        for s in saved:
            await db.refresh(s)

    return success_response(
        data={"saved": [_parse_out(s) for s in saved], "errors": errors},
        msg=f"上传完成：成功 {len(saved)}，失败 {len(errors)}",
    )


@router.post("/export-dataset")
async def export_dataset(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:list"))],
):
    """导出 YOLO 数据集 zip（供平台外训练）。

    body: {"category_codes": [...], "val_split": 0.2}
    names 顺序 = category_codes 顺序（索引即 class id）。
    返回 zip 流下载。
    """
    import io
    import zipfile

    from fastapi.responses import StreamingResponse
    from sqlalchemy import select as _select

    from src.plugins.builtin.ai_vision.paths import datasets_dir
    from src.plugins.builtin.ai_vision.runtime.datasets import build_dataset

    codes = body.get("category_codes") or []
    if not isinstance(codes, list) or not codes:
        raise HTTPException(status_code=400, detail="category_codes 必填（数组）")
    val_split = float(body.get("val_split", 0.2))

    stmt = _select(AIVisionSample).where(
        AIVisionSample.label_status == "labeled",
        AIVisionSample.category_code.in_(codes),
    )
    samples = (await db.execute(stmt)).scalars().all()
    if not samples:
        raise HTTPException(status_code=400, detail="所选类别下没有已标注样本")

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = datasets_dir() / f"export_{ts}"
    stats = build_dataset(samples, codes, out_dir, val_split=val_split)
    if stats["train"] == 0:
        raise HTTPException(status_code=400, detail="标注框均不在所选类别内，无法导出")

    # 打包 zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir).as_posix())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="yolo_dataset_{ts}.zip"'},
    )


@router.post("/{sample_id}/prelabel")
async def prelabel_sample(
    sample_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:edit"))],
    model_id: int = Query(..., ge=1, description="用于预标注的模型（须为检测模型，非 pose）"),
    threshold: float = Query(default=0.3, ge=0.05, le=0.95),
):
    """AI 预标注：用指定检测模型对样本图跑一遍，返回 YOLO 归一化框。

    **不落库**——返回 boxes 供前端填入标注弹窗，人工确认后再保存。
    类别取模型 category_map（平台类别码）。pose 模型无检测框语义，拒绝。
    """
    import asyncio

    from src.plugins.builtin.ai_vision.models import AIVisionModel
    from src.plugins.builtin.ai_vision.runtime.engine import get_engine

    item = await db.get(AIVisionSample, sample_id)
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    img_path = Path(item.file_path)
    if not img_path.exists():
        raise HTTPException(status_code=400, detail="样本图片文件不存在")

    model = await db.get(AIVisionModel, model_id)
    if not model:
        raise HTTPException(status_code=400, detail="模型不存在")
    # pose 模型无检测框语义（cls 恒为 person 且供姿态用），拒绝用于预标注
    if "pose" in (model.name or "").lower():
        raise HTTPException(status_code=400, detail="姿态模型不支持预标注，请选择检测模型（如 coco / 溺水 / 明火）")

    # 解析模型绝对路径（同 worker._load_model_from_record）
    # 本文件位于 ai_vision/api/ 下，parents[1] 即插件根 ai_vision/
    rel = model.file_path
    mp = Path(rel)
    if not mp.is_absolute():
        plugin_root = Path(__file__).resolve().parents[1]  # ai_vision/
        cand = plugin_root / rel
        if not cand.exists():
            cand = Path.cwd() / rel
        if not cand.exists():
            raise HTTPException(status_code=400, detail=f"模型文件不存在: {rel}")
        mp = cand

    try:
        import cv2

        frame = imread_any(str(img_path))
        if frame is None:
            raise HTTPException(status_code=400, detail="图片无法解码")
        h, w = frame.shape[:2]
        cat_map = json.loads(model.category_map or "{}")
        cat_map_int = {int(k): v for k, v in cat_map.items()}
        engine = get_engine()
        handle = await asyncio.to_thread(engine.load, str(mp), None, cat_map_int)
        dets = await asyncio.to_thread(engine.detect, frame, handle, threshold)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"预标注推理失败: {exc}") from exc

    boxes = []
    for d in dets:
        x1, y1, x2, y2 = d.bbox
        cx = ((x1 + x2) / 2) / w
        cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        # 裁剪到 0~1 且保证合法
        cx = min(max(cx, 0.0), 1.0)
        cy = min(max(cy, 0.0), 1.0)
        bw = min(max(bw, 0.001), 1.0)
        bh = min(max(bh, 0.001), 1.0)
        boxes.append({
            "class_name": d.cls_name,
            "x": round(cx, 4),
            "y": round(cy, 4),
            "w": round(bw, 4),
            "h": round(bh, 4),
            "conf": round(float(d.conf), 3),
        })
    return success_response(data={"boxes": boxes, "model_id": model_id}, msg=f"AI 预标注 {len(boxes)} 个框")


@router.post("/batch-delete")
async def batch_delete_samples(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:delete"))],
):
    """批量删除样本（删记录 + 物理文件）。body: {"sample_ids": [...]}"""
    ids = _parse_ids(body)
    items = (await db.execute(
        select(AIVisionSample).where(AIVisionSample.id.in_(ids))
    )).scalars().all()
    deleted = 0
    for item in items:
        try:
            p = Path(item.file_path)
            if p.exists() and str(p).startswith(str(_SAMPLES_DIR)):
                p.unlink(missing_ok=True)
        except BaseException:  # noqa: BLE001 — 删除保护钩子可能抛 SystemExit，不可杀进程
            pass
        await db.delete(item)
        deleted += 1
    await db.commit()
    return success_response(data={"deleted": deleted}, msg=f"已删除 {deleted} 个样本")


@router.get("/folders")
async def list_folders(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:list"))],
):
    """分组文件夹列表（含样本数/已标注数），按名称排序。"""
    # 双列查询用 .all() 拿行元组（.scalars() 只返回第一列，解包会炸）
    rows = (await db.execute(select(AIVisionSample.folder, AIVisionSample.label_status))).all()
    stats: dict[str, dict] = {}
    for folder, status in rows:
        key = folder or ""
        s = stats.setdefault(key, {"folder": key or "未分组", "total": 0, "labeled": 0})
        s["total"] += 1
        if status == "labeled":
            s["labeled"] += 1
    out = sorted(stats.values(), key=lambda x: x["folder"])
    return success_response(data={"items": out})


@router.post("/batch-move")
async def batch_move_samples(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:edit"))],
):
    """批量移动样本到分组。body: {"sample_ids": [...], "folder": "泳池白天"}（空串=移回未分组）。

    分组是**真实目录** samples/folders/<folder>/：物理移动文件并同步
    file_path，训练导出/预览按目录即可整组取用。移动失败不阻塞（保留原位）。
    """
    ids = _parse_ids(body)
    folder = str(body.get("folder", "")).strip()[:100]
    # 目录名清洗：禁止路径穿越与非法字符
    safe_folder = folder.replace("/", "").replace("\\", "").replace("..", "").strip()
    if folder and not safe_folder:
        raise HTTPException(status_code=400, detail="分组名不合法")
    items = (await db.execute(
        select(AIVisionSample).where(AIVisionSample.id.in_(ids))
    )).scalars().all()
    moved = 0
    for item in items:
        old = Path(item.file_path)
        if safe_folder:
            dst_dir = _SAMPLES_DIR / "folders" / safe_folder
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / old.name
            try:
                if old.exists() and str(old).startswith(str(_SAMPLES_DIR)):
                    shutil.move(str(old), str(dst))
                    item.file_path = str(dst)
            except BaseException:  # noqa: BLE001 — 删除保护钩子可能抛 SystemExit
                pass
        else:
            # 移回未分组 → samples/upload/
            dst_dir = _SAMPLES_DIR / "upload"
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / old.name
            try:
                if old.exists() and str(old).startswith(str(_SAMPLES_DIR)):
                    shutil.move(str(old), str(dst))
                    item.file_path = str(dst)
            except BaseException:  # noqa: BLE001
                pass
        item.folder = safe_folder
        moved += 1
    await db.commit()
    return success_response(data={"moved": moved}, msg=f"已移动 {moved} 张到「{safe_folder or '未分组'}」")


@router.post("/batch-prelabel")
async def batch_prelabel_samples(
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:edit"))],
):
    """批量 AI 预标注：对多个样本跑检测模型并**直接落库**为已标注。

    body: {"sample_ids": [...], "model_id": N, "threshold": 0.3}
    保护策略：已有**人工框**（无 conf 字段的框）的样本跳过不覆盖；
    仅有 AI 框或无框的样本用本次结果整体替换（重复执行不堆框）。
    未检出目标的样本保持原状态并计入 no_box。
    """
    import asyncio

    from src.plugins.builtin.ai_vision.models import AIVisionModel
    from src.plugins.builtin.ai_vision.runtime.engine import get_engine

    ids = _parse_ids(body)
    model_id = body.get("model_id")
    try:
        threshold = float(body.get("threshold", 0.3))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="threshold 必须是数字") from None
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id 必填")

    model = await db.get(AIVisionModel, int(model_id))
    if not model:
        raise HTTPException(status_code=400, detail="模型不存在")
    if "pose" in (model.name or "").lower():
        raise HTTPException(status_code=400, detail="姿态模型不支持预标注，请选择检测模型")

    rel = model.file_path
    mp = Path(rel)
    if not mp.is_absolute():
        plugin_root = Path(__file__).resolve().parents[1]  # ai_vision/
        cand = plugin_root / rel
        if not cand.exists():
            cand = Path.cwd() / rel
        if not cand.exists():
            raise HTTPException(status_code=400, detail=f"模型文件不存在: {rel}")
        mp = cand

    items = (await db.execute(
        select(AIVisionSample).where(AIVisionSample.id.in_(ids))
    )).scalars().all()
    if not items:
        raise HTTPException(status_code=400, detail="未找到任何样本")

    import cv2

    cat_map = json.loads(model.category_map or "{}")
    cat_map_int = {int(k): v for k, v in cat_map.items()}
    engine = get_engine()
    handle = await asyncio.to_thread(engine.load, str(mp), None, cat_map_int)

    labeled = skipped_manual = no_box = failed = 0
    for item in items:
        try:
            existing = (json.loads(item.label_data or "{}").get("boxes")) or []
        except (ValueError, TypeError):
            existing = []
        if any(b.get("conf") is None for b in existing):
            skipped_manual += 1
            continue
        img_path = Path(item.file_path)
        if not img_path.exists():
            failed += 1
            continue
        try:
            frame = await asyncio.to_thread(imread_any, str(img_path))
            if frame is None:
                failed += 1
                continue
            h, w = frame.shape[:2]
            dets = await asyncio.to_thread(engine.detect, frame, handle, threshold)
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        boxes = []
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            cx = min(max(((x1 + x2) / 2) / w, 0.0), 1.0)
            cy = min(max(((y1 + y2) / 2) / h, 0.0), 1.0)
            bw = min(max((x2 - x1) / w, 0.001), 1.0)
            bh = min(max((y2 - y1) / h, 0.001), 1.0)
            boxes.append({
                "class_name": d.cls_name,
                "x": round(cx, 4), "y": round(cy, 4),
                "w": round(bw, 4), "h": round(bh, 4),
                "conf": round(float(d.conf), 3),
            })
        if not boxes:
            no_box += 1
            continue
        item.label_data = json.dumps({"boxes": boxes}, ensure_ascii=False)
        item.label_status = "labeled"
        labeled += 1
    await db.commit()
    return success_response(
        data={"labeled": labeled, "skipped_manual": skipped_manual, "no_box": no_box, "failed": failed},
        msg=f"批量预标注完成：标注 {labeled}，跳过已人工标注 {skipped_manual}，未检出 {no_box}，失败 {failed}",
    )


@router.put("/{sample_id}/label")
async def update_sample_label(
    sample_id: int,
    body: SampleLabelUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:edit"))],
):
    """保存样本标注（YOLO 归一化框）。boxes 为空 = 取消标注。"""
    import json as _json

    item = await db.get(AIVisionSample, sample_id)
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    # 中心点+宽高 → 校验不越界（中心±半宽高 须在 0~1）
    for b in body.boxes:
        if not (0.0 <= b.x - b.w / 2 and b.x + b.w / 2 <= 1.0 and 0.0 <= b.y - b.h / 2 and b.y + b.h / 2 <= 1.0):
            raise HTTPException(status_code=400, detail=f"框越界: {b.class_name} ({b.x},{b.y},{b.w},{b.h})")
    item.label_data = _json.dumps(
        {"boxes": [b.model_dump() for b in body.boxes]}, ensure_ascii=False
    )
    item.label_status = "labeled" if body.boxes else "unlabeled"
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="标注已保存" if body.boxes else "已取消标注")


@router.put("/{sample_id}/status")
async def set_sample_status(
    sample_id: int,
    body: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:edit"))],
):
    """设置样本标注状态（主动学习负样本标记）。

    body: {"label_status": "skipped" | "unlabeled" | "labeled"}
    skipped = "确认图中无目标/不该报"，训练 bg_ratio>0 时作背景负样本。
    labeled 仅当样本确有标注框时允许（"取消跳过"恢复原状态用，自查 P1-2）。
    """
    st = str(body.get("label_status", "")).strip()
    if st not in ("skipped", "unlabeled", "labeled"):
        raise HTTPException(status_code=400, detail="label_status 仅支持 skipped/unlabeled/labeled")
    item = await db.get(AIVisionSample, sample_id)
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    if st == "labeled":
        try:
            boxes = json.loads(item.label_data or "{}").get("boxes", [])
        except (ValueError, TypeError):
            boxes = []
        if not boxes:
            raise HTTPException(status_code=400, detail="该样本没有标注框，不能恢复为已标注")
    item.label_status = st
    await db.commit()
    await db.refresh(item)
    return success_response(
        data=_parse_out(item),
        msg={"skipped": "已标记为负样本（背景图）", "labeled": "已恢复为已标注"}.get(st, "已恢复未标注"),
    )


@router.delete("/{sample_id}")
async def delete_sample(
    sample_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:delete"))],
):
    """删除样本（删记录 + 物理文件）。"""
    item = await db.get(AIVisionSample, sample_id)
    if not item:
        raise HTTPException(status_code=404, detail="样本不存在")
    # 物理删除（仅删除样本目录内文件，避免越界）。
    # 注意：必须捕获 BaseException——某些托管环境的删除保护钩子会抛
    # SystemExit，若只捕 Exception 会直接杀死 API 进程（2026-09-28 实锤）。
    # 文件删不掉不影响记录删除（留孤儿文件可后续清理）。
    try:
        p = Path(item.file_path)
        if p.exists() and str(p).startswith(str(_SAMPLES_DIR)):
            p.unlink(missing_ok=True)
    except BaseException:  # noqa: BLE001
        pass
    await db.delete(item)
    await db.commit()
    return success_response(msg="样本已删除")