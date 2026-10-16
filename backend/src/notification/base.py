"""报警外发渠道抽象基类。

报警事件外发（MQTT / Webhook）的统一接口。所有渠道都实现
``NotificationChannel``：

- ``is_enabled()``：该渠道是否启用（配置开关 + 参数齐全）；
- ``send(payload)``：同步发送一条告警快照（由 manager 的后台线程调用，
  必须线程安全且**不抛异常**——异常由 manager 兜底记录）；
- ``test_connection()``：连通性自检（供前端「测试」按钮调用）。

设计约定：
- 渠道实例**持有长连接**（MQTT client / httpx client），由 manager 单例持有；
- 所有发送逻辑同步执行，避免与 FastAPI 事件循环耦合
  （告警产生在 worker 同步线程，推送也走同步后台线程最稳妥）；
- 配置来自 ``sys_setting`` 表（``notification.*`` 前缀），manager 在
  首次发送或收到「刷新配置」信号时重新装载。
"""
from __future__ import annotations

import abc
from typing import Any


class NotificationChannel(abc.ABC):
    """报警外发渠道统一抽象。"""

    #: 渠道唯一标识（mqtt / webhook），同时用作配置 key 前缀
    name: str = "base"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # config 来自 sys_setting 解析后的扁平字典（已展开前缀）
        self.config: dict[str, Any] = config or {}

    @abc.abstractmethod
    def is_enabled(self) -> bool:
        """该渠道是否应当参与发送（开关开启且必要参数齐全）。"""

    @abc.abstractmethod
    def send(self, payload: dict[str, Any]) -> bool:
        """同步发送一条告警快照，返回是否成功。禁止抛出异常。"""

    @abc.abstractmethod
    def test_connection(self) -> tuple[bool, str]:
        """连通性自检，返回 (成功?, 说明)。禁止抛出异常。"""

    def reload(self, config: dict[str, Any]) -> None:
        """manager 重载配置时调用，更新内部配置。"""
        self.config = config or {}
