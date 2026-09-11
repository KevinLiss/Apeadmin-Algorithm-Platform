"""StreamWorker 内部运行时事件（任务 2.3）。

用途：worker 线程与主进程/API 层解耦——worker 通过回调向外发事件，
不直接依赖 FastAPI 或 DB session（DB 写入由 worker 内部独立 SessionLocal
完成，事件仅用于日志/看板/状态同步）。

事件类型：
- ``WORKER_STARTED``：worker 启动成功（含摄像头与任务信息）
- ``WORKER_STOPPED``：worker 正常停止
- ``WORKER_ERROR``：worker 运行异常（含错误信息）
- ``ALARM_RAISED``：产生一条告警（携带告警 dict 快照）
- ``CAMERA_OFFLINE``：摄像头断流超过阈值
- ``CAMERA_RECOVERED``：摄像头断流恢复
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class RuntimeEvent:
    """一次运行时事件。"""

    type: str
    worker_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "worker_id": self.worker_id,
            "payload": self.payload,
            "ts": self.ts,
        }


# 事件类型常量
WORKER_STARTED = "WORKER_STARTED"
WORKER_STOPPED = "WORKER_STOPPED"
WORKER_ERROR = "WORKER_ERROR"
ALARM_RAISED = "ALARM_RAISED"
CAMERA_OFFLINE = "CAMERA_OFFLINE"
CAMERA_RECOVERED = "CAMERA_RECOVERED"

# 回调签名：Callable[[RuntimeEvent], None]
EventListener = Callable[[RuntimeEvent], None]