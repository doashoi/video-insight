FROM python:3.10-slim

# 1. Install System Dependencies
# ffmpeg: For video processing
# --no-install-recommends: Reduce image size
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 2. Set Working Directory
WORKDIR /app

# 3. Copy Dependency Definition
COPY requirements.txt .

# 4. Install Python Dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 5. Copy Application Code
COPY . .

# 6. Expose Port
EXPOSE 9000

# 7. Start Command
CMD ["python", "server.py"]
