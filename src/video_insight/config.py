import os
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class Config:
    # 项目根目录
    ROOT_DIR = Path(__file__).parent.parent.parent
    # 中央临时处理目录 (服务器本地)
    OUTPUT_DIR = ROOT_DIR / "Data_Analysis_Video_Download"
    RESULT_DIR = ROOT_DIR / "result"
    
    # 如果目录不存在则创建
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 飞书应用凭证
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
    FEISHU_DOMAIN = os.getenv("FEISHU_DOMAIN", "https://open.feishu.cn")
    # 默认文件夹 Token（云端部署时使用，确保结果存储在创建者个人空间）
    FEISHU_FOLDER_TOKEN = os.getenv("FEISHU_FOLDER_TOKEN")
    
    # 阿里云 DashScope (用于 AI 分析)
    DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")

    # 源数据表配置 (用于下载视频)
    # 在 extract_information.py 中使用
    SOURCE_APP_TOKEN = os.getenv("SOURCE_APP_TOKEN")
    SOURCE_TABLE_ID = os.getenv("SOURCE_TABLE_ID")
    
    # 分析配置
    # 在 analyze_ads.py 中使用
    WIKI_TOKEN = os.getenv("WIKI_TOKEN")
    ANALYSIS_TABLE_ID = os.getenv("ANALYSIS_TABLE_ID")
    ANALYSIS_VIEW_ID = os.getenv("ANALYSIS_VIEW_ID")

    # 目标数据表配置 (用于同步结果)
    # 在 Sync_to_feishu.py 中使用
    DEST_APP_TOKEN = os.getenv("DEST_APP_TOKEN")
    DEST_TABLE_ID = os.getenv("DEST_TABLE_ID")

    # 模型路径配置
    MODEL_DIR = ROOT_DIR / "models" / "SenseVoiceSmall"
    VAD_MODEL_DIR = ROOT_DIR / "models" / "speech_fsmn_vad"
    FFMPEG_PATH = ROOT_DIR / "ffmpeg_tool" / "ffmpeg.exe"
    
    # 处理配置
    MAX_WORKERS = 5

config = Config()
