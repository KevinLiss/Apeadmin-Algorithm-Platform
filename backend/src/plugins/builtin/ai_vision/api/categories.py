"""类别库 API：内置类别查询 + 自定义类别 CRUD。

路由前缀：``/ai-vision/categories``（挂载后为 ``/api/v1/ai-vision/categories``）。
权限：``ai_vision:category:list/create/edit/delete``。

实现要点（任务 1.3）：
- 内置类别（source=builtin）不允许删除，只能停用（status=0）
- 自定义类别（source=custom）可编辑、可删除
- coco_map 以 JSON 文本存库，读写时序列化/反序列化
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
from src.plugins.builtin.ai_vision.models import AIVisionCategory
from src.plugins.builtin.ai_vision.schemas import CategoryCreate, CategoryOut, CategoryUpdate

router = APIRouter(prefix="/categories", tags=["AI 视觉平台-类别库"])


def _parse_out(item: AIVisionCategory) -> dict:
    """ORM → dict（解析 coco_map JSON）。"""
    data = CategoryOut.model_validate(item).model_dump()
    try:
        data["coco_map"] = json.loads(item.coco_map or "{}")
    except (ValueError, TypeError):
        data["coco_map"] = {}
    return data


@router.get("")
async def list_categories(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:category:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=50),
):
    """分页查询类别（支持关键字过滤）。"""
    stmt = select(AIVisionCategory).where(AIVisionCategory.is_deleted == False)  # noqa: E712
    if keyword:
        stmt = stmt.where(
            AIVisionCategory.name.contains(keyword)
            | AIVisionCategory.code.contains(keyword)
        )
    total = len((await db.execute(stmt)).scalars().all())
    stmt = stmt.order_by(AIVisionCategory.id.asc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
    })


@router.post("")
async def create_category(
    body: CategoryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:category:create"))],
):
    """新增自定义类别。"""
    dup = (await db.execute(select(AIVisionCategory).where(AIVisionCategory.code == body.code))).scalar_one_or_none()
    if dup:
        raise HTTPException(status_code=409, detail=f"类别编码 {body.code} 已存在")

    item = AIVisionCategory(
        code=body.code,
        name=body.name,
        icon=body.icon,
        source="custom",
        coco_map=json.dumps(body.coco_map, ensure_ascii=False),
        status=1,
        description=body.description,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="类别创建成功")


@router.put("/{category_id}")
async def update_category(
    category_id: int,
    body: CategoryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:category:edit"))],
):
    """编辑类别（内置类别禁止改编码，可改名称/映射/停用）。"""
    item = await db.get(AIVisionCategory, category_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="类别不存在")

    update_data = body.model_dump(exclude_unset=True)
    if update_data.get("coco_map") is not None:
        update_data["coco_map"] = json.dumps(update_data["coco_map"], ensure_ascii=False)
    for key, value in update_data.items():
        if key == "coco_map" and value is None:
            continue
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="更新成功")


@router.delete("/{category_id}")
async def delete_category(
    category_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:category:delete"))],
):
    """删除类别（内置类别仅停用，不允许物理删除）。"""
    item = await db.get(AIVisionCategory, category_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="类别不存在")
    if item.source == "builtin":
        raise HTTPException(status_code=400, detail="内置类别不可删除，可停用")
    item.is_deleted = True
    await db.commit()
    return success_response(msg="删除成功")