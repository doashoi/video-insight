import json
import logging
import lark_oapi
from fastapi import FastAPI, Request, Response
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from config import config
from common.bot_actions import handle_message, handle_card_action

# 设置日志
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("FeishuServerRunner")

app = FastAPI()

# 确保 Token 不为 None
verification_token = config.FEISHU_VERIFICATION_TOKEN or ""
encrypt_key = config.FEISHU_ENCRYPT_KEY or ""

event_handler = (
    EventDispatcherHandler.builder(encrypt_key, verification_token)
    .register_p2_im_message_receive_v1(handle_message)
    .register_p2_card_action_trigger(handle_card_action)
    .build()
)


@app.get("/")
async def root():
    return {"status": "ok", "message": "Video Insight Bot is running"}


@app.post("/webhook/event")
async def webhook_event(request: Request):
    # 1. 解析请求
    try:
        req_body = await request.body()
        # 尝试记录日志，但不强制解析 JSON（可能是加密的）
        logger.info(f"Received request body length: {len(req_body)}")
    except Exception as e:
        logger.error(f"Error reading body: {e}")
        return {"msg": "invalid request"}

    # 2. 构造飞书请求对象
    headers = dict(request.headers)
    lark_req = lark_oapi.parse_req(
        arg=lark_oapi.Request(uri=str(request.url), headers=headers, body=req_body)
    )

    # 3. 处理 (包含解密、验证、事件分发)
    # EventDispatcherHandler 会自动处理 url_verification (Challenge)
    lark_resp = event_handler.do(lark_req)

    # 4. 返回响应
    # 必须直接返回 lark_resp 的 body，不能包裹在其他 JSON 中
    # 飞书需要接收原始的 {"challenge": "..."} 响应
    return Response(
        content=lark_resp.body,
        status_code=lark_resp.code,
        headers=dict(lark_resp.headers) if lark_resp.headers else {},
    )


def main():
    import uvicorn

    # FC 自定义容器通常监听 9000 端口，或者我们配置它。
    uvicorn.run(app, host="0.0.0.0", port=9000)


if __name__ == "__main__":
    main()
