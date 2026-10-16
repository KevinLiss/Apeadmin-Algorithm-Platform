"""报警外发配置 REST API。

提供：
- ``GET  /api/v1/notifications/config`` 读取 MQTT/Webhook 配置
- ``POST /api/v1/notifications/config`` 保存配置并热刷新内存态
- ``GET  /api/v1/notifications/status`` 各渠道启用状态
- ``POST /api/v1/notifications/test``    连通性测试

配置持久化在 ``sys_setting`` 表（``notification.mqtt.*`` /
``notification.webhook.*`` 扁平键）。敏感字段（password / secret）
读取时返回掩码，保存时若传空串则保留原值（不覆盖）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user, require_permission
from src.core.exceptions import success_response, ValidationException
from src.crud.setting import crud_setting
from src.db import get_db
from src.models import User
from src.notification.manager import notification_manager

router = APIRouter(prefix="/notifications", tags=["报警外发"])

# 各渠道字段定义与默认值
_FIELDS: dict[str, list[str]] = {
    "mqtt": ["enabled", "broker", "port", "username", "password", "topic"],
    "webhook": ["enabled", "url", "secret", "timeout"],
}
_DEFAULTS: dict[str, dict[str, str]] = {
    "mqtt": {
        "enabled": "false",
        "broker": "",
        "port": "1883",
        "username": "",
        "password": "",
        "topic": "apeadmin/alarm",
    },
    "webhook": {
        "enabled": "false",
        "url": "",
        "secret": "",
        "timeout": "5",
    },
}
_SENSITIVE = {"mqtt": {"password"}, "webhook": {"secret"}}


def _mask(value: str) -> str:
    return "********" if value else ""


async def _read_config(db: AsyncSession) -> dict[str, dict[str, str]]:
    """从 sys_setting 读取通知配置（敏感字段返回掩码）。"""
    out: dict[str, dict[str, str]] = {}
    for prefix, fields in _FIELDS.items():
        out[prefix] = {}
        for field in fields:
            key = f"notification.{prefix}.{field}"
            val = await crud_setting.get_value(db, key, _DEFAULTS[prefix][field])
            if field in _SENSITIVE.get(prefix, set()) and val:
                out[prefix][field] = _mask(val)
            else:
                out[prefix][field] = val
    return out


async def _write_config(db: AsyncSession, data: dict[str, dict[str, str]]) -> None:
    """写入通知配置；敏感字段传空串则保留 DB 原值（不覆盖）。"""
    for prefix, fields in _FIELDS.items():
        incoming = data.get(prefix, {})
        for field in fields:
            key = f"notification.{prefix}.{field}"
            new_val = incoming.get(field, _DEFAULTS[prefix][field])
            if field in _SENSITIVE.get(prefix, set()) and new_val in ("", "********"):
                # 空串或掩码：保留原值（不回写敏感字段）
                continue
            existing = await crud_setting.get_value(db, key, "")
            if new_val == existing:
                continue
            await crud_setting.upsert(
                db,
                key,
                str(new_val),
                category="notification",
                description=f"报警外发-{prefix}-{field}",
                is_public=False,
            )


class NotificationConfigIn(BaseModel):
    """保存通知配置：mqtt / webhook 两个扁平字典（字段均为字符串）。"""

    mqtt: dict[str, str] = Field(default_factory=dict)
    webhook: dict[str, str] = Field(default_factory=dict)


@router.get("/config")
async def get_config(
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_permission("system:notification:list"))],
):
    """读取 MQTT / Webhook 配置（敏感字段为掩码）。"""
    cfg = await _read_config(db)
    return success_response(data=cfg)


@router.post("/config")
async def save_config(
    body: NotificationConfigIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    _: Annotated[User, Depends(require_permission("system:notification:edit"))],
):
    """保存配置并热刷新内存态（下次发送即生效）。"""
    await _write_config(db, body.model_dump())
    await notification_manager.refresh_config()
    return success_response(msg="通知配置已保存")


@router.get("/status")
async def get_status(
    _: Annotated[User, Depends(require_permission("system:notification:list"))],
):
    """返回各渠道当前启用状态。"""
    return success_response(data={"channels": notification_manager.status()})


class TestIn(BaseModel):
    channel: str = Field(..., description="mqtt / webhook")


@router.post("/test")
async def test_channel(
    body: TestIn,
    _: Annotated[User, Depends(require_permission("system:notification:test"))],
):
    """测试指定渠道连通性（配置需先保存）。"""
    if body.channel not in _FIELDS:
        raise ValidationException(msg=f"未知渠道: {body.channel}")
    ok, msg = notification_manager.test_channel(body.channel)
    return success_response(data={"channel": body.channel, "ok": ok, "message": msg}, msg=msg)
