"""数据库迁移：为 ai_vision_cameras 表新增多协议字段。

执行时机：已有旧数据库（ai_vision_cameras 表已存在）时运行一次。
新库通过 init_db() 的 create_all 自动建表，无需运行本脚本。

用法：
    python -m scripts.migrate_camera_protocol
"""

import asyncio
import sys
from pathlib import Path

# 确保 backend 目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from loguru import logger  # noqa: E402

# 需要新增的列：{列名: 列定义}
NEW_COLUMNS = {
    "protocol": "VARCHAR(20) DEFAULT 'rtsp'",
    "gb_device_id": "VARCHAR(50)",
    "gb_ip": "VARCHAR(50)",
    "gb_port": "INTEGER",
    "gb_channel": "VARCHAR(20)",
    "onvif_url": "VARCHAR(200)",
    "onvif_username": "VARCHAR(50)",
    "onvif_password": "VARCHAR(50)",
}


async def migrate() -> bool:
    """执行迁移。"""
    from src.db import engine

    async with engine.begin() as conn:
        # 检测表是否存在
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ai_vision_cameras'"
        ))
        if not result.scalar():
            logger.info("ai_vision_cameras 表不存在（新库），无需迁移")
            return True

        # 获取现有列
        result = await conn.execute(text("PRAGMA table_info(ai_vision_cameras)"))
        existing = {row[1] for row in result.fetchall()}

        added = 0
        for col, ddl in NEW_COLUMNS.items():
            if col not in existing:
                await conn.execute(text(
                    f"ALTER TABLE ai_vision_cameras ADD COLUMN {col} {ddl}"
                ))
                logger.info(f"已添加列: {col}")
                added += 1

        if added:
            logger.info(f"迁移完成：新增 {added} 列")
        else:
            logger.info("所有列已存在，无需迁移")
    return True


if __name__ == "__main__":
    asyncio.run(migrate())
