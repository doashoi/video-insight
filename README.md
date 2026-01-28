# 视频洞察分析机器人 (Video Insight Bot)

这是一个基于多模态大模型 (Qwen-VL) 的视频内容自动化分析系统。它能够自动从飞书多维表格下载视频，进行智能分析（包括内容摘要、投放效果归因等），并将结果同步回飞书。

## 🌟 核心价值
- **ChatOps 体验**: 像聊天一样发送指令或填写卡片即可启动复杂分析任务。
- **多模态分析**: 使用 Qwen-VL 理解视频画面与语音内容，生成深度洞察。
- **自动化闭环**: 从视频下载、预处理 (VAD/ASR)、AI 分析到结果同步，全流程自动化。
- **数据所有权**: 分析结果自动存入用户指定的飞书多维表格，安全可控。

## 🚀 核心功能
- **自动化管线**：视频下载 -> 预处理 (VAD/ASR/拼图) -> AI 分析 -> 结果同步。
- **飞书集成**：
  - 支持飞书机器人 (WebSocket/Webhook) 交互。
  - 支持飞书多维表格 (Bitable) 数据读写。
  - 支持飞书云文档素材管理。
- **灵活部署**：支持本地 WebSocket 模式（无需公网 IP）或云端阿里云函数计算 (FC) 模式。

## 🛠️ 快速开始

### 1. 环境准备
- Python 3.10+
- FFmpeg (用于视频处理)
- [Optional] CUDA 加速 (推荐使用 NVIDIA 显卡)

### 2. 安装依赖
使用 `uv` (推荐) 或 `pip`:
```bash
uv sync
# 或
pip install -r requirements.txt
```

### 3. 配置环境变量
复制 `.env.example` 为 `.env` 并填入必要信息：
```ini
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...
DASHSCOPE_API_KEY=sk-...
```

### 4. 运行模式

#### A. 本地运行 (WebSocket 模式)
无需公网 IP，适合开发调试：
```bash
python bot.py
```

#### B. 命令行运行 (CLI)
手动触发特定任务：
```bash
python main.py all
```

#### C. 云端运行 (Webhook 模式)
适合生产部署，详见 [部署指南](docs/deployment.md)。

## 📂 项目结构
```
.
├── docs/               # 详细文档
├── src/video_insight/  # 核心源码
│   ├── bot/            # 机器人逻辑 (WebSocket/Webhook)
│   ├── ai_analyzer.py  # AI 分析逻辑
│   ├── downloader.py   # 视频下载
│   ├── feishu_syncer.py# 飞书数据同步
│   └── video_processor.py# 视频预处理 (VAD/ASR)
├── bot.py              # WebSocket 模式启动入口
├── server.py           # Webhook 模式启动入口 (FastAPI)
├── main.py             # CLI 入口
└── s.yaml              # Serverless 部署配置
```

## 📜 许可证
MIT License
