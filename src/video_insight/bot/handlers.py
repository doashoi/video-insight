import json
import logging
import threading
import re
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

logger.info(f"Initializing Feishu Client with App ID: {_app_id[:5]}*** (Length: {len(_app_id)})")
logger.info(f"App Secret (Masked): {_app_secret[:2]}***{_app_secret[-2:] if len(_app_secret)>2 else ''} (Length: {len(_app_secret)})")

_client = lark_oapi.Client.builder() \
    .app_id(_app_id) \
    .app_secret(_app_secret) \
    .domain("https://open.feishu.cn") \
    .log_level(lark_oapi.LogLevel.INFO) \
    .build()

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
    """处理传入的文本消息。"""
    try:
        # 记录收到的原始事件类型和基本信息
        msg_id = data.event.message.message_id
        logger.info(f"Received message event. ID: {msg_id}")

        # 只处理文本消息
        if data.event.message.message_type != "text":
            logger.info(f"Ignoring non-text message: {data.event.message.message_type}")
            return

        # 获取用户信息，增加安全性检查
        if not data.event.sender or not data.event.sender.sender_id:
            logger.warning("Message event has no sender info.")
            return
            
        user_id = data.event.sender.sender_id.open_id
        if not user_id:
            logger.warning("Could not extract open_id from sender info.")
            return

        content_str = data.event.message.content
        if not content_str:
            return
            
        content = json.loads(content_str)
        text = content.get("text", "").strip()
        
        # 记录收到的消息内容
        logger.info(f"Message from {user_id}: {text}")
        
        # 1. 检查关键词
        # 允许简单的 "ping" 用于测试连通性
        if text.lower() == "ping":
            send_message(user_id, "pong")
            return

        keywords = ["分析", "start", "menu", "开始", "菜单"]
        if any(keyword in text.lower() for keyword in keywords):
            send_config_card(user_id)
            return

        # 2. 如果任务正在运行，且用户发送的不是指令，则保持沉默
        if TASK_LOCK.locked():
            logger.info(f"Task is running, ignoring message from {user_id}")
            return

        # 3. 只有当用户发送的是明显的文字输入时，才回复提示
        if text and len(text) > 0 and not text.startswith("{"):
            send_message(user_id, "输入 '分析' 或 'Start' 开启配置面板。")
            
    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        # 这里不要再 raise，否则 Webhook 会返回 500

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
                return

            # 尝试在开始前获取锁
            if not TASK_LOCK.acquire(blocking=False):
                send_message(user_id, "⚠️ 系统忙碌中，请稍后再试（当前有任务正在运行）。")
                return

            send_message(user_id, f"✅ 任务已接收！正在解析表格并准备分析环境，请稍后...")
            
            # 在后台线程运行任务
            try:
                t = threading.Thread(target=execute_task, args=(user_id, source_url, template_url))
                t.start()
            except Exception as e:
                if TASK_LOCK.locked():
                    TASK_LOCK.release()
                logger.error(f"Failed to start thread: {e}")
                send_message(user_id, "💥 启动任务失败。")
            
    except Exception as e:
        logger.error(f"Error handling card action: {e}")
