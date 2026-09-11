"""AI 视觉平台插件私有数据模型。

表名约定：``ai_vision_`` 前缀（插件名 snake_case + 资源名）。
所有模型继承 ``Base`` 与 ``IDMixin/TimestampMixin``，``install()`` 时建表。
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base
from src.models.mixins import IDMixin, TimestampMixin

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# 摄像头
# ---------------------------------------------------------------------------


class AIVisionCamera(IDMixin, TimestampMixin, Base):
    """视频源：RTSP 摄像头 / 本地视频文件。

    ``source_type``:
    - ``camera``：RTSP 实时流，``rtsp_url`` 存 RTSP 地址
    - ``video``：本地视频文件（测试/演示），``rtsp_url`` 存文件绝对路径
      （由上传接口 ``POST /ai-vision/videos/upload`` 生成）
    """

    __tablename__ = "ai_vision_cameras"

    name: Mapped[str] = mapped_column(String(200), comment="摄像头名称")
    source_type: Mapped[str] = mapped_column(
        String(20), default="camera", server_default="camera",
        comment="接入类型：camera=RTSP 摄像头 / video=本地视频文件",
    )
    rtsp_url: Mapped[str] = mapped_column(
        String(500), comment="RTSP 地址或视频文件绝对路径"
    )
    location: Mapped[str] = mapped_column(String(200), default="", comment="位置描述")
    vendor: Mapped[str] = mapped_column(String(100), default="", comment="厂商/型号")
    status: Mapped[str] = mapped_column(
        String(20), default="unknown", comment="unknown/online/offline"
    )
    last_online_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近在线时间"
    )
    # 软删除标记
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="软删除")


# ---------------------------------------------------------------------------
# 类别库
# ---------------------------------------------------------------------------


class AIVisionCategory(IDMixin, TimestampMixin, Base):
    """目标类别库：人/车/明火/烟雾/自定义物品。"""

    __tablename__ = "ai_vision_categories"

    code: Mapped[str] = mapped_column(String(50), unique=True, comment="类别编码 person/vehicle/fire/smoke/自定义")
    name: Mapped[str] = mapped_column(String(100), comment="显示名称")
    icon: Mapped[str] = mapped_column(String(50), default="", comment="图标名")
    source: Mapped[str] = mapped_column(String(20), default="builtin", comment="builtin/custom")
    coco_map: Mapped[dict] = mapped_column(  # noqa: UP006
        Text, default="{}", comment="JSON: 模型类别ID→平台类别 映射"
    )
    status: Mapped[int] = mapped_column(Integer, default=1, comment="1启用 0停用")
    description: Mapped[str] = mapped_column(String(500), default="", comment="描述")
    # 软删除标记
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="软删除")


# ---------------------------------------------------------------------------
# 识别事件
# ---------------------------------------------------------------------------


class AIVisionEvent(IDMixin, TimestampMixin, Base):
    """识别事件：类别 + 判定规则 + 模型绑定的配置组合。"""

    __tablename__ = "ai_vision_events"

    name: Mapped[str] = mapped_column(String(200), comment="事件名称")
    description: Mapped[str] = mapped_column(String(500), default="", comment="描述")
    category_codes: Mapped[str] = mapped_column(
        Text, default="[]", comment="JSON 数组：关联类别编码"
    )
    rule: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: threshold/duration/vote_seconds/cooldown/fps/roi/schedule"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="draft", comment="draft/ready/running/paused/pending_train"
    )
    model_ids: Mapped[str] = mapped_column(
        Text, default="[]", comment="JSON 数组：当前绑定的模型 ID 列表"
    )
    # 软删除标记
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, comment="软删除")


# ---------------------------------------------------------------------------
# 模型库
# ---------------------------------------------------------------------------


class AIVisionModel(IDMixin, TimestampMixin, Base):
    """模型文件：内置预训练 / 外训导入 / 训练产物。"""

    __tablename__ = "ai_vision_models"

    name: Mapped[str] = mapped_column(String(200), comment="模型名称")
    file_path: Mapped[str] = mapped_column(String(500), comment="ONNX 文件相对路径")
    sha256: Mapped[str] = mapped_column(String(64), default="", comment="文件哈希")
    file_size: Mapped[int] = mapped_column(Integer, default=0, comment="文件大小字节")
    category_map: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: 模型类别ID→平台类别编码 映射"
    )
    input_size: Mapped[int] = mapped_column(Integer, default=640, comment="输入边长")
    quantized: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否 INT8 量化")
    source: Mapped[str] = mapped_column(
        String(20), default="builtin", comment="builtin/imported/trained"
    )
    parent_model_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="父模型（训练谱系）"
    )
    license_note: Mapped[str] = mapped_column(String(500), default="", comment="来源与许可说明")
    version: Mapped[str] = mapped_column(String(20), default="1.0.0", comment="版本")


# ---------------------------------------------------------------------------
# 事件-模型绑定
# ---------------------------------------------------------------------------


class AIVisionEventModel(IDMixin, TimestampMixin, Base):
    """事件 ↔ 模型绑定（active/backup，支持回滚）。"""

    __tablename__ = "ai_vision_event_models"

    event_id: Mapped[int] = mapped_column(Integer, comment="事件 ID")
    model_id: Mapped[int] = mapped_column(Integer, comment="模型 ID")
    binding: Mapped[str] = mapped_column(
        String(20), default="active", comment="active/backup"
    )
    bound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="绑定时间"
    )


# ---------------------------------------------------------------------------
# 分析任务
# ---------------------------------------------------------------------------


class AIVisionTask(IDMixin, TimestampMixin, Base):
    """摄像头 × 事件 分析任务。"""

    __tablename__ = "ai_vision_tasks"

    camera_id: Mapped[int] = mapped_column(Integer, comment="摄像头 ID")
    event_id: Mapped[int] = mapped_column(Integer, comment="事件 ID")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="pending/running/stopped/degraded"
    )
    analyze_fps: Mapped[int] = mapped_column(Integer, default=2, comment="分析帧率")
    last_stats: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: fps/inference_ms/drop_rate"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近启动时间"
    )


# ---------------------------------------------------------------------------
# 告警
# ---------------------------------------------------------------------------


class AIVisionAlarm(IDMixin, TimestampMixin, Base):
    """告警记录（一期仅抓拍图，无视频片段）。"""

    __tablename__ = "ai_vision_alarms"

    task_id: Mapped[int] = mapped_column(Integer, comment="任务 ID")
    event_id: Mapped[int] = mapped_column(Integer, comment="事件 ID")
    camera_id: Mapped[int] = mapped_column(Integer, comment="摄像头 ID")
    category_code: Mapped[str] = mapped_column(String(50), comment="命中类别")
    confidence: Mapped[float] = mapped_column(Float, default=0.0, comment="置信度")
    snapshot_path: Mapped[str] = mapped_column(String(500), default="", comment="抓拍图路径")
    level: Mapped[str] = mapped_column(
        String(20), default="warning", comment="info/warning/critical"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", comment="pending/acknowledged/false_positive"
    )
    ack_by: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="确认人用户 ID")
    ack_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="确认时间"
    )
    note: Mapped[str] = mapped_column(String(500), default="", comment="备注")


# ---------------------------------------------------------------------------
# 样本库
# ---------------------------------------------------------------------------


class AIVisionSample(IDMixin, TimestampMixin, Base):
    """训练样本：告警采集 / 手工上传 / 数据集。"""

    __tablename__ = "ai_vision_samples"

    file_path: Mapped[str] = mapped_column(String(500), comment="图片路径")
    source: Mapped[str] = mapped_column(
        String(20), default="upload", comment="alarm/upload/dataset"
    )
    label_status: Mapped[str] = mapped_column(
        String(20), default="unlabeled", comment="unlabeled/labeled/skipped"
    )
    label_data: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: YOLO 标注数据"
    )
    category_code: Mapped[str] = mapped_column(String(50), default="", comment="关联类别")
    related_event_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="关联事件"
    )
    width: Mapped[int] = mapped_column(Integer, default=0, comment="图片宽")
    height: Mapped[int] = mapped_column(Integer, default=0, comment="图片高")


# ---------------------------------------------------------------------------
# 训练任务
# ---------------------------------------------------------------------------


class AIVisionTraining(IDMixin, TimestampMixin, Base):
    """训练任务：样本快照 + 超参 + 进度指标 + 产物模型。"""

    __tablename__ = "ai_vision_trainings"

    event_id: Mapped[int] = mapped_column(Integer, comment="事件 ID")
    base_model_id: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="基础模型")
    sample_ids: Mapped[str] = mapped_column(
        Text, default="[]", comment="JSON: 样本 ID 快照"
    )
    params: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: epochs/imgsz/lr"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="queued", comment="queued/running/completed/failed"
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, comment="0-100")
    metrics: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: loss 曲线 / mAP"
    )
    output_model_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="产物模型 ID"
    )
    log_path: Mapped[str] = mapped_column(String(500), default="", comment="训练日志路径")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间"
    )


# ---------------------------------------------------------------------------
# 运行环境状态
# ---------------------------------------------------------------------------


class AIVisionRuntimeStatus(IDMixin, TimestampMixin, Base):
    """运行环境：L1/L2 依赖检测与安装任务记录。"""

    __tablename__ = "ai_vision_runtime_status"

    layer: Mapped[str] = mapped_column(String(10), comment="L1/L2")
    packages: Mapped[str] = mapped_column(
        Text, default="{}", comment="JSON: 各包版本"
    )
    install_status: Mapped[str] = mapped_column(
        String(20), default="idle", comment="idle/installing/failed"
    )
    install_log: Mapped[str] = mapped_column(Text, default="", comment="安装日志")