---
AIGC:
  ContentProducer: '001191110102MAD55U9H0F10002'
  ContentPropagator: '001191110102MAD55U9H0F10002'
  Label: '1'
  ProduceID: 'f97954ca-a259-469d-a85b-f795c9a3160a'
  PropagateID: 'f97954ca-a259-469d-a85b-f795c9a3160a'
  ReservedCode1: 'eed43ef4-12ec-43c2-bd87-7259e50521e2'
  ReservedCode2: 'eed43ef4-12ec-43c2-bd87-7259e50521e2'
---

<div align="center">
  <br/>
  <img src="assets/logo.png" width="130" alt="ApeAdmin Algorithm Platform Logo" />
  <h1>ApeAdmin Algorithm Platform</h1>
  <p>AI 视觉算法管理平台 · 纯 CPU 推理 · 事件驱动 · 拉取即跑</p>
</div>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#平台功能">平台功能</a> ·
  <a href="#内置模型">内置模型</a> ·
  <a href="#架构">架构</a> ·
  <a href="#配置说明">配置</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-0.2.0-blue" alt="version">
  <img src="https://img.shields.io/badge/python-3.11%2B-orange" alt="python">
  <img src="https://img.shields.io/badge/vue-3.5-brightgreen" alt="vue">
  <img src="https://img.shields.io/badge/onnxruntime-CPU%20inference-9cf" alt="onnxruntime">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey" alt="platform">
</p>

---

ApeAdmin Algorithm Platform 是一个开箱即用的 **AI 视觉算法管理平台**：以 ApeAdmin 开源后台框架为底座，以插件形式提供完整的视觉 AI 应用能力——从视频源接入、算法识别、事件告警到样本训练闭环，全部可视化编排。

面向无 GPU 的落地场景设计：ONNX Runtime 纯 CPU 推理、帧率限流、量化优化，普通办公电脑即可运行。

## 平台功能

### AI 视觉平台（核心插件 ai_vision）

**事件驱动链路**：类别库 → 识别事件 → 任务编排 → 自动告警 → 样本训练闭环

- **双视频源** —— RTSP 摄像头直播流 + 本地视频文件上传（播完自动循环，没有摄像头也能跑通全流程）
- **内置识别能力** —— 行人 / 车辆（yolo11n-coco）、明火 / 烟雾（fire-smoke-v1），纯 CPU 实时推理
- **类别库管理** —— 自定义识别类别，按类别编排识别事件，支持启用/停用
- **任务编排** —— 视频源 × 识别事件的灵活组合，支持帧率限流、置信度阈值、生效时段
- **自动告警** —— 命中自动抓拍存图，告警中心统一查看与处理，支持状态流转
- **样本训练闭环** —— 告警图片一键入库为训练样本，配合 L2 训练层迭代自有模型
- **运行环境分层** —— L1 推理层 / L2 训练层按需安装，页面内环境自检、依赖管理、模型文件校验
- **AI 排查助手** —— 插件内置 AI 助手，自动感知运行环境与任务状态，快速定位"任务为什么不跑"

### 页面导览

<table>
  <tr>
    <td width="50%" align="center">
      <img src="frontend/src/assets/ai_vision/guide.jpg" alt="平台引导" /><br/>
      <sub><b>仪表盘 · 全局态势总览</b></sub>
    </td>
    <td width="50%" align="center">
      <img src="frontend/src/assets/ai_vision/categories.jpg" alt="类别库" /><br/>
      <sub><b>类别库 · 识别类别管理</b></sub>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <img src="frontend/src/assets/ai_vision/runtime-env.jpg" alt="运行环境" /><br/>
      <sub><b>运行环境 · 分层依赖控制台</b></sub>
    </td>
    <td width="50%" align="center">
      <sub><b>任务编排 · 视频源×事件灵活组合</b><br/>告警中心 · 抓拍图统一处理</sub>
    </td>
  </tr>
</table>

后台共 8 个业务页面：**仪表盘 / 类别库 / 识别事件 / 任务编排 / 告警中心 / 视频源 / 样本库 / 运行环境**，菜单随插件安装自动挂载，权限走底座 RBAC。

## 快速开始

> 模型文件（约 53MB）不随 git 分发，克隆后用内置脚本一键获取（含 SHA256 校验）。

### 1. 克隆并启动

```bash
git clone https://github.com/KevinLiss/Apeadmin-Algorithm-Platform.git
cd Apeadmin-Algorithm-Platform

# 后端（Python 3.11+）
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows（Linux/macOS: source .venv/bin/activate）
pip install -e .
uvicorn src.main:app --host 0.0.0.0 --port 8000

# 前端（新开终端）
cd frontend
npm install --legacy-peer-deps
npm run dev
```

### 2. 安装向导

打开 `http://localhost:5173`，首次访问自动进入安装向导（三步安装）：选 **SQLite** 零配置建库 → 设置管理员账号 → 完成后按提示重启后端。

### 3. 获取模型

