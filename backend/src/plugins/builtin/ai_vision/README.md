---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: '43cb57b1-1b99-46b7-99fd-84906c2e8d74'
  PropagateID: '43cb57b1-1b99-46b7-99fd-84906c2e8d74'
  ReservedCode1: '7abdd445-855c-406f-bbf2-06ac26daae15'
  ReservedCode2: '7abdd445-855c-406f-bbf2-06ac26daae15'
---

# AI 视觉插件（ai_vision）

面向纯 CPU 推理场景的 AI 视觉算法管理平台插件：事件驱动设计（类别库 → 识别事件 → 任务编排 → 自动告警 → 样本训练闭环），支持 RTSP 摄像头与本地视频文件两种视频源，一期内置人/车/明火烟雾识别能力，二期规划岗位行为监管。

## 拉取仓库后快速跑起来

**模型 ONNX 文件不随 git 分发**（两个模型共约 53MB 二进制），克隆仓库后模型目录为空。两个模型的下载地址均已内置（ultralytics 官方直链 + 本项目 Release 附件），按下面步骤一键补齐：

### 方式一：脚本一键获取（推荐）

```bash
cd backend
python -m src.plugins.builtin.ai_vision.scripts.fetch_models
```

- 自动按 manifest.json 中的地址下载 + SHA256 校验，已就绪自动跳过（可重复运行）
- 无法直连 GitHub 的网络环境，可用镜像参数加速：
  ```bash
  python -m src.plugins.builtin.ai_vision.scripts.fetch_models --mirror https://ghproxy.net
  ```
  镜像原理：把 GitHub 直链改写为 `镜像前缀/https://github.com/...` 形式，不同镜像工具拼接方式可能有差异，以所用镜像的说明为准
- 校验不通过的文件会自动重新下载

### 方式二：手动下载

| 模型 | 大小 | 来源 |
|------|------|------|
| yolo11n-coco | 10.4MB | [ultralytics 官方 ONNX](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx) |
| fire-smoke-v1 | 42.7MB | [本项目 Release v0.2.0 附件](https://github.com/KevinLiss/Apeadmin-Algorithm-Platform/releases/download/v0.2.0/fire-smoke-v1.onnx) |

手动下载后放置到 `backend/src/plugins/builtin/ai_vision/assets/models/`，然后重跑方式一的脚本（或「运行环境」页的校验功能）确认 SHA256 通过。

### 缺失模型的降级行为

模型未就绪时：插件安装与启动完全正常（不阻断），但识别任务无法运行，任务状态会提示模型文件缺失；「运行环境」页的环境自检与模型文件表格会明确标注校验状态，AI 排查助手也能识别该问题并给出指引。

## 运行环境分层

插件运行时依赖不随插件打包，在后台「AI 视觉平台 → 运行环境」页按需安装：

| 层 | 内容 | 大小参考 |
|----|------|----------|
| L1 推理层 | onnxruntime / opencv / numpy / pillow | 约 120MB |
| L2 训练层 | torch / torchvision / ultralytics（AGPL） | 约 2.5GB |

## 视频源与识别能力

- **视频源**：RTSP 摄像头、本地视频文件（上传接口，播完循环）
- **一期内置识别**：person / vehicle / fire / smoke（yolo11n-coco + fire-smoke-v1）
- **事件驱动链路**：类别库 → 识别事件 → 任务编排 → 自动告警（带抓拍图）→ 样本训练闭环
- **纯 CPU 设计**：ONNX 推理、帧率限流、量化优化，无 GPU 也可运行

## 二次开发：导出自己的模型

有自训练 YOLO 权重（.pt）时，可用插件自带脚本导出 ONNX 并生成 manifest 条目：

```bash
cd backend
python -m src.plugins.builtin.ai_vision.scripts.export_models \
  --pt path/to/best.pt --name my-model --out src/plugins/builtin/ai_vision/assets/models
```

脚本会计算 SHA256 与文件大小，打印可直接合并进 manifest.json 的条目；建议同时把新模型的下载地址补进 manifest 的 `url` 字段（或发布到项目 GitHub Releases 后回填），方便其他用户通过 fetch_models 脚本获取。