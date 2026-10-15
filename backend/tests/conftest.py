"""共享测试夹具：内存 SQLite + 聚焦的告警/监控台路由 + 鉴权绕过。

只为告警中心（alarms）与实时监控台（monitor）两条路由构建最小 FastAPI
应用，避免拉起整个插件带来的重依赖（运行时/onnxruntime 等）。

要点：
- 用内存 aiosqlite，每个测试隔离；只建本测试需要的 3 张表
  （ai_vision_alarms / cameras / events），其余表不建（未用到的端点不会触发查询）
- 直接以 importlib 加载子路由模块，绕开 api/__init__ 聚合导入全部兄弟路由
- 依赖覆盖：get_db → 内存 session；get_current_user → 超级管理员（绕过权限校验）
"""
import importlib.util
import sys
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.core.config import settings
from src.core.deps import get_current_user
from src.db import Base, get_db
from src.plugins.builtin.ai_vision.models import (
    AIVisionAlarm,
    AIVisionCamera,
    AIVisionEvent,
)


def _load_router(modname: str, filename: str):
    api_dir = Path(__file__).resolve().parent.parent / "src/plugins/builtin/ai_vision/api"
    spec = importlib.util.spec_from_file_location(modname, str(api_dir / filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod.router


_router_alarms = _load_router("test_router_alarms", "alarms.py")
_router_monitor = _load_router("test_router_monitor", "monitor.py")


@pytest_asyncio.fixture
async def ai_factory():
    """内存数据库 + 会话工厂（仅建告警相关 3 张表）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[
                AIVisionAlarm.__table__,
                AIVisionCamera.__table__,
                AIVisionEvent.__table__,
            ],
        )
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def ai_client(ai_factory):
    """挂载 alarms + monitor 路由的最小应用，带鉴权绕过。"""
    factory = ai_factory

    async def _override_get_db():
        async with factory() as session:
            yield session

    async def _override_get_user(request: Request):
        from src.models import User

        return User(id=1, username=settings.SUPER_ADMIN_USERNAME, status=1)

    app = FastAPI()
    app.include_router(_router_alarms, prefix="/api/v1/ai-vision")
    app.include_router(_router_monitor, prefix="/api/v1/ai-vision")
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_get_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
