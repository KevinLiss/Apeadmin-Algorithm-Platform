"""统计看板 API（任务 3.2 / 3.3）。

路由前缀：``/ai-vision/dashboard``（挂载后为 ``/api/v1/ai-vision/dashboard``）。
权限：``ai_vision:dashboard:list``。

实现要点（任务 3.2）：
- GET /dashboard/runtime：实时运行指标（活跃 worker 数、各 worker fps/延迟/丢帧、CPU/内存）
- GET /dashboard/overview：今日告警数、7 日趋势、事件分布、摄像头排行
"""
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
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
    AIVisionTask,
)
from src.plugins.builtin.ai_vision.runtime import deps
from src.plugins.builtin.ai_vision.runtime.manager import get_manager

router = APIRouter(prefix="/dashboard", tags=["AI 视觉平台-统计看板"])


@router.get("/runtime")
async def dashboard_runtime(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:dashboard:list"))],
):
    """实时运行指标（活跃 worker、各 worker 指标、系统资源）。"""
    manager = get_manager()

    # 系统资源（psutil 是底座依赖，直接可用）
    sys_res = {}
    try:
        import psutil

        vm = psutil.virtual_memory()
        sys_res = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": vm.percent,
            "memory_used_mb": round(vm.used / 1024 / 1024, 1),
            "memory_total_mb": round(vm.total / 1024 / 1024, 1),
        }
    except Exception:  # noqa: BLE001
        sys_res = {"cpu_percent": 0, "memory_percent": 0}

    # 任务统计（DB）
    total_tasks = len((await db.execute(select(AIVisionTask))).scalars().all())
    running_tasks = len(
        (await db.execute(select(AIVisionTask).where(AIVisionTask.status == "running"))).scalars().all()
    )
    total_cameras = len(
        (await db.execute(select(AIVisionCamera).where(AIVisionCamera.is_deleted == False))).scalars().all()  # noqa: E712
    )
    online_cameras = len(
        (await db.execute(
            select(AIVisionCamera).where(
                AIVisionCamera.is_deleted == False,  # noqa: E712
                AIVisionCamera.status == "online",
            )
        )).scalars().all()
    )

    # 运行环境安装状态（引导页第 1 步检测：复用 runtime/status 的层检测逻辑）
    # check_all_layers() 返回 {"L1": {...}, "L2": {...}}（dict 而非 list）
    layers: dict = {}
    try:
        layers = deps.check_all_layers()
    except Exception:  # noqa: BLE001
        layers = {}

    return success_response(data={
        "workers": manager.list_workers(),
        "worker_count": manager.worker_count(),
        "active_task_ids": sorted(manager.active_tasks()),
        "system": sys_res,
        "tasks": {"total": total_tasks, "running": running_tasks},
        "cameras": {"total": total_cameras, "online": online_cameras},
        "layers": layers,
    })


@router.get("/overview")
async def dashboard_overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:dashboard:list"))],
):
    """业务概览：今日告警、7 日趋势、事件分布、摄像头排行。"""
    now = datetime.now(timezone.utc)
    # 日切分统一按北京时间：created_at 为 naive UTC，北京零点 = 其 UTC 值前一日 16:00
    bj_now = now + timedelta(hours=8)
    today_start = datetime.combine(bj_now.date(), dtime.min, tzinfo=timezone.utc) - timedelta(hours=8)

    # 今日告警数
    today_alarms = len(
        (await db.execute(
            select(AIVisionAlarm).where(AIVisionAlarm.created_at >= today_start)
        )).scalars().all()
    )
    # 未处理告警
    pending_alarms = len(
        (await db.execute(
            select(AIVisionAlarm).where(AIVisionAlarm.status == "pending")
        )).scalars().all()
    )

    # 7 日趋势（按天分组，北京日期）
    trend: dict[str, int] = {}
    for i in range(6, -1, -1):
        day = (bj_now - timedelta(days=i)).strftime("%m-%d")
        trend[day] = 0
    alarms = (await db.execute(
        select(AIVisionAlarm.created_at)
    )).scalars().all()
    for ts in alarms:
        if ts is None:
            continue
        # naive UTC → 北京时间（+8h）；带 tzinfo 的直接换时区
        local_ts = ts + timedelta(hours=8) if ts.tzinfo is None else ts.astimezone(timezone(timedelta(hours=8)))
        key = local_ts.strftime("%m-%d")
        if key in trend:
            trend[key] += 1

    # 事件分布（按类别）
    alarm_rows = (await db.execute(
        select(AIVisionAlarm.category_code, func.count(AIVisionAlarm.id))
        .group_by(AIVisionAlarm.category_code)
    )).all()
    event_distribution = [{"category": code or "unknown", "count": cnt} for code, cnt in alarm_rows]

    # 摄像头告警排行
    cam_rows = (await db.execute(
        select(AIVisionAlarm.camera_id, func.count(AIVisionAlarm.id))
        .group_by(AIVisionAlarm.camera_id)
        .order_by(func.count(AIVisionAlarm.id).desc())
        .limit(10)
    )).all()
    cam_names = {
        c.id: c.name
        for c in (await db.execute(
            select(AIVisionCamera).where(
                AIVisionCamera.id.in_([r[0] for r in cam_rows] if cam_rows else [-1])
            )
        )).scalars().all()
    }
    camera_ranking = [
        {"camera_id": cid, "camera_name": cam_names.get(cid, f"#{cid}"), "count": cnt}
        for cid, cnt in cam_rows
    ]

    # 事件运行状态分布
    evt_rows = (await db.execute(
        select(AIVisionEvent.status, func.count(AIVisionEvent.id))
        .where(AIVisionEvent.is_deleted == False)  # noqa: E712
        .group_by(AIVisionEvent.status)
    )).all()
    event_status = [{"status": s, "count": cnt} for s, cnt in evt_rows]

    # 总告警数（引导页第 3 步检测）
    total_alarms = len((await db.execute(select(AIVisionAlarm.id))).scalars().all())

    return success_response(data={
        "today_alarms": today_alarms,
        "pending_alarms": pending_alarms,
        "total_alarms": total_alarms,
        "trend": trend,
        "event_distribution": event_distribution,
        "camera_ranking": camera_ranking,
        "event_status": event_status,
    })