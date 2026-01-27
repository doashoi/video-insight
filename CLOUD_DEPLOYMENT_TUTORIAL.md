# 视频分析机器人云端部署教程

## 概述

本教程将指导您如何将视频分析机器人部署到阿里云函数计算 (FC)，实现完全云端化的视频处理服务。通过容器镜像构建和函数计算部署，您无需本地运行程序，所有视频处理都在云端完成。

## 架构优势

- **完全云端化**: 无需本地运行，按需付费
- **自动扩缩容**: 根据请求量自动调整资源
- **高可用性**: 阿里云基础设施保障
- **成本优化**: 只在处理时计费，空闲时不产生费用
                    
## 前置要求

1. 阿里云账号并开通以下服务：
   - 容器镜像服务 (ACR)
   - 函数计算 (FC)
   - 访问控制 (RAM)

2. 安装必要工具：
   - Docker
   - 阿里云 CLI 工具
   - Serverless Devs CLI

## 部署步骤

### 第一步：准备容器镜像

#### 1.1 创建命名空间

登录阿里云控制台，进入容器镜像服务：

1. 选择您的地域（建议选择华东1-杭州）
2. 创建命名空间，例如：`video-insight`
3. 记录命名空间名称，后续步骤会用到

#### 1.2 构建镜像

在项目根目录执行：

```bash
# 登录阿里云容器镜像服务
sudo docker login --username=阿里云账号 registry.cn-hangzhou.aliyuncs.com

# 构建镜像
docker build -t video-insight:latest .

# 标记镜像（替换YOUR_NAMESPACE为您的命名空间）
docker tag video-insight:latest registry.cn-hangzhou.aliyuncs.com/YOUR_NAMESPACE/video-insight:latest

# 推送镜像到阿里云
docker push registry.cn-hangzhou.aliyuncs.com/YOUR_NAMESPACE/video-insight:latest
```

#### 1.3 Dockerfile说明

项目中的Dockerfile已经配置好所有依赖：

```dockerfile
FROM python:3.10-slim
RUN apt-get update && apt-get install -y ffmpeg libgl1 git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn requests pandas openpyxl python-dotenv pillow torch torchaudio torchvision tqdm lark-oapi opencv-python modelscope funasr numba umap-learn
COPY . .
EXPOSE 9000
CMD ["python", "server.py"]
```

### 第二步：配置函数计算

#### 2.1 创建服务

1. 登录函数计算控制台
2. 选择华东1-杭州地域
3. 创建服务，名称：`video-insight`
4. 开启公网访问权限

#### 2.2 创建函数

使用`s.yaml`配置文件创建函数：

```yaml
services:
  video-insight:
    component: fc
    props:
      region: cn-hangzhou
      service:
        name: video-insight
        internetAccess: true
      function:
        name: bot-processor
        runtime: custom-container
        caPort: 9000
        memorySize: 4096  # 4GB内存
        timeout: 900      # 15分钟超时
        customContainerConfig:
          image: "registry.cn-hangzhou.aliyuncs.com/YOUR_NAMESPACE/video-insight:latest"
        environmentVariables:
          FEISHU_APP_ID: "您的飞书应用ID"
          FEISHU_APP_SECRET: "您的飞书应用密钥"
          FEISHU_VERIFICATION_TOKEN: "您的飞书验证令牌"
          FEISHU_ENCRYPT_KEY: "您的飞书加密密钥"
      triggers:
        - name: http-trigger
          type: http
          config:
            authType: anonymous
            methods:
              - POST
```

#### 2.3 部署函数

```bash
# 安装Serverless Devs
npm install -g @serverless-devs/s

# 配置阿里云凭证
s config add --AccessKeyID 您的AccessKeyID --AccessKeySecret 您的AccessKeySecret -a default

# 部署
s deploy
```

### 第三步：配置飞书机器人

#### 3.1 获取Webhook地址

部署成功后，您会获得一个HTTP触发器地址，格式如下：

```
https://123456789.cn-hangzhou.fc.aliyuncs.com/2016-08-15/proxy/video-insight/bot-processor/webhook/event
```

#### 3.2 配置飞书应用

1. 登录飞书开放平台
2. 进入您的应用设置
3. 在"事件与回调"中配置请求网址URL为上述地址
4. 确保已订阅以下事件：
   - 接收消息
   - 消息已读
   - 卡片互动

### 第四步：配置环境变量

