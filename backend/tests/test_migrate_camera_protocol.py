"""camera 多协议字段迁移脚本集成测试。

不依赖真实数据库引擎：用内存/临时文件 SQLite 替换 ``src.db.engine``，
验证 ALTER 新增列逻辑与幂等性。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import scripts.migrate_camera_protocol as migrate_mod  # noqa: E402


@pytest_asyncio.fixture
async def temp_engine(tmp_path):
    db_file = tmp_path / "migrate_test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with eng.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE ai_vision_cameras (id INTEGER PRIMARY KEY, name VARCHAR(200))"
        ))
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_migrate_adds_columns(temp_engine, monkeypatch):
    monkeypatch.setattr("src.db.engine", temp_engine)
    result = await migrate_mod.migrate()
    assert result is True
    async with temp_engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(ai_vision_cameras)"))).fetchall()
        cols = {r[1] for r in rows}
    for expected in migrate_mod.NEW_COLUMNS:
        assert expected in cols


@pytest.mark.asyncio
async def test_migrate_idempotent(temp_engine, monkeypatch):
    monkeypatch.setattr("src.db.engine", temp_engine)
    await migrate_mod.migrate()
    # 第二次运行应不报错且不再新增列
    await migrate_mod.migrate()
    async with temp_engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA table_info(ai_vision_cameras)"))).fetchall()
        cols = {r[1] for r in rows}
    assert len(cols) == 2 + len(migrate_mod.NEW_COLUMNS)  # id, name + 8 个新列
