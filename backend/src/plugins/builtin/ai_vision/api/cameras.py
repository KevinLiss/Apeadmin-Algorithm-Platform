"""摄像头 API：CRUD + RTSP 连通性测试。

路由前缀：``/ai-vision/cameras``（挂载后为 ``/api/v1/ai-vision/cameras``）。
权限：``ai_vision:camera:list/create/edit/delete/test``。

实现要点（任务 1.4）：
- 摄像头 CRUD（软删除）
- POST /cameras/{id}/test：RTSP 连通性测试，返回快照 base64
- 测试接口在内部 ``ensure_infer_runtime()``，未装 L1 时 409 提示
- 测试用 ``asyncio.to_thread`` 执行，不阻塞事件循环
- 测试成功更新 ``last_online_at`` 与 ``status``
"""
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionCamera
from src.plugins.builtin.ai_vision.runtime.deps import RuntimeNotInstalledError
from src.plugins.builtin.ai_vision.runtime.grabber import test_rtsp
from src.plugins.builtin.ai_vision.schemas import CameraCreate, CameraOut, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["AI 视觉平台-摄像头"])


def _parse_out(item: AIVisionCamera) -> dict:
    """ORM → dict。"""
    return CameraOut.model_validate(item).model_dump()


@router.get("")
async def list_cameras(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str = Query(default="", max_length=50),
    sort: str = Query(default="", max_length=20, description="排序：name=名称拼音升序 / -name=降序，默认 ID 升序"),
):
    """分页查询摄像头（名称/位置关键字过滤 + 名称拼音排序）。

    状态显示为实时覆盖（DB status 只在点"测试"时更新一次，是陈旧快照）：
    - 监控运行中（worker 存活）→ worker 实时 online/offline
    - 未监控：视频源文件已丢失 → offline；其余 → unknown
    """
    stmt = select(AIVisionCamera).where(AIVisionCamera.is_deleted == False)  # noqa: E712
    if keyword:
        stmt = stmt.where(
            AIVisionCamera.name.contains(keyword)
            | AIVisionCamera.location.contains(keyword)
        )
    rows = list((await db.execute(stmt)).scalars().all())

    # 排序：拼音（"汽车着火"→qichezhuohuo，逐字比较，同字看下一字）或 ID
    if sort in ("name", "-name"):
        try:
            from pypinyin import lazy_pinyin

            rows.sort(
                key=lambda c: "".join(lazy_pinyin(c.name or "")),
                reverse=(sort == "-name"),
            )
        except ImportError:
            rows.sort(key=lambda c: (c.name or ""), reverse=(sort == "-name"))
    else:
        rows.sort(key=lambda c: c.id)

    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]

    # 实时状态覆盖（worker 内存态优先于 DB 快照）
    from pathlib import Path

    try:
        from src.plugins.builtin.ai_vision.runtime.manager import get_manager

        wstates = {w["camera_id"]: w for w in get_manager().list_workers()}
    except Exception:  # noqa: BLE001（管理器不可用时退回 DB 状态）
        wstates = {}

    out = []
    for item in items:
        d = _parse_out(item)
        w = wstates.get(item.id)
        if w is not None and w.get("running"):
            d["status"] = w.get("state") or "unknown"
        elif item.source_type == "video" and not Path(item.rtsp_url or "").is_file():
            d["status"] = "offline"
        else:
            d["status"] = "unknown"
        out.append(d)

    return success_response(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": out,
    })


@router.post("")
async def create_camera(
    body: CameraCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:create"))],
):
    """新增视频源（RTSP 摄像头 / 本地视频文件，初始 status=unknown）。"""
    source_type = body.source_type if body.source_type in ("camera", "video") else "camera"
    if source_type == "video":
        # 视频文件源：校验文件存在
        from pathlib import Path

        if not Path(body.rtsp_url).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"视频文件不存在: {body.rtsp_url}，请先上传视频",
            )
    item = AIVisionCamera(
        name=body.name,
        source_type=source_type,
        rtsp_url=body.rtsp_url,
        location=body.location,
        vendor=body.vendor,
        status="unknown",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="摄像头创建成功")


@router.get("/{camera_id}")
async def get_camera(
    camera_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:list"))],
):
    """查询单个摄像头。"""
    item = await _get_or_404(db, camera_id)
    return success_response(data=_parse_out(item))


@router.put("/{camera_id}")
async def update_camera(
    camera_id: int,
    body: CameraUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:edit"))],
):
    """编辑视频源信息。"""
    item = await _get_or_404(db, camera_id)
    data = body.model_dump(exclude_unset=True)
    # source_type 合法性 + video 源文件校验
    if "source_type" in data:
        if data["source_type"] not in ("camera", "video"):
            raise HTTPException(status_code=400, detail="source_type 仅支持 camera/video")
    new_type = data.get("source_type") or item.source_type
    new_url = data.get("rtsp_url") or item.rtsp_url
    if new_type == "video":
        from pathlib import Path

        if not Path(new_url).is_file():
            raise HTTPException(
                status_code=400,
                detail=f"视频文件不存在: {new_url}，请先上传视频",
            )
    for key, value in data.items():
        setattr(item, key, value)
    await db.commit()
    await db.refresh(item)
    return success_response(data=_parse_out(item), msg="更新成功")


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:delete"))],
):
    """删除摄像头（软删除）。"""
    item = await _get_or_404(db, camera_id)
    item.is_deleted = True
    await db.commit()
    return success_response(msg="删除成功")


@router.post("/{camera_id}/test")
async def test_camera(
    camera_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:camera:test"))],
    rtsp_url: str | None = Query(default=None, max_length=500, description="可选：临时测试地址，不覆盖原地址"),
):
    """视频源连通性测试，返回一帧快照 base64。

    - camera 源：RTSP 连通性测试
    - video 源：读取视频第一帧（验证文件可解码）

    测试成功更新 last_online_at / status；失败置 status=offline。
    未安装 L1 运行环境时返回 409 提示。
    """
    item = await _get_or_404(db, camera_id)
    url = rtsp_url or item.rtsp_url

    try:
        result = await test_rtsp(url, timeout=5.0)
    except RuntimeNotInstalledError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"测试执行异常: {exc}") from exc

    # 更新摄像头状态
    if result["ok"]:
        item.status = "online"
        item.last_online_at = datetime.now(timezone.utc)
    else:
        item.status = "offline"
    await db.commit()
    await db.refresh(item)

    result["camera_id"] = camera_id
    result["camera_status"] = item.status
    return success_response(
        data=result,
        msg="视频源测试成功" if result["ok"] else "视频源测试失败",
    )


async def _get_or_404(db: AsyncSession, camera_id: int) -> AIVisionCamera:
    item = await db.get(AIVisionCamera, camera_id)
    if not item or item.is_deleted:
        raise HTTPException(status_code=404, detail="摄像头不存在")
    return item