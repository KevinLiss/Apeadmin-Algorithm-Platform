"""系统日志 API 测试：手动清理（按保留期）+ 统计。

聚焦路由通过 importlib 直接加载，避免触发 src.api 聚合导入。
"""
import importlib.util
import sys
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import settings
from src.core.deps import get_current_user
from src.db import Base, get_db
from src.models.log import SysLog


def _load_router(modname, filename):
    api_dir = Path(__file__).resolve().parent.parent / "src/api"
    spec = importlib.util.spec_from_file_location(modname, str(api_dir / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod.router


_router_logs = _load_router("test_router_logs", "log.py")


@pytest_asyncio.fixture
async def logs_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SysLog.__table__])

    async def _db():
        async with factory() as s:
            yield s

    async def _user(request: Request):
        from src.models import User

        return User(id=1, username=settings.SUPER_ADMIN_USERNAME, status=1)

    app = FastAPI()
    app.include_router(_router_logs, prefix="/api/v1")
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory
    app.dependency_overrides.clear()


async def test_cleanup_removes_old_rows(logs_client):
    client, factory = logs_client
    from datetime import datetime, timedelta

    old = datetime.utcnow() - timedelta(days=120)
    recent = datetime.utcnow()
    async with factory() as db:
        db.add(SysLog(method="POST", path="/x", status_code=200, created_at=old))
        db.add(SysLog(method="POST", path="/y", status_code=200, created_at=recent))
        await db.commit()

    r = await client.post("/api/v1/logs/cleanup", params={"days": 90})
    assert r.status_code == 200
    assert r.json()["data"]["deleted"] == 1  # 只有 120 天前的被删

    async with factory() as db:
        remain = (await db.execute(select(func.count()).select_from(SysLog))).scalar()
    assert remain == 1


async def test_cleanup_keeps_everything_when_days_large(logs_client):
    client, factory = logs_client
    from datetime import datetime, timedelta

    old = datetime.utcnow() - timedelta(days=120)
    async with factory() as db:
        db.add(SysLog(method="POST", path="/x", status_code=200, created_at=old))
        await db.commit()

    r = await client.post("/api/v1/logs/cleanup", params={"days": 3650})
    assert r.json()["data"]["deleted"] == 0


async def test_stats_counts_by_method(logs_client):
    client, factory = logs_client
    async with factory() as db:
        for m in ("GET", "GET", "POST", "DELETE"):
            db.add(SysLog(method=m, path="/x", status_code=200))
        await db.commit()

    r = await client.get("/api/v1/logs/stats")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 4
    assert data["by_method"].get("GET") == 2
    assert data["by_method"].get("POST") == 1
