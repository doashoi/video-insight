# 视频洞察分析机器人 (Video Insight Bot)

这是一个基于多模态大模型 (Qwen-VL) 的视频内容自动化分析系统。它能够自动从飞书多维表格下载视频，进行智能分析（包括内容摘要、投放效果归因等），并将结果同步回飞书。

## 核心功能

- **自动化管线**：视频下载 -> 预处理 (VAD/ASR/拼图) -> AI 分析 -> 结果同步。
- **多模态分析**：使用 Qwen-VL-Plus 理解视频画面与语音内容。
- **飞书集成**：
  - 支持飞书机器人 (Webhook) 交互。
  - 支持飞书多维表格 (Bitable) 数据读写。
  - 支持飞书云文档素材管理。
- **云原生部署**：支持阿里云函数计算 (FC) 部署，自动扩缩容。

---

## 快速开始 (本地开发)

### 1. 环境准备

- Python 3.9+
- FFmpeg (用于视频处理，需添加到系统 PATH)
- 推荐使用 [uv](https://github.com/astral-sh/uv) 进行包管理

### 2. 安装依赖

```bash
# 安装 uv (如果尚未安装)
pip install uv

# 创建环境并安装依赖
uv sync

# 激活环境
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env` 并填入以下信息：

```ini
# 飞书应用配置
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...
FEISHU_VERIFICATION_TOKEN=...
FEISHU_ENCRYPT_KEY=...
FEISHU_DOMAIN=https://open.feishu.cn

# 阿里云模型服务
DASHSCOPE_API_KEY=sk-...

# 可选：默认数据源配置
FEISHU_BITABLE_APP_TOKEN=...
FEISHU_BITABLE_TABLE_ID=...
```

### 4. 运行方式

#### 方式 A: CLI 工具 (命令行)

适合单次运行或调试特定步骤：

```bash
# 运行完整管线
uv run video-insight all

# 仅运行特定步骤
uv run video-insight download  # 下载视频
uv run video-insight process   # 处理视频
uv run video-insight analyze   # AI 分析
uv run video-insight sync      # 同步结果
```

#### 方式 B: 启动机器人 (WebSocket 模式)

适合本地开发调试，无需公网 IP：

```bash
uv run video-insight-bot
```

启动后，您可以在飞书客户端与机器人对话（发送“分析”或“Start”）。

---

## 云端部署 (阿里云函数计算 FC)

本服务支持部署到阿里云函数计算 (FC)，采用 **Custom Container** 模式。

### 1. 准备工作

- 开通阿里云 **容器镜像服务 (ACR)** 和 **函数计算 (FC)**。
- 安装工具：
  - Docker Desktop
  - Serverless Devs (`npm install -g @serverless-devs/s`)

### 2. 构建与推送镜像

在项目根目录下，使用提供的脚本构建并推送镜像：

```bash
# 1. 修改脚本中的 IMAGE_REPO 为您的阿里云镜像仓库地址
# 2. 运行脚本
bash scripts/build-and-push.sh
```

该脚本会自动构建 Docker 镜像并推送到阿里云 ACR。

### 3. 部署服务

1. 修改 `deploy/s.yaml` 中的环境变量配置（如需）。
2. 执行部署命令：

```bash
s deploy -t deploy/s.yaml
```

部署成功后，您将获得一个公网 URL（如 `https://bot-server-xxx.cn-hangzhou.fcapp.run`）。

### 4. 配置飞书事件订阅

1. 进入 [飞书开发者后台](https://open.feishu.cn/app) -> **事件订阅**。
2. 将 **请求地址** 设置为部署生成的 URL + `/webhook/event`。
   - 示例：`https://bot-server-xxx.cn-hangzhou.fcapp.run/webhook/event`
3. 确保订阅了以下事件：
   - `im.message.receive_v1` (接收消息)
   - `card.action.trigger` (卡片交互)

---

## 交互流程说明

1. **唤起机器人**：
   用户在飞书客户端发送关键词 **“分析”**、**“Start”** 或 **“Menu”**。

2. **配置任务**：
   机器人回复**任务配置卡片**，用户填写：
   - **源多维表格链接**：包含视频素材的表格 URL。
   - **新任务名称**：本次分析结果的名称。
   - **目标文件夹** (可选)。

3. **自动执行**：
   点击卡片上的 **“确认提交”** 按钮后，后台自动执行以下流程：
   - 解析源表格 -> 创建新表格 -> 下载视频 -> AI 分析 -> 结果回写。

4. **结果通知**：
   任务完成后，机器人会发送包含结果表格链接的通知消息。

---

## 目录结构

```
.
├── deploy/                 # 部署相关配置 (Dockerfile, s.yaml)
├── models/                 # 本地 AI 模型缓存 (VAD 等)
├── scripts/                # 辅助脚本
├── src/                    # 源代码
│   ├── common/             # 通用组件
│   ├── runners/            # 启动入口 (CLI, Bot, Server)
│   ├── ai_analyzer.py      # AI 分析逻辑
│   ├── core.py             # 核心业务管线
│   ├── downloader.py       # 视频下载器
│   ├── feishu_syncer.py    # 飞书同步器
│   └── video_processor.py  # 视频预处理
├── .env.example            # 环境变量示例
├── pyproject.toml          # 项目依赖配置
└── README.md               # 项目文档
```
