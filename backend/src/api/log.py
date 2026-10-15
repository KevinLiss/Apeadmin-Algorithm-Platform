"""System operation log routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import require_permission
from src.core.exceptions import NotFoundException, success_response
from src.crud import crud_log
from src.db import get_db
from src.models import User

router = APIRouter(prefix="/logs", tags=["系统日志"])


@router.get("")
async def list_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:list"))],
    page: int = 1,
    page_size: int = 20,
    method: str | None = None,
    path: str | None = None,
    status_code: int | None = None,
):
    """Paginated log list with optional filters."""
    filters = []
    if method:
        filters.append(crud_log.model.method == method)
    if path:
        filters.append(crud_log.model.path.like(f"%{path}%"))
    if status_code:
        filters.append(crud_log.model.status_code == status_code)

    items, total = await crud_log.get_multi(
        db, page=page, page_size=page_size, filters=filters or None
    )
    return success_response(
        data={
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "id": log.id,
                    "user_id": log.user_id,
                    "username": log.username,
                    "method": log.method,
                    "path": log.path,
                    "params": log.params,
                    "status_code": log.status_code,
                    "duration_ms": log.duration_ms,
                    "ip": log.ip,
                    "user_agent": log.user_agent,
                    "error": log.error,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in items
            ],
        }
    )


@router.get("/stats")
async def log_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:list"))],
):
    """日志统计：总量 + 按 HTTP 方法分布（验证写操作收窄后 GET 不再增长）。

    注意：必须注册在 ``/{log_id}`` 之前，否则会被 ``/logs/{log_id}``
    把 ``stats`` 当成 log_id（int 转换失败 → 422）。
    """
    from sqlalchemy import func, select

    rows = (
        await db.execute(
            select(crud_log.model.method, func.count(crud_log.model.id)).group_by(
                crud_log.model.method
            )
        )
    ).all()
    by_method = {m: c for m, c in rows}
    return success_response(data={"total": sum(by_method.values()), "by_method": by_method})


@router.get("/{log_id}")
async def get_log(
    log_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:list"))],
):
    """Get a single log entry."""
    log = await crud_log.get(db, log_id)
    if not log:
        raise NotFoundException("日志不存在")
    return success_response(
        data={
            "id": log.id,
            "user_id": log.user_id,
            "username": log.username,
            "method": log.method,
            "path": log.path,
            "params": log.params,
            "status_code": log.status_code,
            "duration_ms": log.duration_ms,
            "ip": log.ip,
            "user_agent": log.user_agent,
            "error": log.error,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
    )


@router.delete("/{log_id}")
async def delete_log(
    log_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:delete"))],
):
    """Delete a single log entry (hard delete)."""
    ok = await crud_log.delete(db, log_id, soft=False)
    if not ok:
        raise NotFoundException("日志不存在")
    return success_response(msg="删除成功")


@router.delete("")
async def clear_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:delete"))],
):
    """Clear all log entries."""
    stmt = sa_delete(crud_log.model)
    await db.execute(stmt)
    await db.commit()
    return success_response(msg="已清空所有日志")


@router.post("/cleanup")
async def cleanup_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_permission("system:log:delete"))],
    days: int = Query(default=90, ge=1, le=3650, description="删除 days 天前的日志"),
):
    """按保留期手动清理（默认 90 天）：删除 created_at 早于 cutoff 的日志。

    非自动删除——仅响应管理员主动调用，避免误删仍在查的操作审计记录。
    （另外 ai_vision 插件启动时仍有一道 30 天安全网，二者互补。）
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = sa_delete(crud_log.model).where(crud_log.model.created_at < cutoff.replace(tzinfo=None))
    result = await db.execute(stmt)
    await db.commit()
    return success_response(
        data={"deleted": result.rowcount or 0},
        msg=f"已清理 {days} 天前的日志 {result.rowcount or 0} 条",
    )
