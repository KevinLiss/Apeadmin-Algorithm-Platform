"""报警外发统一调度管理器（单例）。

职责：
1. 持有所有渠道实例（mqtt / webhook）；
2. 在事件循环中通过 ``refresh_config()``（async）从 ``sys_setting`` 表
   装载 ``notification.*`` 配置并刷新各渠道；
3. 提供 ``submit(payload)``（同步、非阻塞）把告警快照放入有界队列；
4. 后台 daemon 线程消费队列，逐个渠道调用 ``send()``——绝不阻塞
   告警落库（与 alarm_bus 同一设计哲学）。

典型接入点：``StreamWorker._raise_alarm`` 落库后调用
``notification_manager.submit(bus_payload)``。

生命周期：
- FastAPI 启动时调用 ``await notification_manager.refresh_config()`` 装载配置，
  并 ``notification_manager.start()`` 启动消费线程；
- 前端保存通知配置后，API 层再次调用 ``refresh_config()`` 热更新；
- 后台线程只读取配置缓存，不直接触碰数据库（避免 async/sync 线程混用）。
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from sqlalchemy import select

from src.models.setting import Setting
from src.notification.base import NotificationChannel
from src.notification.mqtt import MqttChannel
from src.notification.webhook import WebhookChannel

logger = logging.getLogger("notification.manager")

# 队列上限：超出丢弃最旧一条，保护慢消费（如 MQTT 断网重连）不拖垮推理
_MAX_QUEUE = 500
_CFG_PREFIX = "notification."


class NotificationManager:
    """报警外发管理器单例。"""

    def __init__(self) -> None:
        self._channels: dict[str, NotificationChannel] = {
            "mqtt": MqttChannel(),
            "webhook": WebhookChannel(),
        }
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(maxsize=_MAX_QUEUE)
        self._worker_thread: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()

    # ---- 配置装载（必须在事件循环中调用） ----
    async def refresh_config(self) -> None:
        """从 sys_setting 装载 notification.* 配置并刷新各渠道。"""
        from src.db import SessionLocal

        try:
            async with SessionLocal() as db:
                result = await db.execute(
                    select(Setting).where(Setting.key.like(f"{_CFG_PREFIX}%"))
                )
                rows = result.scalars().all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Notify] 读取通知配置失败: {exc}")
            return

        flat: dict[str, str] = {s.key: s.value for s in rows}
        grouped: dict[str, dict[str, str]] = {name: {} for name in self._channels}
        for key, value in flat.items():
            if not key.startswith(_CFG_PREFIX):
                continue
            rest = key[len(_CFG_PREFIX):]
            if "." not in rest:
                continue
            prefix, field = rest.split(".", 1)
            if prefix in grouped:
                grouped[prefix][field] = value

        for name, cfg in grouped.items():
            self._channels[name].reload(cfg)
        logger.info(
            f"[Notify] 配置已刷新: "
            + ", ".join(f"{n}={'on' if c.is_enabled() else 'off'}" for n, c in self._channels.items())
        )

    # ---- 运行控制 ----
    def start(self) -> None:
        """启动后台消费线程（幂等）。"""
        with self._lock:
            if self._started:
                return
            self._worker_thread = threading.Thread(
                target=self._consume_loop, name="notify-worker", daemon=True
            )
            self._worker_thread.start()
            self._started = True

    def stop(self) -> None:
        """优雅停止消费线程。"""
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    # ---- 对外接口 ----
    def submit(self, payload: dict[str, Any]) -> None:
        """提交一条告警快照（非阻塞，满则丢弃最旧）。"""
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(payload)
            except Exception:  # noqa: BLE001
                pass

    def status(self) -> list[dict[str, Any]]:
        """返回各渠道启用状态（供 API/前端展示）。"""
        return [
            {"name": n, "enabled": c.is_enabled()}
            for n, c in self._channels.items()
        ]

    def test_channel(self, name: str) -> tuple[bool, str]:
        """测试指定渠道连通性。"""
        ch = self._channels.get(name)
        if ch is None:
            return False, f"未知渠道: {name}"
        return ch.test_connection()

    # ---- 内部消费循环 ----
    def _consume_loop(self) -> None:
        while True:
            payload = self._queue.get()
            if payload is None:
                break
            for name, ch in self._channels.items():
                if not ch.is_enabled():
                    continue
                try:
                    ok = ch.send(payload)
                    if not ok:
                        logger.debug(f"[Notify] 渠道 {name} 发送失败（已忽略）")
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"[Notify] 渠道 {name} 异常: {exc}")


# 全局单例
notification_manager = NotificationManager()
