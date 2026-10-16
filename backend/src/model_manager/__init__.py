"""模型版本管理模块。

职责：
- 模型注册表：管理模型元数据（版本、路径、状态）
- 模型加载器：带缓存的模型加载（LRU）
- 模型校验器：输入输出 shape 检查、SHA256 校验

使用示例：
```python
from src.model_manager import ModelRegistry, ModelLoader, ModelValidator

# 列出所有模型
models = ModelRegistry.list_models()

# 获取默认版本
default = ModelRegistry.get_default("face-recognition")

# 加载模型
session = ModelLoader.load("face-recognition", "1.0.0")

# 校验模型
is_valid = await ModelValidator.validate("face-recognition", "1.0.0")
```
"""

from .registry import ModelRegistry
from .loader import ModelLoader
from .validator import ModelValidator
from .api import router as model_router

__all__ = [
    "ModelRegistry",
    "ModelLoader",
    "ModelValidator",
    "model_router",
]
