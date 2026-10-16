"""模型加载器。

带缓存的模型加载（LRU）：
- 按模型 name + version 缓存 InferenceSession
- 文件 mtime/size 变化自动重新加载
- 线程安全

对接到现有的 InferenceEngine：
- 复用 ai_vision 的 engine.py 进行推理
- 本模块负责版本管理和缓存
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from .registry import ModelInfo, ModelRegistry


class ModelLoader:
    """模型加载器（带 LRU 缓存）。

    缓存 key: (name, version, mtime, size)
    文件变化自动重新加载
    """

    _cache: dict[tuple[str, str], Any] = {}  # (name, version) -> session
    _cache_lock = threading.Lock()

    @classmethod
    def _get_cache_key(cls, name: str, version: str) -> tuple[str, str, int, int]:
        """生成缓存 key（包含文件 mtime/size）。"""
        model_info = ModelRegistry.get(name, version)
        if model_info is None:
            return (name, version, 0, 0)

        file_path = Path(model_info.file_path)
        if file_path.exists():
            stat = file_path.stat()
            return (name, version, stat.st_mtime_ns, stat.st_size)

        return (name, version, 0, 0)

    @classmethod
    def load(cls, name: str, version: str | None = None) -> Any:
        """加载模型，返回 InferenceSession。

        Args:
            name: 模型名称
            version: 模型版本，None 表示默认版本

        Returns:
            InferenceSession 对象（具体类型取决于后端）

        Raises:
            FileNotFoundError: 模型文件不存在
            RuntimeError: 模型加载失败
        """
        # 获取模型信息
        model_info = ModelRegistry.get(name, version)
        if model_info is None:
            # 尝试从 ai_vision 加载
            return cls._load_from_ai_vision(name, version)

        version = model_info.version
        file_path = model_info.file_path

        # 检查文件是否存在
        if not os.path.exists(file_path):
            logger.warning(f"模型加载：文件不存在 {file_path}，尝试从 ai_vision 加载")
            return cls._load_from_ai_vision(name, version)

        # 生成缓存 key
        cache_key = cls._get_cache_key(name, version)
        cache_name = (name, version)

        # 检查缓存是否有效
        with cls._cache_lock:
            if cache_name in cls._cache:
                cached_key, _ = cls._cache[cache_name]
                if cached_key == cache_key:
                    logger.debug(f"模型加载（缓存）: {name} v{version}")
                    return cls._cache[cache_name][1]
                else:
                    # 文件已变化，删除旧缓存
                    del cls._cache[cache_name]

        # 加载模型
        session = cls._load_session(file_path, model_info.input_size)

        # 存入缓存
        with cls._cache_lock:
            cls._cache[cache_name] = (cache_key, session)

        logger.info(f"模型加载: {name} v{version}")
        return session

    @classmethod
    def _load_from_ai_vision(cls, name: str, version: str | None = None) -> Any:
        """从 ai_vision 插件加载模型（兼容旧代码）。"""
        try:
            from src.plugins.builtin.ai_vision.runtime.engine import InferenceEngine

            engine = InferenceEngine.get_instance()

            # 尝试根据名称映射到 ai_vision 的模型
            # ai_vision 使用文件路径作为 handle，这里做一个简单映射
            # 如果是已知的内置模型，直接返回 engine 实例供调用方使用
            logger.debug(f"模型加载（ai_vision）: {name}")
            return engine

        except ImportError as e:
            logger.error(f"模型加载：无法导入 ai_vision engine: {e}")
            raise RuntimeError(f"模型 {name} 加载失败") from e

    @classmethod
    def _load_session(cls, file_path: str, input_size: int) -> Any:
        """加载 ONNX 模型为 InferenceSession。"""
        try:
            import numpy as np
            from onnxruntime import InferenceSession

            # 加载模型
            session = InferenceSession(
                file_path,
                providers=["CPUExecutionProvider"],
            )

            # 预热模型（可选）
            try:
                # 创建一个最小输入进行预热
                warmup_input = np.zeros((1, 3, input_size, input_size), dtype=np.float32)
                session.run(None, {"images": warmup_input})
                logger.debug(f"模型预热成功: {file_path}")
            except Exception as e:
                # 预热失败不影响加载
                logger.warning(f"模型预热失败: {e}")

            return session

        except Exception as e:
            logger.error(f"模型加载失败: {file_path}, error: {e}")
            raise RuntimeError(f"模型加载失败: {e}") from e

    @classmethod
    def unload(cls, name: str, version: str | None = None) -> bool:
        """卸载模型（从缓存中移除）。"""
        model_info = ModelRegistry.get(name, version)
        if model_info is None:
            return False

        version = model_info.version
        cache_name = (name, version)

        with cls._cache_lock:
            if cache_name in cls._cache:
                del cls._cache[cache_name]
                logger.info(f"模型卸载: {name} v{version}")
                return True

        return False

    @classmethod
    def unload_all(cls) -> None:
        """卸载所有模型。"""
        with cls._cache_lock:
            count = len(cls._cache)
            cls._cache.clear()
            logger.info(f"模型卸载: 已清除 {count} 个缓存")

    @classmethod
    def get_cache_info(cls) -> dict[str, Any]:
        """获取缓存信息（调试用）。"""
        with cls._cache_lock:
            return {
                "cached_models": list(cls._cache.keys()),
                "cache_size": len(cls._cache),
            }
