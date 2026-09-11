"""环境文件管理 API：模型 ONNX / manifest 清单的文件级管理。

路由前缀：``/ai-vision/runtime/env-files``（挂载后为
``/api/v1/ai-vision/runtime/env-files``）。

能力：
- GET    /env-files                ：列出全部环境文件（模型+manifest），含校验状态与引用
- POST   /env-files/verify         ：重新全量 SHA256 校验
- POST   /env-files/upload         ：上传/替换 ONNX 模型文件（自动算哈希 + 落库）
- DELETE /env-files/{id}           ：删除模型文件（被引用则拒绝）
- POST   /env-files/{id}/reload    ：替换文件后主动触发引擎缓存重载

权限：``ai_vision:model:manage``（管理）、``ai_vision:model:list``（查看）。
"""
import hashlib
import json
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionEvent, AIVisionModel

router = APIRouter(prefix="/runtime/env-files", tags=["AI 视觉平台-环境文件"])


# 模型目录 = plugin 包根下 assets/models（与本文件相对定位：api/env_files.py → api → ai_vision）
MODELS_DIR = Path(__file__).resolve().parent.parent / "assets" / "models"


def _sha256_of(path: Path) -> str:
    """分块计算文件 SHA256。"""
    sha = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _fmt_size(n: int) -> str:
    """字节 → 人类可读。"""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


async def _referencing_events(db: AsyncSession, model_id: int) -> list[dict]:
    """查询引用某模型的事件列表（事件 model_ids JSON 含该模型 ID）。"""
    rows = (await db.execute(
        select(AIVisionEvent).where(AIVisionEvent.is_deleted.is_(False))
    )).scalars().all()
    refs = []
    for evt in rows:
        try:
            ids = json.loads(evt.model_ids or "[]")
        except (ValueError, TypeError):
            ids = []
        if model_id in ids:
            refs.append({"id": evt.id, "name": evt.name, "status": evt.status})
    return refs


def _model_dir_entries() -> list[Path]:
    """模型目录下全部文件（onnx/json），目录缺失时返回空。"""
    if not MODELS_DIR.exists():
        return []
    return sorted(
        [p for p in MODELS_DIR.iterdir() if p.is_file() and p.suffix in {".onnx", ".json"}],
        key=lambda p: p.name,
    )


def _model_dir_models() -> list[Path]:
    """模型目录下全部 .onnx 文件（用于识别孤儿文件）。"""
    if not MODELS_DIR.exists():
        return []
    return sorted([p for p in MODELS_DIR.iterdir() if p.is_file() and p.suffix == ".onnx"], key=lambda p: p.name)


@router.get("")
async def list_env_files(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:list"))],
):
    """列出所有环境文件（模型 + manifest），含 SHA256 校验状态与被引用事件。"""
    # DB 中已注册模型（含 file_path 指向）
    models = (await db.execute(select(AIVisionModel))).scalars().all()

    result = []
    seen_rel = set()

    # 1. 模型文件（以 DB 记录为主，补齐磁盘状态）
    for m in models:
        rel = m.file_path
        seen_rel.add(rel)
        fp = MODELS_DIR.parent / rel if not rel.startswith("assets/") else MODELS_DIR.parent / rel
        fp = MODELS_DIR / Path(rel).name if not fp.exists() else fp
        exists = fp.exists()
        disk_size = fp.stat().st_size if exists else 0
        disk_sha = _sha256_of(fp) if exists else ""
        hash_ok = bool(m.sha256) and disk_sha.lower() == m.sha256.lower() if exists else False
        refs = await _referencing_events(db, m.id)
        result.append({
            "kind": "model",
            "model_id": m.id,
            "name": m.name,
            "file": Path(m.file_path).name,
            "rel_path": m.file_path,
            "abs_path": str(fp),
            "size": disk_size,
            "size_fmt": _fmt_size(disk_size) if exists else "-",
            "sha256": m.sha256,
            "disk_sha256": disk_sha if exists else "",
            "hash_ok": hash_ok,
            "exists": exists,
            "version": m.version,
            "quantized": m.quantized,
            "source": m.source,
            "referenced_by": refs,
        })

    # 2. manifest.json（辅助文件，不计引用）
    manifest = MODELS_DIR / "manifest.json"
    if manifest.exists():
        msize = manifest.stat().st_size
        result.append({
            "kind": "manifest",
            "name": "manifest.json",
            "file_id": None,
            "path": str(manifest),
            "size": msize,
            "size_fmt": _fmt_size(msize),
            "exists": True,
            "hash_ok": True,
            "referenced_by": [],
        })

    # 3. 磁盘上存在但未注册到 DB 的 onnx（孤儿文件，提醒清理/导入）
    for p in _model_dir_models():
        if p.suffix == ".onnx" and p.name not in seen_rel and p.name not in {Path(m.file_path).name for m in models}:
            result.append({
                "kind": "orphan",
                "model_id": None,
                "name": p.stem,
                "file": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "size_fmt": _fmt_size(p.stat().st_size),
                "exists": True,
                "hash_ok": False,
                "referenced_by": [],
            })

    return success_response(data={
        "dir": str(MODELS_DIR),
        "total": len(result),
        "items": result,
    })


