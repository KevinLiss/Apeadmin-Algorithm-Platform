"""推理后端工厂。

根据配置选择合适的推理后端：
- onnx: ONNX Runtime (x86/通用)
- rknn: 瑞芯微 NPU (预留)
- sophon: 比特大陆 TPU (预留)
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from loguru import logger


class BackendType(str, Enum):
    """推理后端类型。"""

    ONNX = "onnx"
    RKNN = "rknn"  # 预留
    SOPHON = "sophon"  # 预留


class InferenceBackend(ABC):
    """推理后端抽象基类。"""

    @abstractmethod
    def load_model(self, model_path: str) -> Any:
        """加载模型。"""
        pass

    @abstractmethod
    def inference(self, session: Any, input_data: Any) -> Any:
        """执行推理。"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查后端是否可用。"""
        pass


class BackendFactory:
    """推理后端工厂。"""

    _backend_cache: dict[str, InferenceBackend] = {}

    @classmethod
    def get_backend(cls, backend_type: BackendType = BackendType.ONNX) -> InferenceBackend:
        """获取推理后端实例。"""
        if backend_type.value in cls._backend_cache:
            return cls._backend_cache[backend_type.value]

        # 创建后端实例
        if backend_type == BackendType.ONNX:
            from .onnx import ONNXBackend

            backend = ONNXBackend()
        elif backend_type == BackendType.RKNN:
            logger.warning("RKNN 后端尚未实现，使用 ONNX 后端作为 fallback")
            from .onnx import ONNXBackend

            backend = ONNXBackend()
        elif backend_type == BackendType.SOPHON:
            logger.warning("Sophon 后端尚未实现，使用 ONNX 后端作为 fallback")
            from .onnx import ONNXBackend

            backend = ONNXBackend()
        else:
            raise ValueError(f"未知的后端类型: {backend_type}")

        cls._backend_cache[backend_type.value] = backend
        return backend

    @classmethod
    def list_available_backends(cls) -> list[str]:
        """列出可用的后端。"""
        available = []
        for backend_type in BackendType:
            try:
                backend = cls.get_backend(backend_type)
                if backend.is_available():
                    available.append(backend_type.value)
            except Exception:
                pass
        return available

    @classmethod
    def clear_cache(cls) -> None:
        """清除后端缓存。"""
        cls._backend_cache.clear()
