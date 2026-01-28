import threading
import traceback
from datetime import datetime
from typing import Optional, Tuple

from config import config
from downloader import run_downloader
from feishu_syncer import FeishuSyncer

# 全局锁，用于保证单任务执行
TASK_LOCK = threading.Lock()


def parse_feishu_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    解析飞书多维表格链接，提取 app_token 和 table_id。
    链接格式: https://{domain}/base/{app_token}?table={table_id}&...
    """
    try:
        if "/base/" not in url:
            return None, None

        # 提取 App Token
        part1 = url.split("/base/")[1]
        app_token = part1.split("?")[0].split("/")[0]

        # 提取 Table ID
        table_id = None
        if "table=" in url:
            table_id = url.split("table=")[1].split("&")[0]

        return app_token, table_id
    except Exception:
        return None, None


def run_pipeline_task(
    user_id: str,
    folder_token: str,
    app_name: str,
    source_url: str = None,
    progress_callback=None,
):
    """
    执行完整的处理管线:
    1. 解析源表格 URL (如果提供)
    2. 创建新的多维表格应用 (Bitable App)
    3. 添加用户为管理员
    4. 初始化表格字段
    5. 运行 下载器 -> 处理器 -> 分析器 -> 同步器
    """

    def report_progress(msg):
        print(f"[Progress] {msg}")
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception as e:
                print(f"[Warning] Failed to send progress update: {e}")

    report_progress(f"🚀 开始执行任务: {app_name}")
    report_progress(f"📂 默认下载文件夹: {config.DOWNLOAD_DIR}")

    print(f"\n[Task] Starting pipeline for User: {user_id}")

    # 如果未提供文件夹 token，使用默认 token
    if not folder_token:
        folder_token = config.FEISHU_FOLDER_TOKEN
        print(f"[Task] Using default folder token: {folder_token}")

    # --- 步骤 0: 解析源 ---
    source_app_token = None
    source_table_id = None
    if source_url:
        source_app_token, source_table_id = parse_feishu_url(source_url)
        print(f"[Task] Source: App={source_app_token}, Table={source_table_id}")
        if not source_app_token:
            print("[Task] Invalid Source URL. Using default config if available.")

    syncer = FeishuSyncer()

    # --- 步骤 1: 创建多维表格应用 ---
    # 添加时间戳到名称以确保唯一性
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    full_app_name = f"{app_name}_{timestamp}"

    app_token = syncer.create_bitable(full_app_name, folder_token)
    if not app_token:
        print("[Task] Failed to create Bitable app. Aborting.")
        return

    # --- 步骤 2: 添加权限 (确保创建者拥有权限) ---
    if not syncer.add_member_permission(app_token, user_id):
        print(f"[Task] Failed to add permission for user {user_id}.")
        # 即使添加权限失败也继续，因为应用已经创建在创建者的空间中

    # --- 步骤 3: 初始化字段 ---
    # 需要先获取默认的 table ID
    table_id = syncer.get_default_table_id(app_token)
    if not table_id:
        print("[Task] Failed to get default table ID. Aborting.")
        return

    syncer.init_table_fields(app_token, table_id)

    # --- 步骤 4: 运行分析管线 ---
    # 延迟导入重型模块，避免影响 Web 服务启动速度
    from video_processor import process_video_folder
    from ai_analyzer import AdsAnalyzer

    try:
        # 4.1 下载视频
        print(">>> [1/4] Downloading Videos...")
        # report_progress("⬇️ [1/4] 正在下载视频...")
        # 下载器会报告 "Task Started"
        run_downloader(source_app_token, source_table_id, report_progress)

        # 4.2 处理视频 (VAD/ASR)
        print(">>> [2/4] Processing Videos...")
        # report_progress("🎵 [2/4] 视频下载完成，正在提取音频并进行语音识别 (VAD/ASR)...")
        # 视频处理器会报告阶段
        process_video_folder(config.DOWNLOAD_DIR, config.OUTPUT_DIR, report_progress)

        # 4.3 AI 分析
        print(">>> [3/4] AI Analysis...")
        # report_progress("🤖 [3/4] 音频提取完成，正在进行 AI 智能分析与截图...")

        analyzer = AdsAnalyzer()
        # 传递源参数和进度回调
        analysis_results = analyzer.process(
            source_app_token, source_table_id, report_progress
        )

        # 4.4 同步到新表格
        print(
            f">>> [4/4] Syncing to New Table (App: {app_token}, Table: {table_id})..."
        )
        report_progress(f"🔄 分析完成，正在同步结果到飞书多维表格...")
        syncer.sync_data(analysis_results, app_token, table_id)

        # 报告成功并附带链接
        table_url = f"{config.FEISHU_DOMAIN}/base/{app_token}?table={table_id}"
        report_progress(
            f"🎉 任务全部完成！\n🔗 新表格链接: {table_url}\n📂 视频文件保存在: {config.OUTPUT_DIR}"
        )

        return True, app_token, full_app_name

    except Exception as e:
        print(f"[Task] Pipeline failed: {e}")
        traceback.print_exc()
        return False, None, str(e)
