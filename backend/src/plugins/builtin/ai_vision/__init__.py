"""AI 视觉平台插件包。

.. note::
   本文件需要导出插件入口类：插件管理器发现插件时 import 本包的
   ``__init__``，并在其中查找 ``PluginInterface`` 的子类。

提供目标类别库 → 识别事件 → 摄像头推理 → 告警 → 样本/训练闭环。
运行时依赖（onnxruntime / opencv / torch / ultralytics）作为第三方内容
在后台「运行环境」页分层一键安装，插件本体零重依赖（全部延迟导入）。
"""
from src.plugins.builtin.ai_vision.plugin import AIVisionPlugin

__all__ = ["AIVisionPlugin"]