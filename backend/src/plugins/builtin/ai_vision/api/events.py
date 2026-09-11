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
from sqlalchemy import select
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
    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.order_by(AIVisionEvent.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
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
    """删除事件（软删除；运行中不允许删除）。"""
    item = await db.get(AIVisionEvent, event_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="事件不存在")
    if item.status == "running":
        raise HTTPException(status_code=400, detail="运行中的事件不可删除，请先停止任务")
    item.is_deleted = True
    await db.commit()
    return success_response(msg="删除成功")