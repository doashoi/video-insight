import json
import logging
import threading
import lark_oapi
from lark_oapi.ws import Client
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1, CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from video_insight.config import config
from video_insight.core import run_pipeline_task, TASK_LOCK

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FeishuBot")

def send_message(user_id: str, content: str, msg_type: str = "text"):
    """Send a message to a user."""
    # Note: Using API Client for sending messages, not WebSocket Client
    client = lark_oapi.Client.builder().app_id(config.FEISHU_APP_ID).app_secret(config.FEISHU_APP_SECRET).build()
    
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
        
    resp = client.im.v1.message.create(req)
    if not resp.success():
        logger.error(f"Failed to send message: {resp.msg}")

def send_config_card(user_id: str):
    """Send the analysis configuration card."""
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
                        "content": "请填写源数据表格链接和任务名称，点击按钮开始分析。",
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
                                "content": "必须是飞书多维表格链接"
                            },
                            "required": True
                        },
                        {
                            "tag": "input",
                            "name": "task_name",
                            "label": {
                                "tag": "plain_text",
                                "content": "新任务名称"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "请输入任务名称"
                            },
                            "required": True,
                            "default_value": "视频分析任务"
                        },
                        {
                            "tag": "input",
                            "name": "folder_token",
                            "label": {
                                "tag": "plain_text",
                                "content": "目标文件夹 Token (可选)"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "可选"
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

def handle_message(data: P2ImMessageReceiveV1):
    """Handle incoming text messages."""
    try:
        content = json.loads(data.event.message.content)
        text = content.get("text", "").strip()
        user_id = data.event.sender.sender_id.open_id
        
        logger.info(f"Received message from {user_id}: {text}")
        
        # Simple keyword trigger
        if "分析" in text or "start" in text.lower() or "menu" in text.lower():
            send_config_card(user_id)
        else:
            send_message(user_id, "输入 '分析' 或 'Start' 开启配置面板。")
    except Exception as e:
        logger.error(f"Error handling message: {e}")

def execute_task(user_id: str, folder_token: str, app_name: str, source_url: str):
    """Execute the pipeline task in a separate thread."""
    try:
        # Define progress callback bound to this specific user_id
        def progress_callback(msg):
            send_message(user_id, msg)
            
        success, app_token, name = run_pipeline_task(user_id, folder_token, app_name, source_url, progress_callback)
        if success:
            send_message(user_id, f"🎉 分析完成！\n应用名称: {name}\nApp Token: {app_token}")
        else:
            send_message(user_id, f"❌ 分析失败: {name if name else '未知错误'}")
    except Exception as e:
        logger.error(f"Task runner error: {e}")
        send_message(user_id, f"💥 运行发生严重错误，请联系管理员。")
    finally:
        TASK_LOCK.release()
        logger.info("Task lock released.")

def handle_card_action(data: P2CardActionTrigger):
    """Handle card button clicks."""
    try:
        user_id = data.event.operator.open_id
        action = data.event.action
        action_value = action.value or {}
        form_data = action.form_value or {}
        
        logger.info(f"Card action from {user_id}: {action}")
        
        # Identify the action
        if action.name == "submit_btn" or action.name == "video_analysis_task_submit" or "source_table_link" in form_data:
            # Extract inputs
            source_url = form_data.get("source_table_link")
            app_name = form_data.get("task_name")
            folder_token = form_data.get("folder_token")
            
            # Validation
            if not source_url:
                send_message(user_id, "⚠️ 请输入源多维表格链接！")
                return

            # Attempt to acquire lock before starting
            if not TASK_LOCK.acquire(blocking=False):
                send_message(user_id, "⚠️ 系统忙碌中，请稍后再试（当前有任务正在运行）。")
                return

            send_message(user_id, f"✅ 任务已启动！\n名称: {app_name}\n源: {source_url}\n请耐心等待...")
            
            # Start task in background thread
            t = threading.Thread(target=execute_task, args=(user_id, folder_token, app_name, source_url))
            t.start()
            
    except Exception as e:
        logger.error(f"Error handling card action: {e}")

def main():
    logger.info("Starting Feishu WebSocket Client...")
    
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(handle_message) \
        .register_p2_card_action_trigger(handle_card_action) \
        .build()

    ws_client = Client(
        config.FEISHU_APP_ID, 
        config.FEISHU_APP_SECRET,
        event_handler=event_handler,
        log_level=lark_oapi.LogLevel.INFO
    )

    ws_client.start()

if __name__ == "__main__":
    main()
