import json
import logging
import lark_oapi
from fastapi import FastAPI, Request, Response
import sys
from pathlib import Path

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from video_insight.config import config
from video_insight.bot import handle_message, handle_card_action
from video_insight.bot import fc_init
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FeishuBot-Webhook")

app = FastAPI()

# --- 事件处理程序 ---
verification_token = config.FEISHU_VERIFICATION_TOKEN or ""
encrypt_key = config.FEISHU_ENCRYPT_KEY or ""

event_handler = EventDispatcherHandler.builder(encrypt_key, verification_token) \
    .register_p2_im_message_receive_v1(handle_message) \
    .register_p2_card_action_trigger(handle_card_action) \
    .build()

@app.post("/webhook/event")
async def webhook_event(request: Request):
    # 1. 获取请求体
    req_body = await request.body()
    
    # 2. 构造 Lark 请求对象
    headers = dict(request.headers)
    lark_req = lark_oapi.parse_req(
        arg=lark_oapi.Request(
            uri=str(request.url), 
            headers=headers, 
            body=req_body
        )
    )
    
    # 3. 分发处理 (SDK 会自动处理 Challenge 和解密)
    lark_resp = event_handler.do(lark_req)
    
    # 4. 直接返回 SDK 的响应
    # 注意：lark_resp.body 是 bytes 或 str，Response 会直接返回它
    return Response(
        content=lark_resp.body, 
        status_code=lark_resp.code,
        media_type="application/json"
    )

# --- 阿里云函数计算 (FC) 初始化适配器 ---
@app.post("/initialize")
async def initialize(request: Request):
    """
    FC Custom Container 初始化接口。
    """
    try:
        logger.info("Received initialization request from FC")
        
        # 构造模拟的 FC Context 对象
        class Credentials:
            def __init__(self, access_key_id, access_key_secret, security_token):
                self.access_key_id = access_key_id
                self.access_key_secret = access_key_secret
                self.security_token = security_token

        class Context:
            def __init__(self, headers):
                self.credentials = Credentials(
                    headers.get("x-fc-access-key-id", ""),
                    headers.get("x-fc-access-key-secret", ""),
                    headers.get("x-fc-security-token", "")
                )
                self.request_id = headers.get("x-fc-request-id", "")
                self.function_name = headers.get("x-fc-function-name", "")

        ctx = Context(request.headers)
        
        # 调用核心初始化逻辑
        fc_init.initialize(ctx)
        
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Initialization failed: {e}", exc_info=True)
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"status": "failed", "error": str(e)})

if __name__ == "__main__":
    import uvicorn
    # 默认监听 9000 端口（FC 自定义容器默认端口）
    uvicorn.run(app, host="0.0.0.0", port=9000)
