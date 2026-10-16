"""GB28181 SIP 服务器（平台侧）。

实现 GB/T 28181-2016 协议中的平台侧功能：
- 设备注册/注销处理
- 心跳响应
- 目录查询响应
- 实时流邀请响应
- RTP 媒体接收
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from .device import GB28181Device, DeviceStatus

# 日志配置
logging.basicConfig(level=logging.DEBUG)
sip_logger = logging.getLogger("sip")
sip_logger.setLevel(logging.DEBUG)


@dataclass
class GB28181ServerConfig:
    """GB28181 服务器配置。"""

    sip_id: str  # 平台ID
    sip_domain: str  # 平台域
    sip_host: str = "0.0.0.0"  # 监听地址
    sip_port: int = 5060  # 监听端口
    realm: str = "3101000000"  # 认证域
    auth_enabled: bool = False  # 是否启用认证


class GB28181Server:
    """GB28181 SIP 服务器（平台侧）。"""

    def __init__(self, config: GB28181ServerConfig):
        self.config = config
        self._devices: dict[str, GB28181Device] = {}
        self._running = False
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: "GB28181Protocol" | None = None

        # 回调函数
        self.on_device_registered: Callable[[GB28181Device], Any] | None = None
        self.on_device_unregistered: Callable[[GB28181Device], Any] | None = None
        self.on_device_heartbeat: Callable[[GB28181Device], Any] | None = None
        self.on_rtp_received: Callable[[str, bytes], Any] | None = None  # (channel_id, rtp_data)

        # RTP 接收配置
        self._rtp_port_start = 9000
        self._rtp_port_end = 9100
        self._rtp_port_current = self._rtp_port_start

    async def start(self) -> None:
        """启动 GB28181 服务器。"""
        if self._running:
            return

        loop = asyncio.get_event_loop()

        # 创建 UDP 传输
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: GB28181Protocol(self),
            local_addr=(self.config.sip_host, self.config.sip_port),
        )

        self._running = True
        logger.info(
            f"GB28181 服务器启动: {self.config.sip_id} @ "
            f"{self.config.sip_host}:{self.config.sip_port}"
        )

    async def stop(self) -> None:
        """停止 GB28181 服务器。"""
        if not self._running:
            return

        if self._transport:
            self._transport.close()

        self._running = False
        logger.info("GB28181 服务器已停止")

    def _get_free_rtp_port(self) -> int:
        """获取一个空闲的 RTP 端口。"""
        port = self._rtp_port_current
        self._rtp_port_current = (self._rtp_port_current - self._rtp_port_start + 1) % (
            self._rtp_port_end - self._rtp_port_start
        ) + self._rtp_port_start
        return port

    def register_device(
        self, device_id: str, ip: str, port: int, expires: int = 3600
    ) -> GB28181Device:
        """注册设备。"""
        if device_id in self._devices:
            device = self._devices[device_id]
            device.ip = ip
            device.port = port
            device._expires = expires
            device.status = DeviceStatus.REGISTERED
            device.registered_at = datetime.now()
        else:
            device = GB28181Device(
                device_id=device_id,
                ip=ip,
                port=port,
                _expires=expires,
                status=DeviceStatus.REGISTERED,
                registered_at=datetime.now(),
            )
            self._devices[device_id] = device

        logger.info(f"设备注册: {device_id} @ {ip}:{port}")
        return device

    def unregister_device(self, device_id: str) -> bool:
        """注销设备。"""
        if device_id in self._devices:
            device = self._devices[device_id]
            device.status = DeviceStatus.OFFLINE
            del self._devices[device_id]
            logger.info(f"设备注销: {device_id}")
            return True
        return False

    def get_device(self, device_id: str) -> GB28181Device | None:
        """获取设备。"""
        return self._devices.get(device_id)

    def list_devices(self) -> list[GB28181Device]:
        """列出所有设备。"""
        return list(self._devices.values())

    def handle_sip_message(self, data: bytes, addr: tuple[str, int]) -> bytes | None:
        """处理 SIP 消息。"""
        try:
            message = data.decode("utf-8", errors="ignore")
            sip_logger.debug(f"收到 SIP 消息:\n{message}")

            # 解析请求行
            lines = message.split("\r\n")
            if not lines:
                return None

            request_line = lines[0]
            parts = request_line.split(" ")
            if len(parts) < 2:
                return None

            method = parts[0].upper()
            uri = parts[1] if len(parts) > 1 else ""

            # 解析消息头
            headers = {}
            for line in lines[1:]:
                if ":" not in line:
                    break
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

            # 处理不同方法
            if method == "REGISTER":
                return self._handle_register(headers, addr)
            elif method == "OPTIONS":
                return self._handle_options(headers, addr)
            elif method == "MESSAGE":
                return self._handle_message(headers, message)
            else:
                logger.debug(f"未处理的 SIP 方法: {method}")

        except Exception as e:
            logger.error(f"处理 SIP 消息失败: {e}")

        return None

    def _handle_register(
        self, headers: dict[str, str], addr: tuple[str, int]
    ) -> bytes | None:
        """处理 REGISTER 请求。"""
        from_contact = headers.get("contact", "")
        expires = int(headers.get("expires", "3600"))

        # 解析设备ID（从 Contact 或 From 头）
        device_id = self._extract_device_id(from_contact or headers.get("from", ""))

        if expires == 0:
            # 注销
            self.unregister_device(device_id)
            response = self._create_response(200, "OK", headers)
        else:
            # 注册
            device = self.register_device(device_id, addr[0], addr[1], expires)
            response = self._create_response(200, "OK", headers)

            # 添加 Contact 头指定媒体接收地址
            rtp_port = self._get_free_rtp_port()
            device.stream_url = f"{self.config.sip_host}:{rtp_port}"
            response += f"Contact: <sip:{device_id}@{addr[0]}:{addr[1]};transport=UDP>;expires={expires}\r\n"

        return response.encode("utf-8")

    def _handle_options(self, headers: dict[str, str], addr: tuple[str, int]) -> bytes | None:
        """处理 OPTIONS 请求（心跳）。"""
        from_header = headers.get("from", "")
        device_id = self._extract_device_id(from_header)

        device = self.get_device(device_id)
        if device:
            device.update_heartbeat()
            if self.on_device_heartbeat:
                asyncio.create_task(self.on_device_heartbeat(device))

        return self._create_response(200, "OK", headers).encode("utf-8")

    def _handle_message(self, headers: dict[str, str], message: str) -> bytes | None:
        """处理 MESSAGE 请求（目录/告警等）。"""
        # TODO: 解析 XML 消息体
        return self._create_response(200, "OK", headers).encode("utf-8")

    def _extract_device_id(self, header: str) -> str:
        """从 SIP 头中提取设备ID。"""
        # 格式: <sip:34010000001310000001@3401000000>
        import re

        match = re.search(r"(\d{20})", header)
        if match:
            return match.group(1)
        # 尝试直接解析
        if "@" in header:
            return header.split("@")[0].replace("sip:", "").replace("<", "")
        return header

    def _create_response(self, status_code: int, reason: str, request_headers: dict[str, str]) -> str:
        """创建 SIP 响应。"""
        # 生成 Via 头
        via = request_headers.get("via", "")
        via = via.replace("Via:", "Via:")

        # 生成 From/To 头
        from_header = request_headers.get("from", "")
        to_header = request_headers.get("to", "")

        # 生成 Call-ID
        call_id = request_headers.get("call-id", f"{id(self)}-{datetime.now().timestamp()}")

        # 生成 CSeq
        cseq = request_headers.get("cseq", "1 REGISTER")

        response = (
            f"SIP/2.0 {status_code} {reason}\r\n"
            f"{via}\r\n"
            f"From: {from_header}\r\n"
            f"To: {to_header}\r\n"
            f"Call-ID: {call_id}\r\n"
            f"{cseq}\r\n"
            f"Content-Length: 0\r\n"
            f"\r\n"
        )
        return response


class GB28181Protocol(asyncio.DatagramProtocol):
    """GB28181 UDP 协议处理。"""

    def __init__(self, server: GB28181Server):
        self.server = server

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """收到数据报。"""
        response = self.server.handle_sip_message(data, addr)
        if response and self.transport:
            self.transport.sendto(response, addr)

    def error_received(self, exc: Exception) -> None:
        """错误发生。"""
        logger.error(f"GB28181 协议错误: {exc}")

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        """连接建立。"""
        logger.debug("GB28181 连接建立")
