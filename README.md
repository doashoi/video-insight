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

## 快速开始

### 1. 环境准备
- Python 3.9+
- FFmpeg (用于视频处理)

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
复制 `.env.example` 为 `.env` 并填入以下信息：
```ini
# 飞书应用配置
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...
FEISHU_VERIFICATION_TOKEN=...
FEISHU_ENCRYPT_KEY=...

# 阿里云模型服务
DASHSCOPE_API_KEY=sk-...

# 飞书数据源配置
FEISHU_BITABLE_APP_TOKEN=...
FEISHU_BITABLE_TABLE_ID=...
```

### 4. 本地运行 (CLI)
使用 `main.py` 命令行工具运行各个阶段：
```bash
# 运行完整管线
python main.py all

# 仅运行特定步骤
python main.py download  # 下载视频
python main.py process   # 处理视频
python main.py analyze   # AI 分析
python main.py sync      # 同步结果
```

## 部署指南 (GitHub Actions 自动化部署)

本项目配置了 GitHub Actions 工作流，支持代码推送到 `main` 分支时自动构建并部署到阿里云函数计算。

### 1. 阿里云资源准备
- 开通 **容器镜像服务 (ACR) 个人版**。
- 开通 **函数计算 (FC)**。
- 创建 RAM 用户并授权 `AliyunFCFullAccess` 和 `AliyunContainerRegistryFullAccess`。

### 2. GitHub Secrets 配置
在仓库 `Settings` -> `Secrets and variables` -> `Actions` 中添加以下 Secrets：

| Secret 名称 | 说明 |
|---|---|
| `ALIYUN_ACCOUNT_ID` | 阿里云主账号名 (如 nick...) |
| `ALIYUN_PASSWORD` | ACR 访问凭证固定密码 (非 AccessKey) |
| `ALIYUN_ACCESS_KEY_ID` | RAM 用户 AccessKey ID |
| `ALIYUN_ACCESS_KEY_SECRET` | RAM 用户 AccessKey Secret |
| `ACR_REGISTRY_URL` | ACR 仓库地址 (专属域名) |
| `ACR_NAMESPACE` | ACR 命名空间 |
| `FEISHU_APP_ID` | 飞书 App ID |
| `FEISHU_APP_SECRET` | 飞书 App Secret |
| `FEISHU_VERIFICATION_TOKEN` | 飞书事件订阅 Token |
| `FEISHU_ENCRYPT_KEY` | 飞书加密 Key |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope Key |

### 3. 触发部署
- 修改代码并 Push 到 `main` 分支。
- 或在 Actions 页面手动触发 `deploy` 工作流。

## 测试与验证

### 1. 飞书回调配置
部署成功后，在 GitHub Actions 的部署日志中找到 **Webhook 地址**：
`https://bot-processor-video-insight-xxxx.cn-hangzhou.fcapp.run/webhook/event`

1. 进入 [飞书开放平台](https://open.feishu.cn/)。
2. 在 **事件订阅** 中填写上述 URL。
3. 确保订阅了 `im.message.receive_v1` (接收消息) 和 `card.action.trigger` (卡片交互)。

### 2. 功能测试
1. **机器人对话**：对机器人发送 "开始" 或 "菜单"。
   - *预期*：机器人回复功能卡片。
2. **提交任务**：在卡片中填写参数并提交。
   - *预期*：收到 "任务已启动" 回复。
3. **结果验证**：检查飞书多维表格是否新增了分析记录。

## 项目结构
```
.
├── .github/workflows/deploy.yml  # 自动部署工作流
├── models/                       # 模型相关文件
├── src/video_insight/            # 核心源码
│   ├── ai_analyzer.py            # Qwen-VL 分析逻辑
│   ├── downloader.py             # 视频下载
│   ├── feishu_syncer.py          # 飞书同步
│   └── video_processor.py        # 视频预处理
├── main.py                       # CLI 入口
├── server.py                     # Webhook 服务端 (FastAPI)
├── bot.py                        # 机器人交互逻辑
└── s.yaml                        # Serverless Devs 部署配置 (模板)
```
