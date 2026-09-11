"""AI 视觉平台运行时（推理/训练）包。

本包**零重依赖**：onnxruntime / opencv / numpy / torch / ultralytics 全部延迟导入。

- ``deps.py``：L1/L2 依赖检测、缺失报告、一键安装任务（任务 1.2 实现）
- ``engine.py``：ONNX 推理引擎 + LRU 模型缓存（任务 2.1 实现）
- ``yolo_postprocess.py``：YOLO ONNX 输出解码（letterbox 逆变换 + numpy NMS）
- ``grabber.py``：RTSP 抓流 / 连通测试（任务 1.4 实现）
- ``worker.py``：StreamWorker 视频分析流水线（任务 2.3 实现）
- ``events.py``：worker 内部运行时事件（任务 2.3 实现）
- ``manager.py``：WorkerManager 任务编排单例（任务 2.4 实现）
- ``trainer.py``：训练后端（二期实现）

安装策略见 ``docs/算法管理平台规划方案.docx`` 第 3 章：L1 推理层必装
（onnxruntime + opencv-headless + numpy + pillow，约 75MB），L2 训练层可选
（torch-cpu + ultralytics，追加约 147MB，AGPL 风险隔离在可选层）。
"""