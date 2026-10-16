"""VideoFactory / VideoProtocol / CameraConfig / VideoSource 单元测试。

覆盖纯逻辑部分（RTSP 创建、协议探测、生命周期），GB28181/ONVIF
需要真实设备或网络，单独放在 test_video_e2e.py 中以 mock 驱动。
"""
from __future__ import annotations

import pytest

from src.video.factory import CameraConfig, VideoFactory, VideoProtocol, VideoSource


@pytest.fixture(autouse=True)
def _reset_video_factory():
    """每个测试后清空工厂的共享类状态，避免互相污染。"""
    yield
    VideoFactory.close_all()
    VideoFactory._gb_server = None
    VideoFactory._onvif_clients.clear()


def test_protocol_enum_values():
    assert VideoProtocol.RTSP.value == "rtsp"
    assert VideoProtocol.GB28181.value == "gb28181"
    assert VideoProtocol.ONVIF.value == "onvif"


def test_camera_config_defaults():
    cfg = CameraConfig(camera_id=1, name="cam")
    assert cfg.protocol == "rtsp"
    assert cfg.source_type == "camera"
    assert cfg.rtsp_url is None


def test_video_source_close():
    cfg = CameraConfig(camera_id=1, name="cam", rtsp_url="rtsp://x")
    src = VideoSource(cfg, stream_url="rtsp://x")
    assert src.is_active is True
    src.close()
    assert src.is_active is False


def test_create_rtsp_source_ok():
    cfg = CameraConfig(camera_id=10, name="cam", protocol="rtsp", rtsp_url="rtsp://host/stream")
    src = VideoFactory._create_rtsp_source(cfg)
    assert src.stream_url == "rtsp://host/stream"
    assert VideoFactory.get_source(10) is src


def test_create_rtsp_source_missing_url():
    cfg = CameraConfig(camera_id=11, name="cam", protocol="rtsp")
    with pytest.raises(ValueError):
        VideoFactory._create_rtsp_source(cfg)


@pytest.mark.asyncio
async def test_create_source_unknown_protocol():
    cfg = CameraConfig(camera_id=12, name="cam", protocol="foo")
    with pytest.raises(ValueError):
        await VideoFactory.create_source(cfg)


def test_detect_protocol():
    assert VideoFactory.detect_protocol("rtsp://host/x") == "rtsp"
    assert VideoFactory.detect_protocol("http://host/onvif") == "onvif"
    assert VideoFactory.detect_protocol("rtmp://host/x") == "rtsp"


def test_close_source_and_list():
    cfg = CameraConfig(camera_id=20, name="cam", protocol="rtsp", rtsp_url="rtsp://h/s")
    VideoFactory._create_rtsp_source(cfg)
    assert len(VideoFactory.list_sources()) == 1
    assert VideoFactory.close_source(20) is True
    assert VideoFactory.get_source(20) is None
    # 关闭不存在的源返回 False
    assert VideoFactory.close_source(999) is False
