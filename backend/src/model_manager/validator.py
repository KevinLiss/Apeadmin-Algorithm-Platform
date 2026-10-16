"""模型校验器。

功能：
- 文件存在性检查
- SHA256 完整性校验
- 输入输出 shape 验证
- 模型可用性测试（dry-run）
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from loguru import logger

from .registry import ModelInfo, ModelRegistry


class ValidationResult:
    """校验结果。"""

    def __init__(
        self,
        is_valid: bool,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
    ):
        self.is_valid = is_valid
        self.errors = errors or []
        self.warnings = warnings or []

    def __repr__(self) -> str:
        status = "✓" if self.is_valid else "✗"
        parts = [f"{status} 校验{'通过' if self.is_valid else '失败'}"]
        if self.errors:
            parts.append(f"错误: {'; '.join(self.errors)}")
        if self.warnings:
            parts.append(f"警告: {'; '.join(self.warnings)}")
        return " ".join(parts)


class ModelValidator:
    """模型校验器。"""

    @classmethod
    async def validate(
        cls,
        name: str,
        version: str | None = None,
        check_sha256: bool = True,
        check_shape: bool = True,
    ) -> ValidationResult:
        """校验模型。

        Args:
            name: 模型名称
            version: 模型版本
            check_sha256: 是否检查 SHA256
            check_shape: 是否检查输入输出 shape

        Returns:
            ValidationResult
        """
        model_info = ModelRegistry.get(name, version)
        if model_info is None:
            return ValidationResult(
                is_valid=False,
                errors=[f"模型不存在: {name} v{version or 'default'}"],
            )

        errors = []
        warnings = []

        # 1. 文件存在性检查
        file_path = Path(model_info.file_path)
        if not file_path.exists():
            errors.append(f"文件不存在: {file_path}")
            return ValidationResult(is_valid=False, errors=errors)

        # 2. 文件大小检查
        actual_size = file_path.stat().st_size
        if model_info.file_size > 0 and actual_size != model_info.file_size:
            warnings.append(
                f"文件大小不匹配: 期望 {model_info.file_size} 字节, 实际 {actual_size} 字节"
            )

        # 3. SHA256 校验
        if check_sha256 and model_info.sha256:
            actual_sha256 = cls._compute_sha256(file_path)
            if actual_sha256 != model_info.sha256:
                errors.append(
                    f"SHA256 不匹配: 期望 {model_info.sha256[:16]}..., 实际 {actual_sha256[:16]}..."
                )

        # 4. Shape 校验
        if check_shape and not errors:
            shape_errors = await cls._validate_shape(model_info)
            errors.extend(shape_errors)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _compute_sha256(file_path: Path) -> str:
        """计算文件 SHA256。"""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # 分块读取，避免大文件占用过多内存
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        return sha256_hash.hexdigest()

    @staticmethod
    async def _validate_shape(model_info: ModelInfo) -> list[str]:
        """验证模型输入输出 shape。"""
        errors = []

        try:
            from onnxruntime import InferenceSession
        except ImportError:
            logger.warning("模型校验：无法导入 onnxruntime，跳过 shape 校验")
            return errors

        try:
            session = InferenceSession(
                model_info.file_path,
                providers=["CPUExecutionProvider"],
            )

            # 检查输入
            inputs = session.get_inputs()
            if not inputs:
                errors.append("模型无输入")
            else:
                # 检查第一个输入的 shape
                input_shape = inputs[0].shape
                logger.debug(f"模型输入 shape: {input_shape}")

                # 简单验证：检查是否为 4 维 [N, C, H, W]
                if len(input_shape) != 4:
                    warnings = [f"输入 shape 维度异常: {input_shape}"]

            # 检查输出
            outputs = session.get_outputs()
            if not outputs:
                errors.append("模型无输出")
            else:
                output_shape = outputs[0].shape
                logger.debug(f"模型输出 shape: {output_shape}")

        except Exception as e:
            errors.append(f"模型加载失败: {e}")

        return errors

    @classmethod
    async def validate_all(cls) -> dict[str, ValidationResult]:
        """校验所有已注册模型。"""
        results = {}

        for model_info in ModelRegistry.list_models():
            result = await cls.validate(model_info.name, model_info.version)
            key = f"{model_info.name}@{model_info.version}"
            results[key] = result

        return results
