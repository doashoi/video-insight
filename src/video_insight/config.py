import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# 加载环境变量 (仅在非 FC 环境下加载 .env)
if not (os.environ.get("FC_FUNCTION_NAME") or os.environ.get("FC_SERVICE_NAME")):
    load_dotenv()

class Config:
    """项目配置类"""
    # 项目根目录
    ROOT_DIR = Path(__file__).parent.parent.parent
    
    # 检测运行环境
    IS_FC = os.environ.get("FC_FUNCTION_NAME") is not None or os.environ.get("FC_SERVICE_NAME") is not None

    # 默认下载路径逻辑
    if IS_FC:
        # 阿里云 FC 环境：只能使用 /tmp 目录
        OUTPUT_DIR = Path("/tmp/video_insight/video_download")
        RESULT_DIR = Path("/tmp/video_insight/result")
        DESKTOP_PATH = Path("/tmp") # FC 环境无桌面，指向 tmp
    elif os.name == 'nt':
        # Windows 本地环境：为了方便测试查看结果，默认指向桌面
        # 实际生产运行（云端）会使用 IS_FC 逻辑
        DESKTOP_PATH = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
        OUTPUT_DIR = DESKTOP_PATH / "Data_Analysis_Video_Download"
        RESULT_DIR = DESKTOP_PATH / "Data_Analysis_Result"
        
        # 本地测试时，如果目录不存在则创建
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
    else:
        # 其他环境 (如通用 Linux/Docker)
        OUTPUT_DIR = ROOT_DIR / "downloads"
        RESULT_DIR = ROOT_DIR / "results"
        DESKTOP_PATH = ROOT_DIR
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

    # FFMPEG 路径配置：跨平台适配
    if os.name == 'nt':
        FFMPEG_PATH = ROOT_DIR / "ffmpeg_tool" / "ffmpeg.exe"
    else:
        # Linux/FC 环境通常直接使用系统中的 ffmpeg
        FFMPEG_PATH = Path("ffmpeg")
    
    # 运行时配置
    MAX_WORKERS = 5
    ANCHOR_START_OFFSET_S = float(os.getenv("ANCHOR_START_OFFSET_S", "0.3"))
    ANCHOR_END_OFFSET_S = float(os.getenv("ANCHOR_END_OFFSET_S", "0.2"))
    ANCHOR_LONG_SENTENCE_MIDPOINT = os.getenv("ANCHOR_LONG_SENTENCE_MIDPOINT", "false").strip().lower() in ("1", "true", "yes", "y", "on")
    
    # 任务锁文件路径 (用于跨进程任务同步)
    LOCK_FILE = ROOT_DIR / ".task.lock"

config = Config()
