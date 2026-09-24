"""任务编排 API（任务 2.4）。

路由前缀：``/ai-vision/tasks``（挂载后为 ``/api/v1/ai-vision/tasks``）。
权限：``ai_vision:task:list/create/edit/delete/control``。

实现要点（任务 2.4）：
- 任务 CRUD：摄像头 × 事件 绑定（同摄像头多任务共享一个 StreamWorker）
- POST /tasks/{id}/start、/stop：启停（经 WorkerManager 聚合）
- GET /tasks/{id}/stats：运行指标（fps/耗时/最近告警）
- 任务删除前必须 stop（否则拒绝）
- 事件必须为 ready 状态才能创建/启动任务
"""
import json

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionCamera, AIVisionEvent, AIVisionTask
from src.plugins.builtin.ai_vision.runtime.manager import get_manager
from src.plugins.builtin.ai_vision.runtime.worker import TaskBinding
from src.plugins.builtin.ai_vision.schemas import TaskCreate, TaskOut

router = APIRouter(prefix="/tasks", tags=["AI 视觉平台-任务编排"])


def _parse_out(item: AIVisionTask) -> dict:
    """ORM → dict（解析 last_stats JSON）。"""
    try:
        last_stats = json.loads(item.last_stats or "{}")
    except (ValueError, TypeError):
        last_stats = {}
    return TaskOut.model_validate({
        "id": item.id,
        "camera_id": item.camera_id,
        "event_id": item.event_id,
        "status": item.status,
        "analyze_fps": item.analyze_fps,
        "last_stats": last_stats,
        "started_at": item.started_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }).model_dump()


async def _get_task_or_404(db: AsyncSession, task_id: int) -> AIVisionTask:
    item = await db.get(AIVisionTask, task_id)
    if not item:
        raise HTTPException(status_code=404, detail="任务不存在")
    return item


async def _build_binding(db: AsyncSession, task: AIVisionTask) -> TaskBinding:
    """从 DB 组装 worker 绑定（加载事件规则 / 类别 / 模型）。"""
    event = await db.get(AIVisionEvent, task.event_id)
    if not event or event.is_deleted:
        raise HTTPException(status_code=400, detail="关联事件不存在，无法启动任务")
    try:
        category_codes = json.loads(event.category_codes or "[]")
        rule = json.loads(event.rule or "{}")
        model_ids = json.loads(event.model_ids or "[]")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"事件配置解析失败: {exc}") from exc
    if not model_ids:
        raise HTTPException(status_code=400, detail="事件未绑定模型，请先绑定模型版本")
    return TaskBinding(
        task_id=task.id,
        event_id=task.event_id,
        camera_id=task.camera_id,
        category_codes=category_codes,
        rule=rule,
        model_ids=model_ids,
        analyze_fps=task.analyze_fps,
        status="running",
    )


# ── CRUD ─────────────────────────────────────────────────

@router.get("")
async def list_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="", max_length=20),
    camera_id: int | None = Query(default=None, ge=1),
    event_id: int | None = Query(default=None, ge=1),
):
    """分页查询任务（支持状态/摄像头/事件过滤）。"""
    stmt = select(AIVisionTask)
    if status:
        stmt = stmt.where(AIVisionTask.status == status)
    if camera_id:
        stmt = stmt.where(AIVisionTask.camera_id == camera_id)
    if event_id:
        stmt = stmt.where(AIVisionTask.event_id == event_id)
    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.order_by(AIVisionTask.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()

    # 联表取摄像头名/事件名（一次查询缓存）
    cam_ids = {t.camera_id for t in items}
    evt_ids = {t.event_id for t in items}
    cams = {
        c.id: c.name
        for c in (await db.execute(
            select(AIVisionCamera).where(AIVisionCamera.id.in_(cam_ids))
        )).scalars().all()
    } if cam_ids else {}
    evts = {
        e.id: e.name
        for e in (await db.execute(
            select(AIVisionEvent).where(AIVisionEvent.id.in_(evt_ids))
        )).scalars().all()
    } if evt_ids else {}

    # 运行中任务的实时指标来自 worker 内存快照（last_stats 不持久化，
    # 仅在启动时清零——列表接口直接叠加，否则"实时指标"列永远为空）
    live_by_task: dict[int, dict] = {}
    try:
        from src.plugins.builtin.ai_vision.runtime.manager import get_manager

        for w in get_manager().list_workers():
            stats = {
                "fps_actual": w["fps_actual"],
                "frames_processed": w["frames_processed"],
                "frames_read": w.get("frames_read"),
                "drop_rate": w.get("drop_rate"),
            }
            for tid in w["tasks"]:
                live_by_task[tid] = stats
    except Exception:  # noqa: BLE001  manager 未就绪时静默降级
        pass

    result = []
    for item in items:
        d = _parse_out(item)
        d["camera_name"] = cams.get(item.camera_id, "")
        d["event_name"] = evts.get(item.event_id, "")
        if item.status == "running" and item.id in live_by_task:
            d["last_stats"] = live_by_task[item.id]
        result.append(d)

    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": result,
    })


