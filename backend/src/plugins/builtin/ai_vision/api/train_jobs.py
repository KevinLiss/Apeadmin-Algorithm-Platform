"""训练任务 API（B 方案：平台内一键续训）。

路由前缀：``/ai-vision/train-jobs``。
权限：``ai_vision:train:list / create / control``。

流程：
1. POST /train-jobs：选基座模型（须有 pt_path）+ 类别 + 超参 →
   校验已标注样本数 → 建 AIVisionTraining 记录 → 后台线程拉起 trainer；
2. GET /train-jobs：列表（含进度/指标）；GET /{id}：详情；
3. POST /{id}/cancel：终止子进程；
4. GET /{id}/log：训练日志尾部（前端轮询展示）。

类别顺序约定：请求的 category_codes 顺序 = 新模型 names 顺序；
发起时校验基座 category_map 的 values 与所选类别集合一致（顺序可不同，
导出按新顺序重映射索引——续训同族模型时建议保持基座顺序）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.db import get_db
from src.models import User
from src.plugins.builtin.ai_vision.models import AIVisionModel, AIVisionSample, AIVisionTraining
from src.plugins.builtin.ai_vision.paths import datasets_dir, train_runs_dir
from src.plugins.builtin.ai_vision.runtime.trainer import get_train_manager
from src.plugins.builtin.ai_vision.schemas import TrainJobCreate, TrainingOut

router = APIRouter(prefix="/train-jobs", tags=["AI 视觉平台-模型训练"])


def _parse_out(item: AIVisionTraining) -> dict:
    d = TrainingOut.model_validate(item).model_dump()
    try:
        d["params"] = json.loads(item.params or "{}")
    except (ValueError, TypeError):
        d["params"] = {}
    try:
        d["metrics"] = json.loads(item.metrics or "{}")
    except (ValueError, TypeError):
        d["metrics"] = {}
    try:
        d["sample_ids"] = json.loads(item.sample_ids or "[]")
    except (ValueError, TypeError):
        d["sample_ids"] = []
    return d


def _base_names(model: AIVisionModel) -> list[str]:
    """基座模型的类别顺序表（category_map 按 key 数字排序取 values）。"""
    try:
        m = json.loads(model.category_map or "{}")
    except (ValueError, TypeError):
        return []
    return [m[k] for k in sorted(m, key=lambda x: int(x))]


@router.get("")
async def list_train_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:list"))],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    stmt = select(AIVisionTraining)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
    stmt = stmt.order_by(AIVisionTraining.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(stmt)).scalars().all()
    # 联表基座/产物模型名
    model_ids = {t.base_model_id for t in items if t.base_model_id} | {t.output_model_id for t in items if t.output_model_id}
    names = {
        m.id: m.name
        for m in (await db.execute(select(AIVisionModel).where(AIVisionModel.id.in_(model_ids)))).scalars().all()
    } if model_ids else {}
    out = []
    for t in items:
        d = _parse_out(t)
        d["base_model_name"] = names.get(t.base_model_id, "")
        d["output_model_name"] = names.get(t.output_model_id, "")
        out.append(d)
    return success_response(data={"total": total, "page": page, "page_size": page_size, "items": out})


def _boxes_of(sample: AIVisionSample) -> list[dict]:
    try:
        return json.loads(sample.label_data or "{}").get("boxes", []) or []
    except (ValueError, TypeError):
        return []


def _select_labeled(samples: list[AIVisionSample], codes: list[str]) -> list[AIVisionSample]:
    """按标注内容筛选：样本的任意框 class_name 命中所选类别即入选。

    注意不能用 category_code（"关联类别"字段）——它由上传时手选/告警来源
    决定，与画框内容可以脱节：关联类别为空但画满 drowning 框的样本
    若按 category_code 筛会被凭空漏掉（2026-09-28 自查 P1-1）。
    """
    cs = set(codes)
    return [s for s in samples if any(b.get("class_name") in cs for b in _boxes_of(s))]


@router.get("/preview")
async def preview_dataset(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:create"))],
    category_codes: str = Query(..., description="逗号分隔类别 code"),
    folder: str = Query(default="", description="限定样本分组（空=全库；__none__=未分组）"),
    sample_ids: str = Query(default="", description="逗号分隔的手动勾选样本 ID（非空优先于 folder）"),
):
    """发起前预览：将参与训练的已标注样本数与每类框数。"""
    codes = [c.strip() for c in category_codes.split(",") if c.strip()]
    id_list = [int(x) for x in sample_ids.split(",") if x.strip().isdigit()]
    stmt = select(AIVisionSample).where(AIVisionSample.label_status == "labeled")
    if id_list:
        stmt = stmt.where(AIVisionSample.id.in_(id_list))
    elif folder == "__none__":
        stmt = stmt.where(AIVisionSample.folder == "")
    elif folder:
        stmt = stmt.where(AIVisionSample.folder == folder)
    samples = (await db.execute(stmt)).scalars().all()
    picked = _select_labeled(samples, codes)
    per_class: dict[str, int] = {c: 0 for c in codes}
    for s in picked:
        for b in _boxes_of(s):
            cn = b.get("class_name")
            if cn in per_class:
                per_class[cn] += 1
    # 背景负样本候选（skipped）数量——前端 bg_ratio 提示用
    bg_stmt = select(AIVisionSample).where(AIVisionSample.label_status == "skipped")
    if folder == "__none__":
        bg_stmt = bg_stmt.where(AIVisionSample.folder == "")
    elif folder:
        bg_stmt = bg_stmt.where(AIVisionSample.folder == folder)
    bg_count = (await db.execute(select(func.count()).select_from(bg_stmt.subquery()))).scalar() or 0
    return success_response(data={
        "samples": len(picked),
        "per_class": per_class,
        "sample_ids": [s.id for s in picked],
        "bg_candidates": bg_count,
    })


@router.post("")
async def create_train_job(
    body: TrainJobCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:create"))],
):
    """发起训练任务（异步执行，立即返回任务记录）。"""
    # 产物名非法字符当场拒绝（而非训练完静默剔除，自查 P2-8）
    if body.output_name:
        import re as _re

        if _re.search(r"[^\w\u4e00-\u9fff.\-]", body.output_name):
            raise HTTPException(
                status_code=400,
                detail="产物名称含非法字符（不能有空格、/ \\ 等），仅允许中文/字母/数字/_/-/.",
            )
    base = await db.get(AIVisionModel, body.base_model_id)
    if not base:
        raise HTTPException(status_code=400, detail="基座模型不存在")
    if not base.pt_path:
        raise HTTPException(status_code=400, detail=f"基座模型「{base.name}」未登记 .pt 权重，无法续训（请到模型管理登记）")
    pt = Path(base.pt_path)
    if not pt.is_absolute():
        # 相对路径解析：插件根（assets/models/*.pt 存这）→ backend 运行目录
        # （本文件在 ai_vision/api/：parents[1]=ai_vision/，parents[6]=backend/）
        plugin_root = Path(__file__).resolve().parents[1]
        for cand in (
            plugin_root / base.pt_path,
            Path.cwd() / base.pt_path,
            Path(__file__).resolve().parents[6] / base.pt_path,
        ):
            if cand.exists():
                pt = cand
                break
    if not pt.exists():
        raise HTTPException(status_code=400, detail=f"基座权重文件不存在: {base.pt_path}")
    base_names = _base_names(base)
    if not base_names:
        raise HTTPException(status_code=400, detail="基座模型缺少类别表（category_map），无法对齐标注索引")
    # 严格校验"顺序"：YOLO 索引按 names 顺序定死，集合相同顺序不同会导致
    # 标注索引与基座头权重错位——训练照样收敛但模型是乱的（自查 P1-2）
    if body.category_codes != base_names:
        raise HTTPException(
            status_code=400,
            detail=f"类别顺序 {body.category_codes} 与基座类别表 {base_names} 不一致（续训须同族同类别且顺序一致）",
        )

    # 已标注样本快照（按标注框内容筛选，非 category_code——P1-1）；
    # 样本范围：手动勾选 sample_ids 优先，其次 folder 分组，都空则全库
    stmt = select(AIVisionSample).where(AIVisionSample.label_status == "labeled")
    if body.sample_ids:
        stmt = stmt.where(AIVisionSample.id.in_(body.sample_ids))
    elif body.folder == "__none__":
        stmt = stmt.where(AIVisionSample.folder == "")
    elif body.folder:
        stmt = stmt.where(AIVisionSample.folder == body.folder)
    all_labeled = (await db.execute(stmt)).scalars().all()
    samples = _select_labeled(all_labeled, body.category_codes)
    if len(samples) < 5:
        scope = "所选样本" if body.sample_ids else (f"分组「{body.folder}」" if body.folder else "样本库")
        raise HTTPException(
            status_code=400,
            detail=f"{scope}中含所选类别标注的样本仅 {len(samples)} 张（至少 5 张，保证训练集 ≥4）",        )

    # 背景负样本（主动学习：误报图人工标"跳过"后进这里）：bg_ratio>0 才查，
    # 与正样本同 folder/sample_ids 范围口径；build_dataset 内按 ratio 抽样
    bg_samples: list[AIVisionSample] = []
    if body.bg_ratio > 0:
        bg_stmt = select(AIVisionSample).where(AIVisionSample.label_status == "skipped")
        if body.sample_ids:
            # 手动勾选模式：背景图不受 sample_ids 限制（勾选的是正样本），
            # 但仍限定同分组，避免跨场景混入
            if body.folder == "__none__":
                bg_stmt = bg_stmt.where(AIVisionSample.folder == "")
            elif body.folder:
                bg_stmt = bg_stmt.where(AIVisionSample.folder == body.folder)
        elif body.folder == "__none__":
            bg_stmt = bg_stmt.where(AIVisionSample.folder == "")
        elif body.folder:
            bg_stmt = bg_stmt.where(AIVisionSample.folder == body.folder)
        bg_samples = list((await db.execute(bg_stmt)).scalars().all())

    if get_train_manager().active_job_id() is not None:
        raise HTTPException(status_code=409, detail="已有训练任务在运行（单机互斥），请等待完成或取消")

    job = AIVisionTraining(
        event_id=0,
        base_model_id=base.id,
        sample_ids=json.dumps([s.id for s in samples]),
        params=json.dumps(body.model_dump(), ensure_ascii=False),
        status="running",
        progress=0,
        metrics="{}",
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    # 原子占位互斥（自查 P2-4）：记录建好后立刻 reserve，堵住
    # "检查互斥→launch 拉起进程"之间双击连发的并发窗口
    if not get_train_manager().try_reserve(job.id):
        job.status = "failed"
        job.metrics = json.dumps({"error": "已有训练任务在运行（单机互斥）"}, ensure_ascii=False)
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=409, detail="已有训练任务在运行（单机互斥），请等待完成或取消")
    # commit 会 expire ORM 属性；launch 在子线程读 samples 字段前先物化，
    # 避免子线程触发懒加载/过期刷新（同步 session 与 async session 不通用）
    samples_snapshot = [
        {"id": s.id, "file_path": s.file_path, "label_data": s.label_data, "label_status": s.label_status}
        for s in samples
    ] + [
        {"id": s.id, "file_path": s.file_path, "label_data": s.label_data, "label_status": s.label_status}
        for s in bg_samples
    ]

    # 后台线程拉起子进程（launch 内含数据集构建，可能抛样本不足）
    import asyncio

    class _SampleView:
        """launch/build_dataset 只需 id/file_path/label_data/label_status 四属性。"""

        def __init__(self, d: dict) -> None:
            self.__dict__.update(d)

    def _launch() -> dict:
        return get_train_manager().launch(
            job_id=job.id,
            base_pt=str(pt),
            names=body.category_codes,
            samples=[_SampleView(d) for d in samples_snapshot],
            params=body.model_dump(),
        )

    try:
        stats = await asyncio.to_thread(_launch)
    except Exception as exc:  # noqa: BLE001 — 任何 launch 失败都必须落 failed，
        # 否则任务卡 running + 互斥永久占用（自查 P1-3）
        get_train_manager().release(job.id)  # 进程未拉起，立即释放占位（P2-4）
        job.status = "failed"
        job.metrics = json.dumps({"error": str(exc)[:500]}, ensure_ascii=False)
        job.finished_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return success_response(data={**_parse_out(job), "dataset": stats}, msg="训练已启动")


@router.get("/{job_id}")
async def get_train_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:list"))],
):
    item = await db.get(AIVisionTraining, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    return success_response(data=_parse_out(item))


@router.get("/{job_id}/log")
async def get_train_log(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:list"))],
    tail: int = Query(default=60, ge=1, le=500),
):
    p = train_runs_dir() / str(job_id) / "train.log"
    if not p.exists():
        return success_response(data={"lines": []})
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    return success_response(data={"lines": lines[-tail:]})


@router.get("/{job_id}/progress")
async def get_train_progress(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:list"))],
):
    """逐 epoch 训练曲线数据（train_runner 写的 progress.jsonl 全量）。

    运行中任务也可拉——每 epoch 追加一行，前端轮询画布即可动态增长。
    """
    item = await db.get(AIVisionTraining, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    p = train_runs_dir() / str(job_id) / "progress.jsonl"
    records = []
    if p.exists():
        for ln in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                records.append(json.loads(ln))
            except ValueError:
                continue
    return success_response(data={"epochs_total": item.params and json.loads(item.params or "{}").get("epochs", 0), "records": records})


@router.post("/{job_id}/cancel")
async def cancel_train_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:control"))],
):
    item = await db.get(AIVisionTraining, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    if item.status != "running":
        raise HTTPException(status_code=400, detail="任务未在运行")
    ok = get_train_manager().cancel(job_id)
    if ok:
        item.status = "failed"
        item.metrics = json.dumps({"error": "用户取消"}, ensure_ascii=False)
        item.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return success_response(msg="已发送取消指令" if ok else "进程已结束")


@router.get("/{job_id}/samples")
async def get_train_samples(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:list"))],
):
    """训练任务的样本快照（数据集版本视图，优化项22）。

    按 sample_ids 里记录的 ID 反查当前样本库：仍在的返回图片+标注框，
    已删除的标记 missing（前端灰显"已删除"）——这样"当初用了哪些图"
    永远可追溯，即使样本后来被清理。
    """
    item = await db.get(AIVisionTraining, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    try:
        ids = json.loads(item.sample_ids or "[]")
    except (ValueError, TypeError):
        ids = []
    rows = {}
    if ids:
        found = (await db.execute(
            select(AIVisionSample).where(AIVisionSample.id.in_(ids))
        )).scalars().all()
        rows = {s.id: s for s in found}
    out = []
    for sid in ids:
        s = rows.get(sid)
        if s:
            out.append({
                "id": sid, "missing": False, "file_path": s.file_path,
                "label_status": s.label_status, "label_data": s.label_data,
                "category_code": s.category_code, "folder": s.folder,
            })
        else:
            out.append({"id": sid, "missing": True})
    return success_response(data={"total": len(ids), "present": len(rows), "items": out})


@router.delete("/{job_id}")
async def delete_train_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:train:control"))],
):
    """删除训练任务记录，并连带清理其磁盘产物（自查 P1-3：磁盘只进不出）。

    清理 datasets/{id}（样本副本）与 train_runs/{id}（日志/权重/导出）。
    产物模型文件在 assets/models 下，属模型库资产，不随任务删除——
    要清它请用模型库的删除接口（避免误删已换绑上线的模型）。
    运行中任务不可删（先取消）。
    """
    import shutil

    item = await db.get(AIVisionTraining, job_id)
    if not item:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    if item.status == "running":
        raise HTTPException(status_code=400, detail="任务运行中，请先取消再删除")

    cleaned = []
    for d in (datasets_dir() / str(job_id), train_runs_dir() / str(job_id)):
        try:
            if d.exists():
                shutil.rmtree(d)
                cleaned.append(d.name)
        except BaseException:  # noqa: BLE001 — 删除钩子可能抛 SystemExit，不可杀进程
            pass

    await db.delete(item)
    await db.commit()
    return success_response(
        msg=f"训练任务 #{job_id} 已删除" + (f"（清理产物目录 {len(cleaned)} 个）" if cleaned else ""),
    )