@router.post("/verify")
async def verify_env_files(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:list"))],
):
    """全量 SHA256 校验模型文件，返回每个文件的完好/损坏/缺失。"""
    models = (await db.execute(select(AIVisionModel))).scalars().all()
    checks = []
    all_ok = True
    for m in models:
        fp = MODELS_DIR / Path(m.file_path).name
        if not fp.exists():
            checks.append({"model_id": m.id, "name": m.name, "status": "missing", "detail": "文件缺失"})
            all_ok = False
            continue
        actual = _sha256_of(fp)
        ok = bool(m.sha256) and actual.lower() == m.sha256.lower()
        if not ok:
            all_ok = False
        checks.append({
            "model_id": m.id,
            "name": m.name,
            "status": "ok" if ok else "corrupted",
            "detail": "校验通过" if ok else f"SHA256 不匹配（期望 {m.sha256[:12]}…，实际 {actual[:12]}…）",
        })
    return success_response(data={"ok": all_ok, "items": checks}, msg="校验完成")


@router.post("/upload")
async def upload_env_file(
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:manage"))],
):
    """上传/替换 ONNX 模型文件（自动算 SHA256，若对应 DB 记录存在则更新）。"""
    filename = Path(file.filename or "").name
    if not filename.endswith(".onnx"):
        raise HTTPException(status_code=400, detail="仅支持上传 .onnx 模型文件")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODELS_DIR / filename

    # 校验上传为有效 ONNX（写入临时文件后检查），先落盘
    tmp = MODELS_DIR / f".{filename}.uploading"
    try:
        with tmp.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        # 尝试用 onnxruntime 打开以验证有效（L1 未装时跳过，仅存文件）
        try:
            import onnxruntime as ort
            ort.InferenceSession(str(tmp), providers=["CPUExecutionProvider"])
        except ImportError:
            pass  # L1 未安装，跳过格式校验
        # 计算哈希并移动到位
        sha = _sha256_of(tmp)
        size = tmp.stat().st_size
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()

    # 更新/新增 DB 记录
    existing = (await db.execute(
        select(AIVisionModel).where(AIVisionModel.name == filename[:-5])
    )).scalars().first()
    if existing:
        existing.file_path = f"assets/models/{filename}"
        existing.sha256 = sha
        existing.file_size = size
        await db.commit()
        await db.refresh(existing)
        model_id = existing.id
    else:
        record = AIVisionModel(
            name=filename[:-5],
            file_path=f"assets/models/{filename}",
            sha256=sha,
            file_size=size,
            category_map="{}",
            input_size=640,
            quantized=False,
            source="imported",
            license_note="运行时上传导入",
            version="1.0.0",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        model_id = record.id

    # 主动失效推理缓存，让引擎下次重新加载
    try:
        from src.plugins.builtin.ai_vision.runtime.engine import get_engine
        get_engine().clear_cache()
    except Exception:  # noqa: BLE001
        pass

    return success_response({
        "model_id": model_id,
        "file": filename,
        "sha256": sha,
        "size": size,
    }, msg="模型文件上传成功")


@router.delete("/{model_id}")
async def delete_env_file(
    model_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:manage"))],
):
    """删除模型文件（被事件引用则拒绝删除）。"""
    m = await db.get(AIVisionModel, model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    refs = await _referencing_events(db, model_id)
    if refs:
        names = "、".join(r["name"] for r in refs)
        raise HTTPException(status_code=400, detail=f"模型被事件引用（{names}），请先解绑再删除")

    fp = MODELS_DIR / Path(m.file_path).name
    if fp.exists():
        fp.unlink()
    await db.delete(m)
    await db.commit()
    try:
        from src.plugins.builtin.ai_vision.runtime.engine import get_engine
        get_engine().clear_cache()
    except Exception:  # noqa: BLE001
        pass
    return success_response(msg="模型文件已删除")


@router.post("/{model_id}/reload")
async def reload_env_file(
    model_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:model:manage"))],
):
    """替换文件后主动清空引擎缓存（下次加载自动按新文件重建）。"""
    m = await db.get(AIVisionModel, model_id)
    if not m:
        raise HTTPException(status_code=404, detail="模型不存在")
    fp = MODELS_DIR / Path(m.file_path).name
    if not fp.exists():
        raise HTTPException(status_code=400, detail="模型文件不存在")
    try:
        from src.plugins.builtin.ai_vision.runtime.engine import get_engine
        get_engine().clear_cache()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"引擎重载失败: {exc}") from exc
    return success_response(msg="引擎缓存已重置，模型将在下次推理时按新文件加载")