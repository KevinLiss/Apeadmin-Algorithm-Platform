"""GB28181Device / DeviceStatus 单元测试。

纯逻辑：在线判定（含心跳超时）、心跳更新、序列化。不需要真实 SIP 网络。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from src.video.gb28181.device import DeviceStatus, GB28181Device


def test_device_status_enum():
    assert DeviceStatus.OFFLINE.value == "offline"
    assert DeviceStatus.ONLINE.value == "online"
    assert DeviceStatus.REGISTERED.value == "registered"
    assert DeviceStatus.STREAMING.value == "streaming"


def test_is_online_offline():
    d = GB28181Device(device_id="1", status=DeviceStatus.OFFLINE)
    assert d.is_online() is False


def test_is_online_online_fresh():
    d = GB28181Device(
        device_id="1", status=DeviceStatus.ONLINE, last_heartbeat=datetime.now()
    )
    assert d.is_online() is True


def test_is_online_stale_heartbeat():
    d = GB28181Device(
        device_id="1",
        status=DeviceStatus.ONLINE,
        last_heartbeat=datetime.now() - timedelta(hours=10),
    )
    assert d.is_online() is False
    assert d.status == DeviceStatus.OFFLINE


def test_update_heartbeat():
    d = GB28181Device(device_id="1", status=DeviceStatus.OFFLINE)
    d.update_heartbeat()
    assert d.last_heartbeat is not None
    assert d.status == DeviceStatus.ONLINE


def test_to_dict():
    d = GB28181Device(
        device_id="100",
        name="cam",
        ip="1.2.3.4",
        port=5060,
        status=DeviceStatus.REGISTERED,
    )
    d2 = d.to_dict()
    assert d2["device_id"] == "100"
    assert d2["status"] == "registered"
    assert d2["ip"] == "1.2.3.4"
    assert d2["port"] == 5060
    assert d2["channels"] == []
