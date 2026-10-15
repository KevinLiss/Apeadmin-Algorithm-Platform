# -*- coding: utf-8 -*-
"""人脸库进程内缓存（P1）。

- :class:`FaceGallery` 单例：全量加载 ``ai_vision_faces`` →
  (ids, names, groups, matrix N×128)，``best_match`` 做分组内 top1 余弦比对；
- 30s TTL 刷新（与 IdentityDetector 的员工档案缓存同思路）；
  API 登记/删除后调 :meth:`invalidate` 主动失效，秒级生效；
- 阈值判定不归本模块——只返回最高相似度的人员与分数，由调用方
  （P2 的 FaceDetector）按事件的 ``face_threshold`` 决定是否命中。

线程安全：多 worker 线程 + API 线程并发读，加载过程持锁。
库为空的场景（未登记任何人）返回 ``(None, 0.0)``，调用方按"未启用"处理。
"""
from __future__ import annotations

import json
import threading
import time

import numpy as np

_TTL = 30.0  # 秒


class FaceGallery:
    """人脸库缓存（进程级单例）。"""

    _instance: "FaceGallery | None" = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._loaded_at = 0.0
        self._ids: list[int] = []
        self._names: list[str] = []
        self._groups: list[str] = []
        self._mat: np.ndarray = np.zeros((0, 128), dtype=np.float32)

    # ── 单例 ───────────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "FaceGallery":
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 公共 API ────────────────────────────────────────────
    def invalidate(self) -> None:
        """主动失效（登记/删除后调用，下次比对立即重载）。"""
        with self._mu:
            self._loaded_at = 0.0

    @property
    def size(self) -> int:
        self._ensure(time.time())
        return len(self._ids)

    def best_match(
        self, vec: np.ndarray, groups: tuple[str, ...] = ("whitelist",), filter_ids: set[int] | None = None
    ) -> tuple[dict | None, float]:
        """指定分组内与 ``vec`` 余弦相似度最高的人员。

        Args:
            vec: L2 归一化的 128 维特征。
            groups: 参与比对的分组（stranger 用 whitelist，blacklist 模式用
                blacklist，record 模式可用全部）。
            filter_ids: 若提供，则只在这些人员 ID 中比对（空集合视为无匹配）。

        Returns:
            (人员 dict 或 None, 相似度)。库空/分组无成员/过滤后无成员返回 (None, 0.0)。
        """
        self._ensure(time.time())
        with self._mu:
            if self._mat.shape[0] == 0 or not self._ids:
                return None, 0.0
            idx = [i for i, g in enumerate(self._groups) if g in set(groups)]
            if filter_ids is not None:
                idx = [i for i in idx if self._ids[i] in filter_ids]
            if not idx:
                return None, 0.0
            sims = self._mat[idx] @ vec
            j = int(np.argmax(sims))
            person = {
                "id": self._ids[idx[j]],
                "name": self._names[idx[j]],
                "group": self._groups[idx[j]],
            }
            return person, float(sims[j])

    def person(self, person_id: int) -> dict | None:
        """按 ID 取人员（不存在返回 None；verify 模式校验核验对象用）。"""
        self._ensure(time.time())
        with self._mu:
            if person_id in self._ids:
                i = self._ids.index(person_id)
                return {
                    "id": self._ids[i],
                    "name": self._names[i],
                    "group": self._groups[i],
                }
        return None

    def match_one(
        self, vec: np.ndarray, person_id: int
    ) -> tuple[dict | None, float]:
        """1:1 比对（verify 模式）：只与指定人员算相似度。

        Returns:
            (人员 dict 或 None, 相似度)。人员不在库返回 (None, 0.0)。
        """
        person = self.person(person_id)
        if person is None:
            return None, 0.0
        with self._mu:
            i = self._ids.index(person_id)
            return person, float(self._mat[i] @ vec)

    # ── 内部实现 ────────────────────────────────────────────
    def _ensure(self, ts: float) -> None:
        """TTL 内不重复查库（double-check 减少锁竞争）。"""
        if ts - self._loaded_at < _TTL:
            return
        with self._mu:
            if ts - self._loaded_at < _TTL:
                return
            try:
                from src.plugins.builtin.ai_vision.models import AIVisionFace
                from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

                ids: list[int] = []
                names: list[str] = []
                groups: list[str] = []
                vecs: list[np.ndarray] = []
                with sync_session() as db:
                    rows = db.query(AIVisionFace).all()
                    for r in rows:
                        try:
                            v = np.array(
                                json.loads(r.embedding or "[]"), dtype=np.float32
                            )
                        except (ValueError, TypeError):
                            continue
                        if v.size != 128:
                            continue
                        ids.append(r.id)
                        names.append(r.name)
                        groups.append(r.group)
                        vecs.append(v)
                mat = (
                    np.vstack(vecs).astype(np.float32)
                    if vecs
                    else np.zeros((0, 128), dtype=np.float32)
                )
                self._ids, self._names, self._groups, self._mat = ids, names, groups, mat
                self._loaded_at = ts
            except Exception:  # noqa: BLE001
                # 查库失败保留旧缓存（可能是瞬时锁竞争），不清空
                self._loaded_at = ts


def get_gallery() -> FaceGallery:
    """获取人脸库缓存单例。"""
    return FaceGallery.get_instance()
