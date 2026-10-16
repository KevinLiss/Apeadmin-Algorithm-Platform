"""ONVIF 媒体服务封装。

对 ONVIFClient 的媒体能力进行面向对象封装，
便于与现有 ai_vision 的 grabber（RTSP 拉流）对接。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from .client import ONVIFClient


class MediaService:
    """ONVIF 媒体服务。"""

    def __init__(self, client: ONVIFClient):
        self.client = client

    async def get_stream_uri(self, profile_token: str | None = None) -> str | None:
        """获取 RTSP 流地址。

        返回的地址可直接喂给现有 ai_vision grabber（OpenCV RTSP 拉流）。
        """
        uri = await self.client.get_stream_uri(profile_token)
        if uri:
            logger.info(f"ONVIF 流地址: {uri}")
        return uri

    async def get_snapshot_uri(self, profile_token: str | None = None) -> str | None:
        """获取快照地址。"""
        return await self.client.get_snapshot_uri(profile_token)

    async def list_profiles(self) -> list[dict[str, Any]]:
        """列出媒体配置文件。"""
        return await self.client.get_profiles()

    async def get_video_source_info(self) -> dict[str, Any]:
        """获取视频源信息（分辨率等）。"""
        # 简化实现：从配置文件提取
        profiles = await self.client.get_profiles()
        return {
            "profiles": profiles,
            "default_profile": profiles[0] if profiles else None,
        }