在函数计算控制台中，为您的函数配置以下环境变量：

| 变量名 | 说明 | 示例 |
|--------|------|------|
| FEISHU_APP_ID | 飞书应用ID | cli_xxxxxxxx |
| FEISHU_APP_SECRET | 飞书应用密钥 | 应用密钥 |
| FEISHU_VERIFICATION_TOKEN | 飞书验证令牌 | 验证令牌 |
| FEISHU_ENCRYPT_KEY | 飞书加密密钥 | 加密密钥 |
| TEMP_CLEANUP_ENABLED | 是否启用临时文件清理 | true |

### 第五步：权限配置

确保您的函数计算服务角色具有以下权限：

1. 访问容器镜像服务权限
2. 访问OSS权限（用于临时文件存储）
3. 访问日志服务权限

## 核心特性

### 1. 创建者数据所有权

云端部署后，所有分析结果将自动存储到任务创建者的多维表格空间中，而不是共享文件夹。这确保了：

- 数据安全和隐私
- 创建者对数据的完全控制权
- 符合企业数据治理要求

### 2. 自动清理机制

系统会自动清理临时文件，包括：
- 下载的视频文件
- 提取的音频文件
- 生成的截图文件

清理时机：
- 任务完成后立即清理
- 超过24小时的临时文件定期清理

### 3. 进度通知

机器人会在关键节点发送进度通知：
- 任务开始
- 视频下载完成（成功/失败统计）
- 音频提取完成
- 截图生成完成
- 分析完成（包含结果表格链接）

## 成本估算

以华东1-杭州地域为例：

| 资源类型 | 配置 | 单价 |
|----------|------|------|
| 函数计算 | 4GB内存，15分钟 | 约0.3元/次 |
| 容器镜像 | 1GB存储 | 约0.3元/月 |
| 流量费用 | 出流量 | 约0.8元/GB |

假设每天处理10个视频，每月成本约：
- 函数计算：0.3元 × 10 × 30 = 90元
- 其他费用：约10元
- **总计：约100元/月**

## 监控与维护

### 查看日志

1. 登录函数计算控制台
2. 选择您的服务和函数
3. 点击"日志"标签页
4. 查看实时日志和历史日志

### 性能监控

函数计算提供以下监控指标：
- 调用次数
- 执行时间
- 内存使用
- 错误率

### 告警配置

建议配置以下告警：
- 函数执行失败率 > 5%
- 平均执行时间 > 10分钟
- 内存使用率 > 90%

## 故障排查

### 常见问题

1. **镜像构建失败**
   - 检查Dockerfile语法
   - 确认基础镜像可访问
   - 检查网络连接

2. **函数部署失败**
   - 检查s.yaml配置
   - 确认镜像已推送
   - 检查权限配置

3. **飞书机器人无响应**
   - 检查Webhook地址配置
   - 验证环境变量
   - 查看函数日志

4. **视频处理失败**
   - 检查内存配置（建议4GB）
   - 确认超时时间（建议15分钟）
   - 查看详细日志

### 获取帮助

如遇到问题，可通过以下方式获取帮助：

1. 查看函数计算官方文档
2. 提交阿里云工单
3. 查看飞书开放平台文档
4. 参考GitHub项目Issues

## 安全建议

1. **密钥管理**
   - 使用阿里云KMS管理敏感信息
   - 定期轮换访问密钥
   - 最小权限原则

2. **网络安全**
   - 配置VPC网络隔离
   - 使用HTTPS加密传输
   - 配置访问控制

3. **数据安全**
   - 启用临时文件加密
   - 定期备份重要数据
   - 配置访问审计

## 扩展功能

### 1. 批量处理

支持一次处理多个视频，提高效率：
- 并行处理多个视频
- 智能资源调度
- 批量结果汇总

### 2. 自定义分析

支持自定义分析规则：
- 自定义截图时间点
- 自定义音频提取参数
- 自定义输出格式

### 3. 集成扩展

可与其他阿里云服务集成：
- 对象存储OSS
- 内容分发CDN
- 消息服务MNS

## 总结

通过本教程，您已经成功将视频分析机器人部署到云端。现在您可以：

- 无需本地运行，完全云端化处理
- 支持多人同时使用
- 自动扩缩容，按需付费
- 数据安全，创建者完全拥有

接下来，您可以继续优化配置，添加更多功能，或集成到现有业务流程中。