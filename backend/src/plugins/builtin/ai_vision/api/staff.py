"""员工体貌档案 API（人岗验证）。

路由前缀：``/ai-vision/staff``（挂载后 ``/api/v1/ai-vision/staff``）。
权限：``ai_vision:staff:*``（复用 event 权限串，admin 直通）。

登记流程：上传 1~N 张员工当班着装照片 → 外观特征引擎（yolo11n-cls）
逐张提 1000 维归一化向量 → 均值再归一 → 存 ai_vision_staff.embedding。
换班/换工装时重新登记覆盖即可。worker 的 IdentityDetector 每 30s
缓存刷新一次档案，登记后约半分钟生效。
"""
import json
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionStaff
from src.plugins.builtin.ai_vision.paths import UPLOADS_AI_VISION_DIR

router = APIRouter(prefix="/staff", tags=["AI 视觉平台-员工体貌档案"])

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _imread_cn(data: bytes):
    """中文路径安全读图（内存字节解码，绕开 cv2.imread 路径限制）。"""
    import cv2

    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def _out(item: AIVisionStaff) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "event_id": item.event_id,
        "photo_path": item.photo_path,
        "photo_count": item.photo_count,
        "note": item.note,
        "has_embedding": bool((item.embedding or "").strip() not in ("", "[]")),
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("")
async def list_staff(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:list"))],
    event_id: int | None = Query(default=None, description="按事件过滤"),
):
    """员工档案列表。"""
    stmt = select(AIVisionStaff)
    if event_id is not None:
        stmt = stmt.where(AIVisionStaff.event_id == event_id)
    stmt = stmt.order_by(AIVisionStaff.id.desc())
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={"items": [_out(i) for i in items]})


@router.post("/register")
async def register_staff(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:edit"))],
    name: Annotated[str, Form(max_length=50)],
    event_id: Annotated[int, Form(ge=1)],
    note: Annotated[str, Form(max_length=200)] = "",
    files: list[UploadFile] = File(..., description="1~5 张当班着装照片"),
):
    """登记/更新员工体貌档案：提特征均值入库（覆盖旧档案同名同事件）。"""
    if not files or len(files) > 5:
        raise HTTPException(status_code=400, detail="请上传 1~5 张照片")
    from src.plugins.builtin.ai_vision.runtime.appearance import get_appearance_engine

    eng = get_appearance_engine()
    if eng is None:
        raise HTTPException(status_code=500, detail="外观特征模型不可用（yolo11n-cls.onnx 缺失）")

    vecs: list[np.ndarray] = []
    saved_path = ""
    staff_dir = UPLOADS_AI_VISION_DIR / "staff" / str(event_id)
    for idx, f in enumerate(files):
        ext = ("." + (f.filename or "").rsplit(".", 1)[-1].lower()) if f.filename else ".jpg"
        if ext not in _ALLOWED_EXT:
            raise HTTPException(status_code=400, detail=f"不支持的图片格式: {f.filename}")
        data = await f.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="单张照片不超过 10MB")
        img = _imread_cn(data)
        if img is None:
            raise HTTPException(status_code=400, detail=f"图片解码失败: {f.filename}")
        emb = eng.embed_crop(img, (0, 0, img.shape[1], img.shape[0]), pad=0.0)
        if emb is None:
            raise HTTPException(status_code=400, detail=f"特征提取失败: {f.filename}")
        vecs.append(emb)
        staff_dir.mkdir(parents=True, exist_ok=True)
        dst = staff_dir / f"{name.replace('/', '_').replace(' ', '_')}_{idx}{ext}"
        dst.write_bytes(data)
        saved_path = str(dst)

    mean = np.mean(vecs, axis=0)
    norm = float(np.linalg.norm(mean))
    mean = (mean / norm).astype(np.float32) if norm > 0 else mean

    # 同名同事件覆盖（换班重登记），否则新建
    existing = (await db.execute(
        select(AIVisionStaff).where(AIVisionStaff.event_id == event_id, AIVisionStaff.name == name)
    )).scalars().first()
    if existing:
        existing.embedding = json.dumps(mean.tolist())
        existing.photo_path = saved_path
        existing.photo_count = len(vecs)
        existing.note = note
        item = existing
    else:
        item = AIVisionStaff(
            name=name,
            event_id=event_id,
            embedding=json.dumps(mean.tolist()),
            photo_path=saved_path,
            photo_count=len(vecs),
            note=note,
        )
        db.add(item)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_out(item), msg=f"已登记 {name}（{len(vecs)} 张照片，约 30 秒内生效）")


@router.delete("/{staff_id}")
async def delete_staff(
    staff_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:edit"))],
):
    """删除员工档案（照片文件一并清理）。"""
    item = await db.get(AIVisionStaff, staff_id)
    if not item:
        raise HTTPException(status_code=404, detail="档案不存在")
    photo = item.photo_path
    name = item.name
    await db.delete(item)
    await db.commit()
    removed = 0
    if photo:
        from pathlib import Path

        try:
            p = Path(photo)
            if p.parent.exists() and str(p.parent).startswith(str(UPLOADS_AI_VISION_DIR)):
                prefix = p.stem.rsplit("_", 1)[0]
                for sib in p.parent.glob(f"{prefix}_*"):
                    sib.unlink(missing_ok=True)
                    removed += 1
        except BaseException:  # noqa: BLE001
            pass
    return success_response(msg=f"已删除 {name} 的档案（清理 {removed} 张照片）")
