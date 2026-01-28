import threading
import traceback
import os
import shutil
import logging
from datetime import datetime
from typing import Optional, Tuple
from pathlib import Path

from video_insight.config import config
from video_insight.downloader import run_downloader
from video_insight.video_processor import process_video_folder
from video_insight.ai_analyzer import AdsAnalyzer
from video_insight.feishu_syncer import FeishuSyncer

logger = logging.getLogger("Core")

# 全局内存锁，用于单进程内的线程同步
TASK_LOCK = threading.Lock()

def parse_feishu_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    解析飞书多维表格链接，提取 app_token 和 table_id。
    """
    try:
        if "/base/" not in url:
            return None, None
        
        part1 = url.split("/base/")[1]
        app_token = part1.split("?")[0].split("/")[0]
        
        table_id = None
        if "table=" in url:
            table_id = url.split("table=")[1].split("&")[0]
            
        return app_token, table_id
    except Exception:
        return None, None

def cleanup_temp_files():
    """清理临时下载和处理目录。"""
    for folder in [config.OUTPUT_DIR, config.RESULT_DIR]:
        if folder.exists():
            logger.info(f"Cleaning up folder: {folder}")
            try:
                # 删除文件夹内所有内容但保留文件夹本身
                for item in folder.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
            except Exception as e:
                logger.error(f"Failed to cleanup {folder}: {e}")

def run_pipeline_task(user_id: str, folder_token: str, app_name: str, source_url: str = None, progress_callback=None):
    """
    执行完整的处理管线。
    """
    def report_progress(msg):
        logger.info(f"[Progress] {msg}")
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception as e:
                logger.warning(f"Failed to send progress update: {e}")

    report_progress(f"🚀 开始执行任务: {app_name}")
    
    # 每次开始前清理旧的临时文件，防止空间占用和干扰
    cleanup_temp_files()

    try:
        # --- 步骤 0: 解析源 ---
        source_app_token = None
        source_table_id = None
        if source_url:
            source_app_token, source_table_id = parse_feishu_url(source_url)
            if not source_app_token:
                 report_progress("⚠️ 无法解析源表格链接，将使用默认配置。")
        
        syncer = FeishuSyncer()
        
        # --- 步骤 1: 创建多维表格应用 ---
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        full_app_name = f"{app_name}_{timestamp}"
        
        app_token = syncer.create_bitable(full_app_name, folder_token)
        if not app_token:
            return False, None, "创建多维表格失败"
        
        # --- 步骤 2: 添加权限 ---
        syncer.add_member_permission(app_token, user_id)
        
        # --- 步骤 3: 初始化字段 ---
        table_id = syncer.get_default_table_id(app_token)
        if not table_id:
            return False, None, "无法获取默认数据表 ID"
            
        syncer.init_table_fields(app_token, table_id)
        
        # --- 步骤 4: 运行分析管线 ---
        # 4.1 下载视频
        report_progress("⬇️ [1/4] 正在从源表格下载视频...")
        run_downloader(source_app_token, source_table_id, report_progress)
        
        # 4.2 处理视频 (VAD/ASR)
        report_progress("🎵 [2/4] 视频下载完成，正在进行语音识别 (ASR)...")
        process_video_folder(config.OUTPUT_DIR, config.RESULT_DIR, report_progress)
        
        # 4.3 AI 分析
        report_progress("🤖 [3/4] 正在使用 AI 分析视频内容并截取封面...")
        analyzer = AdsAnalyzer()
        analysis_results = analyzer.process(source_app_token, source_table_id, report_progress) 
        
        # 4.4 同步到新表格
        report_progress(f"🔄 [4/4] 正在将 {len(analysis_results)} 条分析结果同步到飞书...")
        syncer.sync_data(analysis_results, app_token, table_id)
        
        # 任务完成后再次清理
        cleanup_temp_files()
        
        table_url = f"{config.FEISHU_DOMAIN}/base/{app_token}?table={table_id}"
        return True, app_token, full_app_name
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        traceback.print_exc()
        # 失败时也尝试清理，防止残留
        cleanup_temp_files()
        return False, None, str(e)
