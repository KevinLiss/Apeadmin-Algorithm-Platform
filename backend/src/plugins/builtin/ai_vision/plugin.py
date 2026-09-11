"""AI 视觉平台插件入口类。

生命周期与 dev_example 一致：
- ``install()``：建表（10 张）+ seed（菜单/权限/内置类别）
- ``uninstall()``：删表 + 清理菜单
- ``register()``：挂载路由（``/api/v1/ai-vision/...``）
- ``register_mcp_tools()``：预留（二期联调阶段接入）

.. note::
   运行时依赖（onnxruntime / opencv / torch / ultralytics）不作为本插件
   的硬依赖——全部延迟导入，由「运行环境」页在后台分层一键安装。
"""
from fastapi import FastAPI
from loguru import logger

from src.core.config import settings
from src.db import Base, SessionLocal
from src.mcp.decorators import mcp_tool
from src.plugins import PluginInterface
from src.plugins.builtin.ai_vision.models import (  # noqa: F401
    AIVisionAlarm,
    AIVisionCamera,
    AIVisionCategory,
    AIVisionEvent,
    AIVisionEventModel,
    AIVisionModel,
    AIVisionRuntimeStatus,
    AIVisionSample,
    AIVisionTask,
    AIVisionTraining,
)
from src.plugins.builtin.ai_vision.seed import seed_ai_vision_data

# 建表时需要显式列出全部表，避免 create_all 依赖导入顺序
_AIVISION_TABLES = [
    AIVisionCamera.__table__,
    AIVisionCategory.__table__,
    AIVisionEvent.__table__,
    AIVisionModel.__table__,
    AIVisionEventModel.__table__,
    AIVisionTask.__table__,
    AIVisionAlarm.__table__,
    AIVisionSample.__table__,
    AIVisionTraining.__table__,
    AIVisionRuntimeStatus.__table__,
]


