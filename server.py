import json
import logging
import lark_oapi
from fastapi import FastAPI, Request, Response
import sys
from pathlib import Path
import hashlib
import base64
from Crypto.Cipher import AES

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

# 启动时打印配置状态（安全脱敏）
logger.info("=== Bot Configuration Status ===")
logger.info(f"FEISHU_APP_ID: {config.FEISHU_APP_ID[:4]}***" if config.FEISHU_APP_ID else "FEISHU_APP_ID: Missing")
logger.info(f"FEISHU_VERIFICATION_TOKEN: {'Set' if config.FEISHU_VERIFICATION_TOKEN else 'Missing'}")
logger.info(f"FEISHU_ENCRYPT_KEY: {'Set' if config.FEISHU_ENCRYPT_KEY else 'Missing'}")
logger.info(f"IS_FC: {config.IS_FC}")
logger.info(f"FFMPEG_PATH: {config.FFMPEG_PATH}")
logger.info("================================")

# --- 事件处理程序 ---
verification_token = config.FEISHU_VERIFICATION_TOKEN or ""
encrypt_key = config.FEISHU_ENCRYPT_KEY or ""

# Lark SDK Event Handler (用于处理业务逻辑)
event_handler = EventDispatcherHandler.builder(encrypt_key, verification_token) \
    .register_p2_im_message_receive_v1(handle_message) \
    .register_p2_card_action_trigger(handle_card_action) \
    .build()

class AESCipher(object):
    def __init__(self, key):
        self.bs = AES.block_size
        self.key = hashlib.sha256(AESCipher.str_to_bytes(key)).digest()

    @staticmethod
    def str_to_bytes(data):
        u_type = type(b"".decode('utf8'))
        if isinstance(data, u_type):
            return data.encode('utf8')
        return data

    @staticmethod
    def _unpad(s):
        return s[:-ord(s[len(s) - 1:])]

    def decrypt(self, enc):
        iv = enc[:AES.block_size]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        return self._unpad(cipher.decrypt(enc[AES.block_size:]))

    def decrypt_string(self, enc):
        enc = base64.b64decode(enc)
        return self.decrypt(enc).decode('utf8')

@app.post("/webhook/event")
async def webhook_event(request: Request):
    # 1. 获取请求体
    req_body = await request.body()
    try:
        req_json = json.loads(req_body)
    except Exception:
        # 如果不是 JSON，直接交给 SDK 处理（虽然不太可能）
        req_json = {}

    # 打印简短的请求日志，方便排查
    logger.info(f"Received webhook request: {req_body[:100]}...")

    # 2. 优先处理 url_verification (手动处理以确保 100% 成功)
    # 飞书配置保存时发送的请求
    is_verification = False
    challenge = ""

    # 情况 A: 未加密的 url_verification
    if req_json.get("type") == "url_verification":
        is_verification = True
        challenge = req_json.get("challenge", "")
    
    # 情况 B: 加密的请求 (需要先解密看是不是 url_verification)
    elif "encrypt" in req_json and config.FEISHU_ENCRYPT_KEY:
        try:
            cipher = AESCipher(config.FEISHU_ENCRYPT_KEY)
            decrypted_string = cipher.decrypt_string(req_json["encrypt"])
            decrypted_json = json.loads(decrypted_string)
            
            if decrypted_json.get("type") == "url_verification":
                is_verification = True
                challenge = decrypted_json.get("challenge", "")
        except Exception as e:
            logger.error(f"Manual decryption failed: {e}")
            # 解密失败不立即返回错误，让 SDK 再试一次

    if is_verification and challenge:
        logger.info(f"Handling url_verification manually. Challenge: {challenge}")
        return Response(
            content=json.dumps({"challenge": challenge}), 
            media_type="application/json"
        )

    # 3. 如果不是验证请求，或者手动解密失败，交给 SDK 标准流程
    try:
        headers = dict(request.headers)
        lark_req = lark_oapi.parse_req(
            arg=lark_oapi.Request(
                uri=str(request.url), 
                headers=headers, 
                body=req_body
            )
        )
        
        lark_resp = event_handler.do(lark_req)
        
        # 如果 SDK 返回 500，记录一下 body
        if lark_resp.code == 500:
            logger.error(f"SDK returned 500. Body: {lark_resp.body}")

        return Response(
            content=lark_resp.body or b"", 
            status_code=lark_resp.code or 200,
            media_type="application/json"
        )
    except Exception as e:
        logger.error(f"Error in webhook_event: {e}", exc_info=True)
        return Response(
            content=json.dumps({"status": "failed", "error": str(e)}),
            status_code=500,
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
