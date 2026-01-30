import json
import logging
import threading
import re
import os
import sys
import lark_oapi
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1, CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from video_insight.config import config
from video_insight.core import run_pipeline_task, TASK_LOCK

logger = logging.getLogger("BotHandlers")

# 初始化全局飞书客户端
# 注意：使用自建应用时，app_type 默认为 tenant，无需额外配置
# 如果出现 10003 invalid param，通常是因为缺少 log_level 或其他配置导致的 SDK 内部校验失败
# 或者是因为环境变量中有特殊字符
_app_id = config.FEISHU_APP_ID.strip() if config.FEISHU_APP_ID else ""
_app_secret = config.FEISHU_APP_SECRET.strip() if config.FEISHU_APP_SECRET else ""

# 移除可能存在的引号（防止用户直接从 .env 复制带引号的值）
_app_id = _app_id.replace('"', '').replace("'", "")
_app_secret = _app_secret.replace('"', '').replace("'", "")

# 基本格式校验
if _app_id and not _app_id.startswith("cli_"):
    logger.warning(f"Warning: FEISHU_APP_ID does not start with 'cli_'. Current value starts with: {_app_id[:4]}")

logger.info(f"Initializing Feishu Client (Verified) with App ID: {_app_id[:5]}*** (Length: {len(_app_id)})")
logger.info(f"App Secret (Masked): {_app_secret[:2]}***{_app_secret[-2:] if len(_app_secret)>2 else ''} (Length: {len(_app_secret)})")

_client = lark_oapi.Client.builder() \
    .app_id(_app_id) \
    .app_secret(_app_secret) \
    .domain("https://open.feishu.cn") \
    .log_level(lark_oapi.LogLevel.DEBUG) \
    .build()

# 封装简单的文本回复方法
def send_message(user_id: str, content: str, msg_type: str = "text"):
    """向用户发送消息。"""
    if msg_type == "text":
        content_json = json.dumps({"text": content})
    else:
        content_json = content
        
    req = CreateMessageRequest.builder() \
        .receive_id_type("open_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(user_id)
            .msg_type(msg_type)
            .content(content_json)
            .build()) \
        .build()
        
    resp = _client.im.v1.message.create(req)
    if not resp.success():
        logger.error(f"Failed to send message to {user_id}: {resp.msg} (code: {resp.code})")
    else:
        logger.info(f"Successfully sent {msg_type} message to {user_id}")

def extract_folder_token(text: str) -> str:
    """从 URL 或文本中提取文件夹 token。"""
    if not text:
        return ""
    # 尝试匹配 folder/TOKEN 模式
    match = re.search(r"folder\/([a-zA-Z0-9]+)", text)
    if match:
        return match.group(1)
    # 检查是否看起来像 token
    if re.match(r"^fld[a-zA-Z0-9]+$", text):
        return text
    return ""

def send_config_card(user_id: str):
    """发送分析配置卡片。"""
    card_content = {
        "schema": "2.0",
        "header": {
            "template": "blue",
            "title": {
                "content": "🎬 视频洞察分析 - 任务配置",
                "tag": "plain_text"
            }
        },
        "body": {
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "content": "请填写需要获取信息的飞书表格链接（支持 Base 和 Wiki）。系统将自动创建分析结果表并存储在“自动分析”空间中。",
                        "tag": "plain_text"
                    }
                },
                {
                    "tag": "form",
                    "name": "video_analysis_task_submit",
                    "elements": [
                        {
                            "tag": "input",
                            "name": "source_table_link",
                            "label": {
                                "tag": "plain_text",
                                "content": "源数据表链接"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "粘贴飞书多维表格或知识库表格链接"
                            },
                            "required": True
                        },
                        {
                            "tag": "input",
                            "name": "template_table_link",
                            "label": {
                                "tag": "plain_text",
                                "content": "模板多维表格链接 (可选)"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "如果不填写，将直接复制源数据表的结构"
                            },
                            "required": False
                        },
                        {
                            "tag": "div",
                            "text": {
                                "content": "💡 提示：系统将自动处理视频并生成分析结果，请稍候。",
                                "tag": "lark_md"
                            }
                        },
                        {
                            "tag": "button",
                            "text": {
                                "tag": "plain_text",
                                "content": "确认提交"
                            },
                            "type": "primary",
                            "action_type": "form_submit",
                            "name": "submit_btn"
                        }
                    ]
                }
            ]
        }
    }
    
    send_message(user_id, json.dumps(card_content), "interactive")

def execute_task(user_id: str, source_url: str, template_url: str = None):
    """执行管道任务。"""
    try:
        # 调试代码：检查环境中的 FFmpeg
        import subprocess
        logger.info("[Debug] Checking environment for FFmpeg...")
        try:
            res = subprocess.check_output(["ffmpeg", "-version"], stderr=subprocess.STDOUT)
            logger.info(f"[Debug] FFmpeg found: {res.decode().splitlines()[0]}")
        except Exception as e:
            logger.error(f"[Debug] FFmpeg check failed: {e}")
            # 尝试检查常见路径
            common_paths = ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffprobe"]
            for p in common_paths:
                if os.path.exists(p):
                    logger.info(f"[Debug] Found file at {p}")
                else:
                    logger.info(f"[Debug] {p} not found")

        # 定义绑定到特定 user_id 的进度回调
        def progress_callback(msg):
            send_message(user_id, msg)
            
        success, app_token, name = run_pipeline_task(user_id, source_url, progress_callback, template_url=template_url)
        if success:
            send_message(user_id, f"🎉 任务全部完成！\n新表格名称: {name}\nApp Token: {app_token}")
        else:
            send_message(user_id, f"❌ 任务失败: {name if name else '未知错误'}")
    except Exception as e:
        logger.error(f"Task runner error: {e}", exc_info=True)
        send_message(user_id, f"💥 运行发生严重错误，请联系管理员。")
    finally:
        # 释放全局任务锁
        if TASK_LOCK.locked():
            TASK_LOCK.release()
            logger.info("Task lock released.")

def handle_message(data: P2ImMessageReceiveV1):
    """处理传入的消息。"""
    try:
        # 记录收到的原始事件类型和基本信息
        msg_id = data.event.message.message_id
        msg_type = data.event.message.message_type
        logger.info(f"Received message event. ID: {msg_id}, Type: {msg_type}")

        # 获取用户信息，增加安全性检查
        if not data.event.sender or not data.event.sender.sender_id:
            logger.warning("Message event has no sender info.")
            return {}
            
        user_id = data.event.sender.sender_id.open_id
        if not user_id:
            logger.warning("Could not extract open_id from sender info.")
            return {}

        # 1. 处理文本消息
        if msg_type == "text":
            content_str = data.event.message.content
            if not content_str:
                return {}
                
            content = json.loads(content_str)
            text = content.get("text", "").strip()
            
            # 记录收到的消息内容
            logger.info(f"Message from {user_id}: {text}")
            
            # 允许简单的 "ping" 用于测试连通性
            if text.lower() == "ping":
                send_message(user_id, "pong")
                return {}

            # CID 提取指令
            if text.upper() == "CID":
                send_message(user_id, "📋 请发送包含 'CID' 和 '尺寸' 列的 Excel 或 CSV 文件，我将为您自动提取并整理。")
                return {}

            keywords = ["分析", "start", "menu", "开始", "菜单"]
            if any(keyword in text.lower() for keyword in keywords):
                send_config_card(user_id)
                return {}

            # 如果任务正在运行，且用户发送的不是指令，则保持沉默
            if TASK_LOCK.locked():
                logger.info(f"Task is running, ignoring message from {user_id}")
                return {}

            # 只有当用户发送的是明显的文字输入时，才回复提示
            if text and len(text) > 0 and not text.startswith("{"):
                send_message(user_id, "输入 '分析' 开启配置面板，或发送 'CID' 开启 CID 提取功能。")
                
            return {}

        # 2. 处理文件消息
        elif msg_type == "file":
            content_str = data.event.message.content
            content = json.loads(content_str)
            file_key = content.get("file_key")
            file_name = content.get("file_name", "unknown_file")
            
            if not file_key:
                logger.warning("File message without file_key")
                return {}
            
            # 检查扩展名
            ext = os.path.splitext(file_name)[1].lower()
            if ext not in [".xlsx", ".xls", ".csv"]:
                # 如果不是表格文件，忽略，避免干扰
                return {}
            
            send_message(user_id, f"📥 收到文件: {file_name}，正在解析中，请稍候...")
            
            # 云端临时目录处理
            temp_dir = "/tmp" if config.IS_FC else "temp"
            os.makedirs(temp_dir, exist_ok=True)
            temp_path = os.path.join(temp_dir, f"{msg_id}{ext}")
            
            try:
                from video_insight.feishu_syncer import FeishuSyncer
                syncer = FeishuSyncer()
                
                # 下载并处理
                if syncer.download_im_file(msg_id, file_key, temp_path):
                    data_map = syncer.process_cid_file(temp_path)
                    if not data_map:
                        send_message(user_id, "❌ 文件解析失败，请确保文件中包含 'CID' 和 '尺寸' 列。")
                    else:
                        report_url = syncer.create_cid_report(data_map, user_id)
                        if report_url:
                            send_message(user_id, f"✅ CID 整理表已生成：\n{report_url}\n\n文件已存入“自动提取”文件夹。")
                        else:
                            send_message(user_id, "❌ 生成飞书文档失败，请稍后重试。")
                else:
                    send_message(user_id, "❌ 文件下载失败。")
            except Exception as e:
                logger.error(f"Error processing CID file: {e}", exc_info=True)
                send_message(user_id, f"❌ 处理过程中发生错误: {str(e)}")
            finally:
                # 清理临时文件
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass
            
            return {}

        else:
            logger.info(f"Ignoring message type: {msg_type}")
            return {}
            
    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        return {}