@router.post("")
async def create_task(
    body: TaskCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:create"))],
):
    """新建任务（摄像头 × 事件，初始 pending）。"""
    cam = await db.get(AIVisionCamera, body.camera_id)
    if not cam or cam.is_deleted:
        raise HTTPException(status_code=400, detail="摄像头不存在或已删除")
    evt = await db.get(AIVisionEvent, body.event_id)
    if not evt or evt.is_deleted:
        raise HTTPException(status_code=400, detail="事件不存在或已删除")
    if evt.status != "ready":
        raise HTTPException(status_code=400, detail=f"事件状态 {evt.status} 不可启动任务（需 ready）")

    # 同摄像头同事件去重
    dup = (await db.execute(
        select(AIVisionTask).where(
            AIVisionTask.camera_id == body.camera_id,
            AIVisionTask.event_id == body.event_id,
        )
    )).scalars().first()
    if dup:
        raise HTTPException(status_code=400, detail="该摄像头与事件已存在任务，请勿重复创建")

    item = AIVisionTask(
        camera_id=body.camera_id,
        event_id=body.event_id,
        status="pending",
        analyze_fps=body.analyze_fps,
        last_stats="{}",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="任务创建成功")


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:list"))],
):
    """查询单个任务。"""
    item = await _get_task_or_404(db, task_id)
    return success_response(data=_parse_out(item))


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:delete"))],
):
    """删除任务（运行中必须先停止）。"""
    item = await _get_task_or_404(db, task_id)
    if item.status == "running":
        raise HTTPException(status_code=400, detail="运行中的任务不可删除，请先停止")
    await db.delete(item)
    await db.commit()
    return success_response(msg="任务删除成功")


# ── 启停 ─────────────────────────────────────────────────

@router.post("/{task_id}/start")
async def start_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:control"))],
):
    """启动任务：绑定事件规则/模型 → WorkerManager 聚合到摄像头 worker。"""
    item = await _get_task_or_404(db, task_id)
    if item.status == "running":
        return success_response(data=_parse_out(item), msg="任务已在运行")

    # 事件必须 ready 才可启动
    evt = await db.get(AIVisionEvent, item.event_id)
    if not evt or evt.is_deleted:
        raise HTTPException(status_code=400, detail="关联事件不存在")
    if evt.status not in {"ready", "running"}:
        raise HTTPException(status_code=400, detail=f"事件状态 {evt.status} 不可启动任务")

    # 帧率纠偏：任务 analyze_fps 低于事件规则 fps 时提升到规则值。
    # 场景：跳水等短动作事件规则要求 ≥5fps，但旧任务/监控台早期版本建的
    # 任务写死 2fps——2fps 采样会整段跳过腾空姿态帧导致漏报（真实跳水
    # 视频实测 2fps 只抓到 1/3 次跳水）。落库持久化，worker 按新值跑。
    try:
        rule_fps = int(json.loads(evt.rule or "{}").get("fps", 0) or 0)
    except (ValueError, TypeError):
        rule_fps = 0
    if rule_fps and item.analyze_fps < rule_fps:
        item.analyze_fps = rule_fps

    binding = await _build_binding(db, item)

    # 同步写状态 → 异步启动 worker（WorkerManager 内部有锁）
    item.status = "running"
    item.started_at = datetime.now(timezone.utc)
    item.last_stats = "{}"
    evt.status = "running"
    await db.commit()

    try:
        # 启动在 to_thread 中执行（涉及 DB 读取 + 线程创建，阻塞 IO）
        import asyncio
        from src.plugins.builtin.ai_vision.runtime.manager import get_manager
        await asyncio.to_thread(get_manager().start_task, binding)
    except Exception as exc:  # noqa: BLE001
        # 启动失败回滚任务状态
        item.status = "pending"
        item.started_at = None
        evt.status = "ready"
        await db.commit()
        raise HTTPException(status_code=500, detail=f"任务启动失败: {exc}") from exc

    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="任务已启动")


@router.post("/{task_id}/stop")
async def stop_task(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:control"))],
):
    """停止任务；该摄像头无其他任务时销毁 worker。"""
    item = await _get_task_or_404(db, task_id)
    if item.status != "running":
        raise HTTPException(status_code=400, detail="任务未在运行")

    manager = get_manager()
    try:
        import asyncio
        await asyncio.to_thread(manager.stop_task, item.id, item.camera_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"任务停止失败: {exc}") from exc

    item.status = "stopped"
    # started_at 保留为"最近一次启动时间"（原来清空导致列表恒显示"—"，
    # 历史运行时间无从追溯；重新开始时会被新的启动时间覆盖）
    await db.commit()

    # 事件状态回退：若该事件无其他运行任务 → ready
    evt = await db.get(AIVisionEvent, item.event_id)
    if evt and evt.status == "running":
        other = (await db.execute(
            select(AIVisionTask).where(
                AIVisionTask.event_id == item.event_id,
                AIVisionTask.status == "running",
            )
        )).scalars().first()
        if not other:
            evt.status = "ready"
            await db.commit()

    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="任务已停止")


@router.get("/{task_id}/stats")
async def task_stats(
    task_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:task:list"))],
):
    """运行指标（实时从 worker 读取 + 最近一次落库统计）。"""
    item = await _get_task_or_404(db, task_id)
    manager = get_manager()
    worker = manager.get_worker(item.camera_id)
    live = {}
    if worker:
        live = {
            "state": worker.state,
            "fps_actual": worker.stats.fps_actual,
            "frames_read": worker.stats.frames_read,
            "frames_processed": worker.stats.frames_processed,
            "drop_rate": worker.stats.drop_rate,
            "last_alarm_at": worker.stats.last_alarm_at,
            "tasks_on_camera": worker.task_count(),
        }
    try:
        last_stats = json.loads(item.last_stats or "{}")
    except (ValueError, TypeError):
        last_stats = {}
    return success_response(data={
        "task_id": item.id,
        "status": item.status,
        "live": live,
        "last_stats": last_stats,
    })