"""模型注册表。

管理模型元数据：
- 模型名称、版本、文件路径
- 默认版本切换
- 模型状态（active/inactive/deprecated）

数据存储：
- 内存缓存（运行时）
- 数据库表：ai_model_registry（可选，持久化）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ModelInfo:
    """模型元数据。"""

    name: str  # 模型唯一名称，如 "face-recognition"
    version: str  # 版本号，如 "1.0.0"
    file_path: str  # ONNX 文件路径
    file_size: int  # 文件大小（字节）
    sha256: str | None = None  # SHA256 校验值（可选）
    input_size: int = 640  # 输入尺寸
    description: str = ""  # 描述
    category: str = ""  # 类别：detection/recognition/pose/antispoof
    is_default: bool = False  # 是否为该模型的默认版本
    is_active: bool = True  # 是否激活
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外元数据


class ModelRegistry:
    """模型注册表（内存缓存）。"""

    _models: dict[str, dict[str, ModelInfo]] = {}  # name -> {version -> ModelInfo}
    _initialized: bool = False

    @classmethod
    def _ensure_init(cls) -> None:
        """初始化：加载 manifest.json（如果存在）。"""
        if cls._initialized:
            return

        # 加载内置模型清单
        cls._load_builtin_manifest()
        cls._initialized = True

    @classmethod
    def _load_builtin_manifest(cls) -> None:
        """加载内置 manifest.json。"""
        # 尝试多个可能的 manifest 路径
        possible_paths = [
            Path(__file__).parent.parent
            / "plugins/builtin/ai_vision/assets/models/manifest.json",
            Path(__file__).parent.parent.parent
            / "plugins/builtin/ai_vision/assets/models/manifest.json",
        ]

        for manifest_path in possible_paths:
            if manifest_path.exists():
                cls._load_from_manifest(manifest_path)
                return

        logger.warning("模型注册表：未找到 manifest.json，跳过内置模型加载")

    @classmethod
    def _load_from_manifest(cls, manifest_path: Path) -> None:
        """从 manifest.json 加载模型清单。"""
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for model_data in data.get("models", []):
                name = model_data.get("name", "")
                version = model_data.get("version", "1.0.0")
                file_name = model_data.get("file", "")

                # 解析文件路径
                model_dir = manifest_path.parent
                file_path = str(model_dir / file_name)

                info = ModelInfo(
                    name=name,
                    version=version,
                    file_path=file_path,
                    file_size=model_data.get("file_size", 0),
                    sha256=model_data.get("sha256"),
                    input_size=model_data.get("input_size", 640),
                    description=model_data.get("description", ""),
                    category=cls._infer_category(name),
                    is_default=True,  # 首个版本设为默认
                    metadata={
                        "source": model_data.get("source", "builtin"),
                        "url": model_data.get("url", ""),
                        "license_note": model_data.get("license_note", ""),
                    },
                )

                cls.register(info)
                logger.info(f"模型注册表：已加载 {name} v{version}")

        except Exception as e:
            logger.error(f"模型注册表：加载 manifest.json 失败: {e}")

    @staticmethod
    def _infer_category(name: str) -> str:
        """根据名称推断模型类别。"""
        name_lower = name.lower()
        if "detection" in name_lower or name_lower.endswith("-yunet"):
            return "detection"
        if "recognition" in name_lower or name_lower.endswith("-sface"):
            return "recognition"
        if "pose" in name_lower:
            return "pose"
        if "antispoof" in name_lower or "liveness" in name_lower:
            return "antispoof"
        if "drowning" in name_lower:
            return "detection"
        if "fire" in name_lower or "smoke" in name_lower:
            return "detection"
        if "yolo" in name_lower:
            return "detection"
        return "unknown"

    @classmethod
    def register(cls, model_info: ModelInfo) -> None:
        """注册模型。"""
        cls._ensure_init()

        name = model_info.name
        version = model_info.version

        if name not in cls._models:
            cls._models[name] = {}

        # 如果设为默认版本，先取消其他默认
        if model_info.is_default:
            for v, info in cls._models[name].items():
                info.is_default = False

        cls._models[name][version] = model_info
        logger.debug(f"模型注册表：注册 {name} v{version}")

    @classmethod
    def unregister(cls, name: str, version: str) -> bool:
        """注销模型。"""
        cls._ensure_init()

        if name in cls._models and version in cls._models[name]:
            del cls._models[name][version]
            # 如果删除的是默认版本，设置最新的为默认
            if name in cls._models and cls._models[name]:
                latest = sorted(cls._models[name].keys(), reverse=True)[0]
                cls._models[name][latest].is_default = True
            return True
        return False

    @classmethod
    def get(cls, name: str, version: str | None = None) -> ModelInfo | None:
        """获取模型信息。"""
        cls._ensure_init()

        if name not in cls._models:
            return None

        if version is None:
            # 返回默认版本
            for info in cls._models[name].values():
                if info.is_default:
                    return info
            # 没有默认则返回第一个
            return next(iter(cls._models[name].values()))

        return cls._models[name].get(version)

    @classmethod
    def get_default(cls, name: str) -> ModelInfo | None:
        """获取默认版本。"""
        return cls.get(name, None)

    @classmethod
    def set_default(cls, name: str, version: str) -> bool:
        """设置默认版本。"""
        cls._ensure_init()

        if name not in cls._models or version not in cls._models[name]:
            return False

        # 取消其他默认
        for v, info in cls._models[name].items():
            info.is_default = (v == version)

        return True

    @classmethod
    def list_models(cls) -> list[ModelInfo]:
        """列出所有模型。"""
        cls._ensure_init()

        result = []
        for versions in cls._models.values():
            result.extend(versions.values())
        return result

    @classmethod
    def list_versions(cls, name: str) -> list[ModelInfo]:
        """列出指定模型的所有版本。"""
        cls._ensure_init()

        if name not in cls._models:
            return []
        return list(cls._models[name].values())

    @classmethod
    def list_by_category(cls, category: str) -> list[ModelInfo]:
        """按类别列出模型。"""
        cls._ensure_init()

        result = []
        for versions in cls._models.values():
            for info in versions.values():
                if info.category == category:
                    result.append(info)
        return result

    @classmethod
    def exists(cls, name: str, version: str | None = None) -> bool:
        """检查模型是否存在。"""
        return cls.get(name, version) is not None