```bash
cd backend
python -m src.plugins.builtin.ai_vision.scripts.fetch_models
```

自动下载两个内置模型并做 SHA256 校验；无法直连 GitHub 时可加 `--mirror` 参数走镜像加速。

### 4. 安装推理环境

登录后台 → **AI 视觉平台 → 运行环境** → 一键安装 **L1 推理层**（onnxruntime / opencv 等，约 120MB）。L2 训练层（torch / ultralytics，约 2.5GB）仅在需要自训练模型时安装。

### 5. 跑通第一个识别任务

**视频源** 添加一路 RTSP 或上传一段本地视频 → **识别事件** 选内置类别（如行人/明火） → **任务编排** 组合视频源与事件并启动 → 命中目标后到 **告警中心** 查看抓拍。

> 没有摄像头？直接上传一段含行人或火焰的测试视频即可，播完自动循环。

## 内置模型

| 模型 | 能力 | 大小 | 来源 |
|------|------|------|------|
| yolo11n-coco | 行人 / 车辆（COCO 80 类筛选） | 10.4MB | [ultralytics 官方 ONNX](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx) |
| fire-smoke-v1 | 明火 / 烟雾 | 42.7MB | [Release v0.2.0 附件](https://github.com/KevinLiss/Apeadmin-Algorithm-Platform/releases/download/v0.2.0/fire-smoke-v1.onnx) |

- 模型清单 `manifest.json` 内置 SHA256，安装/校验/下载全链路自动核对
- 模型缺失不阻断系统启动，仅相关识别任务不可运行，运行环境页会明确标注
- 自训练模型：用插件自带 `export_models` 脚本将 YOLO 权重（.pt）导出为 ONNX 并生成清单条目，详见[插件 README](backend/src/plugins/builtin/ai_vision/README.md)

## 架构

```
Apeadmin-Algorithm-Platform/
  backend/                        # FastAPI 后端（Python 3.11+）
    src/
      api/ core/ crud/ models/    # 底座：RBAC / 审计 / 配置 / 加密
      mcp/                        # MCP-SSE 网关（能力封装为 AI 工具）
      ai/                         # AI 智能体（多模型对话 + 工具调用）
      plugins/                    # 插件系统（自动发现 / 生命周期 / 事件总线）
        builtin/
          ai_vision/              # ★ AI 视觉平台插件
            api/                  #   13 组 REST API（事件/任务/告警/样本/环境…）
            runtime/              #   推理引擎 / 任务 worker / 视频流接入 / 告警抓拍
            assets/models/        #   模型清单（onnx 文件由脚本获取）
            scripts/              #   fetch_models 拉取 / export_models 导出
            README.md             #   插件详细文档
          dev_example/            #   插件开发示例
    tests/                        # 插件与底座测试（pytest）
  frontend/                       # Vue3 + Element Plus 前端
    src/views/ai_vision/          # ★ 算法平台 8 个业务页面
    src/api/ai_vision/            #   平台 API 封装
  deploy/                         # 生产部署物料（宝塔 / Docker / Nginx）
```

**底座能力**（来自 [ApeAdmin](https://github.com/KevinLiss/ApeAdmin)，MIT）：RBAC 五表权限（四层数据范围）、插件化架构（ZIP 安装 / 插件市场）、MCP-SSE 网关（把业务能力封装为 AI 工具）、AI 多模型对话（DeepSeek / 千问 / GLM / OpenAI）、安装向导、审计日志。

## 配置说明

安装向导可自动生成 `.env`；手工配置常用项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DB_TYPE` | sqlite | sqlite（零配置） / mysql |
| `JWT_SECRET` | 向导自动生成 | JWT 签名密钥 |
| `MCP_ENABLED` | true | MCP 网关开关 |
| `PLUGINS_ENABLED` | true | 插件系统开关 |
| `SUPER_ADMIN_USERNAME` / `PASSWORD` | admin / admin123 | 超管账号（首次登录后请修改） |

AI 对话模型密钥在后台「AI 助手 → 模型密钥管理」中配置（Fernet 加密存储），算法平台内置 AI 排查助手复用该配置。

## 贡献指南

欢迎贡献！Fork → 创建分支（`feat/` `fix/` `docs/`）→ 确保后端可启动、前端可编译、测试通过 → 向 `master` 提交 Pull Request。

约定：算法能力以插件形式扩展，保持插件与底座解耦；API 遵循 RESTful 风格，前缀 `/api/v1`；模型文件不入 git，新模型请发布到 Release 并回填 manifest 下载地址。

## 协议

- 平台代码：MIT License
- 内置模型权重遵循其上游协议（YOLO11n 为 AGPL-3.0，详见 manifest 中各模型的 license_note），商用部署前请自行评估

---

<p align="center">
  基于 <a href="https://github.com/KevinLiss/ApeAdmin">ApeAdmin</a> 开源后台框架构建 · 由 AI 驱动开发，人工负责产品与质量
</p>