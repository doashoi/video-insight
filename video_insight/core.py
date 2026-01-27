import threading
import traceback
from datetime import datetime
from typing import Optional

from video_insight.config import config
from video_insight.downloader import run_downloader
from video_insight.video_processor import process_video_folder
from video_insight.ai_analyzer import AdsAnalyzer
from video_insight.feishu_syncer import FeishuSyncer

# Global Lock for Single Task Execution
TASK_LOCK = threading.Lock()

def parse_feishu_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse Bitable URL to extract app_token and table_id.
    URL Format: https://{domain}/base/{app_token}?table={table_id}&...
    """
    try:
        if "/base/" not in url:
            return None, None
        
        # Extract App Token
        part1 = url.split("/base/")[1]
        app_token = part1.split("?")[0].split("/")[0]
        
        # Extract Table ID
        table_id = None
        if "table=" in url:
            table_id = url.split("table=")[1].split("&")[0]
            
        return app_token, table_id
    except Exception:
        return None, None

def run_pipeline_task(user_id: str, folder_token: str, app_name: str, source_url: str = None, progress_callback=None):
    """
    Execute the full pipeline:
    1. Parse Source URL (if provided)
    2. Create new Bitable App
    3. Add User as Admin
    4. Initialize Table Fields
    5. Run Downloader -> Processor -> Analyzer -> Syncer
    """
    def report_progress(msg):
        print(f"[Progress] {msg}")
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception as e:
                print(f"[Warning] Failed to send progress update: {e}")

    report_progress(f"🚀 开始执行任务: {app_name}")
    report_progress(f"📂 默认下载文件夹: {config.OUTPUT_DIR}")

    print(f"\n[Task] Starting pipeline for User: {user_id}")

    # Use default folder token if not provided
    if not folder_token:
        folder_token = config.FEISHU_FOLDER_TOKEN
        print(f"[Task] Using default folder token: {folder_token}")
    
    # --- Step 0: Parse Source ---
    source_app_token = None
    source_table_id = None
    if source_url:
        source_app_token, source_table_id = parse_feishu_url(source_url)
        print(f"[Task] Source: App={source_app_token}, Table={source_table_id}")
        if not source_app_token:
             print("[Task] Invalid Source URL. Using default config if available.")
    
    syncer = FeishuSyncer()
    
    # --- Step 1: Create Bitable App ---
    # Append timestamp to name to ensure uniqueness
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    full_app_name = f"{app_name}_{timestamp}"
    
    app_token = syncer.create_bitable(full_app_name, folder_token)
    if not app_token:
        print("[Task] Failed to create Bitable app. Aborting.")
        return

    # --- Step 2: Add Permission (确保创建者拥有权限) ---
    if not syncer.add_member_permission(app_token, user_id):
        print(f"[Task] Failed to add permission for user {user_id}.")
        # 即使添加权限失败也继续，因为应用已经创建在创建者的空间中

    # --- Step 3: Initialize Fields ---
    # Need to get the default table ID first
    table_id = syncer.get_default_table_id(app_token)
    if not table_id:
        print("[Task] Failed to get default table ID. Aborting.")
        return
        
    syncer.init_table_fields(app_token, table_id)

    # --- Step 4: Run Analysis Pipeline ---
    try:
        # 4.1 Download
        print(">>> [1/4] Downloading Videos...")
        # report_progress("⬇️ [1/4] 正在下载视频...")
        # Downloader will report "Task Started"
        run_downloader(source_app_token, source_table_id, report_progress)

        # 4.2 Process (VAD/ASR)
        print(">>> [2/4] Processing Videos...")
        # report_progress("🎵 [2/4] 视频下载完成，正在提取音频并进行语音识别 (VAD/ASR)...")
        # Video Processor will report phases
        process_video_folder(config.OUTPUT_DIR, config.RESULT_DIR, report_progress)

        # 4.3 AI Analysis
        print(">>> [3/4] AI Analysis...")
        # report_progress("🤖 [3/4] 音频提取完成，正在进行 AI 智能分析与截图...")
        
        analyzer = AdsAnalyzer()
        # Pass source params and progress callback
        analysis_results = analyzer.process(source_app_token, source_table_id, report_progress) 

        # 4.4 Sync to New Table
        print(f">>> [4/4] Syncing to New Table (App: {app_token}, Table: {table_id})...")
        report_progress(f"🔄 分析完成，正在同步结果到飞书多维表格...")
        syncer.sync_data(analysis_results, app_token, table_id)
        
        # Report Success with Link
        table_url = f"{config.FEISHU_DOMAIN}/base/{app_token}?table={table_id}"
        report_progress(f"🎉 任务全部完成！\n🔗 新表格链接: {table_url}\n📂 视频文件保存在: {config.OUTPUT_DIR}")
        
        return True, app_token, full_app_name
        
    except Exception as e:
        print(f"[Task] Pipeline failed: {e}")
        traceback.print_exc()
        return False, None, str(e)
    finally:
        pass
