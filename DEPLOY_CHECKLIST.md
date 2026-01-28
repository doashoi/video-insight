# FC 部署检查清单与操作手册

## ⚠️ 关键前置检查
- [ ] **代码**：`server.py` 已包含 `/initialize` 路由适配器。
- [ ] **配置**：`s.yaml` 中 `InitializationTimeout` 为 300，`MemorySize` 为 3072 (e1)。
- [ ] **镜像**：`Dockerfile` 已包含 `pip cache purge` 和 `oss2` 依赖。

## 一、OSS 模型上传 (必须执行)
请使用以下命令将模型上传到 OSS，务必使用 **VPC 内网 Endpoint** 以避免流量费。

```bash
# 假设您在杭州区域
ossutil cp ./SenseVoiceSmall.pt oss://video-insight-models/models/ \
  -e oss-cn-hangzhou-internal.aliyuncs.com
```

## 二、VPC 网络创建 (避坑指南)
在阿里云控制台创建 VPC 时，请注意：
1. **可用区**：必须选择 **I区** 或 **J区** (部分旧可用区不支持 FC)。
2. **安全组**：必须放行 VPC 内网段 (例如 `172.16.0.0/12`)，否则函数无法访问 OSS。
3. **记录信息**：创建后请记录以下 ID，并替换 `s.yaml` 中的占位符：
   - VPC ID (`vpc-xxx`)
   - vSwitch ID (`vsw-xxx`)
   - 安全组 ID (`sg-xxx`)

## 三、RAM 角色授权
函数计算需要权限访问 OSS。

```bash
# 为 FC 默认角色添加 OSS 只读权限
aliyun-cli ram AttachPolicyToRole --RoleName AliyunFCRole \
  --PolicyName AliyunOSSReadOnlyAccess --PolicyType System
```

## 四、ACR 镜像加速验证
推送镜像到 ACR 后，**必须等待镜像加速完成**才能部署函数。

```bash
# 检查是否存在 _accelerated 标签
aliyun-cli cr GetRepoTag --RepoNamespace video-insight --RepoName sensevoice --Tag latest_accelerated
```
*如果未找到该标签，函数启动速度将极慢或超时。*

## 五、成本与风险披露 (必读)

### 1. 显性成本
- **预留实例**：`s.yaml` 配置了 `Target: 1` 个预留实例。
  - 成本约：**36元/月** (e1实例, 3GB内存)。
  - *作用：消除冷启动，保证服务随时可用。*

### 2. 隐性成本 (容易被忽略)
- **NAT 网关**：如果函数需要访问公网 (如飞书 API)，必须配置 NAT 网关。
  - 成本：**15元/月** (网关费) + **0.5元/GB** (公网流量费)。
  - *如果不配置 NAT，配置了 VPC 的函数将无法访问外网！*

### 3. 配额限制
- **ACR 个人版**：每天限制拉取镜像 **500次**。
  - 风险：如果函数频繁冷启动或并发过高，可能触发限流导致服务不可用。
  - *建议：生产环境升级企业版 ACR 或保持预留实例运行。*
