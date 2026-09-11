"""Pydantic 请求/响应模型（Schema）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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