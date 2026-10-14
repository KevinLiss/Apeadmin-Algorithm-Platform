# -*- coding: utf-8 -*-
"""人脸识别记录 API（P1 建表与查询；数据自 P2 worker 上线后产生）。

路由前缀：``/ai-vision/face-logs``（挂载后 ``/api/v1/ai-vision/face-logs``）。
权限：``ai_vision:face_log:list``。

列表联表返回摄像头名（避免前端二次查询）；日期筛选按本地日期
（GMT+8）传入，后端转换为 naive UTC 与 ``created_at`` 比较
（SQLite 无时区类型，``server_default=func.now()`` 存的是 UTC）。
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionCamera, AIVisionFaceLog

router = APIRouter(prefix="/face-logs", tags=["AI 视觉平台-人脸识别记录"])

_LOCAL_TZ = timezone(timedelta(hours=8))  # GMT+8


def _parse_local_date(s: str, end_of_day: bool = False) -> datetime | None:
    """"YYYY-MM-DD" → naive UTC datetime（本地日期边界转 UTC）。"""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式应为 YYYY-MM-DD: {s}")
    if end_of_day:
        d = d + timedelta(days=1)  # 次日 0 点（开区间上界）
    return d.replace(tzinfo=_LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)


@router.get("")
async def list_face_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:face_log:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    camera_id: int | None = Query(default=None, description="按摄像头过滤"),
    person_id: int | None = Query(default=None, description="按人员过滤（0=陌生人）"),
    group: str = Query(default="", description="按分组过滤 whitelist/blacklist"),
    keyword: str = Query(default="", max_length=50, description="姓名关键字"),
    date_from: str = Query(default="", description="起始日期 YYYY-MM-DD（含）"),
    date_to: str = Query(default="", description="截止日期 YYYY-MM-DD（含）"),
):
    """人脸识别流水分页查询（新→旧）。"""
    stmt = select(AIVisionFaceLog)
    if camera_id is not None:
        stmt = stmt.where(AIVisionFaceLog.camera_id == camera_id)
    if person_id is not None:
        stmt = stmt.where(AIVisionFaceLog.person_id == person_id)
    if group:
        stmt = stmt.where(AIVisionFaceLog.group == group)
    if keyword:
        stmt = stmt.where(AIVisionFaceLog.person_name.contains(keyword))
    ts_from = _parse_local_date(date_from)
    ts_to = _parse_local_date(date_to, end_of_day=True)
    if ts_from is not None:
        stmt = stmt.where(AIVisionFaceLog.created_at >= ts_from)
    if ts_to is not None:
        stmt = stmt.where(AIVisionFaceLog.created_at < ts_to)

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar() or 0
    stmt = stmt.order_by(AIVisionFaceLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()

    # 联表摄像头名（一次 IN 查询）
    cam_names: dict[int, str] = {}
    cam_ids = {i.camera_id for i in items if i.camera_id}
    if cam_ids:
        cams = (
            await db.execute(select(AIVisionCamera).where(AIVisionCamera.id.in_(cam_ids)))
        ).scalars().all()
        cam_names = {c.id: c.name for c in cams}

    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": i.id,
                "person_id": i.person_id,
                "person_name": i.person_name,
                "group": i.group,
                "camera_id": i.camera_id,
                "camera_name": cam_names.get(i.camera_id, ""),
                "task_id": i.task_id,
                "similarity": i.similarity,
                "snapshot_path": i.snapshot_path,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ],
    })
