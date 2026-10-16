"""模型管理 API 路由。

提供 REST API 供前端调用：
- GET /api/v1/models - 列出所有模型
- GET /api/v1/models/{name} - 获取模型详情
- GET /api/v1/models/{name}/versions - 列出模型所有版本
- POST /api/v1/models/{name}/switch - 切换默认版本
- POST /api/v1/models/{name}/validate - 校验模型
- GET /api/v1/models/cache - 获取缓存信息

所有成功响应统一包裹为 {code, msg, data} 信封（与全局响应拦截器约定一致）。
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.core.exceptions import success_response
from ..model_manager import ModelLoader, ModelRegistry, ModelValidator

router = APIRouter(prefix="/models", tags=["模型管理"])


class ModelInfoResponse(BaseModel):
    """模型信息响应。"""

    name: str
    version: str
    file_path: str
    file_size: int
    input_size: int
    description: str
    category: str
    is_default: bool
    is_active: bool


class VersionSwitchRequest(BaseModel):
    """版本切换请求。"""

    version: str


class ValidationResponse(BaseModel):
    """校验结果响应。"""

    is_valid: bool
    errors: list[str]
    warnings: list[str]


@router.get("")
async def list_models():
    """列出所有已注册模型。"""
    models = ModelRegistry.list_models()
    data = [
        {
            "name": m.name,
            "version": m.version,
            "file_path": m.file_path,
            "file_size": m.file_size,
            "input_size": m.input_size,
            "description": m.description,
            "category": m.category,
            "is_default": m.is_default,
            "is_active": m.is_active,
        }
        for m in models
    ]
    return success_response(data=data)


@router.get("/categories")
async def list_categories():
    """列出模型类别。"""
    models = ModelRegistry.list_models()
    categories = sorted(set(m.category for m in models if m.category))
    return success_response(data={"categories": categories})


@router.get("/{name}")
async def get_model(name: str):
    """获取模型详情（默认版本）。"""
    model = ModelRegistry.get_default(name)
    if model is None:
        raise HTTPException(status_code=404, detail=f"模型不存在: {name}")
    return success_response(
        data={
            "name": model.name,
            "version": model.version,
            "file_path": model.file_path,
            "file_size": model.file_size,
            "input_size": model.input_size,
            "description": model.description,
            "category": model.category,
            "is_default": model.is_default,
            "is_active": model.is_active,
        }
    )


@router.get("/{name}/versions")
async def list_versions(name: str):
    """列出模型所有版本。"""
    versions = ModelRegistry.list_versions(name)
    if not versions:
        raise HTTPException(status_code=404, detail=f"模型不存在: {name}")
    return success_response(
        data={
            "name": name,
            "versions": [
                {
                    "version": v.version,
                    "is_default": v.is_default,
                    "is_active": v.is_active,
                    "description": v.description,
                }
                for v in versions
            ],
        }
    )


@router.post("/{name}/switch")
async def switch_version(name: str, request: VersionSwitchRequest):
    """切换默认版本。"""
    success = ModelRegistry.set_default(name, request.version)
    if not success:
        raise HTTPException(
            status_code=404, detail=f"版本不存在: {name} v{request.version}"
        )
    # 清除旧缓存
    ModelLoader.unload(name)
    return success_response(msg=f"已切换 {name} 默认版本为 {request.version}")


@router.post("/{name}/validate")
async def validate_model(name: str, version: str | None = None):
    """校验模型完整性。"""
    result = await ModelValidator.validate(name, version)
    return success_response(
        data={
            "is_valid": result.is_valid,
            "errors": result.errors,
            "warnings": result.warnings,
        }
    )


@router.post("/validate-all")
async def validate_all_models():
    """校验所有模型。"""
    results = await ModelValidator.validate_all()
    summary = {
        "total": len(results),
        "valid": sum(1 for r in results.values() if r.is_valid),
        "invalid": sum(1 for r in results.values() if not r.is_valid),
        "details": {
            k: {
                "is_valid": v.is_valid,
                "errors": v.errors,
                "warnings": v.warnings,
            }
            for k, v in results.items()
        },
    }
    return success_response(data=summary)


@router.get("/cache/info")
async def get_cache_info():
    """获取模型缓存信息。"""
    return success_response(data=ModelLoader.get_cache_info())


@router.post("/cache/clear")
async def clear_cache():
    """清空模型缓存。"""
    ModelLoader.unload_all()
    return success_response(msg="模型缓存已清空")
