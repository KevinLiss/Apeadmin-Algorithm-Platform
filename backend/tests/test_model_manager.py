"""ModelRegistry / ModelValidator 单元测试。

通过 autouse fixture 阻止 ``_ensure_init`` 从内置 manifest 加载真实模型，
保证每个测试在干净的注册表上运行。
"""
from __future__ import annotations

import pytest

from src.model_manager.registry import ModelInfo, ModelRegistry
from src.model_manager.validator import ModelValidator, ValidationResult


@pytest.fixture(autouse=True)
def _reset_registry():
    saved_models = ModelRegistry._models
    saved_init = ModelRegistry._initialized
    ModelRegistry._models = {}
    # 置为已初始化，阻止 _ensure_init 从 manifest 加载真实模型
    ModelRegistry._initialized = True
    yield
    ModelRegistry._models = saved_models
    ModelRegistry._initialized = saved_init


def _make_info(
    name: str = "m1",
    version: str = "1.0.0",
    file_path: str = "/tmp/nonexist.onnx",
    is_default: bool = True,
) -> ModelInfo:
    return ModelInfo(
        name=name,
        version=version,
        file_path=file_path,
        file_size=0,
        is_default=is_default,
    )


def test_register_and_get():
    info = _make_info()
    ModelRegistry.register(info)
    assert ModelRegistry.get("m1") is info
    assert ModelRegistry.get("m1", "1.0.0") is info
    assert ModelRegistry.exists("m1") is True
    assert ModelRegistry.exists("nope") is False


def test_register_clears_other_default():
    a = _make_info(name="m", version="1.0.0", is_default=True)
    b = _make_info(name="m", version="2.0.0", is_default=True)
    ModelRegistry.register(a)
    ModelRegistry.register(b)
    assert ModelRegistry.get("m", "1.0.0").is_default is False
    assert ModelRegistry.get("m", "2.0.0").is_default is True
    assert ModelRegistry.get_default("m").version == "2.0.0"


def test_set_default():
    a = _make_info(name="m", version="1.0.0", is_default=True)
    b = _make_info(name="m", version="2.0.0", is_default=False)
    ModelRegistry.register(a)
    ModelRegistry.register(b)
    assert ModelRegistry.set_default("m", "2.0.0") is True
    assert ModelRegistry.get_default("m").version == "2.0.0"


def test_set_default_missing():
    assert ModelRegistry.set_default("missing", "1.0.0") is False


def test_list_and_unregister():
    ModelRegistry.register(_make_info(name="x", version="1.0.0"))
    ModelRegistry.register(_make_info(name="y", version="1.0.0"))
    assert len(ModelRegistry.list_models()) == 2
    assert ModelRegistry.unregister("x", "1.0.0") is True
    assert ModelRegistry.list_models()[0].name == "y"


def test_infer_category():
    assert ModelRegistry._infer_category("face-recognition") == "recognition"
    assert ModelRegistry._infer_category("yolo-detection") == "detection"
    assert ModelRegistry._infer_category("drowning-model") == "detection"
    assert ModelRegistry._infer_category("something-else") == "unknown"


@pytest.mark.asyncio
async def test_validate_missing_model():
    res = await ModelValidator.validate("nope")
    assert res.is_valid is False
    assert any("模型不存在" in e for e in res.errors)


@pytest.mark.asyncio
async def test_validate_missing_file():
    ModelRegistry.register(_make_info(name="z", version="1.0.0", file_path="/no/such/file.onnx"))
    res = await ModelValidator.validate("z")
    assert res.is_valid is False
    assert any("文件不存在" in e for e in res.errors)


@pytest.mark.asyncio
async def test_validate_all():
    ModelRegistry.register(_make_info(name="z", version="1.0.0", file_path="/no/such/file.onnx"))
    results = await ModelValidator.validate_all()
    assert "z@1.0.0" in results
    assert isinstance(results["z@1.0.0"], ValidationResult)


def test_sha256_compute(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hello")
    sha = ModelValidator._compute_sha256(f)
    assert sha == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
