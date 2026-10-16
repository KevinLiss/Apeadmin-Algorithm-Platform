"""通知系统单元测试：Webhook / MQTT 渠道 + NotificationManager 调度。

Webhook 通过本地 threading HTTP server 验证真实的 POST 与 HMAC 签名。
MQTT 验证未配置 broker / 未安装 paho 时的优雅降级（绝不抛异常）。
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.notification.base import NotificationChannel
from src.notification.manager import NotificationManager, notification_manager
from src.notification.mqtt import MqttChannel
from src.notification.webhook import WebhookChannel, _sign


def _start_server(capture: dict) -> HTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            capture["headers"] = dict(self.headers)
            capture["body"] = body
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # 安静
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server._port = port  # type: ignore[attr-defined]
    return server


@pytest.fixture
def local_server():
    capture: dict = {}
    server = _start_server(capture)
    try:
        yield server, capture
    finally:
        server.shutdown()


@pytest.fixture(autouse=True)
def _reset_manager():
    # 重置全局单例的队列与渠道配置，隔离测试
    for ch in notification_manager._channels.values():
        ch.reload({})
    notification_manager._queue = queue.Queue(maxsize=500)
    yield


# ---------------- Webhook ----------------
def test_webhook_disabled_without_config():
    ch = WebhookChannel({})
    assert ch.is_enabled() is False
    assert ch.send({"x": 1}) is False


def test_webhook_enabled_flag():
    ch = WebhookChannel({"enabled": "true", "url": "http://example.com/hook"})
    assert ch.is_enabled() is True


def test_webhook_send_and_signature(local_server):
    server, capture = local_server
    secret = "s3cr3t"
    ch = WebhookChannel(
        {"enabled": "true", "url": f"http://127.0.0.1:{server._port}/hook", "secret": secret}
    )
    ok = ch.send({"event": "alarm", "id": 7})
    assert ok is True
    assert capture["body"] is not None
    payload = json.loads(capture["body"])
    assert payload["id"] == 7
    sig = capture["headers"].get("X-ApeAdmin-Signature")
    assert sig == _sign(secret, capture["body"])


def test_webhook_test_connection_ok(local_server):
    server, _ = local_server
    ch = WebhookChannel({"enabled": "true", "url": f"http://127.0.0.1:{server._port}/hook"})
    ok, msg = ch.test_connection()
    assert ok is True
    assert isinstance(msg, str)


def test_webhook_test_connection_bad_url():
    ch = WebhookChannel({"enabled": "true", "url": "not-a-url"})
    ok, msg = ch.test_connection()
    assert ok is False
    assert isinstance(msg, str)


# ---------------- MQTT ----------------
def test_mqtt_disabled_no_config():
    ch = MqttChannel({})
    assert ch.is_enabled() is False


def test_mqtt_send_disabled_returns_false():
    ch = MqttChannel({})
    assert ch.send({"x": 1}) is False


def test_mqtt_test_connection_no_broker():
    # 无论 paho 是否安装，无 broker 都必须返回 False 且不抛异常
    ch = MqttChannel({"enabled": "true", "broker": "", "port": "1883"})
    ok, msg = ch.test_connection()
    assert ok is False
    assert isinstance(msg, str)


# ---------------- Manager ----------------
def test_manager_status_channels():
    statuses = notification_manager.status()
    names = {s["name"] for s in statuses}
    assert names == {"mqtt", "webhook"}
    for s in statuses:
        assert s["enabled"] is False  # 无配置时禁用


def test_manager_submit_nonblocking():
    payload = {"id": 1}
    notification_manager.submit(payload)
    assert notification_manager._queue.qsize() == 1


def test_manager_test_channel_unknown():
    ok, msg = notification_manager.test_channel("nope")
    assert ok is False
    assert "未知渠道" in msg


def test_manager_start_idempotent():
    # 不应抛异常，且幂等
    notification_manager.start()
    notification_manager.start()
    notification_manager.stop()
