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
# We manually extract dependencies or just install key ones if pyproject is complex
# Here we install the key packages directly to ensure stability
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    requests \
    pandas \
    openpyxl \
    python-dotenv \
    pillow \
    torch \
    torchaudio \
    torchvision \
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