def handle_card_action(data: P2CardActionTrigger):
    """处理卡片按钮点击。"""
    try:
        user_id = data.event.operator.open_id
        action = data.event.action
        form_data = action.form_value or {}
        
        logger.info(f"Card action from {user_id}: {action}")
        
        if action.name == "submit_btn" or action.name == "video_analysis_task_submit" or "source_table_link" in form_data:
            # 提取输入
            source_url = form_data.get("source_table_link")
            template_url = form_data.get("template_table_link")
            
            # 验证
            if not source_url:
                send_message(user_id, "⚠️ 请输入源多维表格链接！")
                return {"toast": {"type": "error", "content": "请输入源表格链接"}}

            # 尝试在开始前获取锁
            if not TASK_LOCK.acquire(blocking=False):
                send_message(user_id, "⚠️ 系统忙碌中，请稍后再试（当前有任务正在运行）。")
                return {"toast": {"type": "warn", "content": "系统忙碌中"}}

            send_message(user_id, f"✅ 任务已接收！正在解析表格并准备分析环境，请稍后...")
            logger.info(f"Starting background thread for user {user_id}...")
            
            # 显式刷新输出，确保日志可见
            try:
                sys.stdout.flush()
            except NameError:
                logger.error("NameError: sys is not defined during flush")
            
            # 在后台线程运行任务
            try:
                if config.IS_FC:
                    # 在 FC 环境下，使用异步调用 (Async Invocation)
                    # 避免 HTTP 请求超时或容器冻结
                    logger.info(f"Preparing async invocation for user {user_id} (FC Mode)...")
                    
                    from video_insight import fc_context
                    import fc2

                    # 1. 获取上下文信息
                    func_name = fc_context.fc_function_name.get()
                    service_name = fc_context.fc_service_name.get()
                    region = fc_context.fc_region.get()
                    account_id = fc_context.fc_account_id.get()
                    
                    if not func_name or not account_id:
                        logger.warning("Missing FC context (func_name or account_id). Falling back to synchronous execution.")
                        execute_task(user_id, source_url, template_url)
                    else:
                        # 2. 构造 Client
                        access_key_id = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
                        access_key_secret = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
                        security_token = os.environ.get('ALIBABA_CLOUD_SECURITY_TOKEN')
                        
                        endpoint = f"https://{account_id}.{region}.fc.aliyuncs.com"
                        
                        client = fc2.Client(
                            endpoint=endpoint,
                            accessKeyID=access_key_id,
                            accessKeySecret=access_key_secret,
                            securityToken=security_token
                        )
                        
                        # 3. 构造 Payload
                        payload = json.dumps({
                            "action": "run_task_sync",
                            "user_id": user_id,
                            "source_url": source_url,
                            "template_url": template_url
                        })
                        
                        # 4. 执行异步调用
                        target_service = service_name if service_name else "SenseVoiceService"
                        logger.info(f"Invoking function: {target_service}/{func_name} (Async)")
                        
                        try:
                            client.invoke_function(
                                target_service,
                                func_name,
                                payload=payload,
                                headers={'x-fc-invocation-type': 'Async'}
                            )
                            logger.info("Async invocation success. Task offloaded.")
                            if TASK_LOCK.locked():
                                TASK_LOCK.release()
                        except Exception as invoke_err:
                            logger.error(f"Async invocation failed: {invoke_err}. Falling back to sync.")
                            execute_task(user_id, source_url, template_url)

                else:
                    # 本地模式继续使用后台线程
                    logger.info(f"Starting background thread for user {user_id} (Local Mode)...")
                    t = threading.Thread(target=execute_task, args=(user_id, source_url, template_url))
                    t.start()
            except Exception as e:
                if TASK_LOCK.locked():
                    TASK_LOCK.release()
                logger.error(f"Failed to start task: {e}")
                return {"toast": {"type": "error", "content": "启动任务失败"}}

            return {"toast": {"type": "success", "content": "任务已接收"}}
            
        return {} # 默认返回空对象，避免 SDK 报错
            
    except Exception as e:
        logger.error(f"Error handling card action: {e}", exc_info=True)
        return {"toast": {"type": "error", "content": f"处理失败: {str(e)}"}}
