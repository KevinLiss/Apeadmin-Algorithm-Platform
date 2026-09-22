"""实时监控台 API（页面归并一期）。

路由前缀：``/ai-vision/monitor``（挂载后为 ``/api/v1/ai-vision/monitor``）。

端点：
- GET /monitor/alarms-since：增量拉取告警（id > since_id），供监控台
  时间线轮询刷新（前端现有约定为轮询，见 runtime-env 页日志实现）；
- GET /monitor/status：监控台顶部指标卡 + worker 运行态快照
  （今日告警数 / 未处理数 / 活跃 worker 数 / 实时帧率）。

权限：``ai_vision:alarm:list``（监控台以告警为中心）。

说明：视频文件播放不走本模块——上传视频与抓拍图同在
``uploads/ai_vision`` 下，已由 ``plugin.register()`` 挂载的 StaticFiles
（``/api/v1/ai-vision/media``）直接服务，原生支持 HTTP Range（进度条拉拽）。
"""
from datetime import datetime, time as dtime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import (
    AIVisionAlarm,
    AIVisionCamera,
    AIVisionEvent,
)

router = APIRouter(prefix="/monitor", tags=["AI 视觉平台-实时监控台"])


def _media_url(path: str) -> str:
    """绝对路径 → 静态挂载 URL（与前端 snapshotUrl 规则一致）。"""
    if not path:
        return ""
    idx = path.replace("\\", "/").find("ai_vision/")
    if idx < 0:
        return ""
    rel = path.replace("\\", "/")[idx + len("ai_vision/"):]
    return f"{settings.API_PREFIX}/ai-vision/media/{rel}"


@router.get("/alarms-since")
async def alarms_since(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:list"))],
    since_id: int = Query(default=0, ge=0, description="只返回 id 大于该值的告警"),
    camera_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    """增量拉取告警（按 id 升序），附摄像头名/事件名/抓拍图 URL/视频时间戳来源。"""
    stmt = select(AIVisionAlarm).where(AIVisionAlarm.id > since_id)
    if camera_id:
        stmt = stmt.where(AIVisionAlarm.camera_id == camera_id)
    stmt = stmt.order_by(AIVisionAlarm.id.asc()).limit(limit)
    items = (await db.execute(stmt)).scalars().all()

    cam_ids = {a.camera_id for a in items}
    evt_ids = {a.event_id for a in items}
    cams = {
        c.id: c.name
        for c in (await db.execute(
            select(AIVisionCamera).where(AIVisionCamera.id.in_(cam_ids))
        )).scalars().all()
    } if cam_ids else {}
    evts = {
        e.id: e.name
        for e in (await db.execute(
            select(AIVisionEvent).where(AIVisionEvent.id.in_(evt_ids))
        )).scalars().all()
    } if evt_ids else {}
    # 摄像头源类型（video 源告警可跳转进度条；camera 源看抓拍图）
    cam_types = {
        c.id: (c.source_type or "camera")
        for c in (await db.execute(
            select(AIVisionCamera).where(AIVisionCamera.id.in_(cam_ids))
        )).scalars().all()
    } if cam_ids else {}

    out = []
    for a in items:
        out.append({
            "id": a.id,
            "task_id": a.task_id,
            "event_id": a.event_id,
            "camera_id": a.camera_id,
            "camera_name": cams.get(a.camera_id, ""),
            "source_type": cam_types.get(a.camera_id, "camera"),
            "event_name": evts.get(a.event_id, ""),
            "category_code": a.category_code,
            "confidence": a.confidence,
            "level": a.level,
            "status": a.status,
            "snapshot_url": _media_url(a.snapshot_path),
            "video_ts": a.video_ts or 0.0,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })
    return success_response(data={"items": out, "next_since_id": items[-1].id if items else since_id})


@router.get("/status")
async def monitor_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:list"))],
):
    """监控台指标：今日告警 / 未处理 / 活跃 worker 快照。"""
    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), dtime.min, tzinfo=timezone.utc)

    today_total = (await db.execute(
        select(func.count(AIVisionAlarm.id)).where(AIVisionAlarm.created_at >= day_start)
    )).scalar_one()
    pending_total = (await db.execute(
        select(func.count(AIVisionAlarm.id)).where(AIVisionAlarm.status == "pending")
    )).scalar_one()

    from src.plugins.builtin.ai_vision.runtime.manager import get_manager

    workers = get_manager().list_workers()
    return success_response(data={
        "today_alarms": int(today_total or 0),
        "pending_alarms": int(pending_total or 0),
        "workers": workers,
        "active_workers": len(workers),
    })


# ---------------------------------------------------------------------------
# 视频源解析：把 rtsp_url（video 源的本地绝对路径）映射为可播放的 media URL
# ---------------------------------------------------------------------------

@router.get("/sources/{camera_id}")
async def resolve_source(
    camera_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:alarm:list"))],
):
    """解析视频源的播放地址（监控台 <video> 用）。

    - video 源：``rtsp_url`` 存的是本地绝对路径，若位于
      ``uploads/ai_vision`` 下则映射为 ``/api/v1/ai-vision/media/...``
      （StaticFiles 原生支持 Range，可拖动进度条）；否则 play_url 为空；
    - camera（RTSP）源：浏览器无法直接播放 RTSP，play_url 为空，
      前端走 ``/monitor/stream/{id}`` 的 MJPEG 通道。
    """
    cam = await db.get(AIVisionCamera, camera_id)
    if not cam or cam.is_deleted:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    source_type = cam.source_type or "camera"
    play_url = ""
    if source_type == "video":
        play_url = _media_url(cam.rtsp_url)
    return success_response(data={
        "camera_id": cam.id,
        "name": cam.name,
        "source_type": source_type,
        "status": cam.status,
        "play_url": play_url,
        "rtsp_url": cam.rtsp_url if source_type == "camera" else "",
    })


