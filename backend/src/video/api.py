"""视频源接入 API 路由。

提供：
- GET  /api/v1/video/protocols - 支持的视频协议列表
- GET  /api/v1/video/gb28181/status - GB28181 服务器状态
- GET  /api/v1/video/gb28181/devices - 已注册的 GB28181 设备列表
- POST /api/v1/video/gb28181/start - 启动 GB28181 服务器
- POST /api/v1/video/gb28181/stop - 停止 GB28181 服务器
- POST /api/v1/video/onvif/discover - ONVIF 设备发现
- POST /api/v1/video/onvif/test - ONVIF 设备连通性测试
- POST /api/v1/video/onvif/ptz - ONVIF 云台控制
- POST /api/v1/video/sources - 创建视频源
- GET  /api/v1/video/sources - 活跃视频源列表
- DELETE /api/v1/video/sources/{camera_id} - 关闭视频源

所有成功响应统一包裹为 {code, msg, data} 信封。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from loguru import logger

from src.core.deps import get_current_user
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from sqlalchemy.ext.asyncio import AsyncSession

from .factory import CameraConfig, VideoFactory

router = APIRouter(prefix="/video", tags=["视频源接入"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GB28181StartRequest(BaseModel):
    """GB28181 服务器启动请求。"""

    sip_id: str = "31010000002000000001"
    sip_domain: str = "3101000000"
    sip_host: str = "0.0.0.0"
    sip_port: int = 5060


class ONVIFTestRequest(BaseModel):
    """ONVIF 测试请求。"""

    onvif_url: str
    username: str = "admin"
    password: str = "admin"


class ONVIFPTZRequest(BaseModel):
    """ONVIF PTZ 控制请求。"""

    camera_id: int
    action: str  # up/down/left/right/zoom_in/zoom_out/stop
    speed: float = 0.5
    duration: float = 1.0


class VideoSourceRequest(BaseModel):
    """视频源创建请求（按协议类型）。"""

    camera_id: int
    name: str
    protocol: str = "rtsp"
    rtsp_url: str | None = None
    gb_device_id: str | None = None
    gb_ip: str | None = None
    gb_port: int | None = None
    gb_channel: str | None = None
    onvif_url: str | None = None
    onvif_username: str | None = None
    onvif_password: str | None = None


# ---------------------------------------------------------------------------
# 协议列表
# ---------------------------------------------------------------------------


@router.get("/protocols")
async def list_protocols(
    user: Annotated[User, Depends(get_current_user)],
):
    """列出支持的视频协议。"""
    return success_response(
        data={
            "protocols": [
                {
                    "value": "rtsp",
                    "label": "RTSP/RTP",
                    "description": "传统摄像头实时流（直接拉流）",
                    "builtin": True,
                },
                {
                    "value": "gb28181",
                    "label": "GB28181 国标",
                    "description": "GB/T 28181 国标协议（设备注册+推流）",
                    "builtin": True,
                },
                {
                    "value": "onvif",
                    "label": "ONVIF",
                    "description": "ONVIF 网络视频接口标准（含 PTZ 云台）",
                    "builtin": True,
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# GB28181
# ---------------------------------------------------------------------------


@router.get("/gb28181/status")
async def gb28181_status(
    user: Annotated[User, Depends(get_current_user)],
):
    """GB28181 服务器状态。"""
    server = VideoFactory.get_gb28181_server()
    if server is None:
        return success_response(data={"running": False, "devices": []})

    return success_response(
        data={
            "running": True,
            "config": {
                "sip_id": server.config.sip_id,
                "sip_domain": server.config.sip_domain,
                "sip_host": server.config.sip_host,
                "sip_port": server.config.sip_port,
            },
            "device_count": len(server.list_devices()),
        }
    )


@router.post("/gb28181/start")
async def gb28181_start(
    body: GB28181StartRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    """启动 GB28181 服务器。"""
    try:
        server = await VideoFactory.start_gb28181_server(
            sip_id=body.sip_id,
            sip_domain=body.sip_domain,
            sip_host=body.sip_host,
            sip_port=body.sip_port,
        )
        return success_response(
            msg="GB28181 服务器已启动",
            data={
                "config": {
                    "sip_id": server.config.sip_id,
                    "sip_port": server.config.sip_port,
                }
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动失败: {e}") from e


@router.post("/gb28181/stop")
async def gb28181_stop(
    user: Annotated[User, Depends(get_current_user)],
):
    """停止 GB28181 服务器。"""
    server = VideoFactory.get_gb28181_server()
    if server is None:
        return success_response(msg="GB28181 服务器未运行")

    await server.stop()
    VideoFactory._gb_server = None
    return success_response(msg="GB28181 服务器已停止")


@router.get("/gb28181/devices")
async def gb28181_devices(
    user: Annotated[User, Depends(get_current_user)],
):
    """已注册的 GB28181 设备列表。"""
    server = VideoFactory.get_gb28181_server()
    if server is None:
        return success_response(data={"total": 0, "items": []})

    devices = server.list_devices()
    return success_response(
        data={"total": len(devices), "items": [d.to_dict() for d in devices]}
    )


# ---------------------------------------------------------------------------
# ONVIF
# ---------------------------------------------------------------------------


@router.post("/onvif/discover")
async def onvif_discover(
    user: Annotated[User, Depends(get_current_user)],
    timeout: float = 3.0,
):
    """ONVIF 设备发现（WS-Discovery 组播）。"""
    from .onvif import ONVIFClient

    devices = await ONVIFClient.discover(timeout=timeout)
    return success_response(data={"total": len(devices), "items": devices})


@router.post("/onvif/test")
async def onvif_test(
    body: ONVIFTestRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    """ONVIF 设备连通性测试。"""
    from urllib.parse import urlparse

    from .onvif import ONVIFClient

    parsed = urlparse(
        body.onvif_url if "://" in body.onvif_url else f"http://{body.onvif_url}"
    )

    client = ONVIFClient(
        host=parsed.hostname or body.onvif_url,
        port=parsed.port or 80,
        username=body.username,
        password=body.password,
    )

    connected = await client.connect()
    if not connected:
        return success_response(data={"ok": False, "message": "连接失败"})

    try:
        stream_url = await client.get_stream_uri()
        info = await client.get_device_information()
        profiles = await client.get_profiles()
        return success_response(
            data={
                "ok": True,
                "message": "连接成功",
                "stream_url": stream_url,
                "device_info": info,
                "profiles": profiles,
            }
        )
    except Exception as e:
        return success_response(data={"ok": False, "message": f"测试异常: {e}"})
    finally:
        await client.disconnect()


@router.post("/onvif/ptz")
async def onvif_ptz(
    body: ONVIFPTZRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    """ONVIF 云台控制。"""
    client = VideoFactory._onvif_clients.get(body.camera_id)
    if client is None:
        raise HTTPException(
            status_code=404,
            detail=f"摄像头 {body.camera_id} 的 ONVIF 客户端不存在，请先创建视频源",
        )

    action = body.action.lower()
    try:
        if action == "stop":
            ok = await client.stop()
        elif action == "up":
            ok = await client.continuous_move(tilt=-body.speed, timeout=body.duration)
        elif action == "down":
            ok = await client.continuous_move(tilt=body.speed, timeout=body.duration)
        elif action == "left":
            ok = await client.continuous_move(pan=-body.speed, timeout=body.duration)
        elif action == "right":
            ok = await client.continuous_move(pan=body.speed, timeout=body.duration)
        elif action == "zoom_in":
            ok = await client.continuous_move(zoom=body.speed, timeout=body.duration)
        elif action == "zoom_out":
            ok = await client.continuous_move(zoom=-body.speed, timeout=body.duration)
        else:
            raise HTTPException(status_code=400, detail=f"未知动作: {action}")

        return success_response(data={"ok": ok, "action": action})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PTZ 控制失败: {e}") from e


# ---------------------------------------------------------------------------
# 视频源管理
# ---------------------------------------------------------------------------


@router.post("/sources")
async def create_source(
    body: VideoSourceRequest,
    user: Annotated[User, Depends(get_current_user)],
):
    """创建视频源（按协议类型）。"""
    config = CameraConfig(
        camera_id=body.camera_id,
        name=body.name,
        protocol=body.protocol,
        rtsp_url=body.rtsp_url,
        gb_device_id=body.gb_device_id,
        gb_ip=body.gb_ip,
        gb_port=body.gb_port,
        gb_channel=body.gb_channel,
        onvif_url=body.onvif_url,
        onvif_username=body.onvif_username,
        onvif_password=body.onvif_password,
    )

    try:
        source = await VideoFactory.create_source(config)
        return success_response(
            msg="视频源创建成功",
            data={
                "ok": True,
                "camera_id": source.config.camera_id,
                "protocol": source.config.protocol,
                "stream_url": source.stream_url,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建失败: {e}") from e


@router.get("/sources")
async def list_sources(
    user: Annotated[User, Depends(get_current_user)],
):
    """活跃视频源列表。"""
    sources = VideoFactory.list_sources()
    return success_response(data={"total": len(sources), "items": sources})


@router.delete("/sources/{camera_id}")
async def close_source(
    camera_id: int,
    user: Annotated[User, Depends(get_current_user)],
):
    """关闭视频源。"""
    ok = VideoFactory.close_source(camera_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"视频源不存在: camera#{camera_id}")
    return success_response(msg=f"视频源已关闭: camera#{camera_id}")
