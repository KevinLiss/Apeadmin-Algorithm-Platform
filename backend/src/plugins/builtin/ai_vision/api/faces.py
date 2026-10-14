# -*- coding: utf-8 -*-
"""人脸库 API（P1）。

路由前缀：``/ai-vision/faces``（挂载后 ``/api/v1/ai-vision/faces``）。
权限：``ai_vision:face:list``（查询）/ ``ai_vision:face:create``（登记）/
``ai_vision:face:delete``（删除，admin 直通）。

登记流程（两个入口，均落 ``ai_vision_faces`` 表）：
1. ``POST /faces/register``：上传 1~5 张照片 → 逐张检测最大人脸 →
   SFace 提 128 维特征 → 均值归一入库。同名覆盖重登记（换装/补角度）。
2. ``POST /faces/register-from-alarm``：从告警干净抓拍图提取人脸入库
   （告警中心事后补登记，无需翻找原始照片）。

照片存放：``uploads/ai_vision/faces/<group>/<姓名>/photo_N.<ext>``，
删除档案时整目录清理。登记/删除后主动失效 FaceGallery 缓存
（30s TTL 兜底），worker 侧秒级生效。

人脸属生物识别敏感信息：列表不返回 embedding 原文，仅返回
``has_embedding`` 标志（与 staff API 同一约定）。
"""
import json
import shutil
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionAlarm, AIVisionFace
from src.plugins.builtin.ai_vision.paths import faces_dir

router = APIRouter(prefix="/faces", tags=["AI 视觉平台-人脸库"])

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_ALLOWED_GROUPS = {"whitelist", "blacklist"}
_MAX_FILE_MB = 10
_MAX_PHOTOS = 5


def _imread_cn(data: bytes):
    """中文路径安全读图（内存字节解码，绕开 cv2.imread 路径限制）。"""
    import cv2

    return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)


def _safe_name(name: str) -> str:
    """姓名 → 安全目录名（防路径穿越）。"""
    keep = []
    for ch in name.strip():
        keep.append(ch if (ch.isalnum() or ch in "_-（）()·") else "_")
    return "".join(keep)[:50] or "unknown"


def _out(item: AIVisionFace) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "staff_no": item.staff_no,
        "group": item.group,
        "photo_path": item.photo_path,
        "photo_count": item.photo_count,
        "note": item.note,
        "has_embedding": bool((item.embedding or "").strip() not in ("", "[]")),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def _validate_group(group: str) -> str:
    g = (group or "").strip()
    if g not in _ALLOWED_GROUPS:
        raise HTTPException(status_code=400, detail="分组必须是 whitelist 或 blacklist")
    return g


async def _do_register(
    db: AsyncSession,
    name: str,
    group: str,
    staff_no: str,
    note: str,
    images: list[tuple[bytes, str]],
    tag: str,
) -> AIVisionFace:
    """登记核心逻辑（上传/告警转登记共用）：提特征均值 + 落库 + 失效缓存。

    Args:
        images: [(文件字节, 展示用文件名), ...]，至少 1 张。
        tag: 照片文件名标记（upload / alarm{id}_）。
    """
    from src.plugins.builtin.ai_vision.runtime.face.engine import get_face_engine
    from src.plugins.builtin.ai_vision.runtime.face.gallery import get_gallery

    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    if len(name) > 50:
        raise HTTPException(status_code=400, detail="姓名过长（≤50 字符）")
    group = _validate_group(group)
    if not images:
        raise HTTPException(status_code=400, detail="至少需要 1 张照片")
    if len(images) > _MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"一次最多登记 {_MAX_PHOTOS} 张照片")

    eng = get_face_engine()
    if eng is None:
        raise HTTPException(
            status_code=503,
            detail="人脸模型不可用：assets/models 下缺少 YuNet/SFace ONNX（请先放置模型文件）",
        )

    person_dir = faces_dir() / group / _safe_name(name)
    vecs: list[np.ndarray] = []
    saved_path = ""
    for idx, (data, fname) in enumerate(images):
        if len(data) > _MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"单张照片不超过 {_MAX_FILE_MB}MB: {fname}")
        img = _imread_cn(data)
        if img is None:
            raise HTTPException(status_code=400, detail=f"图片解码失败: {fname}")
        face = eng.detect_largest(img)
        if face is None:
            raise HTTPException(
                status_code=400,
                detail=f"照片 {fname} 未检测到人脸：请使用正脸清晰照（人脸占画面 1/5~1/2）",
            )
        vec = eng.embed(img, face)
        if vec is None:
            raise HTTPException(status_code=400, detail=f"特征提取失败: {fname}")
        vecs.append(vec)

        ext = ("." + fname.rsplit(".", 1)[-1].lower()) if "." in fname else ".jpg"
        if ext not in _ALLOWED_EXT:
            ext = ".jpg"
        person_dir.mkdir(parents=True, exist_ok=True)
        dst = person_dir / f"{tag}{idx}{ext}"
        dst.write_bytes(data)
        saved_path = str(dst)

    mean = np.mean(vecs, axis=0)
    norm = float(np.linalg.norm(mean))
    mean = (mean / norm).astype(np.float32) if norm > 0 else mean

    # 同名覆盖重登记（换装/补角度后更新）；若历史数据存在同名多行，取最新行
    existing = (
        await db.execute(
            select(AIVisionFace)
            .where(AIVisionFace.name == name)
            .order_by(AIVisionFace.id.desc())
        )
    ).scalars().first()
    if existing:
        # 分组变更 → 迁移照片目录（先写新目录再删旧目录）
        if existing.group != group:
            old_dir = faces_dir() / existing.group / _safe_name(name)
            new_dir = faces_dir() / group / _safe_name(name)
            new_dir.mkdir(parents=True, exist_ok=True)
            if old_dir.exists():
                for src in old_dir.iterdir():
                    shutil.move(str(src), str(new_dir / src.name))
                old_dir.rmdir()
            existing.group = group
        existing.embedding = json.dumps(mean.tolist())
        existing.photo_path = saved_path
        existing.photo_count = len(vecs)
        existing.staff_no = staff_no
        existing.note = note
        item = existing
    else:
        item = AIVisionFace(
            name=name,
            staff_no=staff_no,
            group=group,
            embedding=json.dumps(mean.tolist()),
            photo_path=saved_path,
            photo_count=len(vecs),
            note=note,
        )
        db.add(item)
    await db.commit()
    await db.refresh(item)

    get_gallery().invalidate()
    return item


