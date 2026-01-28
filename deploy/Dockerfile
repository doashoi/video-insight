# 第一阶段：构建环境 (Builder)
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 
ENV UV_LINK_MODE=copy

WORKDIR /app

# 1. 复制依赖文件
COPY pyproject.toml uv.lock ./

# 2. 安装依赖 (使用 --frozen 确保版本一致)
RUN uv sync --frozen --no-dev --no-install-project

# 3. 复制源代码
COPY . .

# 4. 安装项目本身
RUN uv sync --frozen --no-dev

# 第二阶段：运行环境 (Runner)
# 使用与 Builder 相同的镜像源，提高国内访问成功率（包含 uv 但不影响运行）
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim

WORKDIR /app

# 安装运行时系统库 (OpenCV, FFmpeg)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 从 Builder 阶段复制虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制项目代码
COPY src/ src/
# 避免将大模型文件打入镜像，建议通过挂载或运行时下载
# COPY models/ models/

# 设置环境变量
ENV PATH="/app/.venv/bin:$PATH"
# 设置缓存目录指向 /tmp (适配 FC 只读文件系统)
ENV MODELSCOPE_CACHE=/tmp/modelscope
ENV HF_HOME=/tmp/huggingface

EXPOSE 9000

# 启动 Web 服务
CMD ["python", "-m", "src.runners.server_runner"]
