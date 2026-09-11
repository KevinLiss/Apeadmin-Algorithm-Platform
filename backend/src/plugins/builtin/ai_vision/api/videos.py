"""视频源上传 API（视频识别模块）。

路由前缀：``/ai-vision/videos``（挂载后为 ``/api/v1/ai-vision/videos``）。
权限：``ai_vision:camera:create``（与摄像头创建同权：上传即创建视频源前置步骤）。

实现要点：
- POST /videos/upload：multipart 上传视频文件，落盘到
  ``uploads/ai_vision/videos/``，返回绝对路径供「摄像头管理」页创建
  source_type=video 的视频源时填入 rtsp_url 字段；
- 白名单扩展名：mp4/avi/mkv/mov/flv/wmv/webm；
- 大小限制：500MB（本地测试视频一般远小于此）；
- 文件名规范化（防路径穿越），按日期子目录存放。
"""
import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.paths import UPLOADS_AI_VISION_DIR

router = APIRouter(prefix="/videos", tags=["AI 视觉平台-视频源"])

_ALLOWED_EXT = {".mp4", ".avi", ".mkv", ".mov", ".flv", ".wmv", ".webm"}
_MAX_SIZE = 500 * 1024 * 1024  # 500MB


def videos_dir() -> Path:
    """视频文件存放目录：uploads/ai_vision/videos。"""
    return UPLOADS_AI_VISION_DIR / "videos"


@router.post("/upload")
async def upload_video(
    file: Annotated[UploadFile, File(description="视频文件（mp4/avi/mkv/mov/flv/wmv/webm）")],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:create"))],
):
    """上传视频文件，返回绝对路径（用于创建 source_type=video 的视频源）。

    前端流程：上传 → 拿到 ``file_path`` → 创建摄像头时填入 rtsp_url，
    source_type 选 video。
    """
    # 扩展名校验
    raw_name = file.filename or "video.mp4"
    ext = Path(raw_name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的视频格式 {ext}，允许：{'/'.join(sorted(_ALLOWED_EXT))}",
        )

    # 文件名规范化：仅保留 basename + 时间戳防冲突
    safe_stem = Path(raw_name).stem
    # 过滤非法字符（防路径穿越 / 特殊字符）
    safe_stem = "".join(c for c in safe_stem if c.isalnum() or c in "-_中")[:80] or "video"
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest_dir = videos_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{ts}_{safe_stem}{ext}"

    # 流式写盘 + 大小限制
    size = 0
    try:
        with open(dest, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB 块
                size += len(chunk)
                if size > _MAX_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"视频过大（>{_MAX_SIZE // (1024 * 1024)}MB），请压缩后重试",
                    )
                f.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}") from exc

    return success_response(
        data={
            "file_path": str(dest),
            "file_name": dest.name,
            "size": size,
            "size_mb": round(size / (1024 * 1024), 2),
        },
        msg="视频上传成功",
    )


@router.get("/list")
async def list_videos(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:list"))],
):
    """列出已上传的视频文件（供前端下拉选择）。"""
    vdir = videos_dir()
    items = []
    if vdir.exists():
        for p in sorted(vdir.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file() and p.suffix.lower() in _ALLOWED_EXT:
                st = p.stat()
                items.append({
                    "file_path": str(p),
                    "file_name": p.name,
                    "size_mb": round(st.st_size / (1024 * 1024), 2),
                    "uploaded_at": int(st.st_mtime),
                })
    return success_response(data={"items": items, "total": len(items)})
