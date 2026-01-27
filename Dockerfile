FROM python:3.10-slim

# 1. Install System Dependencies
# ffmpeg: For video processing
# libgl1: For opencv
# git: For pip install git+...
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libgl1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# 2. Set Working Directory
WORKDIR /app

# 3. Copy Dependency Definition
COPY pyproject.toml .

# 4. Install Python Dependencies
# 关键优化：优先安装 CPU 版本的 PyTorch，大幅减小镜像体积 (3GB -> 500MB)
RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# 安装其余依赖 (移除了 torchvision)
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    requests \
    pandas \
    openpyxl \
    python-dotenv \
    pillow \
    tqdm \
    lark-oapi \
    opencv-python \
    modelscope \
    funasr \
    numba \
    umap-learn

# 5. Copy Application Code
COPY . .

# 6. Expose Port
EXPOSE 9000

# 7. Start Command
CMD ["python", "server.py"]