class AIVisionPlugin(PluginInterface):
    """AI 视觉算法管理平台插件。

    闭环：类别库 → 识别事件 → 摄像头推理 → 告警 → 样本库 → 训练 → 模型绑定回事件。
    """

    # ── 插件元数据（与 plugin.json 对应）──────────────
    name = "ai_vision"
    display_name = "AI 视觉平台"
    description = "AI 视觉算法管理平台：类别库 → 识别事件 → 摄像头推理 → 告警 → 样本训练闭环"
    version = "0.1.0"
    author = "KevinLiss"

    # ── 生命周期：内存加载 ─────────────────────────────
    def on_load(self) -> None:
        """插件被加载到内存时调用（模块被 import）。

        此时尚未建表、尚未注册路由，只初始化内存状态。
        """
        logger.info("[AIVision] loaded into memory")

    # ── 生命周期：安装（建表 + seed）──────────────────
    async def install(self) -> None:
        """插件被启用时调用。

        职责：
        1. 创建 10 张 ``ai_vision_*`` 表
        2. Seed 菜单树 + 权限 + admin 角色绑定 + 内置类别
        """
        from src.db import engine
        from src.models.mixins import IDMixin, TimestampMixin  # noqa: F401 — ensure mixins imported

        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=_AIVISION_TABLES
                )
            )

        async with SessionLocal() as db:
            await seed_ai_vision_data(db)
            await db.commit()
        logger.info("[AIVision] installed — 10 tables ready, menus + categories seeded")

    # ── 生命周期：卸载（清理）────────────────────────
    async def uninstall(self) -> None:
        """插件被卸载时调用（keep_data=False 时）。

        职责：
        1. 删除 10 张插件表（不可逆）
        2. 清理菜单（permission 前缀 + path 前缀）
        """
        from sqlalchemy import delete

        from src.db import engine
        from src.models import Menu

        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.drop_all(
                    sync_conn, tables=_AIVISION_TABLES
                )
            )

        async with SessionLocal() as db:
            # 清理插件菜单（按钮权限以 ai_vision: 开头 / 页面路径 /ai-vision/...）
            await db.execute(delete(Menu).where(Menu.permission.like("ai_vision:%")))
            await db.execute(delete(Menu).where(Menu.path.like("/ai-vision%")))
            await db.commit()
        logger.info("[AIVision] uninstalled — tables dropped, menus removed")

    # ── 生命周期：注册路由 ─────────────────────────────
    def register(self, app: FastAPI) -> None:
        """注册路由。最终路径：``{settings.API_PREFIX}/ai-vision/...``。

        同时：
        1. 直接在 app 上挂载静态图片目录（抓拍图/样本图）——
           FastAPI 0.141 的 ``include_router`` 不传递子 router 的
           ``mount()``，故必须在此处挂 ``app.mount``：
           ``{settings.API_PREFIX}/ai-vision/media`` → ``uploads/ai_vision``
        2. 扫描 ``ai_vision_tasks`` 中 status=running 的任务自动恢复运行
           （服务重启后无人工干预自动拉起，见任务 2.4）。
        """
        from fastapi.staticfiles import StaticFiles

        from src.plugins.builtin.ai_vision.api import router
        from src.plugins.builtin.ai_vision.paths import UPLOADS_AI_VISION_DIR

        app.include_router(router, prefix=settings.API_PREFIX)

        # 静态图片：挂载点 /api/v1/ai-vision/media → backend/uploads/ai_vision
        UPLOADS_AI_VISION_DIR.mkdir(parents=True, exist_ok=True)
        app.mount(
            f"{settings.API_PREFIX}/ai-vision/media",
            StaticFiles(directory=str(UPLOADS_AI_VISION_DIR)),
            name="ai_vision_media",
        )
        logger.info("[AIVision] registered — routes + media mounted")

        # 重启恢复：DB_READY 后扫描 running 任务
        try:
            import asyncio
            from src.plugins.builtin.ai_vision.runtime.manager import get_manager

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(get_manager().restore_running_tasks())
            else:
                loop.run_until_complete(get_manager().restore_running_tasks())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] 自动恢复任务失败: {exc}")

    # ── 生命周期：注册 MCP 工具 ─────────────────────────
    def register_mcp_tools(self) -> None:
        """注册 AI 环境排查工具（供「运行环境」页 AI 助手调用）。

        仅注册「查状态 / 测环境 / 装依赖」三项只读或可控动作，
        **刻意不注册卸载**——AI 助手可排查、可安装，但不能卸载依赖
        （卸载属高风险操作，仅允许人工在页面操作）。
        """
        try:
            from src.mcp.decorators import register_decorated_tools
            register_decorated_tools(self, plugin_name=self.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] MCP tools registration failed: {exc}")

    # ── AI 排查工具（MCP 装饰器注册）───────────────────
    @mcp_tool(
        "ai_vision_runtime_status",
        "查询 AI 视觉平台 L1 推理层 / L2 训练层依赖安装状态（含缺失包、版本）。",
        permissions=["ai_vision:runtime:list"],
        category="ai_vision",
    )
    def runtime_status_tool(self, layer: str = "") -> str:
        """查询运行环境依赖状态。

        Args:
            layer: L1 或 L2，留空查询全部。
        """
        from src.plugins.builtin.ai_vision.runtime import deps
        layer = (layer or "").upper()
        if layer:
            return __import__("json").dumps(deps.check_layer(layer), ensure_ascii=False)
        return __import__("json").dumps(deps.check_all_layers(), ensure_ascii=False)

    @mcp_tool(
        "ai_vision_runtime_test",
        "对 AI 视觉平台运行环境做深度健康测试（真实 import + 模型加载探测），返回各包是否可用。",
        permissions=["ai_vision:runtime:test"],
        category="ai_vision",
    )
    def check_env_test_tool(self, layer: str = "") -> str:
        """执行环境健康测试。

        Args:
            layer: L1/L2，留空则全部。
        Returns:
            健康测试结果（各包 importable 状态 + 模型加载探测）。
        """
        from src.plugins.builtin.ai_vision.runtime import deps
        layers = ["L1", "L2"] if not (layer or "").upper() else [(layer or "").upper()]
        out = {}
        for l in layers:
            try:
                out[l] = deps.test_layer(l)
            except Exception as exc:  # noqa: BLE001
                out[l] = {"layer": l, "ok": False, "error": str(exc)}
        return __import__("json").dumps(out, ensure_ascii=False)

    @mcp_tool(
        "ai_vision_runtime_install",
        "启动 AI 视觉平台指定依赖层（L1 推理 / L2 训练）的安装。安装是后台任务，本工具只触发启动并返回 pid。",
        permissions=["ai_vision:runtime:install"],
        category="ai_vision",
    )
    def install_layer_tool(self, layer: str = "L1") -> str:
        """启动一层依赖安装。

        Args:
            layer: L1 或 L2，默认 L1。
        Returns:
            安装启动结果。
        """
        from src.plugins.builtin.ai_vision.runtime import deps
        try:
            result = deps.install_layer((layer or "L1").upper())
            return __import__("json").dumps({**result, "message": f"{layer} 依赖安装已启动（后台）"}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return __import__("json").dumps({"error": str(exc)}, ensure_ascii=False)

    # ── 生命周期：卸载运行时资源 ───────────────────────
    def unregister(self, app: FastAPI) -> None:
        """插件被禁用时调用。路由/MCP 工具由插件管理器自动移除。

        同时停止全部推理 worker（释放 VideoCapture / 推理会话）。
        """
        try:
            from src.plugins.builtin.ai_vision.runtime.manager import get_manager

            get_manager().stop_all()
            logger.info("[AIVision] all workers stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[AIVision] 停止 worker 时异常: {exc}")
        logger.info("[AIVision] unregistered — runtime resources released")