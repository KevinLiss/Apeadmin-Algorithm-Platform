"""多平台推理后端接口（预留）。

为支持多硬件平台预留的接口：
- ONNX (x86/通用)
- RKNN (瑞芯微 NPU)
- Sophon (比特大陆 TPU)

当前只实现 ONNX 后端，其他平台预留接口。
"""

from .factory import BackendFactory, InferenceBackend

__all__ = [
    "BackendFactory",
    "InferenceBackend",
]