@router.get("")
async def list_faces(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:face:list"))],
    group: str = Query(default="", description="按分组过滤 whitelist/blacklist"),
    keyword: str = Query(default="", max_length=50, description="姓名/工号关键字"),
):
    """人脸库列表（不含 embedding 原文）。"""
    stmt = select(AIVisionFace)
    if group:
        stmt = stmt.where(AIVisionFace.group == _validate_group(group))
    if keyword:
        stmt = stmt.where(
            AIVisionFace.name.contains(keyword) | AIVisionFace.staff_no.contains(keyword)
        )
    stmt = stmt.order_by(AIVisionFace.id.desc())
    items = (await db.execute(stmt)).scalars().all()
    return success_response(data={"total": len(items), "items": [_out(i) for i in items]})


@router.post("/register")
async def register_face(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:face:create"))],
    name: Annotated[str, Form(max_length=50)],
    files: list[UploadFile] = File(..., description="1~5 张正脸照片"),
    group: Annotated[str, Form()] = "whitelist",
    staff_no: Annotated[str, Form(max_length=50)] = "",
    note: Annotated[str, Form(max_length=200)] = "",
):
    """上传照片登记：提特征均值入库（同名覆盖）。"""
    if not files or len(files) > _MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"请上传 1~{_MAX_PHOTOS} 张照片")
    images: list[tuple[bytes, str]] = []
    for f in files:
        ext = ("." + (f.filename or "").rsplit(".", 1)[-1].lower()) if f.filename else ".jpg"
        if ext not in _ALLOWED_EXT:
            raise HTTPException(status_code=400, detail=f"不支持的图片格式: {f.filename}")
        images.append((await f.read(), f.filename or f"photo{len(images)}.jpg"))
    item = await _do_register(db, name, group, staff_no, note, images, tag="upload")
    return success_response(
        data=_out(item), msg=f"已登记 {name}（{item.photo_count} 张照片，即时生效）"
    )


@router.post("/register-from-alarm")
async def register_face_from_alarm(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:face:create"))],
    alarm_id: Annotated[int, Form(ge=1)],
    name: Annotated[str, Form(max_length=50)],
    group: Annotated[str, Form()] = "whitelist",
    staff_no: Annotated[str, Form(max_length=50)] = "",
    note: Annotated[str, Form(max_length=200)] = "",
):
    """从告警抓拍登记：取干净抓拍图（无画框）提取人脸入库。"""
    alarm = await db.get(AIVisionAlarm, alarm_id)
    if not alarm:
        raise HTTPException(status_code=404, detail="告警不存在")
    src = alarm.clean_snapshot_path or alarm.snapshot_path
    if not src or not Path(src).exists():
        raise HTTPException(status_code=400, detail="该告警没有可用抓拍图")
    data = Path(src).read_bytes()
    item = await _do_register(
        db, name, group, staff_no, note,
        [(data, f"alarm{alarm_id}.jpg")], tag=f"alarm{alarm_id}_",
    )
    return success_response(
        data=_out(item), msg=f"已从告警 #{alarm_id} 抓拍登记 {name}（即时生效）"
    )


@router.delete("/{face_id}")
async def delete_face(
    face_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:face:delete"))],
):
    """删除人脸档案（照片目录一并清理 + 缓存失效）。"""
    from src.plugins.builtin.ai_vision.runtime.face.gallery import get_gallery

    item = await db.get(AIVisionFace, face_id)
    if not item:
        raise HTTPException(status_code=404, detail="人脸档案不存在")
    name = item.name
    await db.delete(item)
    await db.commit()

    removed = 0
    person_dir = faces_dir() / item.group / _safe_name(name)
    if person_dir.exists() and str(person_dir.resolve()).startswith(str(faces_dir().resolve())):
        try:
            removed = len(list(person_dir.iterdir()))
            shutil.rmtree(person_dir)
        except BaseException:  # noqa: BLE001
            pass
    get_gallery().invalidate()
    return success_response(msg=f"已删除 {name} 的人脸档案（清理 {removed} 张照片）")
