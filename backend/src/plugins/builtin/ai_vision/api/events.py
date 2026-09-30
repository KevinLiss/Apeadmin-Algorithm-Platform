"""识别事件 API：事件 CRUD + 发布 / 退役 状态流转。

路由前缀：``/ai-vision/events``（挂载后为 ``/api/v1/ai-vision/events``）。
权限：``ai_vision:event:list/create/edit/delete``。

实现要点（任务 1.3）：
- 事件创建时校验：所选类别必须存在且启用、规则合法（EventRuleSchema）
- 状态机：draft → ready → running → paused → pending_train
- 事件不物理删除（软删除），已发布事件删除前需退役
"""
import json

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionCategory, AIVisionEvent
from src.plugins.builtin.ai_vision.schemas import EventCreate, EventOut, EventRule, EventUpdate

router = APIRouter(prefix="/events", tags=["AI 视觉平台-识别事件"])


def _parse_out(item: AIVisionEvent) -> dict:
    """ORM → dict（解析 category_codes / rule / model_ids JSON）。"""
    try:
        category_codes = json.loads(item.category_codes or "[]")
    except (ValueError, TypeError):
        category_codes = []
    try:
        rule = json.loads(item.rule or "{}")
    except (ValueError, TypeError):
        rule = {}
    try:
        model_ids = json.loads(item.model_ids or "[]")
    except (ValueError, TypeError):
        model_ids = []
    data = EventOut.model_validate({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "category_codes": category_codes,
        "rule": rule,
        "status": item.status,
        "model_ids": model_ids,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }).model_dump()
    return data


async def _validate_categories(db: AsyncSession, codes: list[str]) -> None:
    """校验所选类别均存在且启用。"""
    if not codes:
        raise HTTPException(status_code=400, detail="至少选择一个类别")
    result = await db.execute(
        select(AIVisionCategory).where(
            AIVisionCategory.code.in_(codes),
            AIVisionCategory.status == 1,
            AIVisionCategory.is_deleted == False,  # noqa: E712
        )
    )
    found = {c.code for c in result.scalars().all()}
    missing = set(codes) - found
    if missing:
        raise HTTPException(status_code=400, detail=f"类别不存在或已停用: {', '.join(sorted(missing))}")


@router.get("")
async def list_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=50),
    status: str = Query(default="", max_length=20),
):
    """分页查询事件（支持关键字/状态过滤）。"""
    stmt = select(AIVisionEvent).where(AIVisionEvent.is_deleted == False)  # noqa: E712
    if keyword:
        stmt = stmt.where(AIVisionEvent.name.contains(keyword))
    if status:
        stmt = stmt.where(AIVisionEvent.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(AIVisionEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()

    # 规则质量自检（近7天）：各事件告警数/误报数 → 误报率与建议（自查优化项14）。
    # 一次 group by 双列统计，不逐事件查。
    quality: dict[int, dict] = {}
    try:
        from datetime import datetime, time as dtime, timedelta, timezone

        from sqlalchemy import case

        from src.plugins.builtin.ai_vision.models import AIVisionAlarm

        now_utc = datetime.now(timezone.utc)
        bj_today = (now_utc + timedelta(hours=8)).date()
        week_start = datetime.combine(bj_today - timedelta(days=6), dtime.min, tzinfo=timezone.utc) - timedelta(hours=8)
        rows = (await db.execute(
            select(
                AIVisionAlarm.event_id,
                func.count(),
                func.sum(case((AIVisionAlarm.status == "false_positive", 1), else_=0)),
            )
            .where(AIVisionAlarm.created_at >= week_start)
            .group_by(AIVisionAlarm.event_id)
        )).all()
        for eid, total_cnt, fp_cnt in rows:
            fp_cnt = int(fp_cnt or 0)
            rate = fp_cnt / total_cnt if total_cnt else 0
            tip = ""
            if total_cnt >= 5 and rate >= 0.5:
                tip = "误报率过半：建议提高置信度阈值或缩短判定持续时长，并转误报为负样本复训"
            elif total_cnt >= 5 and rate >= 0.3:
                tip = "误报偏高：建议适当提高阈值，或用「智能建议」把误报图转负样本复训"
            quality[eid] = {"alarms_7d": total_cnt, "false_positives_7d": fp_cnt, "fp_rate": round(rate, 3), "tip": tip}
    except Exception:  # noqa: BLE001 — 质量统计失败不影响列表主体
        pass

    out = []
    for item in items:
        d = _parse_out(item)
        d["quality"] = quality.get(item.id, {"alarms_7d": 0, "false_positives_7d": 0, "fp_rate": 0, "tip": ""})
        out.append(d)
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": out,
    })


