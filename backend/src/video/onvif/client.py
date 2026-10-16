"""ONVIF 客户端。

基于 httpx + SOAP over HTTP 的轻量 ONVIF 客户端。
不依赖 onvif-zeep 等重型库，减少镜像体积。

支持的核心服务：
- Device Management (tds)
- Media (trt: med)
- PTZ (tptz)
- Events (tev)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger


class ONVIFError(Exception):
    """ONVIF 协议错误。"""


class ONVIFClient:
    """ONVIF 设备客户端。"""

    def __init__(
        self,
        host: str,
        port: int = 80,
        username: str = "admin",
        password: str = "admin",
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout

        self._client: httpx.AsyncClient | None = None
        self._device_service_url: str | None = None
        self._media_service_url: str | None = None
        self._ptz_service_url: str | None = None
        self._profiles: list[dict[str, Any]] = []
        self._connected = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def connect(self) -> bool:
        """连接设备并发现服务地址。"""
        if self._connected:
            return True

        try:
            self._client = httpx.AsyncClient(
                auth=(self.username, self.password),
                timeout=self.timeout,
            )

            # 获取设备服务地址（大多数设备在根路径提供）
            self._device_service_url = f"{self.base_url}/onvif/device_service"

            # 查询设备能力，发现各服务地址
            capabilities = await self.get_capabilities()
            if capabilities:
                media = capabilities.get("Media", {})
                ptz = capabilities.get("PTZ", {})
                self._media_service_url = media.get("XAddr", f"{self.base_url}/onvif/media_service")
                self._ptz_service_url = ptz.get("XAddr", f"{self.base_url}/onvif/ptz_service")

            # 获取媒体配置文件
            self._profiles = await self.get_profiles()

            self._connected = True
            logger.info(f"ONVIF 连接成功: {self.host}:{self.port}")
            return True

        except Exception as e:
            logger.error(f"ONVIF 连接失败: {e}")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """断开连接。"""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    # ------------------------------------------------------------------
    # SOAP 请求
    # ------------------------------------------------------------------

    async def _soap_request(self, url: str, body: str) -> str:
        """发送 SOAP 请求。"""
        if not self._client:
            raise ONVIFError("客户端未连接")

        soap_envelope = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/03/soap-envelope"'
            ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
            ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            "<s:Header>"
            "<wsse:Security>"
            '<wsse:UsernameToken>'
            f"<wsse:Username>{self.username}</wsse:Username>"
            f"<wsse:Password>{self.password}</wsse:Password>"
            "</wsse:UsernameToken>"
            "</wsse:Security>"
            "</s:Header>"
            f"<s:Body>{body}</s:Body>"
            "</s:Envelope>"
        )

        headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
        resp = await self._client.post(url, content=soap_envelope, headers=headers)

        if resp.status_code != 200:
            raise ONVIFError(f"SOAP 请求失败: HTTP {resp.status_code}")

        return resp.text

    # ------------------------------------------------------------------
    # Device Management (tds)
    # ------------------------------------------------------------------

    async def get_capabilities(self) -> dict[str, Any]:
        """获取设备能力。"""
        body = "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>"
        resp = await self._soap_request(self._device_service_url, body)
        return self._parse_capabilities(resp)

    def _parse_capabilities(self, xml: str) -> dict[str, Any]:
        """解析设备能力 XML。"""
        import re

        caps: dict[str, Any] = {}
        for service in ("Media", "PTZ", "Events", "Device"):
            m = re.search(
                rf"<tds:Capabilities.*?>.*?<tds:{service}.*?XAddr>(.*?)</tds:XAddr>",
                xml,
                re.DOTALL,
            )
            if m:
                caps[service] = {"XAddr": m.group(1)}
        return caps

    async def get_device_information(self) -> dict[str, str]:
        """获取设备信息。"""
        body = "<tds:GetDeviceInformation/>"
        resp = await self._soap_request(self._device_service_url, body)

        import re

        info = {}
        for field in ("Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"):
            m = re.search(rf"<tds:{field}>(.*?)</tds:{field}>", resp)
            if m:
                info[field] = m.group(1)
        return info

    async def get_system_date(self) -> datetime:
        """获取设备系统时间。"""
        body = "<tds:GetSystemDateAndTime/>"
        resp = await self._soap_request(self._device_service_url, body)
        return datetime.now(timezone.utc)  # 简化：实际应解析 XML

    # ------------------------------------------------------------------
    # Media (trt)
    # ------------------------------------------------------------------

    async def get_profiles(self) -> list[dict[str, Any]]:
        """获取媒体配置文件列表。"""
        if not self._media_service_url:
            return []

        body = "<trt:GetProfiles/>"
        resp = await self._soap_request(self._media_service_url, body)

        import re

        profiles = []
        for m in re.finditer(r"<trt:Profiles[^>]*token=\"([^\"]+)\"[^>]*>", resp):
            profiles.append({"token": m.group(1)})

        # 提取名称
        for p in profiles:
            name_m = re.search(rf"token=\"{p['token']}\"[^>]*>.*?<tt:Name>(.*?)</tt:Name>", resp, re.DOTALL)
            if name_m:
                p["name"] = name_m.group(1)

        return profiles

    async def get_stream_uri(self, profile_token: str | None = None) -> str | None:
        """获取实时流 RTSP 地址。"""
        if not self._media_service_url:
            return None

        if not profile_token and self._profiles:
            profile_token = self._profiles[0]["token"]

        body = (
            "<trt:GetStreamUri>"
            "<trt:StreamSetup>"
            '<tt:Stream xmlns:tt="http://www.onvif.org/ver10/schema">RTP-Unicast</tt:Stream>'
            '<tt:Transport xmlns:tt="http://www.onvif.org/ver10/schema">'
            "<tt:Protocol>RTSP</tt:Protocol>"
            "</tt:Transport>"
            "</trt:StreamSetup>"
            f"<trt:ProfileToken>{profile_token}</trt:ProfileToken>"
            "</trt:GetStreamUri>"
        )
        resp = await self._soap_request(self._media_service_url, body)

        import re

        m = re.search(r"<tt:Uri>(.*?)</tt:Uri>", resp)
        if m:
            return m.group(1)
        return None

    async def get_snapshot_uri(self, profile_token: str | None = None) -> str | None:
        """获取快照 URI。"""
        if not self._media_service_url:
            return None

        if not profile_token and self._profiles:
            profile_token = self._profiles[0]["token"]

        body = f"<trt:GetSnapshotUri><trt:ProfileToken>{profile_token}</trt:ProfileToken></trt:GetSnapshotUri>"
        resp = await self._soap_request(self._media_service_url, body)

        import re

        m = re.search(r"<tt:Uri>(.*?)</tt:Uri>", resp)
        if m:
            return m.group(1)
        return None

    # ------------------------------------------------------------------
    # PTZ (tptz)
    # ------------------------------------------------------------------

    async def continuous_move(
        self,
        profile_token: str | None = None,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        timeout: float = 1.0,
    ) -> bool:
        """连续移动（PTZ）。

        Args:
            pan: 水平速度 (-1.0 ~ 1.0，负左正右)
            tilt: 垂直速度 (-1.0 ~ 1.0，负上正下)
            zoom: 缩放速度 (-1.0 ~ 1.0)
            timeout: 超时时间（秒），超时后自动停止
        """
        if not self._ptz_service_url:
            return False

        if not profile_token and self._profiles:
            profile_token = self._profiles[0]["token"]

        body = (
            "<tptz:ContinuousMove>"
            f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
            "<tptz:Velocity>"
            '<tt:PanTilt xmlns:tt="http://www.onvif.org/ver10/schema" '
            f'x="{pan}" y="{tilt}"/>'
            f'<tt:Zoom xmlns:tt="http://www.onvif.org/ver10/schema" x="{zoom}"/>'
            "</tptz:Velocity>"
            "</tptz:ContinuousMove>"
        )

        try:
            await self._soap_request(self._ptz_service_url, body)
            if timeout > 0:
                await self._stop_after_delay(profile_token, timeout)
            return True
        except ONVIFError as e:
            logger.error(f"PTZ 移动失败: {e}")
            return False

    async def stop(self, profile_token: str | None = None) -> bool:
        """停止 PTZ 移动。"""
        if not self._ptz_service_url:
            return False

        if not profile_token and self._profiles:
            profile_token = self._profiles[0]["token"]

        body = (
            "<tptz:Stop>"
            f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
            "<tptz:PanTilt>true</tptz:PanTilt>"
            "<tptz:Zoom>true</tptz:Zoom>"
            "</tptz:Stop>"
        )

        try:
            await self._soap_request(self._ptz_service_url, body)
            return True
        except ONVIFError as e:
            logger.error(f"PTZ 停止失败: {e}")
            return False

    async def goto_preset(
        self,
        preset_token: str,
        profile_token: str | None = None,
    ) -> bool:
        """转到预置位。"""
        if not self._ptz_service_url:
            return False

        if not profile_token and self._profiles:
            profile_token = self._profiles[0]["token"]

        body = (
            "<tptz:GotoPreset>"
            f"<tptz:ProfileToken>{profile_token}</tptz:ProfileToken>"
            f"<tptz:PresetToken>{preset_token}</tptz:PresetToken>"
            "</tptz:GotoPreset>"
        )

        try:
            await self._soap_request(self._ptz_service_url, body)
            return True
        except ONVIFError as e:
            logger.error(f"预置位跳转失败: {e}")
            return False

    async def _stop_after_delay(self, profile_token: str | None, delay: float) -> None:
        """延迟后停止。"""
        import asyncio

        await asyncio.sleep(delay)
        await self.stop(profile_token)

    # ------------------------------------------------------------------
    # 设备发现
    # ------------------------------------------------------------------

    @staticmethod
    async def discover(timeout: float = 3.0) -> list[dict[str, str]]:
        """WS-Discovery 设备发现。"""
        import asyncio

        probe = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<e:Envelope xmlns:e="http://www.w3.org/2003/03/soap-envelope"'
            ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/09/discovery"'
            ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            "<e:Header><w:MessageID>uuid:discovery-probe</w:MessageID>"
            "<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>"
            "<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
            "</e:Header>"
            "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
            "</e:Envelope>"
        )

        found: list[dict[str, str]] = []
        done = asyncio.Event()

        class DiscoveryProtocol(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                try:
                    text = data.decode("utf-8", errors="ignore")
                    import re

                    xaddrs = re.findall(r"<d:XAddrs>(.*?)</d:XAddrs>", text)
                    for xaddr in xaddrs:
                        found.append({"xaddr": xaddr, "ip": addr[0]})
                except Exception:
                    pass

        try:
            loop = asyncio.get_event_loop()
            transport, _ = await loop.create_datagram_endpoint(
                DiscoveryProtocol,
                local_addr=("0.0.0.0", 0),
            )
            transport.sendto(
                probe.encode(),
                ("239.255.255.250", 3702),
            )
            await asyncio.wait_for(done.wait(), timeout=timeout)
            transport.close()
        except Exception as e:
            logger.warning(f"ONVIF 设备发现失败: {e}")

        return found
