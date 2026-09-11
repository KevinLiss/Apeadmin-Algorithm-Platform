"""WorkerManager 任务编排单例（任务 2.4）。

职责：
- 按摄像头聚合 StreamWorker：同一摄像头多个任务共享一个 worker（引用计数），
  全部停止才销毁 worker（避免重复 VideoCapture 解码）；
- 提供任务启停 / 重启恢复入口（供 ``api/tasks.py`` 与 ``plugin.py`` 调用）；
- 全局锁保护 worker 字典；路数上限（配置项 max_workers=4，见任务 3.2）；
- 服务重启后由 ``plugin.py`` 调用 ``restore_running_tasks()`` 自动拉起
  ``ai_vision_tasks`` 中 status=running 的任务。

线程模型：
- API 层（async）通过 ``asyncio.to_thread`` 调用本模块同步方法，
  避免阻塞事件循环（worker 启停涉及 DB 查询 + 线程操作）。
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from loguru import logger

from src.plugins.builtin.ai_vision.runtime.worker import StreamWorker, TaskBinding

# 路数上限（一期 4 路 @2fps，见任务 3.2 可配置化）
MAX_WORKERS = 4


class WorkerManager:
    """进程内单例：摄像头 → StreamWorker 的聚合管理。"""

    _instance: "WorkerManager | None" = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._workers: dict[int, StreamWorker] = {}
        self._refs: dict[int, set[int]] = {}  # camera_id → {task_id, ...}
        self._lock = threading.Lock()
        self._max_workers = MAX_WORKERS

    @classmethod
    def get_instance(cls) -> "WorkerManager":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 查询 ───────────────────────────────────────────────
    def get_worker(self, camera_id: int) -> StreamWorker | None:
        with self._lock:
            return self._workers.get(camera_id)

    def worker_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def list_workers(self) -> list[dict[str, Any]]:
        """返回 worker 快照（供看板 / 状态接口）。"""
        with self._lock:
            out = []
            for cam_id, worker in self._workers.items():
                out.append({
                    "camera_id": cam_id,
                    "tasks": sorted(self._refs.get(cam_id, set())),
                    "state": worker.state,
                    "running": worker.is_running,
                    "fps_actual": worker.stats.fps_actual,
                    "frames_processed": worker.stats.frames_processed,
                    "last_alarm_at": worker.stats.last_alarm_at,
                })
            return out

    def active_tasks(self) -> set[int]:
        """当前活跃任务 ID 集合。"""
        with self._lock:
            return {tid for refs in self._refs.values() for tid in refs}

    # ── 任务启停 ───────────────────────────────────────────
    def start_task(self, task: TaskBinding) -> None:
        """启动一个任务（聚合到对应摄像头 worker）。

        调用方负责：task 已从 DB 加载并置为 running；本方法只负责
        内存态 worker 的创建/绑定。
        """
        camera_id = task.camera_id
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                if len(self._workers) >= self._max_workers:
                    raise RuntimeError(
                        f"已达路数上限（{self._max_workers} 路），"
                        "请降低分析 fps / 缩小 ROI / 增加算力后重试"
                    )
                # 从 DB 取摄像头源信息（同步 session：本方法运行在线程池）
                from src.plugins.builtin.ai_vision.models import AIVisionCamera
                from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

                rtsp = ""
                source_type = "camera"
                with sync_session() as db:
                    cam = db.get(AIVisionCamera, camera_id)
                    if not cam or cam.is_deleted:
                        raise RuntimeError(f"摄像头不存在或已删除: #{camera_id}")
                    rtsp = cam.rtsp_url
                    source_type = cam.source_type or "camera"
                worker = StreamWorker(
                    camera_id=camera_id,
                    rtsp_url=rtsp,
                    source_type=source_type,
                )
                worker.start()
                self._workers[camera_id] = worker
                self._refs[camera_id] = set()
                logger.info(f"[AIVision] worker started for camera #{camera_id}")

            worker.add_task(task)
            self._refs[camera_id].add(task.task_id)

    def stop_task(self, task_id: int, camera_id: int) -> None:
        """停止单个任务；该摄像头无剩余任务时销毁 worker。"""
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return
            worker.remove_task(task_id)
            refs = self._refs.get(camera_id, set())
            refs.discard(task_id)
            if not refs:
                worker.stop()
                self._workers.pop(camera_id, None)
                self._refs.pop(camera_id, None)
                logger.info(f"[AIVision] worker for camera #{camera_id} destroyed (no tasks)")

    def stop_all(self) -> None:
        """停止全部 worker（插件卸载 / 服务关闭时调用）。"""
        with self._lock:
            for camera_id, worker in list(self._workers.items()):
                worker.stop()
                self._workers.pop(camera_id, None)
                self._refs.pop(camera_id, None)
            logger.info("[AIVision] all workers stopped")

    # ── 重启恢复 ───────────────────────────────────────────
    async def restore_running_tasks(self) -> int:
        """服务重启后，自动恢复 DB 中 status=running 的任务。返回恢复数量。"""
        from sqlalchemy import select

        from src.db import SessionLocal
        from src.plugins.builtin.ai_vision.models import (
            AIVisionCamera,
            AIVisionEvent,
            AIVisionTask,
        )

        restored = 0
        try:
            async with SessionLocal() as db:
                tasks = (await db.execute(
                    select(AIVisionTask).where(AIVisionTask.status == "running")
                )).scalars().all()
                for task in tasks:
                    cam = await db.get(AIVisionCamera, task.camera_id)
                    evt = await db.get(AIVisionEvent, task.event_id)
                    if not cam or cam.is_deleted or not evt or evt.is_deleted:
                        logger.warning(f"[AIVision] restore skip task#{task.id}: 摄像头/事件不存在")
                        continue
                    try:
                        category_codes = json.loads(evt.category_codes or "[]")
                        rule = json.loads(evt.rule or "{}")
                        model_ids = json.loads(evt.model_ids or "[]")
                        binding = TaskBinding(
                            task_id=task.id,
                            event_id=task.event_id,
                            camera_id=task.camera_id,
                            category_codes=category_codes,
                            rule=rule,
                            model_ids=model_ids,
                            analyze_fps=task.analyze_fps,
                            status="running",
                        )
                        await asyncio.to_thread(self.start_task, binding)
                        restored += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.error(f"[AIVision] 恢复任务 task#{task.id} 失败: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] restore_running_tasks 异常: {exc}")
        logger.info(f"[AIVision] restored {restored} running tasks")
        return restored


def get_manager() -> WorkerManager:
    """获取 WorkerManager 单例。"""
    return WorkerManager.get_instance()