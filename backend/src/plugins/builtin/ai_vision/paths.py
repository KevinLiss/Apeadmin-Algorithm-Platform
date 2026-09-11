"""AI 视觉插件统一路径常量。

背景：插件文件位于 ``backend/src/plugins/builtin/ai_vision/``，
插件自身的上传目录约定为 ``backend/uploads/ai_vision``（注意：不是底座
的 ``src/uploads``），由 ``Path(__file__).resolve().parents[4]`` 定位
``backend`` 根。此前 4 个文件各自重复计算，统一收敛到本模块：

- ``UPLOADS_AI_VISION_DIR``：抓拍图/样本图根目录（backend/uploads/ai_vision）
- ``snapshots_dir(camera_id)``：单摄像头抓拍图目录
- ``samples_dir()``：样本图目录
- ``ensure_dirs()``：幂等创建全部目录

注意：
- 目录可能不存在（插件首次安装前），所有调用方须容忍缺失
  （StaticFiles 挂载前检查 ``exists()``）；
- ``ensure_dirs()`` 仅在确有落盘需求时调用（抓图/上传时自动创建）。
"""
from __future__ import annotations

from pathlib import Path

# backend/ 根（ai_vision/paths.py → ai_vision → builtin → plugins → src → backend）
# 注意：本文件位于 ai_vision/ 包根，比 api/、runtime/ 下的文件浅一层，
# 因此这里是 parents[4]（api/、runtime/ 下的文件是 parents[5]）。
BACKEND_ROOT = Path(__file__).resolve().parents[4]

# 插件上传根目录：backend/uploads/ai_vision
UPLOADS_AI_VISION_DIR = BACKEND_ROOT / "uploads" / "ai_vision"

# backend/uploads（= UPLOADS_AI_VISION_DIR 的父级，语义化引用）
UPLOADS_ROOT = BACKEND_ROOT / "uploads"


def snapshots_dir(camera_id: int) -> Path:
    """单摄像头抓拍图目录：uploads/ai_vision/snapshots/{camera_id}。"""
    return UPLOADS_AI_VISION_DIR / "snapshots" / str(camera_id)


def samples_dir() -> Path:
    """样本图目录：uploads/ai_vision/samples。"""
    return UPLOADS_AI_VISION_DIR / "samples"


def ensure_dirs() -> None:
    """幂等创建插件上传目录（抓拍/样本）。"""
    snapshots = UPLOADS_AI_VISION_DIR / "snapshots"
    samples = UPLOADS_AI_VISION_DIR / "samples"
    snapshots.mkdir(parents=True, exist_ok=True)
    samples.mkdir(parents=True, exist_ok=True)
