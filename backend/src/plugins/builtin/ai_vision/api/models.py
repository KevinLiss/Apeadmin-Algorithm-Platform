"""模型库 API：查询内置/导入/训练模型列表（供事件绑定选择）。

路由前缀：``/ai-vision/models``（挂载后为 ``/api/v1/ai-vision/models``）。
权限：``ai_vision:model:list``。

说明：一期只开放查询（事件绑模型用）；模型导入/训练属二期
（训练层作为可选层，见任务清单 M3 之后）。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionModel
from src.plugins.builtin.ai_vision.schemas import ModelOut

router = APIRouter(prefix="/models", tags=["AI 视觉平台-模型库"])


def _parse_out(item: AIVisionModel) -> dict:
    """ORM → dict（ModelOut 无 JSON 字段，直接 dump）。"""
    return ModelOut.model_validate(item).model_dump()


@router.get("")
async def list_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=50),
):
    """分页查询模型（支持名称关键字过滤，按 id 升序）。"""
    stmt = select(AIVisionModel).order_by(AIVisionModel.id.asc())
    if keyword:
        stmt = stmt.where(AIVisionModel.name.contains(keyword))
    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
    })