@router.post("")
async def create_event(
    body: EventCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:create"))],
):
    """创建识别事件（校验类别与规则，状态初始 draft）。"""
    await _validate_categories(db, body.category_codes)

    item = AIVisionEvent(
        name=body.name,
        description=body.description,
        category_codes=json.dumps(body.category_codes, ensure_ascii=False),
        rule=body.rule.model_dump_json(),
        status="draft",
        model_ids=json.dumps(body.model_ids, ensure_ascii=False),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="事件创建成功")


@router.get("/{event_id}")
async def get_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:list"))],
):
    """查询单个事件详情。"""
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    return success_response(data=_parse_out(item))


@router.put("/{event_id}")
async def update_event(
    event_id: int,
    body: EventUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:edit"))],
):
    """编辑事件（运行中事件禁止修改规则）。"""
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    if item.status == "running":
        raise HTTPException(status_code=400, detail="运行中的事件不可编辑，请先暂停")

    update_data = body.model_dump(exclude_unset=True)
    if update_data.get("category_codes") is not None:
        await _validate_categories(db, update_data["category_codes"])
        update_data["category_codes"] = json.dumps(update_data["category_codes"], ensure_ascii=False)
    if update_data.get("rule") is not None:
        # model_dump() 后 rule 已是 dict（非 EventRule 对象）
        update_data["rule"] = json.dumps(update_data["rule"], ensure_ascii=False)
    if update_data.get("model_ids") is not None:
        update_data["model_ids"] = json.dumps(update_data["model_ids"], ensure_ascii=False)
    for key, value in update_data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="更新成功")


@router.post("/{event_id}/publish")
async def publish_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:edit"))],
):
    """发布事件：draft/ready → ready（可被任务编排引用）。"""
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    if item.status not in {"draft", "ready"}:
        raise HTTPException(status_code=400, detail=f"当前状态 {item.status} 不可发布")
    item.status = "ready"
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="事件已发布")


@router.post("/{event_id}/retire")
async def retire_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:edit"))],
):
    """退役事件：ready/running/paused → pending_train（提示可发起训练）。"""
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    if item.status == "running":
        raise HTTPException(status_code=400, detail="运行中的事件请先停止任务再退役")
    item.status = "pending_train"
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="事件已退役，可基于样本发起训练")


@router.delete("/{event_id}")
async def delete_event(
    event_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:event:delete"))],
):
    """删除事件（软删除；运行中不允许删除）。

    级联（系统体检 P2-2）：引用该事件的 pending/stopped 任务一并删除——
    旧版不管任务，留下事件名为空的僵尸任务；有 running 任务的事件本就
    status=running 被上面拦截，双保险再查一次。
    """
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    if item.status == "running":
        raise HTTPException(status_code=400, detail="运行中的事件不可删除，请先停止任务")
    from src.plugins.builtin.ai_vision.models import AIVisionTask

    ref_tasks = (await db.execute(
        select(AIVisionTask).where(AIVisionTask.event_id == event_id)
    )).scalars().all()
    running_ref = [t for t in ref_tasks if t.status == "running"]
    if running_ref:
        raise HTTPException(status_code=400, detail=f"仍有 {len(running_ref)} 个运行中任务引用该事件，请先停止")
    for t in ref_tasks:
        await db.delete(t)
    item.is_deleted = True
    await db.commit()
    return success_response(
        msg=f"删除成功{f'（连带删除 {len(ref_tasks)} 个关联任务）' if ref_tasks else ''}",
    )