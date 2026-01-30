import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量 (仅在非 FC 环境下加载 .env)
if not (os.environ.get("FC_FUNCTION_NAME") or os.environ.get("FC_SERVICE_NAME")):
    load_dotenv()

class Config:
    """项目配置类"""
    def __init__(self):
        # 文件名配置
        self.USER_DATA_FILE = "user_folders.json"
        
        # 环境判断
        self.IS_FC = os.environ.get("FC_FUNCTION_NAME") is not None or os.environ.get("FC_SERVICE_NAME") is not None
        
        # 路径配置
        if self.IS_FC:
            self.ROOT_DIR = Path("/tmp/video_insight")
            self.OUTPUT_DIR = self.ROOT_DIR / "video_download"
            self.RESULT_DIR = self.ROOT_DIR / "result"
            self.DESKTOP_PATH = Path("/tmp")
            self.USER_DATA_FILE = str(self.ROOT_DIR / "user_folders.json")
        elif os.name == 'nt':
            self.ROOT_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            self.OUTPUT_DIR = self.ROOT_DIR / "downloads"
            self.RESULT_DIR = self.ROOT_DIR / "results"
            self.DESKTOP_PATH = self.ROOT_DIR
            self.USER_DATA_FILE = str(self.ROOT_DIR / "user_folders.json")
        else:
            self.ROOT_DIR = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            self.OUTPUT_DIR = self.ROOT_DIR / "downloads"
            self.RESULT_DIR = self.ROOT_DIR / "results"
            self.DESKTOP_PATH = self.ROOT_DIR
            self.USER_DATA_FILE = str(self.ROOT_DIR / "user_folders.json")

        # 确保目录存在
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.RESULT_DIR.mkdir(parents=True, exist_ok=True)
        if self.IS_FC:
             Path(os.path.dirname(self.USER_DATA_FILE)).mkdir(parents=True, exist_ok=True)

        # FFMPEG 路径配置：跨平台适配
        if os.name == 'nt':
            self.FFMPEG_PATH = self.ROOT_DIR / "ffmpeg_tool" / "ffmpeg.exe"
        else:
            # Linux/FC 环境通常直接使用系统中的 ffmpeg
            self.FFMPEG_PATH = Path("ffmpeg")
        
        # 任务锁文件路径 (用于跨进程任务同步)
        self.LOCK_FILE = self.ROOT_DIR / ".task.lock"
    
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

    # 运行时配置
    MAX_WORKERS = 5
    ANCHOR_START_OFFSET_S = float(os.getenv("ANCHOR_START_OFFSET_S", "0.3"))
    ANCHOR_END_OFFSET_S = float(os.getenv("ANCHOR_END_OFFSET_S", "0.2"))
    ANCHOR_LONG_SENTENCE_MIDPOINT = os.getenv("ANCHOR_LONG_SENTENCE_MIDPOINT", "false").strip().lower() in ("1", "true", "yes", "y", "on")
    
config = Config()
