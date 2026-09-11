"""告警中心 API（任务 2.5）。

路由前缀：``/ai-vision/alarms``（挂载后为 ``/api/v1/ai-vision/alarms``）。
权限：``ai_vision:alarm:list/edit``。

实现要点（任务 2.5）：
- GET /alarms：筛选（时间/摄像头/事件/状态/置信度区间，分页）
- POST /alarms/{id}/ack：确认
- POST /alarms/{id}/false-positive：误报
- POST /alarms/{id}/to-sample：转样本（复制抓拍图到 samples 目录）
- POST /alarms/batch-ack：批量确认
- 列表联表返回摄像头名/事件名（避免前端二次查询）
"""
import json
import shutil

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import (
    AIVisionAlarm,
    AIVisionCamera,
    AIVisionEvent,
    AIVisionSample,
)
from src.plugins.builtin.ai_vision.paths import samples_dir
from src.plugins.builtin.ai_vision.schemas import AlarmOut, SampleOut

router = APIRouter(prefix="/alarms", tags=["AI 视觉平台-告警中心"])

# 样本图目录（backend/uploads/ai_vision/samples，统一由 paths.py 管理）
_SAMPLES_DIR = samples_dir()


def _parse_out(item: AIVisionAlarm) -> dict:
    return AlarmOut.model_validate(item).model_dump()


def _sample_out(s: AIVisionSample) -> dict:
    return SampleOut.model_validate(s).model_dump()


async def _get_alarm_or_404(db: AsyncSession, alarm_id: int) -> AIVisionAlarm:
    item = await db.get(AIVisionAlarm, alarm_id)
    if not item:
        raise HTTPException(status_code=404, detail="告警不存在")
    return item


@router.get("")
async def list_alarms(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    camera_id: int | None = Query(default=None, ge=1),
    event_id: int | None = Query(default=None, ge=1),
    status: str = Query(default="", max_length=20),
    level: str = Query(default="", max_length=20),
    min_conf: float | None = Query(default=None, ge=0.0, le=1.0),
    max_conf: float | None = Query(default=None, ge=0.0, le=1.0),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
):
    """分页查询告警（多条件筛选，联表返回摄像头名/事件名）。"""
    stmt = select(AIVisionAlarm)
    if camera_id:
        stmt = stmt.where(AIVisionAlarm.camera_id == camera_id)
    if event_id:
        stmt = stmt.where(AIVisionAlarm.event_id == event_id)
    if status:
        stmt = stmt.where(AIVisionAlarm.status == status)
    if level:
        stmt = stmt.where(AIVisionAlarm.level == level)
    if min_conf is not None:
        stmt = stmt.where(AIVisionAlarm.confidence >= min_conf)
    if max_conf is not None:
        stmt = stmt.where(AIVisionAlarm.confidence <= max_conf)
    if start_at:
        stmt = stmt.where(AIVisionAlarm.created_at >= start_at)
    if end_at:
        stmt = stmt.where(AIVisionAlarm.created_at <= end_at)

    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.order_by(AIVisionAlarm.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()

    # 联表取摄像头名/事件名（一次查询缓存）
    cam_ids = {a.camera_id for a in items}
    evt_ids = {a.event_id for a in items}
    cams = {
        c.id: c.name
        for c in (await db.execute(select(AIVisionCamera).where(AIVisionCamera.id.in_(cam_ids)))).scalars().all()
    } if cam_ids else {}
    evts = {
        e.id: e.name
        for e in (await db.execute(select(AIVisionEvent).where(AIVisionEvent.id.in_(evt_ids)))).scalars().all()
    } if evt_ids else {}

    result = []
    for a in items:
        d = _parse_out(a)
        d["camera_name"] = cams.get(a.camera_id, "")
        d["event_name"] = evts.get(a.event_id, "")
        result.append(d)

    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": result,
    })


@router.post("/{alarm_id}/ack")
async def ack_alarm(
    alarm_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:edit"))],
):
    """确认告警（pending → acknowledged）。"""
    item = await _get_alarm_or_404(db, alarm_id)
    item.status = "acknowledged"
    item.ack_by = user.id
    item.ack_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="已确认")


@router.post("/{alarm_id}/false-positive")
async def false_positive_alarm(
    alarm_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:edit"))],
    note: str = Query(default="", max_length=500),
):
    """标记误报（pending/acknowledged → false_positive，留痕）。"""
    item = await _get_alarm_or_404(db, alarm_id)
    item.status = "false_positive"
    item.ack_by = user.id
    item.ack_at = datetime.now(timezone.utc)
    if note:
        item.note = note
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="已标记为误报")


@router.post("/{alarm_id}/to-sample")
async def alarm_to_sample(
    alarm_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:edit"))],
):
    """误报/告警转样本：复制抓拍图到 samples 目录 + 建样本记录。"""
    item = await _get_alarm_or_404(db, alarm_id)
    if not item.snapshot_path:
        raise HTTPException(status_code=400, detail="该告警无抓拍图，无法转样本")

    src = Path(item.snapshot_path)
    if not src.exists():
        raise HTTPException(status_code=400, detail=f"抓拍图不存在: {item.snapshot_path}")

    # 复制到 samples 目录（目录按事件组织）
    sample_dir = _SAMPLES_DIR / str(item.event_id)
    sample_dir.mkdir(parents=True, exist_ok=True)
    dst = sample_dir / f"alarm_{item.id}_{src.name}"
    shutil.copy2(src, dst)

    # 解析图片尺寸
    width = height = 0
    try:
        import cv2

        img = cv2.imread(str(dst))
        if img is not None:
            height, width = img.shape[:2]
    except Exception:  # noqa: BLE001
        pass

    sample = AIVisionSample(
        file_path=str(dst),
        source="alarm",
        label_status="unlabeled",
        label_data="{}",
        category_code=item.category_code,
        related_event_id=item.event_id,
        width=width,
        height=height,
    )
    db.add(sample)
    await db.commit()
    await db.refresh(sample)
    return success_response(data=_sample_out(sample), msg="已转样本")


@router.post("/batch-ack")
async def batch_ack(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:edit"))],
    alarm_ids: list[int] = Query(default=...),
):
    """批量确认告警。"""
    if not alarm_ids:
        raise HTTPException(status_code=400, detail="alarm_ids 不能为空")
    items = (await db.execute(
        select(AIVisionAlarm).where(AIVisionAlarm.id.in_(alarm_ids))
    )).scalars().all()
    now = datetime.now(timezone.utc)
    for item in items:
        if item.status == "pending":
            item.status = "acknowledged"
            item.ack_by = user.id
            item.ack_at = now
    await db.commit()
    return success_response(data={"acked": len(items)}, msg=f"已批量确认 {len(items)} 条")