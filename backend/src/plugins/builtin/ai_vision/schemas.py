"""Pydantic 请求/响应模型（Schema）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ── 通用 ─────────────────────────────────────────────────────

class PageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list


# ── 摄像头 ─────────────────────────────────────────────

class CameraCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="摄像头名称")
    source_type: str = Field(default="camera", description="camera=RTSP / video=本地视频文件")
    rtsp_url: str = Field(..., min_length=1, max_length=500, description="RTSP 地址或视频文件绝对路径")
    location: str = Field(default="", max_length=200)
    vendor: str = Field(default="", max_length=100)


class CameraUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: str | None = Field(default=None, description="camera/video")
    rtsp_url: str | None = Field(default=None, min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    vendor: str | None = Field(default=None, max_length=100)


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    source_type: str = "camera"
    rtsp_url: str
    location: str
    vendor: str
    status: str
    last_online_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── 类别 ─────────────────────────────────────────────────

class CategoryCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z_][a-z0-9_]*$")
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field(default="", max_length=50)
    coco_map: dict[str, int] = Field(default_factory=dict, description="模型类别ID→平台类别 映射")
    description: str = Field(default="", max_length=500)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: str | None = Field(default=None, max_length=50)
    coco_map: dict[str, int] | None = None
    description: str | None = Field(default=None, max_length=500)
    status: int | None = Field(default=None, ge=0, le=1)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    icon: str
    source: str
    status: int
    description: str
    created_at: datetime


# ── 事件 ─────────────────────────────────────────────────

class EventRule(BaseModel):
    """判定规则 JSON。"""
    threshold: float = Field(default=0.45, ge=0.0, le=1.0, description="置信度阈值")
    duration: int = Field(default=0, ge=0, description="持续时长(秒)，0=单帧即告警")
    vote_seconds: int = Field(default=0, ge=0, description="投票窗口(秒)，0=不投票")
    cooldown: int = Field(default=60, ge=0, description="告警冷却(秒)")
    fps: int = Field(default=2, ge=1, le=10, description="分析帧率")
    roi: list[list[float]] = Field(default_factory=list, description="ROI 多边形 [[x,y],...] 空=全图")
    schedule: list[dict[str, Any]] = Field(default_factory=list, description="时间计划 [{start,end},...]")
    # ── 动作识别（diving 跳水判定器，需绑定 pose 模型）──
    detector: str = Field(default="object", description="判定方式：object=目标检测 / diving=跳水动作识别")
    angle_thr: float = Field(default=55.0, ge=10.0, le=90.0, description="躯干倾角阈值(度)")
    min_air_seconds: float = Field(default=0.4, ge=0.0, le=5.0, description="最短腾空时长(秒)")
    miss_seconds: float = Field(default=0.8, ge=0.0, le=10.0, description="入水判定消失时长(秒)")
    max_air_seconds: float = Field(default=4.0, ge=1.0, le=30.0, description="腾空状态超时重置(秒)")
    descent_frac: float = Field(default=0.2, ge=0.0, le=1.0, description="入水下落确认比例(画面高占比)，0=禁用")
    head_down_frac: float = Field(default=0.02, ge=0.0, le=0.5, description="头朝下腾空信号阈值(鼻低于肩占框高比例)，跳水髋部遮挡时的兜底判定")
    # ── 高空俯视跳水（diving_top 判定器，需绑定 COCO 检测模型）──
    pool_roi: list[list[float]] = Field(default_factory=list, description="俯视水池水面多边形 [[x,y],...] 归一化0~1，≥3顶点；消失点须在其中")
    move_speed_thr: float = Field(default=0.12, ge=0.01, le=1.0, description="俯视跳水轨迹速度阈值(画面宽/秒)")
    disappear_seconds: float = Field(default=1.5, ge=0.3, le=5.0, description="俯视入水消失判定时长(秒)")
    # ── 攀爬翻越（climbing 判定器，需绑定 pose 模型）──
    hand_over_thr: float = Field(default=0.3, ge=0.05, le=1.5, description="最高腕举过肩的高度(躯干长倍数)，尺度不变指标")
    hand_dur: float = Field(default=1.2, ge=0.2, le=5.0, description="手过头需持续秒数才认定攀爬")
    hard_dur: float = Field(default=2.2, ge=1.0, le=20.0, description="仅手过头也告警的硬持续时长(秒)")
    arm_uses_elbow: bool = Field(default=True, description="腕被遮挡时用肘点兜底（贴墙攀爬腕常不可见）")
    cy_rise_frac: float = Field(default=0.06, ge=0.01, le=0.3, description="重心上升确认阈值(画面高占比)")
    dwell_seconds: float = Field(default=2.0, ge=0.5, le=8.0, description="俯视高速后骤停驻留确认秒数(扒边)")
    alarm_line: list[list[float]] = Field(default_factory=list, description="越线确认警戒线段 [[x1,y1],[x2,y2]] 归一化（方案四预留，空=不启用）")

    @model_validator(mode="after")
    def _sync_duration_vote(self) -> "EventRule":
        """duration 与 vote_seconds 同义（worker 读 vote_seconds 优先）。

        前端表单只写 duration；若不落库同步，vote_seconds 默认 0 会永远
        覆盖 duration，导致"持续时长"设置失效（单帧即告警）。
        """
        if not self.vote_seconds and self.duration:
            self.vote_seconds = self.duration
        elif not self.duration and self.vote_seconds:
            self.duration = self.vote_seconds
        return self


class EventCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    category_codes: list[str] = Field(..., min_length=1, description="关联类别编码")
    rule: EventRule = Field(default_factory=EventRule)
    model_ids: list[int] = Field(default_factory=list, description="绑定模型 ID 列表")


class EventUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)
    category_codes: list[str] | None = None
    rule: EventRule | None = None
    model_ids: list[int] | None = Field(default=None, description="绑定模型 ID 列表")


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    category_codes: list[str]
    rule: dict[str, Any]
    status: str
    model_ids: list[int]
    created_at: datetime
    updated_at: datetime


# ── 模型 ─────────────────────────────────────────────────

class ModelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    file_path: str
    sha256: str
    file_size: int
    input_size: int
    quantized: bool
    source: str
    parent_model_id: int | None
    license_note: str
    version: str
    created_at: datetime


# ── 任务 ─────────────────────────────────────────────────

class TaskCreate(BaseModel):
    camera_id: int = Field(..., ge=1)
    event_id: int = Field(..., ge=1)
    analyze_fps: int = Field(default=2, ge=1, le=10)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    camera_id: int
    event_id: int
    status: str
    analyze_fps: int
    last_stats: dict[str, Any]
    started_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ── 告警 ─────────────────────────────────────────────────

class AlarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    event_id: int
    camera_id: int
    category_code: str
    confidence: float
    snapshot_path: str
    video_ts: float = 0.0
    level: str
    status: str
    ack_by: int | None
    ack_at: datetime | None
    note: str
    created_at: datetime


# ── 样本 ─────────────────────────────────────────────────

class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    file_path: str
    source: str
    label_status: str
    category_code: str
    related_event_id: int | None
    width: int
    height: int
    created_at: datetime