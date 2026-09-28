"""模型库 API：查询内置/导入/训练模型列表（供事件绑定选择）。

路由前缀：``/ai-vision/models``（挂载后为 ``/api/v1/ai-vision/models``）。
权限：``ai_vision:model:list``（查询）、``ai_vision:model:manage``（删除）。

说明：删除仅针对导入/训练产物；被识别事件绑定中的模型不可删（防线上任务
加载不到权重）。builtin 内置模型同样允许删除——但会做绑定校验，用户确认
后可删（文件与 DB 记录一并清理）。
"""
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionEvent, AIVisionModel
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
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_parse_out(item) for item in items],
    })


def _resolve_model_file(rel: str) -> Path | None:
    """模型文件路径解析：绝对路径直用；相对路径按 插件根 → backend 根 尝试。"""
    if not rel:
        return None
    p = Path(rel)
    if p.is_absolute():
        return p if p.exists() else None
    plugin_root = Path(__file__).resolve().parents[1]  # ai_vision/
    for cand in (plugin_root / rel, Path.cwd() / rel):
        if cand.exists():
            return cand
    return None


@router.delete("/{model_id}")
async def delete_model(
    model_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:manage"))],
):
    """删除模型（记录 + ONNX/.pt 文件一并清理）。

    保护：被任何识别事件 model_ids 绑定的模型不可删（线上任务在用）。
    """
    item = await db.get(AIVisionModel, model_id)
    if not item:
        raise HTTPException(status_code=404, detail="模型不存在")
    # 绑定校验：遍历未删除事件的 model_ids JSON 数组
    events = (await db.execute(
        select(AIVisionEvent).where(AIVisionEvent.is_deleted == False)  # noqa: E712
    )).scalars().all()
    bound = []
    for e in events:
        try:
            ids = json.loads(e.model_ids or "[]")
        except (ValueError, TypeError):
            ids = []
        if model_id in ids:
            bound.append(e.name)
    if bound:
        raise HTTPException(
            status_code=400,
            detail=f"模型被识别事件绑定中，不可删除：{'、'.join(bound)}（请先换绑其他模型）",
        )
    # 清理磁盘文件（删除钩子可能抛 SystemExit，捕获 BaseException 防杀进程）
    name = item.name
    removed_files = 0
    for rel in (item.file_path, item.pt_path):
        fp = _resolve_model_file(rel or "")
        if fp:
            try:
                fp.unlink(missing_ok=True)
                removed_files += 1
            except BaseException:  # noqa: BLE001
                pass
    await db.delete(item)
    await db.commit()
    return success_response(msg=f"模型「{name}」已删除（含 {removed_files} 个文件）")