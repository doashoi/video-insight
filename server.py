import json
import logging
import threading
import re
import lark_oapi
from fastapi import FastAPI, Request, BackgroundTasks
import sys
from pathlib import Path

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1, CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger

from video_insight.config import config
from video_insight.core import run_pipeline_task, TASK_LOCK

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FeishuServer")

app = FastAPI()

# --- 辅助函数 ---

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
        logger.error(f"Failed to send message: {resp.msg}")

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

# --- 逻辑处理程序 ---

def execute_task(user_id: str, folder_token: str, app_name: str, source_url: str):
    """执行管道任务。"""
    try:
        # 定义绑定到特定 user_id 的进度回调
        def progress_callback(msg):
            send_message(user_id, msg)
            
        # 使用创建者的个人空间，不再使用共享文件夹
        # 如果用户提供了文件夹token，使用用户的；否则使用系统默认但确保是创建者的空间
        target_token = folder_token if folder_token else config.FEISHU_FOLDER_TOKEN
        
        if target_token == config.FEISHU_FOLDER_TOKEN:
             progress_callback(f"📂 结果将保存到您的个人空间")
        else:
             progress_callback(f"📂 使用您指定的文件夹")

        success, app_token, name = run_pipeline_task(user_id, target_token, app_name, source_url, progress_callback)
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

def handle_message(data: P2ImMessageReceiveV1):
    """处理传入的文本消息。"""
    try:
        content = json.loads(data.event.message.content)
        text = content.get("text", "").strip()
        user_id = data.event.sender.sender_id.open_id
        
        logger.info(f"Received message from {user_id}: {text}")
        
        # 简单的关键词触发
        if "分析" in text or "start" in text.lower() or "menu" in text.lower():
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
            
            # 我们需要在后台运行此任务。
            # 但是 handle_card_action 被 Dispatcher 同步调用。
            # 我们可以在这里使用 threading，就像之前一样，因为 EventDispatcherHandler 只是一个函数调用。
            # 或者：因为我们在 FastAPI 中，如果我们以不同方式调用它，我们可以使用 app 的后台任务。
            # 但 EventDispatcherHandler 隐藏了请求上下文。
            # 所以 Threading 仍然是处理程序中“即发即弃”的最简单方法。
            # 但是，对于 FC，如果进程冻结，Threading 是有风险的。
            # 但我们使用的是具有“始终开启”或异步调用的“自定义容器”。
            # 如果使用 FC 异步调用，我们应该触发另一个函数。
            # 对于 MVP，如果超时时间足够长，Threading 是可以的。
            
            try:
                t = threading.Thread(target=execute_task, args=(user_id, folder_token, app_name, source_url))
                t.start()
            except Exception as e:
                TASK_LOCK.release()
                logger.error(f"Failed to start thread: {e}")
                send_message(user_id, "💥 启动任务失败。")
            
    except Exception as e:
        logger.error(f"Error handling card action: {e}")

# --- 事件处理程序 ---
# 确保 Token 不为 None
verification_token = config.FEISHU_VERIFICATION_TOKEN or ""
encrypt_key = config.FEISHU_ENCRYPT_KEY or ""

event_handler = EventDispatcherHandler.builder(encrypt_key, verification_token) \
    .register_p2_im_message_receive_v1(handle_message) \
    .register_p2_card_action_trigger(handle_card_action) \
    .build()

@app.post("/webhook/event")
async def webhook_event(request: Request):
    # 1. 解析请求
    try:
        req_body = await request.body()
        req_dict = json.loads(req_body)
    except:
        return {"msg": "invalid json"}

    # 2. 挑战检查 (Challenge Check)
    if "challenge" in req_dict:
        return {"challenge": req_dict["challenge"]}
    
    # 3. 分发
    # 手动创建一个 Lark Request 对象
    # 注意: event_handler.do(req) 期望一个 Lark Request 对象
    # 我们可以简化: Lark 的 Flask/Django 适配器会做这件事。
    # 对于 FastAPI，我们可以构建它。
    
    headers = dict(request.headers)
    lark_req = lark_oapi.parse_req(
        arg=lark_oapi.Request(
            uri=str(request.url), 
            headers=headers, 
            body=req_body
        )
    )
    
    # 4. 处理
    lark_resp = event_handler.do(lark_req)
    
    # 5. 返回响应
    return {
        "code": lark_resp.code,
        "msg": lark_resp.msg,
        "data": lark_resp.data
    }

if __name__ == "__main__":
    import uvicorn
    # FC 自定义容器通常监听 9000 端口，或者我们配置它。
    uvicorn.run(app, host="0.0.0.0", port=9000)