# ---------------------------------------------------------------------------
# MJPEG 实时流：<img src> 无法携带 Authorization 头，token 走 query 参数
# ---------------------------------------------------------------------------

def _auth_from_query(token: str | None) -> None:
    """校验 query 参数中的 JWT（MJPEG 流专用，规则与 get_current_user 一致）。"""
    from src.core.exceptions import AuthException
    from src.core.security import decode_token

    if not token:
        raise AuthException("Missing authentication token")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise AuthException("Invalid or expired token")


@router.get("/stream/{camera_id}")
async def mjpeg_stream(
    camera_id: int,
    token: Annotated[str | None, Query(description="JWT access token（img 标签无法带 Header）")] = None,
    fps: int = Query(default=5, ge=1, le=15, description="推流帧率上限"),
):
    """摄像头实时画面 MJPEG 流（multipart/x-mixed-replace）。

    从 WorkerManager 取该摄像头 worker 的最近帧，按 fps 节流编码推送：
    - worker 运行中 → 推送带时间水印的实时帧（推理画框由前端叠加层处理，
      一期直接推原始帧 + 左上角时间/摄像头信息水印）；
    - worker 未运行 → 推送灰色占位帧（"信号未启动"）。

    注意：本端点是长连接，客户端断开（GeneratorExit）时自然结束。
    """
    _auth_from_query(token)

    from src.plugins.builtin.ai_vision.runtime.manager import get_manager

    manager = get_manager()
    interval = 1.0 / max(1, min(fps, 15))

    async def gen():
        import asyncio

        while True:
            worker = manager.get_worker(camera_id)
            frame = None
            video_ts = 0.0
            boxes: list[dict] = []
            if worker is not None:
                frame, frame_ts, video_ts = await asyncio.to_thread(worker.latest_frame)
                boxes = await asyncio.to_thread(worker.latest_detections)
            jpeg = await asyncio.to_thread(_encode_stream_frame, frame, camera_id, video_ts, boxes)
            if jpeg is None:
                return  # 编码器不可用（L1 未装）：直接结束流
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                + jpeg + b"\r\n"
            )
            await asyncio.sleep(interval)

    return StreamingResponse(
        gen(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, private", "X-Accel-Buffering": "no"},
    )


def _encode_stream_frame(frame, camera_id: int, video_ts: float, boxes: list[dict] | None = None) -> bytes | None:
    """帧 → JPEG（同步，在线程池中调用）。frame 为 None 时生成占位图。

    boxes：worker 最近一次推理的检测框（归一化坐标），直接画在帧上，
    保证画面与框严格对齐（视频文件源监控中同样走此通道）。
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if frame is None:
        # 占位帧：深灰底 + 提示文字
        img = np.full((360, 640, 3), 48, dtype=np.uint8)
        cv2.putText(img, "NO SIGNAL - task not running", (140, 180),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (160, 160, 160), 2)
        cv2.putText(img, f"camera #{camera_id}", (250, 220),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1)
    else:
        img = frame
        h, w = img.shape[:2]
        # 检测框叠加（橙色框 + 黑底标签），画在水印之下
        # COCO 骨架连线（pose 事件附带 keypoints 字段时绘制）
        skeleton = [
            (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12),
            (11, 13), (13, 15), (12, 14), (14, 16),
        ]
        for b in boxes or []:
            kpts = b.get("keypoints") or []
            if len(kpts) >= 17:
                for a, bb in skeleton:
                    pa, pb = kpts[a], kpts[bb]
                    if pa[2] >= 0.3 and pb[2] >= 0.3:
                        cv2.line(img, (int(pa[0] * w), int(pa[1] * h)),
                                 (int(pb[0] * w), int(pb[1] * h)), (0, 220, 130), 2)
                for p in kpts:
                    if p[2] >= 0.3:
                        cv2.circle(img, (int(p[0] * w), int(p[1] * h)), 3, (0, 0, 255), -1)
            x1, y1 = int(b["x1"] * w), int(b["y1"] * h)
            x2, y2 = int(b["x2"] * w), int(b["y2"] * h)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 165, 255), 2)
            label = str(b.get("label", ""))
            if label:
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                ty = max(y1 - th - 6, 0)
                cv2.rectangle(img, (x1, ty), (x1 + tw + 6, ty + th + 6), (0, 0, 0), -1)
                cv2.putText(img, label, (x1 + 3, ty + th + 2),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)
        # 左上角水印：时间 + 摄像头号（+ 视频源播放位置）
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        label = f"CAM#{camera_id}  {stamp}"
        if video_ts > 0:
            label += f"  @{int(video_ts // 60):02d}:{int(video_ts % 60):02d}"
        cv2.rectangle(img, (0, 0), (min(len(label) * 11 + 12, img.shape[1]), 26), (0, 0, 0), -1)
        cv2.putText(img, label, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    return buf.tobytes() if ok else None
