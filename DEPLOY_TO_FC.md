# 阿里云函数计算 (FC) 部署指南

本指南将帮助您将 **Video Insight** 飞书机器人部署到阿里云函数计算 (FC)，实现 24 小时在线、自动扩缩容和免运维。

## 1. 准备工作

### 1.1 阿里云账号与工具
1.  注册并登录 [阿里云](https://www.aliyun.com/)。
2.  开通 **函数计算 FC** 和 **容器镜像服务 ACR**。
3.  安装 Docker Desktop (本地构建镜像用)。
4.  安装 Serverless Devs 工具 (`s`):
    ```bash
    npm install -g @serverless-devs/s
    s config add # 配置阿里云 AccessKey
    ```

### 1.2 飞书配置变更
由于 FC 是无服务器架构，不支持 WebSocket 长连接，我们需要切换为 **Webhook** 模式。
1.  进入 [飞书开放平台](https://open.feishu.cn/) -> 您的应用。
2.  **事件订阅**:
    -   加密策略：建议暂时关闭（或在 `server.py` 配置 Key）。
    -   请求网址 (URL)：部署完成后填写（见下文）。
    -   订阅事件：保持不变 (`im.message.receive_v1`, `card.action.trigger`)。

## 2. 构建与部署

### 2.1 构建 Docker 镜像
在项目根目录下运行：

```bash
# 1. 登录阿里云 ACR (需先在控制台创建实例和命名空间)
docker login --username=您的用户名 registry.cn-hangzhou.aliyuncs.com

# 2. 构建镜像
docker build -t registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest .

# 3. 推送镜像
docker push registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest
```

### 2.2 修改配置文件
打开 `s.yaml`，修改 `image` 字段为您刚才推送的镜像地址：
```yaml
customContainerConfig:
  image: "registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest"
```

确保本地 `.env` 文件包含以下变量（部署时会自动读取）：
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `DASHSCOPE_API_KEY`

### 2.3 一键部署
```bash
s deploy
```
部署成功后，终端会输出一个 **公网访问地址** (HTTP Trigger URL)，类似：
`https://bot-processor-video-insight-xxxx.cn-hangzhou.fcapp.run`

## 3. 完成飞书连接

1.  复制上面的 **公网访问地址**。
2.  回到飞书开放平台 -> **事件订阅**。
3.  将地址粘贴到 **请求网址 URL** 栏，末尾加上 `/webhook/event`。
    -   例如：`https://...fcapp.run/webhook/event`
4.  点击“保存”。飞书会发送一个 Challenge 请求，我们的 `server.py` 会自动处理并返回，保存成功即表示连接建立。

## 4. 验证测试

1.  在飞书群或单聊中对机器人发送“开始”。
2.  机器人应回复配置卡片。
3.  填写任务并提交。
4.  **观察**：
    -   机器人响应“任务已启动”。
    -   视频将在云端自动下载、处理。
    -   处理完成后，原始视频自动删除。
    -   结果表格会自动存入您配置的 **飞书共享文件夹**。

## 常见问题
- **超时**：如果视频特别长，900秒（15分钟）不够用，请在 FC 控制台申请更长超时或优化视频处理逻辑。
- **冷启动**：第一次请求可能需要几秒钟启动容器，飞书可能会重试，这是正常现象。
