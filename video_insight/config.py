import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    # Project Paths
    ROOT_DIR = Path(__file__).parent.parent
    # Central Temp Processing Directory (Server Local)
    OUTPUT_DIR = ROOT_DIR / "Data_Analysis_Video_Download"
    RESULT_DIR = ROOT_DIR / "result"
    
    # Create directories if they don't exist
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Feishu App Credentials
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
    FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "https://open.feishu.cn")
    # 默认文件夹Token（云端部署时使用，确保结果存储在创建者个人空间）
    FEISHU_FOLDER_TOKEN = os.getenv("FEISHU_FOLDER_TOKEN")
    
    # DashScope
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

    # Source Table (for downloading videos)
    # Used in extract_information.py
    SOURCE_APP_TOKEN = os.getenv("SOURCE_APP_TOKEN")
    SOURCE_TABLE_ID = os.getenv("SOURCE_TABLE_ID")
    
    # Analysis Configuration
    # Used in analyze_ads.py
    WIKI_TOKEN = os.getenv("WIKI_TOKEN")
    ANALYSIS_TABLE_ID = os.getenv("ANALYSIS_TABLE_ID")
    ANALYSIS_VIEW_ID = os.getenv("ANALYSIS_VIEW_ID")

    # Destination Table (for syncing results)
    # Used in Sync_to_feishu.py
    DEST_APP_TOKEN = os.getenv("DEST_APP_TOKEN")
    DEST_TABLE_ID = os.getenv("DEST_TABLE_ID")

    # Models
    MODEL_DIR = ROOT_DIR / "models" / "SenseVoiceSmall"
    VAD_MODEL_DIR = ROOT_DIR / "models" / "speech_fsmn_vad"
    FFMPEG_PATH = ROOT_DIR / "ffmpeg_tool" / "ffmpeg.exe"
    
    # Processing
    MAX_WORKERS = 5

config = Config()
