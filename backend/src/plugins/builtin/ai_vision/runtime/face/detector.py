# -*- coding: utf-8 -*-
"""人脸识别判定器（P2）： stranger / blacklist / record 三模式。

由 worker 在 ``rule.detector == "face"`` 时创建，每分析帧喂入当前帧 +
:class:`FaceEngine`，返回 (是否告警, 本帧识别结果, 流水行)。

规则参数（必须在 schemas.EventRule 白名单声明，否则 PUT 时被 Pydantic
静默丢弃——2026-09-30 离岗字段丢失教训）：
- face_mode: stranger=陌生人告警 / blacklist=黑名单告警 / record=识别记录
- face_threshold: 余弦相似度阈值（默认 0.363，SFace 官方值；P0 实测
  同人 min 0.634 / 异人 max 0.209，分布完全分离）
- face_min_size: 最小人脸边长 px（默认 40，过滤远处小人脸）
- face_interval: 识别间隔秒（默认 1.0；非识别帧沿用 IoU 跟踪的身份，
  监控台标签不闪烁，也省 CPU）
- face_hold_seconds: 陌生人持续出现秒数（默认 3，stranger 模式）
- face_record_interval: 同一人流水节流秒（默认 60，record 模式）
- face_liveness: 是否开启活体检测（默认 False；挡照片/屏幕翻拍攻击）
- face_liveness_threshold: 真人概率阈值（默认 0.5，低于视为翻拍）
- face_verify_person_id: 核验对象人脸库 ID（verify 模式，0=未选择）
- face_verify_seconds: 人证不符持续秒数（默认 10，verify 模式）
- cooldown: 告警冷却（三种模式共用）

人脸跟踪：帧间用 IoU 匹配沿用上次识别结果（姓名/相似度），保证监控台
标签稳定 + "持续出现" 累计有意义（不上 DeepSORT，IoU 足够）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

# 帧间 IoU 匹配阈值 / 跟踪失活秒数
_IOU_MATCH_THR = 0.3
_TRACK_TTL = 2.0


@dataclass(slots=True)
class FaceMatch:
    """一帧内的一个人脸识别结果。

    属性命名对齐 worker 的 Detection/PoseDetection：``_raise_alarm`` 取
    ``d.conf`` 作告警置信度、``_save_snapshot`` 用 ``d.bbox``/``d.cls_name``
    画框，故 FaceMatch 可直接混入 dets 列表。
    """

    bbox: list[float]  # [x1, y1, x2, y2] 原图坐标
    conf: float        # 已知=相似度；陌生人=检测 score（"确定这是张脸"的把握）
    cls_name: str      # 姓名（已知）或 "陌生人"
    person_id: int = 0  # 0=陌生人
    similarity: float = 0.0
    is_known: bool = False
    group: str = ""


@dataclass(slots=True)
class _Track:
    bbox: list[float]
    name: str
    person_id: int
    similarity: float
    is_known: bool
    group: str
    first_seen: float
    last_seen: float
    score: float = 0.0       # YuNet 检测分（陌生人告警的置信度来源）
    fired: bool = False      # 本片段是否已告警（stranger）
    last_log_at: float = 0.0  # 本片段最近一条流水时间（record）


@dataclass(slots=True)
class FeedResult:
    alarm: bool
    matches: list[FaceMatch]
    log_rows: list[dict] = field(default_factory=list)


def _iou(a: list[float], b: list[float]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


class FaceDetector:
    """人脸识别三+一模式判定器（worker 每任务一个实例）。

    模式：stranger（陌生人告警）/ blacklist（黑名单告警）/
    record（识别记录流水）/ verify（1:1 人证核验）。
    """

    def __init__(self, rule: dict, fps: int) -> None:
        self.mode = str(rule.get("face_mode", "stranger"))
        self.threshold = float(rule.get("face_threshold", 0.363))
        self.min_size = float(rule.get("face_min_size", 40))
        self.interval = max(0.2, float(rule.get("face_interval", 1.0)))
        self.hold_seconds = float(rule.get("face_hold_seconds", 3))
        self.record_interval = max(10.0, float(rule.get("face_record_interval", 60)))
        self.cooldown = int(rule.get("cooldown", 60))
        # 活体防伪（默认关）：开启后翻拍攻击人脸直接丢弃，不识别不告警
        self.liveness_on = bool(rule.get("face_liveness", False))
        self.liveness_threshold = float(rule.get("face_liveness_threshold", 0.5))
        # 1:1 核验参数
        self.verify_person_id = int(rule.get("face_verify_person_id", 0) or 0)
        self.verify_seconds = float(rule.get("face_verify_seconds", 10))
        self._last_check = 0.0
        self._last_alarm_at = 0.0
        self._tracks: list[_Track] = []
        self._warned_no_engine = False
        self._warned_liveness = False
        self._warned_verify = False

    # ── 主入口 ──────────────────────────────────────────────
    def feed(
        self,
        frame: np.ndarray,
        engine,
        ts: float,
        task_id: int = 0,
        camera_id: int = 0,
    ) -> FeedResult:
        """喂入当前帧，返回判定结果。

        非识别帧直接返回跟踪保持的上一轮结果（标签稳定 + 省 CPU）。
        """
        if ts - self._last_check >= self.interval:
            self._last_check = ts
            self._recognize(frame, engine, ts)
        self._expire(ts)

        matches = [self._to_match(t) for t in self._tracks]
        if not matches:
            return FeedResult(alarm=False, matches=[])

        alarm = False
        if self.mode == "stranger":
            alarm = self._check_stranger(ts)
        elif self.mode == "blacklist":
            alarm = self._check_blacklist(ts)
        elif self.mode == "verify":
            alarm = self._check_verify(ts)

        log_rows: list[dict] = []
        if self.mode == "record":
            log_rows = self._collect_logs(ts, task_id, camera_id)

        return FeedResult(alarm=alarm, matches=matches, log_rows=log_rows)

    # ── 识别 + 跟踪更新 ─────────────────────────────────────
    def _recognize(self, frame: np.ndarray, engine, ts: float) -> None:
        from src.plugins.builtin.ai_vision.runtime.face.gallery import get_gallery

        try:
            faces = engine.detect_faces(frame)
        except Exception:  # noqa: BLE001
            return
        # 过滤过小人脸（远景/背景）
        faces = [
            f for f in faces
            if min(f.bbox[2] - f.bbox[0], f.bbox[3] - f.bbox[1]) >= self.min_size
        ]

        # 活体门控：翻拍攻击（照片/屏幕）直接丢弃——不跟踪、不告警、不记录。
        # 模型缺失时 fail-open（放行 + 一次性告警日志），不阻断识别主流程。
        if self.liveness_on:
            from src.plugins.builtin.ai_vision.runtime.face.liveness import (
                get_liveness_engine,
            )

            liv = get_liveness_engine()
            if liv is None:
                if not self._warned_liveness:
                    logger.warning(
                        "[AIVision] face 活体模型缺失（assets/models 下 "
                        "face_antispoof_minifasnet_v2se.onnx），本次 fail-open 放行"
                    )
                    self._warned_liveness = True
            else:
                live_faces = []
                for f in faces:
                    s = liv.score(frame, f.bbox)
                    if s is None or s >= self.liveness_threshold:
                        live_faces.append(f)
                    # else: 判定为翻拍攻击 → 静默丢弃
                faces = live_faces

        gallery = get_gallery()
        verify_person: dict | None = None
        groups: tuple[str, ...] = ("whitelist",)
        if self.mode == "stranger":
            groups = ("whitelist",)
        elif self.mode == "blacklist":
            groups = ("blacklist",)
        elif self.mode == "record":
            groups = ("whitelist", "blacklist")
        elif self.mode == "verify":
            # 1:1 核验：对象未选/已删除 → 不判定（一次性告警，避免全员误报）
            verify_person = gallery.person(self.verify_person_id)
            if verify_person is None:
                if not self._warned_verify:
                    logger.warning(
                        f"[AIVision] face verify 核验对象 #{self.verify_person_id} "
                        "不可用（未选择或已从人脸库删除），本次不判定"
                    )
                    self._warned_verify = True
                return

        dets: list[dict] = []
        for f in faces:
            vec = engine.embed(frame, f)
            if vec is None:
                continue
            if self.mode == "verify":
                person, sim = gallery.match_one(vec, self.verify_person_id)
                known = person is not None and sim >= self.threshold
                name = f"核验通过:{person['name']}" if known else "人证不符"
                group = person["group"] if known else ""
            else:
                person, sim = gallery.best_match(vec, groups=groups)
                known = person is not None and sim >= self.threshold
                name = person["name"] if known else "陌生人"
                group = person["group"] if known else ""
            dets.append({
                "bbox": f.bbox,
                "score": f.score,
                "name": name,
                "person_id": person["id"] if known else 0,
                "similarity": round(sim, 3),
                "is_known": known,
                "group": group,
            })

        # IoU 贪心匹配：新检测 ↔ 旧跟踪
        used_tracks: set[int] = set()
        for d in dets:
            best_iou, best_j = 0.0, -1
            for j, t in enumerate(self._tracks):
                if j in used_tracks:
                    continue
                v = _iou(d["bbox"], t.bbox)
                if v > best_iou:
                    best_iou, best_j = v, j
            if best_j >= 0 and best_iou >= _IOU_MATCH_THR:
                t = self._tracks[best_j]
                used_tracks.add(best_j)
                t.bbox = d["bbox"]
                t.name, t.person_id, t.similarity = d["name"], d["person_id"], d["similarity"]
                t.is_known, t.group = d["is_known"], d["group"]
                t.score = d["score"]
                t.last_seen = ts
            else:
                self._tracks.append(_Track(
                    bbox=d["bbox"], name=d["name"], person_id=d["person_id"],
                    similarity=d["similarity"], is_known=d["is_known"], group=d["group"],
                    first_seen=ts, last_seen=ts, score=d["score"],
                    # 初始化为「创建时刻减去一个节流周期」：首个识别帧即记一条流水
                    # （否则 last_log_at=0 会让首条流水被推迟到轨道存活满一个周期，
                    #  短时出现的人脸永远记不到——E2E 实测教训）
                    last_log_at=ts - self.record_interval,
                ))

    def _expire(self, ts: float) -> None:
        self._tracks = [t for t in self._tracks if ts - t.last_seen < _TRACK_TTL]

    # ── 三模式判定 ───────────────────────────────────────────
    def _check_stranger(self, ts: float) -> bool:
        """未知人脸持续 ≥ hold_seconds → 告警（每片段一次）。"""
        for t in self._tracks:
            if t.is_known or t.fired:
                continue
            if ts - t.first_seen < self.hold_seconds:
                continue
            if ts - self._last_alarm_at < self.cooldown:
                continue
            t.fired = True
            self._last_alarm_at = ts
            return True
        return False

    def _check_blacklist(self, ts: float) -> bool:
        """命中黑名单 → 立即告警（cooldown 约束）。"""
        for t in self._tracks:
            if not (t.is_known and t.group == "blacklist"):
                continue
            if ts - self._last_alarm_at < self.cooldown:
                continue
            self._last_alarm_at = ts
            return True
        return False

    def _check_verify(self, ts: float) -> bool:
        """1:1 核验：人证不符（非核验对象）持续 ≥ verify_seconds → 告警。

        核验通过（is_known）的片段不告警；每个不符片段只报一次。
        """
        for t in self._tracks:
            if t.is_known or t.fired:
                continue
            if ts - t.first_seen < self.verify_seconds:
                continue
            if ts - self._last_alarm_at < self.cooldown:
                continue
            t.fired = True
            self._last_alarm_at = ts
            return True
        return False

    def _collect_logs(self, ts: float, task_id: int, camera_id: int) -> list[dict]:
        """record 模式：每个片段每 record_interval 产出一条流水。"""
        rows: list[dict] = []
        for t in self._tracks:
            if ts - t.last_log_at < self.record_interval:
                continue
            t.last_log_at = ts
            rows.append({
                "person_id": t.person_id,
                "person_name": t.name,
                "group": t.group,
                "similarity": t.similarity,
                "bbox": list(t.bbox),
                "task_id": task_id,
                "camera_id": camera_id,
            })
        return rows

    # ── 输出转换 ────────────────────────────────────────────
    def _to_match(self, t: _Track) -> FaceMatch:
        # 已知 → 置信度用相似度（"有多像登记人"）；陌生人 → 用检测分
        # （"多确定这是张人脸"）；verify 模式人证不符 → 用相似度（"离目标
        # 有多远"也是关键信息）。三者都是各自语义下最有信息量的值。
        if t.is_known or self.mode == "verify":
            conf = t.similarity
        else:
            conf = t.score
        return FaceMatch(
            bbox=list(t.bbox),
            conf=conf,
            cls_name=t.name,
            person_id=t.person_id,
            similarity=t.similarity,
            is_known=t.is_known,
            group=t.group,
        )
