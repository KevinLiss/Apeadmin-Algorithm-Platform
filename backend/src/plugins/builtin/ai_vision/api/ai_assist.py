"""AI 环境排查助手 API（问答式排查 AI 视觉平台运行环境问题）。

路由前缀：``/ai-vision/runtime/ai``（挂载后为
``/api/v1/ai-vision/runtime/ai``）。

能力：
- POST /runtime/ai/chat        ：非流式对话
- POST /runtime/ai/chat/stream ：SSE 流式对话（打字机）

复用底座 ``src.ai.agent.chat_stream``：
- 自定义 ai_vision 排查 system_prompt（聚焦 L1/L2 依赖、模型文件、任务状态）
- ``enable_tools=True``，让 AI 通过 function calling 调用已注册的 ai_vision
  MCP 排查工具（查状态 / 测环境 / 装依赖）——**刻意不含卸载**
- provider 复用底座「模型供应商」（DeepSeek 等），API Key 加密存储于底座

权限：``ai_vision:runtime:list``（查看/对话）。
"""
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from loguru import logger

from src.core.deps import get_current_user
from src.core.deps import require_permission as _require_perm
from src.core.exceptions import AppException, success_response
from src.db import SessionLocal
from src.models import User

router = APIRouter(prefix="/runtime/ai", tags=["AI 视觉平台-环境排查助手"])

# 环境排查助手专用 system prompt
AIVISION_ENV_PROMPT = """你是 AI 视觉平台的「运行环境排查助手」，帮助用户诊断和解决平台推理/训练运行环境问题。

你的职责范围：
1. 查询 L1 推理层 / L2 训练层的依赖安装状态（缺失哪些包、当前版本）
2. 对运行环境做深度健康测试（真实 import 各依赖、试加载 ONNX 模型）
3. 触发缺失依赖层的安装（后台任务）
4. 排查模型文件完整性（SHA256 校验）、磁盘占用等常见问题

可用的工具（仅这些）：
- ai_vision_runtime_status：查依赖安装状态
- ai_vision_runtime_test：深度健康测试
- ai_vision_runtime_install：触发依赖安装

使用规则：
- 优先用工具获取真实环境数据，再基于数据给出诊断和可执行建议
- 只调用上面列出的 ai_vision 排查工具，不要调用其他系统管理工具
- 回答使用简体中文，专业、简洁，给出下一步操作
- 若某工具权限不足或执行失败，如实说明原因
- 对于纯咨询（如「L1 装了什么」），可直接回答，不必强行调工具

注意：卸载依赖、删除模型文件属高风险操作，一律不执行，只建议用户在页面人工处理。
"""


async def _resolve_provider(provider_id: int | None = None) -> Any | None:
    """取 AI 供应商：优先指定 id 的启用供应商，否则回退首个启用。"""
    from src.crud.ai import crud_ai_provider
    async with SessionLocal() as db:
        if provider_id:
            provider = await crud_ai_provider.get(db, provider_id)
            if provider and provider.enabled == 1:
                return provider
        return await crud_ai_provider.get_first_enabled(db)


def _env_snapshot() -> str:
    """生成当前运行环境快照文本，注入 AI 上下文帮助定位。"""
    try:
        from src.plugins.builtin.ai_vision.runtime import deps
        status = deps.check_all_layers()
        lines = []
        for key in ("L1", "L2"):
            layer = status.get(key)
            if not layer:
                continue
            ok = sum(1 for p in layer.get("packages", []) if p.get("version"))
            total = len(layer.get("packages", []))
            missing = layer.get("missing") or []
            lines.append(
                f"- {layer.get('label', key)}: {ok}/{total} 已安装"
                f"，缺失: {('、'.join(missing)) if missing else '无'}"
            )
        return "\n".join(lines) if lines else "（暂无环境数据）"
    except Exception as exc:  # noqa: BLE001
        return f"环境快照获取失败: {exc}"


def _build_system_prompt() -> str:
    """拼接基础人设 + 当前环境快照。"""
    return AIVISION_ENV_PROMPT + "\n\n## 当前运行环境快照\n" + _env_snapshot()


@router.post("/chat")
async def ai_assist_chat(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:runtime:list"))],
):
    """非流式 AI 排查对话。Body: {"message": "...", "provider_id": 可选}"""
    from src.ai.agent import chat_non_stream

    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise AppException(msg="消息不能为空", code=400)

    provider = await _resolve_provider(body.get("provider_id"))
    if provider is None:
        raise AppException(msg="未配置可用的 AI 模型供应商，请先到「模型密钥管理」配置 DeepSeek 等供应商", code=409)

    result = await chat_non_stream(
        messages=[{"role": "user", "content": message}],
        provider=provider,
        model=body.get("model") or None,
        enable_tools=True,
        user=user,
    )
    return success_response(data=result)


@router.post("/chat/stream")
async def ai_assist_chat_stream(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    _perm: Annotated[User, Depends(_require_perm("ai_vision:runtime:list"))],
):
    """SSE 流式 AI 对话（前端打字机效果）。"""
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        raise AppException(msg="消息不能为空", code=400)

    provider = await _resolve_provider(body.get("provider_id"))
    if provider is None:
        raise AppException(msg="未配置可用的 AI 模型供应商，请先到「模型密钥管理」配置 DeepSeek 等供应商", code=409)

    from src.ai.agent import chat_stream

    async def event_generator():
        try:
            async for chunk in chat_stream(
                messages=[{"role": "user", "content": message}],
                provider=provider,
                model=body.get("model") or None,
                enable_tools=True,
                user=user,
                system_prompt=_build_system_prompt(),
            ):
                yield f"data: {chunk}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[AIVision] AI 排查对话异常: {exc}")
            err = json.dumps({"type": "error", "message": f"对话异常：{exc}"}, ensure_ascii=False)
            yield f"data: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )