import threading
import traceback
import os
import shutil
import logging
import re
import time
import sys
from datetime import datetime
from typing import Optional, Tuple
from pathlib import Path

import lark_oapi
from lark_oapi.api.wiki.v2.model import GetNodeSpaceRequest

from video_insight.config import config
from video_insight.downloader import run_downloader
from video_insight.video_processor import process_video_folder
from video_insight.ai_analyzer import AdsAnalyzer
from video_insight.feishu_syncer import FeishuSyncer

logger = logging.getLogger("Core")

# 全局内存锁，用于单进程内的线程同步
TASK_LOCK = threading.Lock()

def resolve_wiki_token(wiki_token: str) -> Tuple[Optional[str], Optional[str]]:
    """
    通过 Wiki Token 解析出对应的 Bitable App Token。
    """
    logger.info(f"Resolving wiki token: {wiki_token}")
    client = lark_oapi.Client.builder().app_id(config.FEISHU_APP_ID).app_secret(config.FEISHU_APP_SECRET).build()
    try:
        req = GetNodeSpaceRequest.builder() \
            .token(wiki_token) \
            .build()
        resp = client.wiki.v2.space.get_node(req)
        
        if not resp.success():
            logger.error(f"Failed to resolve wiki token: {resp.msg}")
            return None, None
            
        node = resp.data.node
        if node.obj_type == "bitable":
            logger.info(f"Resolved wiki token to bitable: {node.obj_token}")
            return node.obj_token, None # table_id 无法从 wiki token 直接获取，通常默认为第一个表
        else:
            logger.warning(f"Wiki node is not a bitable: {node.obj_type}")
            return None, None
    except Exception as e:
        logger.error(f"Error resolving wiki token: {e}")
        return None, None

def parse_feishu_url(url: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    解析飞书链接，支持多维表格链接和知识库链接。
    返回 (app_token, table_id, domain)
    """
    try:
        url = url.strip() # Remove whitespace
        logger.info(f"Parsing URL: {url}")
        
        # 提取域名
        domain_match = re.search(r"https?://([^/]+)", url)
        domain = domain_match.group(0) if domain_match else config.FEISHU_DOMAIN
        
        # 1. 检查是否是 Wiki 链接
        wiki_match = re.search(r"\/wiki\/([a-zA-Z0-9]+)", url)
        if wiki_match:
            wiki_token = wiki_match.group(1)
            logger.info(f"Detected Wiki link, token: {wiki_token}")
            app_token, table_id = resolve_wiki_token(wiki_token)
            return app_token, table_id, domain

        # 2. 检查是否是普通的 Base 链接
        if "/base/" in url:
            part1 = url.split("/base/")[1]
            app_token = part1.split("?")[0].split("/")[0]
            
            table_id = None
            if "table=" in url:
                table_id = url.split("table=")[1].split("&")[0]
                
            return app_token, table_id, domain
            
        return None, None, domain
    except Exception as e:
        logger.error(f"Error parsing Feishu URL: {e}")
        return None, None, config.FEISHU_DOMAIN

def cleanup_temp_files(folders: list = None):
    """清理临时下载和处理目录。"""
    if folders is None:
        folders = [config.OUTPUT_DIR, config.RESULT_DIR]
    
    for folder in folders:
        if folder.exists():
            # 减少日志输出，除非是 DEBUG 模式
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Cleaning up folder: {folder}")
            try:
                # 删除文件夹内所有内容但保留文件夹本身
                for item in folder.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                # 尝试删除文件夹本身（如果是动态生成的缓存目录）
                try:
                    folder.rmdir()
                except OSError:
                    pass 
            except Exception as e:
                logger.error(f"Failed to cleanup {folder}: {e}")

def run_pipeline_task(user_id: str, source_url: str, progress_callback=None, template_url: str = None):
    """
    执行完整的处理管线。
    """
    def report_progress(msg):
        logger.info(f"[Progress] {msg}")
        sys.stdout.flush() # 强制刷新日志
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception as e:
                logger.warning(f"Failed to send progress update: {e}")

    report_progress("🚀 开始执行视频洞察分析任务...")
    
    # 动态生成缓存目录
    cache_root_dir = None
    video_download_dir = None
    result_dir = None

    try:
        # --- 步骤 0: 解析源 ---
        syncer = FeishuSyncer()
        report_progress("🔍 正在解析源表格链接...")
        source_app_token, source_table_id, domain = parse_feishu_url(source_url)
        
        if not source_app_token:
             return False, None, "无法解析源表格链接，请确保链接正确且机器人有权限访问。"
        
        # 获取原表名称
        original_name = syncer.get_app_name(source_app_token) or "未命名表格"
        # 移除可能不合法的文件名字符
        safe_name = re.sub(r'[\\/*?:"<>|]', "", original_name)
        report_progress(f"📋 已定位源表格: {original_name}")

        # 设置临时目录
        # 统一使用项目根目录下的子目录，避免污染用户桌面
        if config.IS_FC:
            cache_root_dir = Path("/tmp") / f"task_{user_id}_{int(time.time())}"
        else:
            # 本地环境下使用 .cache 目录
            cache_root_dir = config.ROOT_DIR / ".cache" / f"task_{user_id}_{int(time.time())}"
            
        video_download_dir = cache_root_dir / "downloads"
        result_dir = cache_root_dir / "results"
        
        # 确保目录存在
        video_download_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)
        
        # report_progress(f"📂 临时工作目录: {cache_root_dir}")

        # --- 步骤 1: 准备目标空间 ---
        report_progress("📂 正在准备“自动分析”空间...")
        folder_token = syncer.get_or_create_folder("自动分析", user_id)
        if not folder_token:
            return False, None, "无法在您的空间创建“自动分析”文件夹。"

        # --- 步骤 2: 创建结果表格 ---
        full_app_name = f"{original_name}_自动分析"
        report_progress(f"🆕 正在创建结果表: {full_app_name} ...")
        
        # 决定复制源
        copy_source_app_token = source_app_token
        if template_url and template_url.strip():
            report_progress("🎨 正在解析模板表格链接...")
            template_app_token, _, _ = parse_feishu_url(template_url)
            if template_app_token:
                copy_source_app_token = template_app_token
                report_progress("✨ 已切换至用户自定义模板。")
            else:
                report_progress("⚠️ 模板链接解析失败，将使用原表结构作为兜底。")

        # 使用 copy_bitable 替代 create_bitable，以保留原表结构
        app_token = syncer.copy_bitable(copy_source_app_token, full_app_name, folder_token, user_id)
        if not app_token:
            error_msg = getattr(syncer, 'last_error', None) or "复制多维表格失败"
            return False, None, error_msg
        
        # --- 步骤 3: 初始化权限和获取 Schema ---
        syncer.add_member_permission(app_token, user_id)
        
        table_id = syncer.get_default_table_id(app_token)
        if not table_id:
            return False, None, "无法获取新表的默认数据表 ID"
            
        # 获取目标表的结构定义
        report_progress("📋 正在获取目标表结构定义...")
        schema = syncer.get_table_schema(app_token, table_id)
        if not schema:
             report_progress("⚠️ 无法获取表结构，将使用默认分析逻辑。")
        else:
             report_progress(f"✅ 已成功解析 {len(schema)} 个字段定义。")

        # --- 步骤 4: 意图确认与主动追问 (新增环节) ---
        report_progress("🤔 正在生成分析意图确认清单...")
        analyzer = AdsAnalyzer(output_dir=result_dir, assets_dir=result_dir)
        confirmation_list = analyzer.analyze_template(schema)
        
        if confirmation_list:
            # 这里的逻辑在实际生产中应该：
            # 1. 发送消息卡片给用户
            # 2. 等待用户确认或修改指令
            # 3. 如果用户修改，则更新 user_logic 重新分析或直接应用
            # 目前作为 MVP 阶段，我们模拟这一过程或将清单记录到日志中
            report_progress("📝 AI 对当前模板的理解如下：")
            for item in confirmation_list:
                status_icon = "✅" if item['status'] == 'resolved' else "❓"
                report_progress(f"{status_icon} 【{item['field_name']}】: {item['logic_description']}")
                if item['status'] != 'resolved':
                    report_progress(f"   👉 追问: {item['clarification_question']}")
            
            # TODO: 这里需要一个真正的交互循环
            # user_logic = wait_for_user_confirmation(confirmation_list)
            user_logic = "" # 暂时留空，表示使用 AI 默认生成的逻辑
        else:
            user_logic = ""

        # 告知用户新表链接
        # 确保域名不包含多余字符
        clean_domain = domain.rstrip("/")
        table_url = f"{clean_domain}/base/{app_token}?table={table_id}"
        report_progress(f"✅ 结果表已准备就绪！\n🔗 链接: {table_url}\n\n现在开始处理视频，这可能需要几分钟时间，请稍后查看结果表。")

        # --- 步骤 5: 运行下载与分析管线 ---
        # 5.1 下载视频
        report_progress("⬇️ [1/4] 正在下载视频...")
        run_downloader(source_app_token, source_table_id, report_progress, output_dir=video_download_dir)
        
        # 5.2 处理视频 (VAD/ASR)
        report_progress("🎵 [2/4] 正在进行语音识别 (ASR)...")
        process_video_folder(video_download_dir, result_dir, report_progress)
        
        # 5.3 AI 分析 (传入 user_logic)
        report_progress("🤖 [3/4] 正在使用 AI 分析视频内容...")
        analysis_results = analyzer.process(source_app_token, source_table_id, report_progress, schema=schema, user_logic=user_logic) 
        
        # 5.4 同步到新表格
        report_progress(f"🔄 [4/4] 正在同步 {len(analysis_results)} 条分析结果到飞书...")
        syncer.sync_data(analysis_results, app_token, table_id)
        
        # 任务完成后清理
        if cache_root_dir and cache_root_dir.exists():
            # report_progress(f"🧹 正在清理临时文件: {cache_root_dir}")
            shutil.rmtree(cache_root_dir, ignore_errors=True)
        
        return True, app_token, full_app_name
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        logger.error(traceback.format_exc())
        # 出错时也尝试清理
        if cache_root_dir and cache_root_dir.exists():
             shutil.rmtree(cache_root_dir, ignore_errors=True)
        return False, None, str(e)
