import os
import time
import logging
import oss2
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FC-Initializer")

def initialize(context):
    """
    FC Initializer - 自动执行，非HTTP接口 (逻辑实现)
    由 server.py 的 /initialize 路由适配调用
    """
    start_time = time.time()
    logger.info(f"[INIT] Starting initialization...")

    try:
        # 1. 获取配置
        oss_endpoint = os.environ.get('OSS_ENDPOINT')
        bucket_name = os.environ.get('OSS_BUCKET')
        model_path = os.environ.get('MODEL_PATH', '/tmp/models')
        # 允许的误差范围 (MB)
        size_tolerance_mb = 50 
        # 预期的模型大小 (MB)，如果环境变量未设置则不校验或使用默认值
        expected_size_mb = float(os.environ.get('MODEL_SIZE_MB', 0))

        if not oss_endpoint or not bucket_name:
            logger.error("[INIT] Missing OSS configuration.")
            return

        # 2. 准备路径
        model_dir = Path(model_path)
        model_dir.mkdir(parents=True, exist_ok=True)
        
        # 假设模型文件名为 SenseVoiceSmall.pt，实际应根据需求调整
        # 这里演示下载主模型文件
        model_filename = "SenseVoiceSmall.pt"
        local_file = model_dir / model_filename
        object_key = f"models/{model_filename}"

        # 3. 设置 OSS 认证 (使用 STS)
        creds = context.credentials
        auth = oss2.StsAuth(creds.access_key_id, creds.access_key_secret, creds.security_token)
        bucket = oss2.Bucket(auth, oss_endpoint, bucket_name)

        # 4. 检查本地文件 (大小校验)
        need_download = True
        if local_file.exists():
            local_size_mb = local_file.stat().st_size / (1024 * 1024)
            if expected_size_mb > 0:
                diff = abs(local_size_mb - expected_size_mb)
                if diff <= size_tolerance_mb:
                    logger.info(f"[INIT] Model exists and size is valid ({local_size_mb:.2f}MB). Skipping download.")
                    need_download = False
                else:
                    logger.warning(f"[INIT] Model size mismatch (Local: {local_size_mb:.2f}MB, Expected: {expected_size_mb}MB). Re-downloading.")
            else:
                 # 如果没有预期大小，只要存在就跳过（或者可以加更复杂的逻辑）
                 logger.info(f"[INIT] Model exists. Skipping download.")
                 need_download = False

        # 5. 下载逻辑 (带重试)
        if need_download:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    logger.info(f"[INIT] Downloading {object_key} (Attempt {attempt+1}/{max_retries})...")
                    bucket.get_object_to_file(object_key, str(local_file))
                    
                    # 下载后再次校验大小
                    if expected_size_mb > 0:
                         local_size_mb = local_file.stat().st_size / (1024 * 1024)
                         if abs(local_size_mb - expected_size_mb) > size_tolerance_mb:
                             raise Exception(f"Downloaded file size mismatch: {local_size_mb:.2f}MB")

                    download_time = time.time() - start_time
                    logger.info(f"[INIT] Downloaded model in {download_time:.2f}s")
                    break
                except Exception as e:
                    logger.error(f"[INIT] Download failed: {e}")
                    if attempt == max_retries - 1:
                        raise e
                    time.sleep(2) # 等待后重试

        logger.info(f"[INIT] Initialization completed in {time.time() - start_time:.2f}s")

    except Exception as e:
        logger.error(f"[INIT] Initialization failed: {e}")
        # 在 FC 中，Initializer 失败会导致实例启动失败，这是预期的
        raise e
