import os
from pathlib import Path
from dotenv import load_dotenv
import imageio_ffmpeg

# 加载环境变量
load_dotenv()


class Config:
    # 项目根目录
    ROOT_DIR = Path(__file__).parent.parent
    # 中央临时处理目录 (服务器本地)
    DOWNLOAD_DIR = ROOT_DIR / "Data_Analysis_Video_Download"
    OUTPUT_DIR = ROOT_DIR / "output"
    RESULT_DIR = ROOT_DIR / "result"

    # 如果目录不存在则创建
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    # 飞书应用凭证
    FEISHU_APP_ID = os.getenv("FEISHU_APP_ID")
    FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET")
    FEISHU_VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN")
    FEISHU_ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY")
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

    # 自动配置 FFmpeg
    # funasr 等库依赖系统 PATH 中必须有名为 "ffmpeg" 的可执行文件
    # imageio-ffmpeg 下载的文件名通常包含版本号，直接添加到 PATH 可能无法被识别
    _original_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    # 尝试使用项目内 ffmpeg_tool 目录作为标准路径
    FFMPEG_DIR = ROOT_DIR / "ffmpeg_tool"
    FFMPEG_DIR.mkdir(exist_ok=True)

    if os.name == "nt":  # Windows
        FFMPEG_PATH = FFMPEG_DIR / "ffmpeg.exe"
    else:
        FFMPEG_PATH = FFMPEG_DIR / "ffmpeg"

    # 如果目标文件不存在，或者源文件更新了，则进行复制
    # 这里简单处理：如果不存在就复制
    if not FFMPEG_PATH.exists() and os.path.exists(_original_ffmpeg):
        import shutil

        print(
            f"Copying ffmpeg from {_original_ffmpeg} to {FFMPEG_PATH} for compatibility..."
        )
        shutil.copy2(_original_ffmpeg, FFMPEG_PATH)

    # 如果复制失败（例如权限问题）且原文件存在，回退使用原文件
    if not FFMPEG_PATH.exists() and os.path.exists(_original_ffmpeg):
        FFMPEG_PATH = Path(_original_ffmpeg)

    # 处理配置
    MAX_WORKERS = 5


config = Config()
