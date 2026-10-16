"""InferenceBackend / BackendFactory 单元测试。

验证：
- 后端类型枚举
- 工厂按类型返回实例并缓存
- 预留后端（RKNN/SOPHON）尚未实现时回退到 ONNX
- 可用后端列表
"""
from __future__ import annotations

import pytest

from src.algo.backends.factory import BackendFactory, BackendType, InferenceBackend


def test_backend_type_enum():
    assert BackendType.ONNX.value == "onnx"
    assert BackendType.RKNN.value == "rknn"
    assert BackendType.SOPHON.value == "sophon"


def test_get_backend_onnx():
    b = BackendFactory.get_backend(BackendType.ONNX)
    assert isinstance(b, InferenceBackend)
    try:
        import onnxruntime  # noqa: F401

        assert b.is_available() is True
    except ImportError:
        pytest.skip("onnxruntime 未安装，跳过可用性断言")


def test_get_backend_cache():
    b1 = BackendFactory.get_backend(BackendType.ONNX)
    b2 = BackendFactory.get_backend(BackendType.ONNX)
    assert b1 is b2


def test_get_backend_rknn_fallback_onnx():
    b = BackendFactory.get_backend(BackendType.RKNN)
    # 预留后端尚未实现，应回退到 ONNX 后端
    assert isinstance(b, InferenceBackend)


def test_get_backend_sophon_fallback_onnx():
    b = BackendFactory.get_backend(BackendType.SOPHON)
    assert isinstance(b, InferenceBackend)


def test_list_available_backends():
    avail = BackendFactory.list_available_backends()
    try:
        import onnxruntime  # noqa: F401

        assert "onnx" in avail
    except ImportError:
        pytest.skip("onnxruntime 未安装")


def test_clear_cache():
    BackendFactory.clear_cache()
    assert BackendFactory._backend_cache == {}
