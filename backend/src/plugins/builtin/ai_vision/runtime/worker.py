"""StreamWorker 推理流水线（任务 2.3）。

职责（一个摄像头 = 一个 StreamWorker 线程）：
1. ``FrameGrabber`` 循环：cv2.VideoCapture 抓帧，按该摄像头全部事件
   的最高分析 fps 节流（避免重复解码）；
2. 推理分发：一帧 → 该摄像头全部启用事件的模型推理（模型去重后逐个
   ``InferenceEngine.detect``，同一模型只推理一次，结果按事件过滤类别）；
3. ``PostProcessor``：ROI 多边形过滤 / 最小尺寸过滤 / 置信度过滤；
4. ``EventDetector``（一期简化版）：连续 N 帧命中投票 + 冷却期；
5. 告警落库：``ai_vision_alarms`` + 抓拍图
   ``uploads/ai_vision/snapshots/{camera_id}/{ts}.jpg``（cv2 画框 + 标签）。

设计原则：
- **线程内独立 DB session**（不能跨线程复用 async session）：worker 内
  用 ``SessionLocal()`` 同步连接写库，避免阻塞事件循环；
- 推理引擎线程安全（onnxruntime run 可并发），多个事件共享同一模型句柄；
- 抓帧失败 → 指数退避（1s/2s/4s…上限 60s）重连；连续 5 分钟失败 →
  事件 ``CAMERA_OFFLINE`` + 任务状态 ``degraded``；
- 一期 EventDetector 按状态机抽象（``feed(detections, ts) -> bool``），
  二期替换为完整时序实现（见任务 4.1）。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from src.plugins.builtin.ai_vision.runtime.deps import ensure_infer_runtime
from src.plugins.builtin.ai_vision.runtime.engine import InferenceEngine, get_engine
from src.plugins.builtin.ai_vision.runtime.events import (
    ALARM_RAISED,
    CAMERA_OFFLINE,
    CAMERA_RECOVERED,
    WORKER_ERROR,
    WORKER_STARTED,
    WORKER_STOPPED,
    RuntimeEvent,
)

# 断线重连参数
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 60.0
_OFFLINE_AFTER_SECONDS = 5 * 60  # 连续 5 分钟失败 → offline


@dataclass(slots=True)
class TaskBinding:
    """一个运行中任务在 worker 内的绑定信息。"""

    task_id: int
    event_id: int
    camera_id: int
    category_codes: list[str]
    rule: dict[str, Any]
    model_ids: list[int]
    analyze_fps: int = 2
    status: str = "running"
    detector: "EventDetector | DivingDetector | None" = None  # 每个任务一个判定器实例


@dataclass(slots=True)
class PostProcessor:
    """检测结果过滤（ROI / 最小尺寸 / 置信度）。"""

    roi: list[list[float]] = field(default_factory=list)  # [[x,y],...] 空=全图
    min_size: float = 20.0  # 最小检测框边长（像素）
    threshold: float = 0.45

    def filter(self, dets: list[Any]) -> list[Any]:
        """按 ROI / 最小尺寸过滤。置信度过滤已在引擎内完成。"""
        out = []
        for d in dets:
            x1, y1, x2, y2 = d.bbox
            w, h = x2 - x1, y2 - y1
            if w < self.min_size or h < self.min_size:
                continue
            if self.roi and not self._in_roi((x1 + x2) / 2, (y1 + y2) / 2):
                continue
            out.append(d)
        return out

    def _in_roi(self, cx: float, cy: float) -> bool:
        """点在 ROI 多边形内（含边界）。"""
        import cv2

        poly = np.array([(float(p[0]), float(p[1])) for p in self.roi], dtype=np.float32)
        if len(poly) < 3:
            return True  # 无效多边形视为全图
        return cv2.pointPolygonTest(poly, (cx, cy), False) >= 0


class EventDetector:
    """一期简化判定器：连续 N 帧命中投票 + 冷却期。

    接口按状态机抽象（``feed(detections, ts) -> bool``），二期（任务 4.1）
    替换为 SlidingWindowVoter + DurationTimer + CooldownGate 完整实现。

    规则参数：
    - vote_seconds: 投票窗口（秒）；0 = 单帧命中即告警。
      兼容前端事件表单的 duration 字段（两者同义，vote_seconds 优先）
    - cooldown: 告警冷却（秒）
    - fps: 分析帧率（用于计算 N = ceil(fps × vote_seconds)）
    """

    def __init__(self, rule: dict[str, Any], fps: int) -> None:
        self.threshold = float(rule.get("threshold", 0.45))
        self.vote_seconds = int(rule.get("vote_seconds", rule.get("duration", 0)) or 0)
        self.cooldown = int(rule.get("cooldown", 60))
        self.fps = max(1, int(fps))
        self._hit_count = 0
        self._last_alarm_at = 0.0
        self._vote_n = max(1, int(self.fps * self.vote_seconds)) if self.vote_seconds > 0 else 1

    def feed(self, detections: list[Any], ts: float) -> bool:
        """喂入一帧检测结果，返回是否触发告警（达到投票阈值且在冷却期外）。"""
        hit = len(detections) > 0
        if hit:
            self._hit_count += 1
        else:
            self._hit_count = 0

        if self._hit_count < self._vote_n:
            return False

        # 冷却期检查
        if ts - self._last_alarm_at < self.cooldown:
            return False

        self._last_alarm_at = ts
        self._hit_count = 0
        return True


class DivingDetector:
    """跳水动作判定器（pose 模型 + 躯干倾角状态机）。

    原理：跳水动作在低帧率（2~5fps）下表现为三段可观测特征——
    1. **腾空**：躯干（肩中点→髋中点连线）与竖直方向夹角 ≥ angle_thr
       （站立 ~0-20°；起跳/翻转/入水姿态 55-180°），且持续 ≥ min_air_frames；
    2. **入水**：腾空后目标从画面中消失（水花遮挡/沉入水中）≥ miss_frames；
    3. 满足 1→2 序列即告警，冷却期与 EventDetector 一致。

    接口与 EventDetector 相同（``feed(dets, ts) -> bool``），dets 为
    ``PoseDetection`` 列表（取置信度最高的 person 作为跟踪目标）。

    规则参数（存于事件 rule JSON）：
    - angle_thr: 躯干倾角阈值（度），默认 55
    - min_air_seconds: 最短腾空时长（秒），默认 0.4
    - miss_seconds: 入水判定的目标消失时长（秒），默认 0.8
    - max_air_seconds: 腾空状态超时重置（秒），默认 4（防止弯腰等误挂状态）
    - descent_frac: 复现确认入水的下落比例（框中心下降 ≥ 画面高×该值时，
      即使消失未满 miss_seconds 也判定入水；0=禁用），默认 0.2
    - cooldown: 告警冷却（秒）
    """

    # COCO 关键点索引：鼻 = 0，左/右肩 = 5/6，左/右髋 = 11/12
    _SHOULDER = (5, 6)
    _HIP = (11, 12)
    _KPT_CONF_MIN = 0.3

    def __init__(self, rule: dict[str, Any], fps: int) -> None:
        self.fps = max(1, int(fps))
        self.cooldown = int(rule.get("cooldown", 30))
        self.angle_thr = float(rule.get("angle_thr", 55.0))
        self.min_air_frames = max(1, int(round(float(rule.get("min_air_seconds", 0.4)) * self.fps)))
        self.miss_frames = max(1, int(round(float(rule.get("miss_seconds", 0.8)) * self.fps)))
        self.max_air_frames = max(2, int(round(float(rule.get("max_air_seconds", 4.0)) * self.fps)))
        self.descent_frac = float(rule.get("descent_frac", 0.2))
        # 头朝下兜底信号阈值：(鼻y-肩中点y)/框高 ≥ 该值判定倒立。
        # 跳水多为手+头先入水，髋部被遮挡导致躯干角算不出（实测 conf<0.3），
        # 而鼻/肩关键点全程 0.9+——用高置信度信号补上腾空判定入口。
        self.head_down_frac = float(rule.get("head_down_frac", 0.02))
        # 框骤缩腾空通道阈值：当前框高 < 近 8 可见帧最大框高 × 该比例 且
        # 最大框 >0.35 → 判为"站台竖直→空中横体"跳水轨迹（远景高速翻转时
        # 躯干角/头朝下全失效的兜底）。默认 0.30（实测：坐下骤缩比~0.32 不
        # 触发，跳水腾空 0.19~0.24 触发）。
        self.shrink_air_frac = float(rule.get("shrink_air_frac", 0.30))
        self._state = "idle"  # idle / airborne
        self._air_count = 0
        self._miss_count = 0
        self._air_start_cy = 0.0  # 进入腾空态时目标框中心 y（像素）
        self._last_cy = 0.0  # 最近一次可见时目标框中心 y
        self._min_cy = float("inf")  # 本轮最高点 cy（下落确认基准，idle 帧重置）
        self._last_alarm_at = -float("inf")  # 初始视为"从未告警"，不拦首次
        self._track_box: tuple | list | None = None  # 腾空期锁定目标框（防多人漂移）
        self._min_box_h_frac = 1.0  # 本轮最小框高/画面高（深坠通道入水小框特征）
        self._max_air_box_h_frac = 0.0  # 腾空期最大框高/画面高（收缩比分母）
        self._last_box_h_frac = 0.0  # 最近一次可见框高/画面高（收缩比分子）
        # 本轮腾空期见过的最高检测置信度：告警触发时目标已消失（dets 为空），
        # 落库 confidence 需回退到该值，否则恒为 0.0
        self.episode_max_conf = 0.0
        # 框高骤缩通道（第三腾空入口）：远景高速翻转跳水时躯干角/头朝下
        # 信号全部失效（实测 ang≤33、headrel 恒负），但"框高 1.6s 内骤降
        # 过半→消失"是跳水的独特轨迹（空中横体框小+入水不可见）。
        self._box_hist: list[float] = []  # 最近 8 个可见帧的框高/画面高
        self._miss_gap = 0  # idle 态连续检测空档计数（>2 清空框高历史）
        self._prev_box: tuple | list | None = None  # 上一可见帧目标框（骤缩通道 IoU 连续性校验）
        self._pre_air_box = 0.0  # 进入腾空前的近期最大框高（骤缩通道收缩基准）
        self._air_via_shrink = False  # 本轮腾空是否由框骤缩通道进入（骤缩=跳水铁证，消失即告警）
        # 供画面叠加 / 调试
        self.last_angle: float | None = None
        self.last_head_rel: float | None = None
        self.last_state = "idle"

    def feed(self, detections: list[Any], ts: float, frame_h: int = 0) -> bool:
        """喂入一帧 pose 检测结果，返回是否触发跳水告警。

        frame_h: 帧高度（像素），用于下落比例确认；0=跳过该项确认。

        腾空信号双通道（取或）：
        1. 躯干倾角 ≥ angle_thr（髋部可见时最可靠，脚先入水/侧拍场景）；
        2. 头朝下：鼻低于肩中点 ≥ 框高×head_down_frac（手+头先入水时髋被
           遮挡、躯干角算不出的主场景——2026-09-23 真实跳水视频漏报根因）。
        入水确认双通道（任一满足即告警，均需先满足下落确认）：
        a) 消失通道：腾空达标后跟踪目标消失 ≥ miss_frames（水花遮蔽/沉水）；
        b) 深坠通道：目标仍可见但已从最高点下坠 ≥ descent_frac×1.2 画面高
           （清澈泳池入水后人继续被检出的场景——真实跳水视频01 漏报根因，
           miss 永远凑不满）。
        多人场景防目标漂移：进入腾空态后按 IoU 锁定目标框，不再取"置信度
        最高的人"（围观者/泳客会打断 miss 计数造成漏报）。
        """
        det = self._select_target(detections, frame_h)
        angle = self._torso_angle(det)
        head_rel = self._head_rel(det)
        self.last_angle = angle
        self.last_head_rel = head_rel
        cy = 0.0
        box_shrink_air = False
        if det is not None:
            self._miss_gap = 0
            cy = (det.bbox[1] + det.bbox[3]) / 2.0
            self._last_cy = cy
            self._track_box = det.bbox  # 锁定框跟随目标移动
            self.episode_max_conf = max(self.episode_max_conf, float(det.conf))
            if frame_h > 0:
                box_h_frac = (det.bbox[3] - det.bbox[1]) / frame_h
                self._min_box_h_frac = min(self._min_box_h_frac, box_h_frac)
                self._last_box_h_frac = box_h_frac
                if self._state == "airborne":
                    self._max_air_box_h_frac = max(self._max_air_box_h_frac, box_h_frac)
                # 框高骤缩腾空入口：站台上竖直(大框)→空中翻转横体(小框)是
                # 远景高速跳水的独特轨迹，此时躯干角/头朝下信号全失效
                # （实测 ang≤33、headrel 恒负）。用"当前框高 < 近 8 可见帧
                # 最大框高×0.30 且该最大框 >0.35"捕捉骤降，作为第三腾空通道。
                # 0.30 为实测标定：台上坐下骤缩比 ~0.32 不触发；跳水腾空
                # 骤缩比 0.19~0.24 触发。
                recent_max = max(self._box_hist, default=0.0)
                # 骤缩必须是"连续可见的同一目标在缩小"：历史大框若来自
                # 10+ 帧前的旧目标，会被 idle 空档清空逻辑（见下）挡掉；
                # 高速下坠时框位置漂移大，不做 IoU 强校验（会误伤真跳水）
                if recent_max > 0.35 and box_h_frac < self.shrink_air_frac * recent_max:
                    box_shrink_air = True
                self._box_hist.append(box_h_frac)
                if len(self._box_hist) > 8:
                    self._box_hist.pop(0)
                self._prev_box = det.bbox
            # 最高点基准：全程（含腾空弧顶）持续取最小 cy
            self._min_cy = min(self._min_cy, cy)

        airborne_now = det is not None and (
            (angle is not None and angle >= self.angle_thr)
            or (head_rel is not None and head_rel >= self.head_down_frac)
            or box_shrink_air
        )

        if airborne_now:
            # 腾空姿态：进入/维持腾空态
            if self._state == "idle":
                self._state = "airborne"
                self._air_count = 1
                self._air_start_cy = cy
                # 骤缩通道进入标记：站台大框→空中横体骤缩本身就是跳水
                # 铁证（坐下/躺下骤缩比≥0.32 进不了本通道），后续只要
                # 再消失即告警，免受 fell/shrink 二次确认的限制——远景
                # 人群镜头下跟踪目标易被观众劫持，二次确认常失效
                # （montage 视频 10.2s 奥运跳水漏报根因）。
                self._air_via_shrink = bool(box_shrink_air)
                # 骤缩进入时的历史最大框（站台大框）：下方 upright 分支用
                # 它判断"小框=人还在空中/水下"，避免回合被闪烁清零
                self._pre_air_box = max(self._box_hist, default=0.0)
                # 新一轮腾空：置信度基准重置为当前帧（_reset 不清此值，
                # 保留给告警落库读取；下一轮进入 airborne 时重新起算）
                self.episode_max_conf = float(det.conf) if det is not None else 0.0
                if self._min_cy == float("inf"):
                    self._min_cy = cy
            else:
                self._air_count += 1
            self._miss_count = 0
        elif det is not None:
            # 目标可见但姿态直立：腾空未达标 → 复位；已达标 → 保持（等待消失确认入水）
            if self._state == "airborne":
                if self._air_count < self.min_air_frames:
                    self._reset()
                else:
                    # 腾空已达标后重现的目标若是"小框"（入水后只露头/臂，
                    # 框高不足腾空期最大框高的一半），视作仍在水下——miss
                    # 继续累计（真实跳水视频01：入水后头部间歇检出，若按
                    # 常规"重现即清零"则 miss 永远凑不满导致漏报）。
                    # 大框重现（人还在台上/换人）→ 本轮结束，miss 不累计。
                    small_now = (
                        frame_h > 0
                        and self._max_air_box_h_frac > 0.05
                        and (det.bbox[3] - det.bbox[1]) / frame_h
                        < 0.5 * self._max_air_box_h_frac
                    )
                    if small_now:
                        self._miss_count += 1
                    self._air_count += 1  # 超时由 max_air_frames 复位
            else:
                # idle 且直立（站台上走动等）：重置最高点基准，跟随当前 cy
                self._min_cy = cy
        else:
            # 目标消失：仅在腾空达标后计数（入水特征）
            if self._state == "airborne" and self._air_count >= self.min_air_frames:
                self._miss_count += 1
            elif self._state == "airborne":
                self._reset()
            else:
                # idle 且目标消失：连续空档超过 2 帧才清空框高历史。真实跳水的
                # "大框→骤缩"是连续可见过程（偶发 1 帧漏检不应中断参照）；
                # 而误报源（如汽车视频 2 秒空档后冒出的静止小框）空档长达
                # 10+ 帧，必须清空历史大框才能避免"新小框 vs 老历史大框"误判
                # （2026-09-23 汽车误报根因）。
                self._miss_gap += 1
                if self._miss_gap > 2:
                    self._box_hist = []
                    self._prev_box = None

        self.last_state = self._state

        # 超时复位：长时间挂起（如弯腰检修）不产生告警
        if self._state == "airborne" and self._air_count > self.max_air_frames:
            self._reset()
            return False

        # ── 告警判定：腾空达标 + 消失确认 + 入水物理特征 ──
        if self._state == "airborne" and self._air_count >= self.min_air_frames:
            peak = self._min_cy if self._min_cy != float("inf") else self._air_start_cy
            # 入水确认：腾空达标后目标消失 ≥ miss_frames（水花遮蔽/沉入水下）
            miss_hit = self._miss_count >= self.miss_frames
            # 入水物理特征（任一满足即确认，二者都排除"原地被遮挡消失"）：
            #  ① 下坠：最后可见位置比本轮最高点(含腾空弧顶)低 ≥ descent_frac×画面高
            #     （近景/俯拍机位明显，如真实跳水视频02 下坠 30%）；
            #  ② 框收缩比：腾空期最大框高 → 入水前最后框高 骤降过半
            #     （身体没入水只露头/臂；远景机位 cy 位移弱但框高剧变，
            #     真实跳水视频01 实测收缩比 0.36；横躺划水误报仅 0.67）。
            fell = False
            if frame_h > 0 and self.descent_frac > 0:
                fell = (self._last_cy - peak) >= self.descent_frac * frame_h
            shrink_hit = (
                self._max_air_box_h_frac > 0.05
                and (self._last_box_h_frac / self._max_air_box_h_frac) < 0.5
            )
            # 骤缩通道进入的回合：骤缩本身即跳水铁证，跟踪目标入水后常被
            # 人群镜头劫持导致 fell/shrink 二次确认失效（montage 10.2s 奥运
            # 跳水漏报根因）——此时 miss_hit 单独成立即告警。
            if miss_hit and (fell or shrink_hit or self._air_via_shrink):
                self._reset()
                if ts - self._last_alarm_at < self.cooldown:
                    return False
                self._last_alarm_at = ts
                return True
            if miss_hit:
                # 消失但无入水物理特征（原地遮挡/跟丢）→ 复位防挂状态
                self._reset()
                return False
        elif self._state == "airborne" and self._miss_count >= self.miss_frames:
            self._reset()
        return False

    def _reset(self) -> None:
        self._state = "idle"
        self._air_count = 0
        self._miss_count = 0
        self._min_cy = float("inf")
        self._min_box_h_frac = 1.0
        self._max_air_box_h_frac = 0.0
        self._last_box_h_frac = 0.0
        self._track_box = None

    def _select_target(self, detections: list[Any], frame_h: int = 0) -> Any:
        """选跟踪目标：idle 取置信度最高者；airborne 优先与锁定框 IoU 最大者。

        多人场景（围观跳水/泳客同框）下，"conf 最高的人"会在跳者与旁观者
        之间来回切换，导致 miss 计数被打断（跳水视频01 漏报根因之一）。
        画面只有一个人时无歧义直接跟随——入水后框骤变 IoU 不达标，但人
        就是那个跳水者，若按"消失"处理会冻结 cy 轨迹、丢失下坠证据。
        """
        if not detections:
            return None
        if self._state == "airborne" and self._track_box is not None:
            if len(detections) == 1:
                return detections[0]  # 单人场景无歧义，直接跟随
            best, best_iou = None, 0.0
            for d in detections:
                iou = self._box_iou(self._track_box, d.bbox)
                if iou > best_iou:
                    best, best_iou = d, iou
            if best is not None and best_iou >= 0.08:
                return best
            # 多人且 IoU 全不达标（目标入水框剧变/离开画面）→ 视为消失处理
            return None
        return max(detections, key=lambda d: d.conf)

    @staticmethod
    def _box_iou(a: Any, b: Any) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 1e-6 else 0.0

    @staticmethod
    def _torso_angle(det: Any) -> float | None:
        """躯干与竖直方向的夹角（度）。0=直立，90=水平，180=头朝下。

        关键点置信度不足（遮挡/远景）时返回 None。
        静态方法：供 feed() 与 worker 叠加层共用。
        """
        if det is None or not getattr(det, "keypoints", None):
            return None
        kpts = det.keypoints
        if len(kpts) < 13:
            return None
        pts = [kpts[i] for i in (*DivingDetector._SHOULDER, *DivingDetector._HIP)]
        if any(p[2] < DivingDetector._KPT_CONF_MIN for p in pts):
            return None
        sx = (pts[0][0] + pts[1][0]) / 2.0
        sy = (pts[0][1] + pts[1][1]) / 2.0
        hx = (pts[2][0] + pts[3][0]) / 2.0
        hy = (pts[2][1] + pts[3][1]) / 2.0
        vx, vy = sx - hx, sy - hy  # 髋→肩 向量（图像坐标 y 向下）
        norm = (vx * vx + vy * vy) ** 0.5
        if norm < 1e-6:
            return None
        # 竖直向上为 (0, -1)：cos = dot / norm
        cos_a = max(-1.0, min(1.0, -vy / norm))
        import math

        return math.degrees(math.acos(cos_a))

    @staticmethod
    def _head_rel(det: Any) -> float | None:
        """鼻子相对肩中点的纵向偏移 / 框高（图像坐标，y 向下为正）。

        正值 = 头低于肩（倒立/头先入水姿态）。站立时约 -0.02~-0.13，
        头朝下入水时 +0.03~+0.15（真实跳水视频实测），符号即天然分界。
        仅依赖鼻(0)/肩(5,6) 关键点——跳水场景髋部被遮挡时唯一稳定信号。
        返回 None 表示关键点置信度不足或框高无效。
        """
        if det is None or not getattr(det, "keypoints", None):
            return None
        kpts = det.keypoints
        if len(kpts) < 7:
            return None
        nose, sh_l, sh_r = kpts[0], kpts[5], kpts[6]
        if min(nose[2], sh_l[2], sh_r[2]) < DivingDetector._KPT_CONF_MIN:
            return None
        box_h = det.bbox[3] - det.bbox[1]
        if box_h < 1e-6:
            return None
        sh_mid_y = (sh_l[1] + sh_r[1]) / 2.0
        return (nose[1] - sh_mid_y) / box_h


class OverheadDivingDetector:
    """跳水判定器——高空俯视/无人机机位专用（detector=diving_top）。

    与侧拍 DivingDetector 的本质区别：俯视时画面 y 轴 ≠ 重力方向，
    垂直下坠在画面里几乎无位移，姿态/躯干角/框骤缩/下坠全部失效
    （实测俯视视频：pose 37 帧仅 2 帧命中，水花帧差无区分度）。

    俯视下幸存的可用信号（实测标定）：
    1. 检测框水平轨迹——跳水者以 ~20%/秒 的速度掠过水面上空
       （普通泳者 ~8-12%/秒，游泳者持续可见不消失）；
    2. 突然消失——入水后人体没入水下/被水花遮蔽，检测中断；
    3. 水池 ROI（pool_roi 多边形）——消失点必须位于水面区域内，
       排除"跑出画面""离场"等场景。

    判定链：连续 ≥2 帧命中且近 3s 轨迹速度 ≥ move_speed_thr → candidate；
    candidate 后 disappear_seconds 内不再命中（消失）→ 若最后位置在
    pool_roi 内 → 告警（冷却同 EventDetector）。candidate 超 5s 未消失
    则复位（泳客巡航/误检）。

    输入用 COCO 检测模型（yolo11n-coco）的 person 框，检测阈值建议
    ≤0.18（俯视人目标小，实测 0.3 会漏掉几乎全部轨迹）。

    规则参数（存于事件 rule JSON）：
    - pool_roi: 水面多边形 [[x,y],...] 归一化 0~1，≥3 顶点
    - move_speed_thr: 轨迹速度阈值（画面宽/秒），默认 0.12
    - disappear_seconds: 消失判定时长（秒），默认 1.5
    - cooldown: 告警冷却（秒）
    """

    _HISTORY_WINDOW = 3.0  # 轨迹速度计算窗口（秒）
    _CANDIDATE_TTL = 5.0   # candidate 最长存活（秒），超时复位

    def __init__(self, rule: dict[str, Any], fps: int) -> None:
        self.fps = max(1, int(fps))
        self.cooldown = int(rule.get("cooldown", 60))
        self.move_speed_thr = float(rule.get("move_speed_thr", 0.12))
        self.disappear_seconds = float(rule.get("disappear_seconds", 1.5))
        # 水池多边形（归一化顶点）；空 → 不校验消失位置（记一次警告）
        raw = rule.get("pool_roi") or []
        self.pool_roi: list[tuple[float, float]] = [
            (float(p[0]), float(p[1])) for p in raw if len(p) >= 2
        ]
        self._state = "idle"  # idle / candidate
        self._hist: list[tuple[float, float, float]] = []  # (cx, cy, ts) 归一化
        self._candidate_since = 0.0
        self._last_hit_ts = 0.0
        self._last_alarm_at = -float("inf")
        # 告警落库置信度回退（消失触发时 dets 为空）
        self.episode_max_conf = 0.0
        # 供画面叠加/调试
        self.last_speed = 0.0
        self.last_state = "idle"

    def feed(self, detections: list[Any], ts: float, frame_w: int = 0, frame_h: int = 0) -> bool:
        """喂入一帧 person 检测结果（俯视 COCO 框），返回是否告警。

        detections 的 bbox 为像素坐标；内部统一归一化到 0~1 后做轨迹
        与水池 ROI 判定（frame_w/frame_h 缺失时按已归一化处理）。
        """
        if frame_w <= 0:
            frame_w = frame_h or 1  # 兜底：无宽度假定输入已归一化
        det = self._select_target(detections, frame_w, frame_h)
        if det is not None:
            cx = (det.bbox[0] + det.bbox[2]) / 2.0 / frame_w
            cy = (det.bbox[1] + det.bbox[3]) / 2.0 / frame_h
            fresh = not self._hist  # 新轨迹首帧：置信度基准重新起算
            self._hist.append((cx, cy, ts))
            self._last_hit_ts = ts
            if fresh:
                self.episode_max_conf = float(det.conf)
            else:
                self.episode_max_conf = max(self.episode_max_conf, float(det.conf))
            # 滑窗裁剪
            cutoff = ts - self._HISTORY_WINDOW
            while self._hist and self._hist[0][2] < cutoff:
                self._hist.pop(0)
            speed = self._trail_speed()
            self.last_speed = speed
            if self._state == "idle":
                if speed >= self.move_speed_thr and len(self._hist) >= 2:
                    self._state = "candidate"
                    self._candidate_since = ts
            elif self._state == "candidate" and ts - self._candidate_since > self._CANDIDATE_TTL:
                # 一直可见（泳客巡航）→ 放弃本轮
                self._reset_track_state()
        else:
            # 无命中：candidate 态检查是否已"消失"足够久
            if (
                self._state == "candidate"
                and self._hist
                and ts - self._last_hit_ts >= self.disappear_seconds
            ):
                last_cx, last_cy, _ = self._hist[-1]
                in_pool = self._in_pool(last_cx, last_cy)
                # 告警置信度须在 reset 前取（消失触发时本轮 episode 峰值）
                peak_conf = self.episode_max_conf
                self._reset_track_state()
                if not in_pool:
                    return False  # 消失点在池外（离场/出画）→ 不是跳水
                if ts - self._last_alarm_at < self.cooldown:
                    return False
                self._last_alarm_at = ts
                self.episode_max_conf = peak_conf  # 供 _raise_alarm 回退读取
                return True
        self.last_state = self._state
        return False

    def _reset_track_state(self) -> None:
        self._state = "idle"
        self._hist = []
        self.episode_max_conf = 0.0

    def _trail_speed(self) -> float:
        """近窗口轨迹路径长度 / 时长（画面宽/秒，归一化单位）。"""
        pts = self._hist
        if len(pts) < 2:
            return 0.0
        dt = pts[-1][2] - pts[0][2]
        if dt < 0.25:
            return 0.0
        dist = 0.0
        for i in range(1, len(pts)):
            dist += ((pts[i][0] - pts[i - 1][0]) ** 2 + (pts[i][1] - pts[i - 1][1]) ** 2) ** 0.5
        return dist / dt

    def _in_pool(self, x: float, y: float) -> bool:
        """射线法点-多边形判定；未标定 ROI 时恒 True（宽松模式）。"""
        poly = self.pool_roi
        if len(poly) < 3:
            return True
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                inside = not inside
            j = i
        return inside

    def _select_target(self, detections: list[Any], frame_w: int = 1, frame_h: int = 0) -> Any:
        """选跟踪目标：优先离上一命中位置最近（≤0.3 归一化距离）者，
        否则取置信度最高（新目标出现）。"""
        if not detections:
            return None
        if self._hist:
            lx, ly, _ = self._hist[-1]
            best, best_d = None, 1e9
            for d in detections:
                cx = (d.bbox[0] + d.bbox[2]) / 2.0 / frame_w
                cy = (d.bbox[1] + d.bbox[3]) / 2.0 / (frame_h or frame_w)
                dist = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
                if dist < best_d:
                    best, best_d = d, dist
            if best is not None and best_d <= 0.3:
                return best
        return max(detections, key=lambda d: d.conf)


class ClimbingDetector:
    """攀爬/翻越判定器（detector=climbing，绑定 pose 模型）。

    适用：翻越围栏、桥边、湖岸石堤等"手撑上方+身体上抬"动作。
    信号源（真实攀爬素材逐帧标定，Mixkit 4 段）：

    通道A 姿态（主，侧拍/斜俯可靠）：
      - hand_over = (鼻y - 腕最低y)/画面高：攀爬期持续 ≥0.2（手抓上方
        支撑物举过头；实测翻墙 -20%、抱石 +22%），挥手/拉伸为瞬时波动
        → 要求持续 ≥ hand_dur 秒；
      - 辅助确认（任一）：leg_rise 膝踝高于臀（翻墙实测稳定；抱石场景
        臀也抬起会失效，故只作确认不作必要条件）或 cy 重心上升 ≥cy_rise
        或 硬时长 ≥ hard_dur 秒（抱石 8s+ 连续手过头）。
      流程：hand_over 达标持续 hand_dur → climbing 态；climbing 态内
      出现任一确认信号 → 告警。中途手放下>容忍窗（闪烁）→ 复位。

    通道B 轨迹（正俯视机位用，姿态不可靠时兜底）：
      - "接近→扒边→越过"轨迹：高速接近（≥approach_speed）后目标骤停
        驻留（速度跌到 1/3 以下且停住 ≥dwell_seconds，扒着边）→告警；
        或高速后目标消失 ≥disappear_seconds（翻过去出画）→告警。
      - 泳客巡航不会"高速后骤停驻留"；游泳者速度平缓无接近段。

    通道A/B 独立计时，任一成立即告警（共享冷却）。

    预留（方案四·警戒线，默认关闭）：rule.alarm_line = [[x1,y1],[x2,y2]]
    （归一化线段）。配置后：确认事件（告警）要求目标当前/最后位置与
    轨迹起点分居线两侧（即真正"越过去了"）。空=不校验。

    规则参数（rule JSON）：
    - hand_over_thr: 手举过头阈值(占画面高比例)，默认 0.12
    - hand_dur: 手过头需持续秒数，默认 1.2
    - hard_dur: 仅手过头也告警的硬时长(秒)，默认 6.0
    - cy_rise_frac: 重心上升确认阈值，默认 0.06
    - dwell_seconds: 俯视高速后骤停驻留确认秒数，默认 2.0
    - disappear_seconds: 俯视高速后消失确认秒数，默认 1.2
    - approach_speed: 俯视接近速度阈值(画面宽/秒)，默认 0.12
    - alarm_line: 越线确认线段（预留），默认 []
    - cooldown: 冷却(秒)
    """

    _KPT_CONF_MIN = 0.3
    _HISTORY_WINDOW = 3.0
    _HAND_GAP_TOL = 1.0  # 手信号中断容忍（攀爬小目标 pose 闪烁常见；
                         # 游泳靠躯干竖直门控排除，放宽此值不影响特异度）

    def __init__(self, rule: dict[str, Any], fps: int) -> None:
        self.fps = max(1, int(fps))
        self.cooldown = int(rule.get("cooldown", 30))
        self.hand_over_thr = float(rule.get("hand_over_thr", 0.3))
        self.hand_dur = float(rule.get("hand_dur", 1.2))
        # 2.2s：无人机素材标定——正样本最长与门连续段 2.4~4.2s，负样本
        # （跳水/游泳）最长仅 1.0s，取中间值兼顾召回与特异度
        self.hard_dur = float(rule.get("hard_dur", 2.2))
        self.upright_max_deg = float(rule.get("upright_max_deg", 60.0))
        # 腕被遮挡时用肘点兜底（贴墙攀爬腕常不可见）
        self.arm_uses_elbow = bool(rule.get("arm_uses_elbow", True))
        self.cy_rise_frac = float(rule.get("cy_rise_frac", 0.06))
        self.dwell_seconds = float(rule.get("dwell_seconds", 2.0))
        self.disappear_seconds = float(rule.get("disappear_seconds", 1.2))
        self.approach_speed = float(rule.get("approach_speed", 0.12))
        line = rule.get("alarm_line") or []
        self.alarm_line: list[tuple[float, float]] = [
            (float(p[0]), float(p[1])) for p in line if len(p) >= 2
        ]
        # ── 通道A 状态 ──
        self._hand_since: float | None = None   # 本轮手过头起点
        self._hand_last: float = -9.0           # 最近一次手过头帧 ts
        self._hit_dur = 0.0                     # 本轮"姿态命中"累计时长（闪烁段不计）
        self._prev_pose_ts: float | None = None # 上一帧姿态时刻（算连续命中段）
        self._climbing = False
        self._leg_hits = 0      # 近 5 帧内 leg_rise 命中数（持续收腿才可信）
        self._leg_window: list[bool] = []
        self._cy_ref: float | None = None       # 本轮起点重心 y（归一化）
        self._cy_risen = False
        # ── 通道B 状态 ──
        self._hist: list[tuple[float, float, float]] = []  # (cx, cy, ts)
        self._last_hit_ts = -9.0
        self._approached_at: float | None = None  # 高速段起点
        self._slow_since: float | None = None     # 骤停起点
        self._last_alarm_at = -float("inf")
        self.episode_max_conf = 0.0
        # 调试
        self.last_hand = 0.0
        self.last_speed = 0.0
        self.last_state = ""
        self.last_alarm_reason = ""  # 本轮告警触发通道（诊断用）

    # ── 特征计算 ─────────────────────────────────────────

    def _hand_over(self, det: Any, frame_h: int) -> float | None:
        """(肩中点y - 最高腕y)/躯干长（欧氏，肩中点→髋中点）。正值=手举过肩。

        尺度不变设计（无人机航拍标定教训，2026-09-24）：旧版以"画面高"
        归一，航拍人只占画面 35~55%，举手绝对值被稀释到 0.08~0.13 恒低于
        阈值 → 两段真实无人机攀爬素材全漏。改用躯干长归一后攀爬 0.3~2.2、
        游泳即使划臂过肩也被"躯干竖直"与门排除（0% 命中）。
        取"最高腕"（攀爬总有一只手在上方抓握），另一手垂下不再拉低均值。
        """
        kp = getattr(det, "keypoints", None)
        if not kp or len(kp) < 13 or frame_h <= 0:
            return None
        sh = [kp[5], kp[6]]
        hip = [kp[11], kp[12]]
        if min(s[2] for s in sh) < ClimbingDetector._KPT_CONF_MIN:
            return None
        if min(x[2] for x in hip) < ClimbingDetector._KPT_CONF_MIN:
            return None
        smx = (sh[0][0] + sh[1][0]) / 2.0
        smy = (sh[0][1] + sh[1][1]) / 2.0
        hmx = (hip[0][0] + hip[1][0]) / 2.0
        hmy = (hip[0][1] + hip[1][1]) / 2.0
        tl = ((smx - hmx) ** 2 + (smy - hmy) ** 2) ** 0.5
        if tl < 15:  # 躯干过短（远景噪声框）不可信
            return None
        wrists = [kp[i] for i in (9, 10) if kp[i][2] >= ClimbingDetector._KPT_CONF_MIN]
        # 腕被墙/岩体遮挡时（贴墙攀爬常见）用肘点(7/8)兜底——实测抱石墙
        # 素材腕遮挡使与门占比 35%→61%；跳水/游泳负样本加肘后最长与门
        # 连续段仍仅 1.0s/0.2s，安全边际不变
        if self.arm_uses_elbow:
            wrists += [kp[i] for i in (7, 8) if kp[i][2] >= ClimbingDetector._KPT_CONF_MIN]
        if not wrists:
            return None
        return (smy - min(w[1] for w in wrists)) / tl

    @staticmethod
    def _leg_rise(det: Any, frame_h: int) -> bool:
        """膝/踝任一点高于臀（翻墙抬腿特征）。"""
        kp = getattr(det, "keypoints", None)
        if not kp or len(kp) < 17 or frame_h <= 0:
            return False
        hips = [kp[i] for i in (11, 12) if kp[i][2] >= ClimbingDetector._KPT_CONF_MIN]
        legs = [kp[i] for i in (13, 14, 15, 16) if kp[i][2] >= ClimbingDetector._KPT_CONF_MIN]
        if not hips or not legs:
            return False
        return min(l[1] for l in legs) < min(h[1] for h in hips) - 0.01 * frame_h

    def feed(self, detections: list[Any], ts: float, frame_w: int = 1, frame_h: int = 0) -> bool:
        """喂入 pose 检测结果（像素坐标），返回是否告警。"""
        if frame_w <= 0:
            frame_w = frame_h or 1
        if frame_h <= 0:
            frame_h = frame_w
        det = self._select_target(detections, frame_w, frame_h)
        alarm_reason = ""
        if det is not None:
            cx = (det.bbox[0] + det.bbox[2]) / 2.0 / frame_w
            cy = (det.bbox[1] + det.bbox[3]) / 2.0 / frame_h
            self._hist.append((cx, cy, ts))
            if len(self._hist) > 60:
                self._hist.pop(0)
            cutoff = ts - self._HISTORY_WINDOW
            while self._hist and self._hist[0][2] < cutoff:
                self._hist.pop(0)
            self._last_hit_ts = ts
            self.episode_max_conf = max(self.episode_max_conf, float(det.conf))

            # ── 通道A：姿态（手过头 AND 躯干竖直 同帧与门）──
            # 实测分布（5fps 采样占比）：4 段攀爬 torso 竖直 65~95%、
            # hand_over≥0.12 38~85%；游泳 hand 46% 但 torso 竖直仅 14%
            # （横躺划臂手会瞬时过肩）。两信号同帧成立才累计，游泳被躯干
            # 维度排除，扶栏站立（竖直但手不过头）被 hand 维度排除。
            hand = self._hand_over(det, frame_h)
            self.last_hand = hand or 0.0
            torso = DivingDetector._torso_angle(det)
            upright = torso is None or torso <= self.upright_max_deg
            climb_pose = hand is not None and hand >= self.hand_over_thr and upright
            if climb_pose:
                # 新一轮判定：上轮已复位 或 中断超容忍窗
                if self._hand_since is None or ts - self._hand_last > self._HAND_GAP_TOL:
                    self._hand_since = ts
                    self._hit_dur = 0.0
                    self._prev_pose_ts = None
                    self._climbing = False
                    self._leg_window = []
                    self._leg_hits = 0
                    self._cy_ref = cy
                    self._cy_risen = False
                # 命中时长只累计"连续姿态段"（单帧间隔 >1.2s 的漏检段不计），
                # 防"踩水挥手"等稀疏命中靠墙上时间混进 hard_dur
                gap = ts - self._prev_pose_ts if self._prev_pose_ts is not None else 0.0
                self._hit_dur += min(gap, 0.5)
                self._prev_pose_ts = ts
                self._hand_last = ts
                dur = ts - self._hand_since
                if self._cy_ref is not None and self._cy_ref - cy >= self.cy_rise_frac:
                    self._cy_risen = True
                self._leg_window.append(self._leg_rise(det, frame_h))
                if len(self._leg_window) > 5:
                    self._leg_window.pop(0)
                self._leg_hits = sum(1 for x in self._leg_window if x)
                if dur >= self.hand_dur:
                    self._climbing = True
                # 告警（用连续命中时长 hit_dur 作时序证据，墙上 dur 只作
                # 粗门槛）：持续抬腿（翻墙收腿，硬特征）→ hand_dur 即报；
                # 否则需 hit_dur ≥ hard_dur（扒挂类弱位移持续攀爬）。
                # 不用 cy_risen 作确认（检测 y 抖动会误触发，踩水场景）。
                if self._climbing and (
                    self._leg_hits >= 3 or self._hit_dur >= self.hard_dur
                ) and self._cross_ok(cx, cy):
                    alarm_reason = "posture"
            elif self._hand_since is not None and ts - self._hand_last > self._HAND_GAP_TOL:
                # 姿态消失超过容忍窗 → 本轮攀爬结束
                self._reset_pose_episode()

            # ── 通道B：轨迹（高速接近→骤停驻留）──
            speed = self._trail_speed()
            self.last_speed = speed
            if self._approached_at is None and speed >= self.approach_speed and len(self._hist) >= 3:
                self._approached_at = ts
            if self._approached_at is not None:
                if speed <= self.approach_speed / 3:
                    if self._slow_since is None:
                        self._slow_since = ts
                    elif ts - self._slow_since >= self.dwell_seconds and self._cross_ok(cx, cy):
                        alarm_reason = alarm_reason or "dwell"
                else:
                    self._slow_since = None

        else:
            # 无检测帧：通道B 不做 vanish 确认——"高速后消失"与跳水入水/
            # 目标丢失本质不可分（实测跳水视频在此误报），翻越出画场景
            # 由 dwell（急停扒边驻留）覆盖；待接入方案四警戒线后，
            # 再启用"越线一侧消失"作为补充确认。
            pass

        self.last_state = ("climbing" if self._climbing else "") + (
            "/approached" if self._approached_at is not None else ""
        )

        if alarm_reason:
            self.last_alarm_reason = alarm_reason
            peak = self.episode_max_conf
            self._reset_all()
            if ts - self._last_alarm_at < self.cooldown:
                return False
            self._last_alarm_at = ts
            self.episode_max_conf = peak  # 供 _raise_alarm 回退读取
            return True
        return False

    def _cross_ok(self, x: float, y: float) -> bool:
        """预留方案四：警戒线未配置时恒 True；配置后要求当前点与
        轨迹起点分居线两侧（叉积符号相反 = 已越过）。"""
        if len(self.alarm_line) < 2:
            return True
        (x1, y1), (x2, y2) = self.alarm_line[0], self.alarm_line[1]
        cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
        if len(self._hist) >= 2:
            sx, sy, _ = self._hist[0]
            s_cross = (x2 - x1) * (sy - y1) - (y2 - y1) * (sx - x1)
            return cross * s_cross < 0
        return False

    def _trail_speed(self) -> float:
        pts = self._hist
        if len(pts) < 2:
            return 0.0
        dt = pts[-1][2] - pts[0][2]
        if dt < 0.25:
            return 0.0
        dist = 0.0
        for i in range(1, len(pts)):
            dist += ((pts[i][0] - pts[i - 1][0]) ** 2 + (pts[i][1] - pts[i - 1][1]) ** 2) ** 0.5
        return dist / dt

    def _reset_pose_episode(self) -> None:
        self._hand_since = None
        self._climbing = False
        self._leg_window = []
        self._leg_hits = 0
        self._cy_ref = None
        self._cy_risen = False

    def _reset_all(self) -> None:
        self._reset_pose_episode()
        self._hand_last = -9.0
        self._hist = []
        self._approached_at = None
        self._slow_since = None
        self.episode_max_conf = 0.0

    def _select_target(self, detections: list[Any], frame_w: int = 1, frame_h: int = 0) -> Any:
        """选跟踪目标：优先与上一命中位置最近（≤0.3 归一化）者，否则最高 conf。"""
        if not detections:
            return None
        fh = frame_h or frame_w
        if self._hist:
            lx, ly, _ = self._hist[-1]
            best, best_d = None, 1e9
            for d in detections:
                cx = (d.bbox[0] + d.bbox[2]) / 2.0 / frame_w
                cy = (d.bbox[1] + d.bbox[3]) / 2.0 / fh
                dist = ((cx - lx) ** 2 + (cy - ly) ** 2) ** 0.5
                if dist < best_d:
                    best, best_d = d, dist
            if best is not None and best_d <= 0.3:
                return best
        return max(detections, key=lambda d: d.conf)


@dataclass(slots=True)
class WorkerStats:
    """worker 运行指标（供 /tasks/{id}/stats）。"""

    frames_read: int = 0
    frames_processed: int = 0
    fps_actual: float = 0.0
    inference_ms: float = 0.0
    drop_rate: float = 0.0
    last_alarm_at: float = 0.0


class StreamWorker:
    """单视频源推理 worker（线程）。

    启动后持续抓帧 → 推理 → 检测器判定 → 告警落库，直到 ``stop()`` 被调用。

    源类型（``source_type``）：
    - ``camera``：RTSP 实时流，断线指数退避重连；
    - ``video``：本地视频文件（测试/演示），播完自动回绕循环播放。
    """

    def __init__(
        self,
        camera_id: int,
        rtsp_url: str,
        source_type: str = "camera",
        engine: InferenceEngine | None = None,
        event_listener: Any | None = None,
        uploads_dir: str | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.rtsp_url = rtsp_url
        self.source_type = source_type if source_type in ("camera", "video") else "camera"
        self.engine = engine or get_engine()
        self.event_listener = event_listener
        # uploads 根目录：默认 backend/uploads/（统一由 paths.py 管理）
        if uploads_dir is not None:
            self.uploads_dir = uploads_dir
        else:
            from src.plugins.builtin.ai_vision.paths import UPLOADS_ROOT

            self.uploads_dir = str(UPLOADS_ROOT)

        self._tasks: dict[int, TaskBinding] = {}
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()
        self._lock = threading.RLock()
        # 模型句柄缓存：model_id → (handle, resolved_path, cat_map_json)
        # 热路径优化：_process_frame 每帧调用 _load_model，避免每帧开
        # SQLite session 查库；引擎层已按 mtime+size 失效模型文件缓存，
        # 此处缓存的是「DB 记录 → 路径/类别映射」的解析结果。
        # 记录变更（重新导出/换文件）通过 _refresh_model_handles() 失效。
        self._model_cache: dict[int, tuple[Any, str, str]] = {}
        self.stats = WorkerStats()
        self._worker_id = f"cam-{camera_id}"
        self._video_fps = 0.0  # 视频源原始帧率（用于实时播放节流）
        self._video_ts = 0.0  # 视频源当前播放位置（秒；RTSP 源恒 0）

        # 断线重连状态
        self._reconnect_delay = _RECONNECT_BASE
        self._offline_since: float | None = None
        self._state = "unknown"  # unknown/online/offline
        self._last_frame: Any = None  # 最近一次成功帧（供抓拍图使用）
        self._last_frame_ts = 0.0
        # 最近一次推理的检测框（归一化坐标），供监控台实时画面叠加画框
        self._det_lock = threading.Lock()
        self._last_detections: list[dict] = []
        self._last_det_ts = 0.0

    # ── 任务绑定 ─────────────────────────────────────────────
    def add_task(self, task: TaskBinding) -> None:
        with self._lock:
            self._tasks[task.task_id] = task

    def remove_task(self, task_id: int) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def task_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def snapshot_tasks(self) -> list[TaskBinding]:
        with self._lock:
            return list(self._tasks.values())

    # ── 实时帧读取（监控台 MJPEG 流用）────────────────────
    def latest_frame(self) -> tuple[Any, float, float]:
        """返回 (最近帧副本, 采集时刻 epoch 秒, 视频源播放位置秒)。

        - 引用赋值在 GIL 下是原子的，copy 后调用方可安全跨线程使用；
        - RTSP 源 video_ts 恒 0；
        - 尚无帧时返回 (None, 0.0, 0.0)。
        """
        frame = self._last_frame
        if frame is None:
            return None, 0.0, 0.0
        try:
            return frame.copy(), self._last_frame_ts, self._video_ts
        except Exception:  # noqa: BLE001
            return None, 0.0, 0.0

    def latest_detections(self, max_age: float = 2.5) -> list[dict]:
        """最近一次推理的检测框（归一化坐标），供 MJPEG 流在画面上叠加画框。

        每项：{x1,y1,x2,y2 (0~1), label}。超过 max_age 秒未更新（无命中/
        任务已停止）返回空列表。
        """
        with self._det_lock:
            if not self._last_detections or time.time() - self._last_det_ts > max_age:
                return []
            return list(self._last_detections)

    # ── 启停 ─────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"stream-{self.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def state(self) -> str:
        return self._state

    # ── 主循环 ───────────────────────────────────────────────
    def _run(self) -> None:
        self._emit(WORKER_STARTED, {"camera_id": self.camera_id})
        try:
            self._loop()
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[AIVision] worker {self._worker_id} error: {exc}")
            self._emit(WORKER_ERROR, {"error": str(exc)})
        finally:
            self._emit(WORKER_STOPPED, {"camera_id": self.camera_id})

    def _loop(self) -> None:
        ensure_infer_runtime()
        import cv2

        cap = None
        frames_read = 0
        frames_processed = 0
        # 节流计时（上次处理帧的时刻）与统计窗口起点分离：
        # window_start 若兼任节流基准，会在每次处理后重置，
        # 导致 1 秒统计窗口永远凑不满，stats 恒为 0。
        last_process_at = time.monotonic()
        stats_window_start = time.monotonic()
        window_frames = 0
        window_processed = 0
        target_fps = self._max_fps()
        last_target = target_fps
        min_interval = 1.0 / max(1, target_fps)
        video_read_interval = 0.0  # 视频源实时播放节奏（打开 capture 后更新）
        last_read_at = 0.0  # 视频源实时播放节奏基准

        while not self._stop_flag.is_set():
            # 重新打开连接（首次或断线重连）
            if cap is None or not cap.isOpened():
                cap = self._open_capture(cv2)
                if cap is None:
                    self._mark_read_failure()
                    delay = self._reconnect_delay
                    self._reconnect_delay = min(self._reconnect_delay * 2, _RECONNECT_MAX)
                    if self._stop_flag.wait(timeout=delay):
                        break
                    continue
                # 打开成功后按视频原始帧率设置实时播放间隔
                if self.source_type == "video" and self._video_fps > 0:
                    video_read_interval = 1.0 / self._video_fps

            # 视频源节流：按原始帧率实时播放，否则 read() 全速快进、CPU 空转
            if self.source_type == "video" and self._video_fps > 0:
                now = time.monotonic()
                wait = video_read_interval - (now - last_read_at)
                if wait > 0:
                    if self._stop_flag.wait(timeout=wait):
                        break
                    continue
                last_read_at = now

            ok, frame = cap.read()
            if not ok or frame is None:
                # 视频文件源：播到末尾 → 回绕循环（测试/演示友好）
                if self.source_type == "video":
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                # RTSP 源：视为断线，走重连
                self._mark_read_failure()
                cap.release()
                cap = None
                continue

            frames_read += 1
            window_frames += 1
            self._mark_read_ok()
            self._last_frame = frame
            self._last_frame_ts = time.time()
            # 视频源：记录当前播放位置（秒），供告警时间线跳转回看
            if self.source_type == "video":
                try:
                    self._video_ts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                except Exception:  # noqa: BLE001
                    pass

            # 帧率节流：按当前最大任务 fps 计算最小间隔
            target_fps = self._max_fps()
            if target_fps != last_target:
                min_interval = 1.0 / max(1, target_fps)
                last_target = target_fps
            now = time.monotonic()
            if now - last_process_at < min_interval:
                continue  # 节流：跳过本帧
            last_process_at = now

            frames_processed += 1
            window_processed += 1
            self._process_frame(frame)

            # 统计（1 秒窗口，独立于节流计时）
            elapsed = now - stats_window_start
            if elapsed >= 1.0:
                self.stats.frames_read = frames_read
                self.stats.frames_processed = frames_processed
                self.stats.fps_actual = round(window_processed / elapsed, 2)
                self.stats.drop_rate = round(
                    max(0.0, 1.0 - (window_processed / max(1, window_frames))), 4
                )
                window_frames = 0
                window_processed = 0
                stats_window_start = now

        if cap is not None:
            cap.release()

    def _process_frame(self, frame: Any) -> None:
        """推理分发：模型去重 → 逐事件判定 → 告警落库 → 更新画面叠加框。"""
        tasks = self.snapshot_tasks()
        if not tasks:
            with self._det_lock:
                self._last_detections = []
                self._last_det_ts = time.time()
            return

        # 模型去重：model_id → ModelHandle（同一摄像头多事件共享一个推理）
        handles: dict[int, Any] = {}
        for task in tasks:
            for mid in task.model_ids:
                if mid not in handles:
                    handles[mid] = self._load_model(mid)

        all_boxes: list[dict] = []
        for task in tasks:
            try:
                all_boxes.extend(self._process_event(task, frame, handles))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AIVision] task {task.task_id} frame error: {exc}")

        # 供监控台实时画面叠加画框（归一化坐标，MJPEG 编码时还原像素位置）
        with self._det_lock:
            self._last_detections = all_boxes
            self._last_det_ts = time.time()

    def _process_event(self, task: TaskBinding, frame: Any, handles: dict[int, Any]) -> list[dict]:
        """单事件处理：收集所有绑定模型的检测结果 → 过滤 → 判定 → 告警。

        返回本事件在当前帧上的检测框（归一化 0~1 坐标 + 标签 + 可选关键点），
        供上层在实时画面上叠加绘制。

        判定方式由 ``rule.detector`` 决定：
        - 缺省/``object``：EventDetector（目标检测 + 投票）
        - ``diving``：DivingDetector（pose 关键点 + 躯干倾角状态机），
          仅对 pose 模型推理；非 pose 模型绑定会被跳过并告警日志。
        - ``diving_top``：OverheadDivingDetector（高空俯视：检测框轨迹
          速度 + 水池 ROI + 突然消失），绑定 COCO 检测模型（yolo11n-coco）。
        """
        rule = task.rule or {}
        threshold = float(rule.get("threshold", 0.45))
        mode = str(rule.get("detector", "object"))
        pp = PostProcessor(
            roi=rule.get("roi", []),
            min_size=float(rule.get("min_size", 20)),
            threshold=threshold,
        )
        if task.detector is None:
            if mode == "diving":
                task.detector = DivingDetector(rule, task.analyze_fps)
            elif mode == "diving_top":
                task.detector = OverheadDivingDetector(rule, task.analyze_fps)
            elif mode == "climbing":
                task.detector = ClimbingDetector(rule, task.analyze_fps)
            else:
                task.detector = EventDetector(rule, task.analyze_fps)

        fh, fw = frame.shape[:2]

        # ── pose 路径：跳水动作识别 / 攀爬翻越 ──────────────────
        if isinstance(task.detector, (DivingDetector, ClimbingDetector)):
            pose_handles = [
                handles.get(mid) for mid in task.model_ids
            ]
            pose_handles = [h for h in pose_handles if h is not None and getattr(h, "is_pose", False)]
            if not pose_handles and not getattr(self, "_pose_warned", False):
                logger.warning(
                    f"[AIVision] task {task.task_id}: {mode} 判定需要绑定 pose 模型（如 yolo11n-pose），当前绑定无效"
                )
                self._pose_warned = True

            pose_dets: list[Any] = []
            for handle in pose_handles:
                cat_filter = {
                    cid for cid, cat in handle.category_map.items() if cat in set(task.category_codes)
                } or None
                try:
                    pose_dets.extend(
                        self.engine.detect_pose(frame, handle, threshold=threshold, classes_filter=cat_filter)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AIVision] pose detect failed (task {task.task_id}): {exc}")

            # ROI / 最小尺寸过滤（PoseDetection 有 bbox 属性，复用 PostProcessor）
            pose_dets = pp.filter(pose_dets)

            boxes = [
                self._pose_box_dict(d, fw, fh)
                for d in sorted(pose_dets, key=lambda x: x.conf, reverse=True)[:3]
            ]
            if isinstance(task.detector, ClimbingDetector):
                if task.detector.feed(pose_dets, time.time(), frame_w=fw, frame_h=fh):
                    self._raise_alarm(task, pose_dets, action_label="climbing")
            else:
                if task.detector.feed(pose_dets, time.time(), frame_h=fh):
                    self._raise_alarm(task, pose_dets, action_label="diving")
            return boxes

        # ── 俯视跳水路径（diving_top）：COCO person 框轨迹 ──────────
        if isinstance(task.detector, OverheadDivingDetector):
            det_handles = [handles.get(mid) for mid in task.model_ids]
            # 优先非 pose 的 COCO 检测模型；若只绑了 pose 模型也能用其 person 框
            det_handles = [h for h in det_handles if h is not None]
            if not det_handles and not getattr(self, "_top_warned", False):
                logger.warning(
                    f"[AIVision] task {task.task_id}: diving_top 需绑定目标检测模型（如 yolo11n-coco），当前绑定无效"
                )
                self._top_warned = True
            top_dets: list[Any] = []
            for handle in det_handles:
                cat_filter = {
                    cid for cid, cat in handle.category_map.items() if cat in set(task.category_codes)
                } or None
                try:
                    top_dets.extend(
                        self.engine.detect(frame, handle, threshold=threshold, classes_filter=cat_filter)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[AIVision] diving_top detect failed (task {task.task_id}): {exc}")
            # 仅最小尺寸过滤（不做 roi 过滤——轨迹起点在池外，池判定交给检测器）
            top_dets = [
                d for d in top_dets
                if (d.bbox[2] - d.bbox[0]) >= pp.min_size and (d.bbox[3] - d.bbox[1]) >= pp.min_size
            ]
            boxes = [
                {
                    "x1": max(0.0, min(1.0, d.bbox[0] / fw)),
                    "y1": max(0.0, min(1.0, d.bbox[1] / fh)),
                    "x2": max(0.0, min(1.0, d.bbox[2] / fw)),
                    "y2": max(0.0, min(1.0, d.bbox[3] / fh)),
                    "label": f"{d.cls_name} {d.conf:.2f}",
                }
                for d in sorted(top_dets, key=lambda x: x.conf, reverse=True)[:5]
            ]
            if task.detector.feed(top_dets, time.time(), frame_w=fw, frame_h=fh):
                self._raise_alarm(task, [], action_label="diving_top")
            return boxes

        # ── 目标检测路径（原有逻辑）─────────────────────────
        # 收集该事件关心的所有检测
        all_dets: list[Any] = []
        for mid in task.model_ids:
            handle = handles.get(mid)
            if handle is None:
                continue
            cat_filter = {cid for cid, cat in handle.category_map.items() if cat in set(task.category_codes)}
            if not cat_filter:
                continue
            try:
                dets = self.engine.detect(frame, handle, threshold=threshold, classes_filter=cat_filter)
                all_dets.extend(dets)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[AIVision] detect failed (task {task.task_id}): {exc}")

        all_dets = pp.filter(all_dets)

        # 归一化检测框（画面叠加层用）
        boxes = [
            {
                "x1": max(0.0, min(1.0, d.bbox[0] / fw)),
                "y1": max(0.0, min(1.0, d.bbox[1] / fh)),
                "x2": max(0.0, min(1.0, d.bbox[2] / fw)),
                "y2": max(0.0, min(1.0, d.bbox[3] / fh)),
                "label": f"{d.cls_name} {d.conf:.2f}",
            }
            for d in sorted(all_dets, key=lambda x: x.conf, reverse=True)[:5]
        ]

        if task.detector.feed(all_dets, time.time()):
            self._raise_alarm(task, all_dets)
        return boxes

    def _pose_box_dict(self, d: Any, fw: int, fh: int) -> dict:
        """PoseDetection → 归一化叠加框（附关键点，供 MJPEG 层绘制骨架）。"""
        angle = DivingDetector._torso_angle(d)  # 静态复用躯干角计算
        if angle is not None:
            angle = round(angle, 1)
        label = f"person {d.conf:.2f}"
        if angle is not None:
            label = f"person {d.conf:.2f} torso:{angle}°"
        box = {
            "x1": max(0.0, min(1.0, d.bbox[0] / fw)),
            "y1": max(0.0, min(1.0, d.bbox[1] / fh)),
            "x2": max(0.0, min(1.0, d.bbox[2] / fw)),
            "y2": max(0.0, min(1.0, d.bbox[3] / fh)),
            "label": label,
            "keypoints": [
                [max(0.0, min(1.0, p[0] / fw)), max(0.0, min(1.0, p[1] / fh)), p[2]]
                for p in (getattr(d, "keypoints", None) or [])
            ],
        }
        return box

    def _raise_alarm(self, task: TaskBinding, dets: list[Any], action_label: str = "") -> None:
        """告警落库 + 抓拍图 + 事件回调 + 告警总线发布。

        action_label: 动作识别结果标签（如 diving），附加到告警 note 字段。
        """
        try:
            snap = self._save_snapshot(task, dets, action_label=action_label)
            # 置信度：告警触发瞬间目标通常已消失（dets 为空）——跳水路径回退到
            # 本轮腾空期最高检测置信度（episode_max_conf），避免落库恒为 0.0
            if dets:
                conf = round(max(d.conf for d in dets), 4)
            else:
                conf = round(float(getattr(task.detector, "episode_max_conf", 0.0) or 0.0), 4)
            alarm = {
                "task_id": task.task_id,
                "event_id": task.event_id,
                "camera_id": self.camera_id,
                "category_code": task.category_codes[0] if task.category_codes else "",
                "confidence": conf,
                "snapshot_path": snap,
                "video_ts": round(self._video_ts, 2),
                "level": "warning",
                "status": "pending",
                "note": f"动作识别: {action_label}" if action_label else "",
            }
            alarm_id = self._db_insert_alarm(alarm)
            self.stats.last_alarm_at = time.time()
            # 总线快照：附加告警 ID 与视频时间戳（供监控台时间线跳转）
            bus_payload = {
                **alarm,
                "alarm_id": alarm_id,
                "video_ts": round(self._video_ts, 2),
                "source_type": self.source_type,
                "ts": time.time(),
            }
            try:
                from src.plugins.builtin.ai_vision.runtime.alarm_bus import publish

                publish(bus_payload)
            except Exception:  # noqa: BLE001
                pass
            self._emit(ALARM_RAISED, bus_payload)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] alarm save failed: {exc}")

    # COCO 骨架连线（关键点索引对）：用于抓拍图/实时画面绘制
    _SKELETON = [
        (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
        (11, 13), (13, 15), (12, 14), (14, 16),
    ]

    def _save_snapshot(self, task: TaskBinding, dets: list[Any], action_label: str = "") -> str:
        """画框 + 标签（pose 结果附骨架）→ JPEG（quality 85，最多 top-3）→ 绝对路径。"""
        import cv2

        frame = self._last_frame
        if frame is None:
            return ""
        img = frame.copy()
        top = sorted(dets, key=lambda d: d.conf, reverse=True)[:3]
        for d in top:
            x1, y1, x2, y2 = [int(v) for v in d.bbox]
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 165, 255), 2)
            label = f"{d.cls_name} {d.conf:.2f}"
            if action_label:
                label = f"{action_label} {d.conf:.2f}"
            cv2.putText(img, label, (x1, max(y1 - 6, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
            # pose：绘制骨架
            kpts = getattr(d, "keypoints", None)
            if kpts and len(kpts) >= 17:
                for a, b in self._SKELETON:
                    pa, pb = kpts[a], kpts[b]
                    if pa[2] >= 0.3 and pb[2] >= 0.3:
                        cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (0, 220, 130), 2)
                for p in kpts:
                    if p[2] >= 0.3:
                        cv2.circle(img, (int(p[0]), int(p[1])), 3, (0, 0, 255), -1)

        snap_dir = Path(self.uploads_dir) / "ai_vision" / "snapshots" / str(self.camera_id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = snap_dir / f"{ts}_{task.event_id}.jpg"
        cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return str(path)

    def _db_insert_alarm(self, alarm: dict) -> int | None:
        """独立同步 Session 写库（不阻塞事件循环），返回新告警 ID。"""
        from src.plugins.builtin.ai_vision.models import AIVisionAlarm
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        with sync_session() as db:
            row = AIVisionAlarm(**alarm)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id

    # ── 模型加载 ─────────────────────────────────────────────
    def _load_model(self, model_id: int) -> Any | None:
        """按模型记录加载 ONNX（带进程内缓存，命中则复用句柄）。

        热路径优化：先查 worker 级 ``_model_cache``（记录解析结果），
        命中则直接走 engine.load（其内部按 mtime+size 判定文件变化，
        未变化时 O(1) 返回缓存句柄）；仅首次（或缓存失效后）查 DB。
        """
        with self._lock:
            cached = self._model_cache.get(model_id)
        if cached is not None:
            handle, _, _ = cached
            return handle

        from src.plugins.builtin.ai_vision.models import AIVisionModel
        from src.plugins.builtin.ai_vision.runtime.syncdb import sync_session

        try:
            with sync_session() as db:
                rec = db.get(AIVisionModel, model_id)
                if not rec:
                    return None
                handle = self._load_model_from_record(rec)
                if handle is not None:
                    with self._lock:
                        self._model_cache[model_id] = (
                            handle,
                            rec.file_path,
                            rec.category_map or "{}",
                        )
                return handle
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] 模型加载失败 model#{model_id}: {exc}")
            return None

    def _refresh_model_handles(self) -> None:
        """清空 worker 级模型记录缓存（模型记录变更 / 重新导出后调用）。

        engine 层缓存按文件 mtime+size 自动失效，无需手动清理；
        此处仅清理「DB 记录 → 路径/类别映射」的解析缓存。
        """
        with self._lock:
            self._model_cache.clear()

    def _load_model_from_record(self, rec: Any) -> Any | None:
        """按 ORM 记录解析路径与类别映射并加载（不查库）。"""
        rel = rec.file_path  # 形如 assets/models/xxx.onnx
        # 解析路径：优先绝对路径；相对路径从插件根解析
        model_path = Path(rel)
        if not model_path.is_absolute():
            plugin_root = Path(__file__).resolve().parent.parent  # ai_vision/
            candidate = plugin_root / rel
            if candidate.exists():
                model_path = candidate
            else:
                # 退回到 backend 运行目录
                candidate2 = Path.cwd() / rel
                if candidate2.exists():
                    model_path = candidate2
                else:
                    logger.error(f"[AIVision] 模型文件不存在: {rel}")
                    return None
        cat_map = json.loads(rec.category_map or "{}")
        cat_map_int = {int(k): v for k, v in cat_map.items()}
        return self.engine.load(str(model_path), category_map=cat_map_int)

    # ── 抓帧辅助 ─────────────────────────────────────────────
    def _open_capture(self, cv2: Any) -> Any | None:
        """按源类型打开 VideoCapture，失败返回 None。

        - camera：RTSP 流（FFMPEG 后端 + 打开超时）
        - video：本地视频文件（路径必须存在且非目录）
        """
        if self.source_type == "video":
            path = Path(self.rtsp_url)
            if not path.is_file():
                logger.error(f"[AIVision] 视频文件不存在: {self.rtsp_url}")
                return None
            cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                logger.error(f"[AIVision] 视频文件无法打开（格式不支持）: {self.rtsp_url}")
                return None
            fps = cap.get(cv2.CAP_PROP_FPS)
            # 视频文件 read() 全速返回，记录原始帧率供主循环按实时节奏播放
            self._video_fps = float(fps) if fps and fps > 0 else 25.0
            return cap

        # camera：RTSP
        cap = cv2.VideoCapture(self.rtsp_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return None
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
        return cap

    def _mark_read_ok(self) -> None:
        if self._state in ("offline", "unknown"):
            if self._state == "offline":
                self._emit(CAMERA_RECOVERED, {"camera_id": self.camera_id})
            self._state = "online"
        self._offline_since = None
        self._reconnect_delay = _RECONNECT_BASE

    def _mark_read_failure(self) -> None:
        if self._offline_since is None:
            self._offline_since = time.time()
            self._state = "offline"
            self._emit(CAMERA_OFFLINE, {"camera_id": self.camera_id})
        elif time.time() - self._offline_since > _OFFLINE_AFTER_SECONDS:
            self._state = "offline"  # 保持离线（已发过事件）

    def _max_fps(self) -> int:
        """该摄像头全部任务的最大分析 fps。"""
        tasks = self.snapshot_tasks()
        if not tasks:
            return 2
        return max((t.analyze_fps for t in tasks), default=2)

    def _emit(self, etype: str, payload: dict) -> None:
        if self.event_listener:
            try:
                self.event_listener(RuntimeEvent(type=etype, worker_id=self._worker_id, payload=payload))
            except Exception:  # noqa: BLE001
                pass