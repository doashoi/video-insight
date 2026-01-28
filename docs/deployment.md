# 视频分析机器人云端部署指南 (Alibaba Cloud FC)

本指南详细说明了如何将视频分析机器人部署到阿里云函数计算 (FC)，实现 24 小时在线、自动扩缩容和免运维。

## 1. 架构优势
- **完全云端化**: 无需本地运行，按需付费。
- **自动扩缩容**: 根据请求量自动调整资源。
- **高可用性**: 阿里云基础设施保障。
- **成本优化**: 只在处理时计费，空闲时不产生费用。

## 2. 前置要求
1. **阿里云账号**并开通以下服务：
   - 容器镜像服务 (ACR)
   - 函数计算 (FC)
   - 访问控制 (RAM)
   - 对象存储 (OSS) - 用于存储模型文件
2. **安装工具**：
   - Docker (本地构建镜像)
   - [Serverless Devs CLI](https://docs.serverless-devs.com/user-guide/install) (`npm install -g @serverless-devs/s`)
   - 阿里云 CLI (可选)

## 3. 部署步骤

### 3.1 准备模型文件 (OSS 上传)
由于模型文件较大，建议存放在 OSS 中，在函数初始化时自动下载。
1. 在 OSS 控制台创建 Bucket（如 `video-insight-models`）。
2. 将模型文件上传到 `models/` 目录下。
   - 例如：`oss://video-insight-models/models/SenseVoiceSmall.pt`

### 3.2 构建并推送镜像
1. **登录 ACR**:
   ```bash
   docker login --username=您的用户名 registry.cn-hangzhou.aliyuncs.com
   ```
2. **构建镜像**:
   ```bash
   docker build -t registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest .
   ```
3. **推送镜像**:
   ```bash
   docker push registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest
   ```

### 3.3 配置与部署
1. **修改 `s.yaml`**:
   确保 `image` 字段指向您推送的镜像地址。
   ```yaml
   customContainerConfig:
     image: "registry.cn-hangzhou.aliyuncs.com/您的命名空间/video-insight:latest"
   ```
2. **环境变量**:
   在 FC 控制台或 `s.yaml` 中配置以下变量：
   - `FEISHU_APP_ID`, `FEISHU_APP_SECRET`
   - `DASHSCOPE_API_KEY`
   - `OSS_ENDPOINT`, `OSS_BUCKET` (用于模型下载)
3. **执行部署**:
   ```bash
   s deploy
   ```

### 3.4 飞书 Webhook 配置
1. 部署成功后获取 **HTTP Trigger URL**。
2. 在飞书开放平台 -> **事件订阅** 中填写该 URL，末尾加上 `/webhook/event`。
3. 确保订阅了 `im.message.receive_v1` 和 `card.action.trigger` 事件。

## 4. 关键配置检查清单
- [ ] **内存限制**: 建议配置 4GB 以上内存以处理视频。
- [ ] **超时设置**: 建议配置 900 秒（15分钟）以上。
- [ ] **网络访问**: 如果配置了 VPC，需确保配置了 NAT 网关以访问飞书 API。
- [ ] **权限**: FC 角色需具有 `AliyunOSSReadOnlyAccess` 权限。

## 5. 常见问题
- **冷启动**: 第一次请求可能较慢，建议配置预留实例。
- **超时**: 如果视频过长导致处理失败，请检查 FC 超时设置或优化视频分段。
- **清理**: 系统会自动清理 `/tmp` 下的临时文件，但建议定期监控存储空间。
