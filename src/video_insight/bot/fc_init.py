import os
import time
import logging
from pathlib import Path

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FC-Initializer")

def initialize(context):
    """
    FC Initializer - 已改为轻量级初始化
    由于现在使用 DashScope API 替代本地模型，不再需要预下载大模型文件。
    """
    logger.info("[INIT] Initialization completed (Cloud API mode).")
