"""AI 视觉平台插件 API 路由包。

各资源 CRUD 路由按任务清单逐期在子模块中实现（camera / category /
event / task / alarm / sample / runtime_env 等），并在本文件聚合到
``router``，由 ``plugin.py`` 的 ``register()`` 挂载。

路由前缀约定：``/ai-vision``（挂载后为 ``{settings.API_PREFIX}/ai-vision``），
权限标识：``ai_vision:{resource}:{action}`。

静态图片（抓拍图/样本图）不在本包挂载——FastAPI 0.141 的
``include_router`` 不会传递子 router 上的 ``mount()``，故静态目录由
``plugin.py`` 的 ``register()`` 直接 ``app.mount()``（见任务 2.5）。
"""
from fastapi import APIRouter

from src.core.exceptions import success_response
from src.plugins.builtin.ai_vision.api.ai_assist import router as ai_assist_router
from src.plugins.builtin.ai_vision.api.alarms import router as alarms_router
from src.plugins.builtin.ai_vision.api.cameras import router as cameras_router
from src.plugins.builtin.ai_vision.api.categories import router as categories_router
from src.plugins.builtin.ai_vision.api.dashboard import router as dashboard_router
from src.plugins.builtin.ai_vision.api.env_files import router as env_files_router
from src.plugins.builtin.ai_vision.api.events import router as events_router
from src.plugins.builtin.ai_vision.api.models import router as models_router
from src.plugins.builtin.ai_vision.api.runtime_env import router as runtime_env_router
from src.plugins.builtin.ai_vision.api.samples import router as samples_router
from src.plugins.builtin.ai_vision.api.tasks import router as tasks_router
from src.plugins.builtin.ai_vision.api.videos import router as videos_router

router = APIRouter(prefix="/ai-vision", tags=["AI 视觉平台"])

# 子模块路由聚合
router.include_router(ai_assist_router)
router.include_router(alarms_router)
router.include_router(cameras_router)
router.include_router(categories_router)
router.include_router(dashboard_router)
router.include_router(env_files_router)
router.include_router(events_router)
router.include_router(models_router)
router.include_router(runtime_env_router)
router.include_router(samples_router)
router.include_router(tasks_router)
router.include_router(videos_router)


@router.get("/health")
async def health_check() -> dict:
    """插件健康检查：确认路由已挂载。"""
    return success_response(data={"name": "ai_vision", "status": "ok"})