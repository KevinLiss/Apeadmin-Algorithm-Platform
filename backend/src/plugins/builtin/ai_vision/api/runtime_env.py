"""运行环境 API：L1/L2 依赖状态检测、一键安装、SSE 实时日志。

路由前缀：``/ai-vision/runtime``（挂载后为 ``/api/v1/ai-vision/runtime``）。
权限：``ai_vision:runtime:list``（查看）、``ai_vision:runtime:install``（安装）。

实现要点（任务 1.2）：
- GET  /runtime/status        ：检测 L1/L2 状态（importlib.metadata，不真实 import）
- POST /runtime/install       ：后台子进程 pip install，互斥单任务
- GET  /runtime/install-log   ：轮询返回当前安装进度（供前端定时器）
- GET  /runtime/install-log/sse：SSE 实时推送安装日志
"""
import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import success_response
from src.models import User
from src.plugins.builtin.ai_vision.runtime import deps

router = APIRouter(prefix="/runtime", tags=["AI 视觉平台-运行环境"])


@router.get("/status")
async def runtime_status(
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:list")),
):
    """检测 L1/L2 依赖状态（不真实 import，避免污染内存）。"""
    status = deps.check_all_layers()
    status["installing"] = deps.is_installing()
    return success_response(data=status)


@router.post("/install")
async def runtime_install(
    layer: str = Query(..., description="L1 或 L2"),
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:install")),
):
    """启动依赖安装（后台 pip 子进程，SSE 查看实时日志）。"""
    layer = layer.upper()
    try:
        result = deps.install_layer(layer)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        from src.core.exceptions import AppException
        raise AppException(msg=str(exc), code=409) from exc
    logger.info(f"[AIVision] runtime install started layer={layer} pid={result.get('pid')}")
    return success_response(data=result, msg=f"{layer} 依赖安装已启动")


@router.post("/uninstall")
async def runtime_uninstall(
    layer: str = Query(..., description="L1 或 L2"),
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:uninstall")),
):
    """启动依赖卸载（后台 pip uninstall，SSE 查看实时日志）。

    卸载前自动停止所有正在运行的分析任务，避免引用运行时崩溃。
    """
    layer = layer.upper()

    # 卸载前强制停止所有运行任务（释放对 L1/L2 的引用）
    from sqlalchemy import select

    from src.db import get_db
    from src.plugins.builtin.ai_vision.models import AIVisionTask
    from src.plugins.builtin.ai_vision.runtime.manager import get_manager

    manager = get_manager()
    stopped = 0
    try:
        from src.db import SessionLocal
        async with SessionLocal() as db:
            rows = (await db.execute(
                select(AIVisionTask).where(AIVisionTask.status == "running")
            )).scalars().all()
            for task in rows:
                try:
                    manager.stop_task(task.id, task.camera_id)
                except Exception:  # noqa: BLE001
                    pass
                task.status = "stopped"
                task.started_at = None
                stopped += 1
            if rows:
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[AIVision] 卸载前停止任务异常: {exc}")

    try:
        result = deps.uninstall_layer(layer)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        from src.core.exceptions import AppException
        raise AppException(msg=str(exc), code=409) from exc
    logger.info(f"[AIVision] runtime uninstall started layer={layer} pid={result.get('pid')} stopped={stopped}")
    return success_response(
        data={**result, "stopped_tasks": stopped},
        msg=f"{layer} 依赖卸载已启动，已停止 {stopped} 个运行任务",
    )


@router.post("/test")
async def runtime_test(
    layer: str = Query(default="", description="L1/L2，空则全部"),
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:test")),
):
    """深度环境健康测试：真实 import + 模型加载探测（供一键自检 / AI 助手）。"""
    layers = ["L1", "L2"] if not layer else [layer.upper()]
    results = {}
    for l in layers:
        try:
            results[l] = deps.test_layer(l)
        except Exception as exc:  # noqa: BLE001
            results[l] = {"layer": l, "ok": False, "error": str(exc)}
    ok = all(r.get("ok") for r in results.values())
    return success_response(data={"ok": ok, "layers": results}, msg="环境自检完成")


@router.get("/install-log")
async def runtime_install_log(
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:list")),
):
    """轮询最近一次安装任务的状态与日志。"""
    return success_response(data=deps.read_install_log())


@router.get("/install-log/sse")
async def runtime_install_log_sse(
    request: Request,
    user: User = Depends(get_current_user),
    _perm: User = Depends(_require_perm("ai_vision:runtime:list")),
):
    """SSE 实时推送安装日志（前端 EventSource 连接）。"""
    async def event_generator():
        last_len = -1
        while True:
            if await request.is_disconnected():
                break
            log = deps.read_install_log()
            text = log["log"]
            if len(text) != last_len:
                last_len = len(text)
                yield f"data: {__import__('json').dumps(log, ensure_ascii=False)}\n\n"
            if not log["running"]:
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )