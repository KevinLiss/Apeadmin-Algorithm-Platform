"""MQTT 报警外发渠道（paho-mqtt）。

通过 MQTT broker 把告警快照发布到指定 topic，便于与物联网平台 /
边缘网关 / 第三方告警系统对接（对接 cosmo-edge 等生态时常见需求）。

依赖：``paho-mqtt``（轻量纯 Python 库）。若运行环境未安装，则本渠道
``is_enabled`` 始终返回 False，不影响其他渠道与系统启动（优雅降级）。

线程模型：paho 默认 ``CallbackAPIVersion.VERSION2`` 在独立网络线程回调，
``client.publish`` 同步投递到本地队列后由网络线程发送，调用方线程安全。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from src.notification.base import NotificationChannel

logger = logging.getLogger("notification.mqtt")

try:
    from paho.mqtt import client as mqtt_client
    from paho.mqtt.enums import CallbackAPIVersion

    _PAHO_AVAILABLE = True
except Exception:  # noqa: BLE001
    _PAHO_AVAILABLE = False

# module-level 单例与锁（由 manager 持有并复用）
_client: Any | None = None
_client_lock = threading.Lock()


def _build_client(config: dict[str, Any]) -> Any | None:
    """根据配置构建一个已连接的 MQTT client（失败返回 None）。"""
    if not _PAHO_AVAILABLE:
        return None
    broker = (config.get("broker") or "").strip()
    port = int(config.get("port") or 1883)
    if not broker:
        return None
    try:
        client_id = f"apeadmin-notify-{id(threading.current_thread())}"
        c = mqtt_client.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        username = config.get("username") or ""
        password = config.get("password") or ""
        if username:
            c.username_pw_set(username, password)
        c.connect(broker, port, keepalive=30)
        c.loop_start()
        return c
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[MQTT] 连接 broker 失败 {broker}:{port} - {exc}")
        return None


class MqttChannel(NotificationChannel):
    name = "mqtt"

    def is_enabled(self) -> bool:
        if not _PAHO_AVAILABLE:
            return False
        if str(self.config.get("enabled", "false")).lower() != "true":
            return False
        broker = (self.config.get("broker") or "").strip()
        return bool(broker)

    def _get_client(self) -> Any | None:
        global _client
        with _client_lock:
            if _client is not None:
                return _client
            _client = _build_client(self.config)
            return _client

    def send(self, payload: dict[str, Any]) -> bool:
        if not self.is_enabled():
            return False
        client = self._get_client()
        if client is None:
            return False
        topic = (self.config.get("topic") or "apeadmin/alarm").strip()
        try:
            msg = json.dumps(payload, ensure_ascii=False, default=str)
            info = client.publish(topic, msg, qos=1)
            return info.rc == 0 or info.is_published()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[MQTT] 发布告警失败: {exc}")
            return False

    def test_connection(self) -> tuple[bool, str]:
        if not _PAHO_AVAILABLE:
            return False, "未安装 paho-mqtt，无法使用 MQTT 渠道"
        broker = (self.config.get("broker") or "").strip()
        port = int(self.config.get("port") or 1883)
        if not broker:
            return False, "未配置 broker 地址"
        try:
            c = _build_client(self.config)
            if c is None:
                return False, f"无法连接 {broker}:{port}"
            c.loop_stop()
            c.disconnect()
            return True, f"已成功连接 {broker}:{port}"
        except Exception as exc:  # noqa: BLE001
            return False, f"连接失败: {exc}"
