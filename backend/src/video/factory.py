"""视频源统一调度器。

根据摄像头配置的协议类型，创建对应的视频源：
- rtsp → 直接使用 RTSP URL（现有 grabber）
- gb28181 → GB28181Server 注册设备 + RTP 接收
- onvif → ONVIFClient 获取 RTSP 流地址 → 退化为 rtsp 拉流

对外提供统一接口：
- ``get_stream_url(camera_config)`` → 返回可用于 OpenCV 拉流的地址
- ``open_camera(camera_config)`` → 打开视频源
- ``close_camera(camera_id)`` → 关闭视频源
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from loguru import logger


class VideoProtocol(str, Enum):
    """视频协议类型。"""

    RTSP = "rtsp"
    GB28181 = "gb28181"
    ONVIF = "onvif"


@dataclass
class CameraConfig:
    """摄像头配置（从 ai_vision_cameras 表映射）。"""

    camera_id: int
    name: str
    protocol: str = "rtsp"
    source_type: str = "camera"
    rtsp_url: str | None = None

    # GB28181
    gb_device_id: str | None = None
    gb_ip: str | None = None
    gb_port: int | None = None
    gb_channel: str | None = None

    # ONVIF
    onvif_url: str | None = None
    onvif_username: str | None = None
    onvif_password: str | None = None

    # 通用
    location: str = ""
    vendor: str = ""


class VideoSource:
    """视频源抽象。

    统一封装三种协议的视频接入，对外暴露：
    - ``stream_url``：可直接用 OpenCV/FFmpeg 拉流的地址
    - ``is_active``：源是否活跃
    - ``open()`` / ``close()``：生命周期管理
    """

    def __init__(self, config: CameraConfig, stream_url: str):
        self.config = config
        self.stream_url = stream_url
        self.is_active = True

    def close(self) -> None:
        self.is_active = False


class VideoFactory:
    """视频源工厂（统一调度入口）。"""

    # GB28181 服务器单例
    _gb_server: Any = None
    # 活跃的 ONVIF 客户端（按 camera_id）
    _onvif_clients: dict[int, Any] = {}
    # 已创建的视频源（按 camera_id）
    _sources: dict[int, VideoSource] = {}
    _lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # GB28181 服务器管理
    # ------------------------------------------------------------------

    @classmethod
    async def start_gb28181_server(
        cls,
        sip_id: str = "31010000002000000001",
        sip_domain: str = "3101000000",
        sip_host: str = "0.0.0.0",
        sip_port: int = 5060,
    ) -> Any:
        """启动 GB28181 服务器（进程内单例）。"""
        async with cls._lock:
            if cls._gb_server is not None:
                return cls._gb_server

            from .gb28181 import GB28181Server, GB28181ServerConfig

            config = GB28181ServerConfig(
                sip_id=sip_id,
                sip_domain=sip_domain,
                sip_host=sip_host,
                sip_port=sip_port,
            )
            server = GB28181Server(config)
            await server.start()
            cls._gb_server = server
            return server

    @classmethod
    def get_gb28181_server(cls) -> Any:
        """获取 GB28181 服务器实例（未启动返回 None）。"""
        return cls._gb_server

    # ------------------------------------------------------------------
    # 核心：创建视频源
    # ------------------------------------------------------------------

    @classmethod
    async def create_source(cls, config: CameraConfig) -> VideoSource:
        """根据协议创建视频源。

        Returns:
            VideoSource（含可直接拉流的 stream_url）

        Raises:
            ValueError: 协议不支持或参数缺失
            ConnectionError: 设备连接失败
        """
        protocol = config.protocol.lower()

        if protocol == VideoProtocol.RTSP.value:
            return cls._create_rtsp_source(config)
        elif protocol == VideoProtocol.ONVIF.value:
            return await cls._create_onvif_source(config)
        elif protocol == VideoProtocol.GB28181.value:
            return await cls._create_gb28181_source(config)
        else:
            raise ValueError(f"不支持的视频协议: {protocol}")

    # ------------------------------------------------------------------
    # RTSP
    # ------------------------------------------------------------------

    @classmethod
    def _create_rtsp_source(cls, config: CameraConfig) -> VideoSource:
        """RTSP 源：直接使用配置的 URL。"""
        if not config.rtsp_url:
            raise ValueError("RTSP 源缺少 rtsp_url")

        source = VideoSource(config, stream_url=config.rtsp_url)
        cls._sources[config.camera_id] = source
        logger.info(f"RTSP 视频源创建: camera#{config.camera_id} -> {config.rtsp_url}")
        return source

    # ------------------------------------------------------------------
    # ONVIF
    # ------------------------------------------------------------------

    @classmethod
    async def _create_onvif_source(cls, config: CameraConfig) -> VideoSource:
        """ONVIF 源：通过 ONVIF 协议获取 RTSP 流地址。"""
        if not config.onvif_url:
            raise ValueError("ONVIF 源缺少 onvif_url")

        from urllib.parse import urlparse

        parsed = urlparse(
            config.onvif_url if "://" in config.onvif_url else f"http://{config.onvif_url}"
        )

        from .onvif import ONVIFClient

        client = ONVIFClient(
            host=parsed.hostname or config.onvif_url,
            port=parsed.port or 80,
            username=config.onvif_username or "admin",
            password=config.onvif_password or "admin",
        )

        connected = await client.connect()
        if not connected:
            raise ConnectionError(f"ONVIF 设备连接失败: {config.onvif_url}")

        stream_url = await client.get_stream_uri()
        if not stream_url:
            await client.disconnect()
            raise ConnectionError(f"ONVIF 设备未返回流地址: {config.onvif_url}")

        # 缓存客户端（PTZ 控制需要）
        cls._onvif_clients[config.camera_id] = client

        source = VideoSource(config, stream_url=stream_url)
        cls._sources[config.camera_id] = source
        logger.info(f"ONVIF 视频源创建: camera#{config.camera_id} -> {stream_url}")
        return source

    # ------------------------------------------------------------------
    # GB28181
    # ------------------------------------------------------------------

    @classmethod
    async def _create_gb28181_source(cls, config: CameraConfig) -> VideoSource:
        """GB28181 源：确保服务器运行，等待设备推流。

        注意：GB28181 是设备主动向平台推流（RTP），平台只需要：
        1. 确保 GB28181 SIP 服务器在运行
        2. 向设备发送 INVITE 邀请（发送 invite）
        3. 在分配的 RTP 端口上接收媒体流

        这里返回的 stream_url 是平台侧的 RTP 接收地址描述，
        实际拉流由 GB28181Server 的 RTP 处理器完成。
        """
        if not config.gb_device_id:
            raise ValueError("GB28181 源缺少 gb_device_id")

        # 确保服务器已启动
        server = cls._gb_server
        if server is None:
            server = await cls.start_gb28181_server()

        # 检查设备是否已注册
        device = server.get_device(config.gb_device_id)
        if device is None or not device.is_online():
            raise ConnectionError(
                f"GB28181 设备未注册或离线: {config.gb_device_id}"
            )

        # GB28181 的 stream_url 是逻辑地址，实际帧从 RTP 端口进来
        # 用特殊 scheme 标记，grabber 层识别后走 RTP 路径
        stream_url = f"gb28181://{config.gb_device_id}/{config.gb_channel or '1'}"

        source = VideoSource(config, stream_url=stream_url)
        cls._sources[config.camera_id] = source
        logger.info(
            f"GB28181 视频源创建: camera#{config.camera_id} -> device={config.gb_device_id}"
        )
        return source

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    @classmethod
    def get_source(cls, camera_id: int) -> VideoSource | None:
        """获取已创建的视频源。"""
        return cls._sources.get(camera_id)

    @classmethod
    def close_source(cls, camera_id: int) -> bool:
        """关闭视频源。"""
        source = cls._sources.pop(camera_id, None)

        # 清理 ONVIF 客户端
        client = cls._onvif_clients.pop(camera_id, None)
        if client:
            try:
                asyncio.get_event_loop().create_task(client.disconnect())
            except Exception:
                pass

        if source:
            source.close()
            logger.info(f"视频源关闭: camera#{camera_id}")
            return True
        return False

    @classmethod
    def close_all(cls) -> None:
        """关闭所有视频源。"""
        for camera_id in list(cls._sources.keys()):
            cls.close_source(camera_id)

    @classmethod
    def list_sources(cls) -> list[dict[str, Any]]:
        """列出所有活跃视频源。"""
        return [
            {
                "camera_id": s.config.camera_id,
                "name": s.config.name,
                "protocol": s.config.protocol,
                "stream_url": s.stream_url,
                "is_active": s.is_active,
            }
            for s in cls._sources.values()
            if s.is_active
        ]

    # ------------------------------------------------------------------
    # 辅助：协议探测
    # ------------------------------------------------------------------

    @staticmethod
    def detect_protocol(url: str) -> str:
        """根据 URL 探测协议类型。"""
        url_lower = url.lower()
        if url_lower.startswith(("rtsp://", "rtmp://", "http://")) and "onvif" not in url_lower:
            return VideoProtocol.RTSP.value
        if "onvif" in url_lower:
            return VideoProtocol.ONVIF.value
        return VideoProtocol.RTSP.value

    # ------------------------------------------------------------------
    # 辅助：GB28181 设备注册回调
    # ------------------------------------------------------------------

    @classmethod
    async def handle_gb_device_registered(cls, device: Any) -> None:
        """GB28181 设备注册回调（供外部挂到 GB28181Server.on_device_registered）。"""
        logger.info(f"GB28181 设备注册回调: {device.device_id}")
        # TODO: 自动在 ai_vision_cameras 表中创建/更新对应记录
