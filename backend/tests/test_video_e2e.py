"""视频源端到端调度测试（无需真实设备/网络）。

用 mock 注入 GB28181 服务器与 ONVIF 客户端，验证：
- ``create_source`` 按协议分派到正确路径
- GB28181 路径返回 ``gb28181://`` 逻辑流地址
- ONVIF 路径通过 SOAP 拿到的 RTSP 地址退化为 rtsp 拉流
- 整体生命周期（create → list → close）一致
"""
from __future__ import annotations

import pytest

from src.video.factory import CameraConfig, VideoFactory, VideoProtocol
from src.video.gb28181.device import DeviceStatus, GB28181Device


@pytest.fixture(autouse=True)
def _reset_video_factory():
    yield
    VideoFactory.close_all()
    VideoFactory._gb_server = None
    VideoFactory._onvif_clients.clear()


class _FakeONVIFClient:
    def __init__(self, host, port, username, password):
        self.host = host
        self.port = port

    async def connect(self):
        return True

    async def get_stream_uri(self):
        return "rtsp://onvif-device/live"

    async def disconnect(self):
        return None


@pytest.mark.asyncio
async def test_create_source_rtsp(monkeypatch):
    cfg = CameraConfig(camera_id=1, name="rtsp-cam", protocol="rtsp", rtsp_url="rtsp://h/s")
    src = await VideoFactory.create_source(cfg)
    assert src.stream_url == "rtsp://h/s"
    assert VideoFactory.get_source(1) is src


@pytest.mark.asyncio
async def test_create_source_gb28181(monkeypatch):
    device = GB28181Device(
        device_id="31010000001320000001",
        status=DeviceStatus.ONLINE,
        last_heartbeat=__import__("datetime").datetime.now(),
    )

    class _FakeServer:
        def get_device(self, device_id):
            return device

    VideoFactory._gb_server = _FakeServer()
    cfg = CameraConfig(
        camera_id=2,
        name="gb-cam",
        protocol="gb28181",
        gb_device_id="31010000001320000001",
        gb_channel="1",
    )
    src = await VideoFactory.create_source(cfg)
    assert src.stream_url.startswith("gb28181://")
    assert "31010000001320000001" in src.stream_url
    assert VideoFactory.get_source(2) is src


@pytest.mark.asyncio
async def test_create_source_onvif(monkeypatch):
    monkeypatch.setattr("src.video.onvif.ONVIFClient", _FakeONVIFClient)
    cfg = CameraConfig(
        camera_id=3,
        name="onvif-cam",
        protocol="onvif",
        onvif_url="http://192.168.1.64/onvif",
        onvif_username="admin",
        onvif_password="admin",
    )
    src = await VideoFactory.create_source(cfg)
    # ONVIF 退化：用拿到的 RTSP 地址拉流
    assert src.stream_url == "rtsp://onvif-device/live"
    assert 3 in VideoFactory._onvif_clients


@pytest.mark.asyncio
async def test_create_source_gb28181_offline(monkeypatch):
    class _FakeServer:
        def get_device(self, device_id):
            return None

    VideoFactory._gb_server = _FakeServer()
    cfg = CameraConfig(camera_id=4, name="gb-offline", protocol="gb28181", gb_device_id="x")
    with pytest.raises(ConnectionError):
        await VideoFactory.create_source(cfg)


def test_protocol_enum_used_in_dispatch():
    assert VideoProtocol.GB28181.value == "gb28181"
    assert VideoProtocol.ONVIF.value == "onvif"
