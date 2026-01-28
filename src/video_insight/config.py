import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class Config:
    """项目配置类"""
    # 项目根目录
    ROOT_DIR = Path(__file__).parent.parent.parent
    
    # 临时处理目录
    OUTPUT_DIR = ROOT_DIR / "Data_Analysis_Video_Download"
    RESULT_DIR = ROOT_DIR / "result"
    
    # 如果目录不存在则创建
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 飞书应用凭证
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
    FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "https://open.feishu.cn")
    FEISHU_VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN")
    FEISHU_ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY")
    
    # 默认文件夹 Token (用于创建新的多维表格)
    FEISHU_FOLDER_TOKEN = os.getenv("FEISHU_FOLDER_TOKEN")
    
    # 阿里云 DashScope (用于 AI 分析)
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

    # 源数据表默认配置 (CLI 模式使用)
    SOURCE_APP_TOKEN = os.getenv("SOURCE_APP_TOKEN")
    SOURCE_TABLE_ID = os.getenv("SOURCE_TABLE_ID")
    
    # 目标数据表默认配置 (同步结果使用)
    DEST_APP_TOKEN = os.getenv("DEST_APP_TOKEN")
    DEST_TABLE_ID = os.getenv("DEST_TABLE_ID")

    # 模型路径配置
    MODEL_DIR = ROOT_DIR / "models" / "SenseVoiceSmall"
    VAD_MODEL_DIR = ROOT_DIR / "models" / "speech_fsmn_vad"
    FFMPEG_PATH = ROOT_DIR / "ffmpeg_tool" / "ffmpeg.exe"
    
    # 运行时配置
    MAX_WORKERS = 5
    
    # 任务锁文件路径 (用于跨进程任务同步)
    LOCK_FILE = ROOT_DIR / ".task.lock"

config = Config()
