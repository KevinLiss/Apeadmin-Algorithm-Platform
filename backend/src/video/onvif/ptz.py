"""ONVIF PTZ 云台控制服务封装。"""

from __future__ import annotations

from loguru import logger

from .client import ONVIFClient


class PTZService:
    """ONVIF PTZ 云台控制服务。"""

    def __init__(self, client: ONVIFClient):
        self.client = client
        self._profile_token: str | None = None

    async def set_profile(self, profile_token: str) -> None:
        """设置要控制的媒体配置文件。"""
        self._profile_token = profile_token

    async def move(self, pan: float = 0.0, tilt: float = 0.0, zoom: float = 0.0, duration: float = 1.0) -> bool:
        """持续移动。

        Args:
            pan: 水平速度 -1.0 ~ 1.0（负=左，正=右）
            tilt: 垂直速度 -1.0 ~ 1.0（负=上，正=下）
            zoom: 缩放速度 -1.0 ~ 1.0（负=缩小，正=放大）
            duration: 持续时间（秒）
        """
        return await self.client.continuous_move(
            profile_token=self._profile_token,
            pan=pan,
            tilt=tilt,
            zoom=zoom,
            timeout=duration,
        )

    async def stop(self) -> bool:
        """停止移动。"""
        return await self.client.stop(self._profile_token)

    async def up(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """向上转动。"""
        return await self.move(tilt=-speed, duration=duration)

    async def down(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """向下转动。"""
        return await self.move(tilt=speed, duration=duration)

    async def left(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """向左转动。"""
        return await self.move(pan=-speed, duration=duration)

    async def right(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """向右转动。"""
        return await self.move(pan=speed, duration=duration)

    async def zoom_in(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """放大。"""
        return await self.move(zoom=speed, duration=duration)

    async def zoom_out(self, speed: float = 0.5, duration: float = 1.0) -> bool:
        """缩小。"""
        return await self.move(zoom=-speed, duration=duration)

    async def goto_preset(self, preset_token: str) -> bool:
        """跳转预置位。"""
        return await self.client.goto_preset(preset_token, self._profile_token)
