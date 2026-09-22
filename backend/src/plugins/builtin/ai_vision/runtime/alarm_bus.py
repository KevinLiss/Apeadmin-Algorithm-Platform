"""进程内告警总线：worker 线程 → API 层（实时监控台）的告警分发。

背景：``StreamWorker`` 在独立线程中产生告警（``ALARM_RAISED`` 事件），
API 层（async）需要实时感知以推送给前端。本模块提供一个极简的
进程内发布/订阅总线：

- ``publish(payload)``：worker 线程调用，非阻塞，把告警快照放入
  每个订阅者的有界队列（满则丢弃最旧，保证不阻塞推理线程）；
- ``subscribe() / unsubscribe(q)``：SSE/轮询端点订阅；
- 订阅者拿到的是 dict 快照（含 alarm_id / camera_id / event_id /
  category_code / confidence / snapshot_path / video_ts / ts）。

线程安全：全部操作在锁内完成；队列本身线程安全。
"""
from __future__ import annotations

import queue
import threading
from typing import Any

# 每个订阅者的缓冲上限（超出丢弃最旧，防止慢消费者拖垮总线）
_MAX_QUEUE = 200

_lock = threading.Lock()
_subscribers: set["queue.Queue[dict[str, Any]]"] = set()


def subscribe() -> "queue.Queue[dict[str, Any]]":
    """注册一个订阅者队列。"""
    q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=_MAX_QUEUE)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: "queue.Queue[dict[str, Any]]") -> None:
    """注销订阅者队列。"""
    with _lock:
        _subscribers.discard(q)


def publish(payload: dict[str, Any]) -> None:
    """发布一条告警快照到所有订阅者（worker 线程调用，绝不阻塞）。"""
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()  # 丢弃最旧一条
                q.put_nowait(payload)
            except Exception:  # noqa: BLE001
                pass


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)
