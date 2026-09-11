"""样本库 API（任务 2.5 一期最小版）。

路由前缀：``/ai-vision/samples``（挂载后为 ``/api/v1/ai-vision/samples``）。
权限：``ai_vision:sample:list/create/delete``。

实现要点（任务 2.5）：
- GET /samples：分页查询（按类别/来源/标注状态过滤）
- POST /samples/upload：批量上传图片（multipart，一期简单版）
- DELETE /samples/{id}：删除样本（软删记录 + 物理删图）
"""
import shutil
import time

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionSample
from src.plugins.builtin.ai_vision.paths import samples_dir
from src.plugins.builtin.ai_vision.schemas import SampleOut

router = APIRouter(prefix="/samples", tags=["AI 视觉平台-样本库"])

# 样本图目录（backend/uploads/ai_vision/samples，统一由 paths.py 管理）
_SAMPLES_DIR = samples_dir()

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


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
    if event_id:
        stmt = stmt.where(AIVisionSample.related_event_id == event_id)
    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.order_by(AIVisionSample.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
    })


@router.post("/upload")
async def upload_samples(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:sample:create"))],
    category_code: str = Query(default="", max_length=50, description="关联类别编码"),
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

            img = cv2.imread(str(dest))
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
    # 物理删除（仅删除样本目录内文件，避免越界）
    try:
        p = Path(item.file_path)
        if p.exists() and str(p).startswith(str(_SAMPLES_DIR)):
            p.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    await db.delete(item)
    await db.commit()
    return success_response(msg="样本已删除")