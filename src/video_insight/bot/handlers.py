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

def send_message(user_id: str, content: str, msg_type: str = "text"):
    """向用户发送消息。"""
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
        logger.error(f"Failed to send message to {user_id}: {resp.msg} (code: {resp.code})")

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
                        "content": "请填写源数据表格链接和任务名称。结果将自动存入您的飞书文件夹。",
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
                            "name": "folder_link",
                            "label": {
                                "tag": "plain_text",
                                "content": "目标文件夹链接 (可选)"
                            },
                            "placeholder": {
                                "tag": "plain_text",
                                "content": "粘贴飞书文件夹链接，结果表将存放在此"
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

def execute_task(user_id: str, folder_token: str, app_name: str, source_url: str):
    """执行管道任务。"""
    try:
        # 定义绑定到特定 user_id 的进度回调
        def progress_callback(msg):
            send_message(user_id, msg)
            
        # 确定目标文件夹
        target_token = folder_token if folder_token else config.FEISHU_FOLDER_TOKEN
        
        if target_token == config.FEISHU_FOLDER_TOKEN:
             progress_callback(f"📂 结果将保存到系统默认空间")
        else:
             progress_callback(f"📂 使用您指定的文件夹")

        success, app_token, name = run_pipeline_task(user_id, target_token, app_name, source_url, progress_callback)
        if success:
            send_message(user_id, f"🎉 分析完成！\n应用名称: {name}\nApp Token: {app_token}")
        else:
            send_message(user_id, f"❌ 分析失败: {name if name else '未知错误'}")
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
        content = json.loads(data.event.message.content)
        text = content.get("text", "").strip()
        user_id = data.event.sender.sender_id.open_id
        
        logger.info(f"Received message from {user_id}: {text}")
        
        # 简单的关键词触发
        if any(keyword in text.lower() for keyword in ["分析", "start", "menu", "开始", "菜单"]):
            send_config_card(user_id)
        else:
            send_message(user_id, "输入 '分析' 或 'Start' 开启配置面板。")
    except Exception as e:
        logger.error(f"Error handling message: {e}")

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
            app_name = form_data.get("task_name")
            folder_link = form_data.get("folder_link", "")
            
            # 提取 Token
            folder_token = extract_folder_token(folder_link)
            
            # 验证
            if not source_url:
                send_message(user_id, "⚠️ 请输入源多维表格链接！")
                return

            # 尝试在开始前获取锁
            if not TASK_LOCK.acquire(blocking=False):
                send_message(user_id, "⚠️ 系统忙碌中，请稍后再试（当前有任务正在运行）。")
                return

            send_message(user_id, f"✅ 任务已启动！\n名称: {app_name}\n源: {source_url}\n请耐心等待...")
            
            # 在后台线程运行任务
            try:
                t = threading.Thread(target=execute_task, args=(user_id, folder_token, app_name, source_url))
                t.start()
            except Exception as e:
                if TASK_LOCK.locked():
                    TASK_LOCK.release()
                logger.error(f"Failed to start thread: {e}")
                send_message(user_id, "💥 启动任务失败。")
            
    except Exception as e:
        logger.error(f"Error handling card action: {e}")
