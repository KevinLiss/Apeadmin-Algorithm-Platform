"""GB28181 设备模型。

表示一个已注册的 GB28181 设备。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DeviceStatus(str, Enum):
    """设备状态。"""

    OFFLINE = "offline"
    ONLINE = "online"
    REGISTERED = "registered"
    STREAMING = "streaming"


@dataclass
class GB28181Device:
    """GB28181 设备。"""

    device_id: str  # 设备ID
    name: str = ""  # 设备名称
    ip: str = ""  # 设备IP
    port: int = 5060  # 设备端口
    transport: str = "UDP"  # 传输协议：UDP/TCP

    # 状态
    status: DeviceStatus = DeviceStatus.OFFLINE
    registered_at: datetime | None = None
    last_heartbeat: datetime | None = None

    # 通道信息
    channels: list[dict[str, Any]] = field(default_factory=list)

    # 媒体流
    stream_url: str | None = None  # RTP 推流地址

    # 内部状态
    _heartbeat_task: asyncio.Task | None = None
    _expires: int = 3600  # 注册有效期（秒）

    def is_online(self) -> bool:
        """检查设备是否在线。"""
        if self.status not in (DeviceStatus.ONLINE, DeviceStatus.REGISTERED, DeviceStatus.STREAMING):
            return False
        # 检查心跳超时
        if self.last_heartbeat:
            elapsed = (datetime.now() - self.last_heartbeat).total_seconds()
            if elapsed > self._expires * 1.5:  # 1.5倍超时视为离线
                self.status = DeviceStatus.OFFLINE
                return False
        return True

    def update_heartbeat(self) -> None:
        """更新心跳时间。"""
        self.last_heartbeat = datetime.now()
        if self.status == DeviceStatus.OFFLINE:
            self.status = DeviceStatus.ONLINE

    def to_dict(self) -> dict[str, Any]:
        """转换为字典。"""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "transport": self.transport,
            "status": self.status.value,
            "registered_at": self.registered_at.isoformat() if self.registered_at else None,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "channels": self.channels,
            "stream_url": self.stream_url,
        }
