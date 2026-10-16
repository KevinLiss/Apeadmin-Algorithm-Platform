"""ONNX Runtime 后端实现。

基于 onnxruntime 的推理后端，支持 CPU 和 GPU。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from .factory import InferenceBackend


class ONNXBackend(InferenceBackend):
    """ONNX Runtime 推理后端。"""

    def __init__(self):
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """检查 onnxruntime 是否可用。"""
        try:
            import onnxruntime

            return True
        except ImportError:
            logger.warning("ONNX Runtime 未安装")
            return False

    def is_available(self) -> bool:
        """检查后端是否可用。"""
        return self._available

    def load_model(self, model_path: str) -> Any:
        """加载 ONNX 模型。"""
        if not self._available:
            raise RuntimeError("ONNX Runtime 不可用")

        from onnxruntime import InferenceSession

        # 根据环境选择 provider
        providers = self._get_providers()

        session = InferenceSession(model_path, providers=providers)
        logger.info(f"ONNX 模型加载成功: {Path(model_path).name}, providers: {providers}")
        return session

    def _get_providers(self) -> list[str]:
        """获取可用的 providers。"""
        from onnxruntime import get_available_providers

        available = get_available_providers()

        # 优先使用 GPU
        preferred = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        providers = []

        for p in preferred:
            if p in available:
                providers.append(p)

        if not providers:
            providers = available if available else ["CPUExecutionProvider"]

        return providers

    def inference(self, session: Any, input_data: np.ndarray) -> np.ndarray:
        """执行推理。

        Args:
            session: ONNX InferenceSession
            input_data: 输入数据 (N, C, H, W)

        Returns:
            输出数据
        """
        # 获取输入名称
        input_name = session.get_inputs()[0].name

        # 执行推理
        outputs = session.run(None, {input_name: input_data})

        return outputs[0]

    def warmup(self, session: Any, input_shape: tuple = (1, 3, 640, 640)) -> None:
        """预热模型。

        Args:
            session: ONNX InferenceSession
            input_shape: 输入 shape
        """
        import numpy as np

        # 创建随机输入
        dummy_input = np.random.randn(*input_shape).astype(np.float32)

        # 执行推理
        self.inference(session, dummy_input)
        logger.debug(f"ONNX 模型预热完成: shape={input_shape}")
