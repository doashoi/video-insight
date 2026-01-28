#!/bin/bash
set -e  # 遇到错误立即退出

# ==============================
# 配置区（请根据实际情况修改）
# ==============================
IMAGE_REPO="crpi-ejvhnaao5o1qzzps.cn-hangzhou.personal.cr.aliyuncs.com/video-insight/video-insight"
IMAGE_TAG="20260128-v4"  # 建议使用日期+版本号或 git commit hash

FULL_IMAGE_NAME="${IMAGE_REPO}:${IMAGE_TAG}"

echo "🚀 构建并推送镜像: ${FULL_IMAGE_NAME}"

# ==============================
# 1. 确保使用 buildx 并启用多平台支持
# ==============================
if ! docker buildx ls | grep -q "docker-container"; then
  echo "🔧 初始化 buildx builder..."
  docker buildx create --name mybuilder --use --bootstrap
else
  docker buildx use mybuilder
fi

# ==============================
# 2. 登录阿里云容器镜像服务（如果尚未登录）
# ==============================
# 注意：你需要先执行 `docker login` 到你的阿里云个人版镜像仓库
# 示例：
#   docker login --username=your-aliyun-username crpi-ejvhnaao5o1qzzps.cn-hangzhou.personal.cr.aliyuncs.com
#
# 如果已登录，可跳过。脚本不自动处理登录（涉及密码安全）。

# ==============================
# 3. 构建并推送镜像（强制 linux/amd64）
# ==============================
echo "📦 正在构建并推送镜像（platform: linux/amd64）..."
docker buildx build \
  --platform linux/amd64 \
  --tag "${FULL_IMAGE_NAME}" \
  --file deploy/Dockerfile \
  --push \
  --provenance=false \
  --sbom=false \
  .

echo "✅ 镜像已成功推送到: ${FULL_IMAGE_NAME}"

# ==============================
# 4. （可选）更新 s.yaml 中的 image 字段（自动替换）
# ==============================
SED_CMD="s|image: .*|image: ${FULL_IMAGE_NAME}|"

# 备份原文件
cp deploy/s.yaml deploy/s.yaml.bak

# 替换 image 行（仅匹配以 'image:' 开头的行）
if [[ "$OSTYPE" == "darwin"* ]]; then
  # macOS 使用 gsed 或内置 sed（需转义）
  sed -i '' "/image:/s|image: .*|image: ${FULL_IMAGE_NAME}|" deploy/s.yaml
else
  # Linux
  sed -i "/image:/s|image: .*|image: ${FULL_IMAGE_NAME}|" deploy/s.yaml
fi

echo "📝 已更新 deploy/s.yaml 中的镜像地址"

# ==============================
# 5. 提示下一步操作
# ==============================
echo ""
echo "📌 下一步：运行部署命令"
echo "   s deploy -t deploy/s.yaml"
echo ""
echo "💡 建议将此 tag 记录到发布日志或 CI/CD 系统中"