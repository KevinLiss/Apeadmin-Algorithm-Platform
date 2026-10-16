"""报警外发模块：MQTT / Webhook 渠道 + 调度管理器。

对外的入口是 ``notification_manager`` 单例：告警产生点调用
``notification_manager.submit(payload)`` 即可（非阻塞）。
"""

from src.notification.manager import notification_manager

__all__ = ["notification_manager"]
