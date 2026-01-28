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
# 确保从配置中获取的值不包含多余空格
_verification_token = config.FEISHU_VERIFICATION_TOKEN.strip() if config.FEISHU_VERIFICATION_TOKEN else ""
_encrypt_key = config.FEISHU_ENCRYPT_KEY.strip() if config.FEISHU_ENCRYPT_KEY else ""

# 如果是空字符串，则设为 None，因为 SDK 内部对 None 有特殊逻辑（如跳过校验）
verification_token = _verification_token if _verification_token else None
encrypt_key = _encrypt_key if _encrypt_key else None

# Lark SDK Event Handler (用于处理业务逻辑)
# 注意：encrypt_key 如果为空字符串，必须传 None，否则 SDK 会尝试解密导致 500
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

    # 2. 优先处理 url_verification (飞书事件配置校验)
    # 注意：校验请求可能包含加密内容，也可能不包含
    try:
        body_json = req_json
        # 处理加密格式的校验
        if "encrypt" in body_json and encrypt_key:
            cipher = AESCipher(encrypt_key)
            decrypted_body = cipher.decrypt_string(body_json["encrypt"])
            body_json = json.loads(decrypted_body)
            
        if body_json.get("type") == "url_verification":
            challenge = body_json.get("challenge")
            if challenge:
                logger.info("Handling URL Verification (Challenge)")
                return {"challenge": challenge}
    except Exception as e:
        logger.debug(f"Pre-check for challenge failed (this is usually fine): {e}")

    # 3. 如果不是验证请求，或者手动解密失败，交给 SDK 标准流程
    try:
        # 记录请求头，方便排查签名问题
        logger.info(f"Request Headers: {dict(request.headers)}")
        
        # 转换 header 键名为 SDK 期望的格式 (有些版本的 SDK 对大小写敏感)
        # 针对 lark-oapi 的特殊处理：确保 SDK 能够找到必要的签名头
        # 我们手动添加大写和原始版本的头，确保万无一失
        standard_headers = {}
        for k, v in request.headers.items():
            # 保持原始 (通常是小写)
            standard_headers[k] = v
            # 兼容 SDK 可能需要的各种格式
            if k.lower() == "x-lark-request-timestamp":
                standard_headers["X-Lark-Request-Timestamp"] = v
            elif k.lower() == "x-lark-request-nonce":
                standard_headers["X-Lark-Request-Nonce"] = v
            elif k.lower() == "x-lark-signature":
                standard_headers["X-Lark-Signature"] = v
        
        # 打印关键头信息，方便排查
        logger.info(f"SDK Headers (Keys): {list(standard_headers.keys())}")

        try:
            from lark_oapi.model import RawRequest
            lark_req = RawRequest()
        except ImportError:
            # 兼容旧版本或不同导入路径
            lark_req = lark_oapi.RawRequest()
            
        lark_req.uri = str(request.url)
        lark_req.headers = standard_headers
        lark_req.body = req_body
        
        try:
            lark_resp = event_handler.do(lark_req)
        except Exception as sdk_ex:
            logger.error(f"Exception during SDK execution: {sdk_ex}", exc_info=True)
            return Response(
                content=json.dumps({"status": "failed", "error": "SDK execution error", "detail": str(sdk_ex)}),
                status_code=500,
                media_type="application/json"
            )
        
        # 记录 SDK 的响应状态
        # 注意：lark-oapi 的 RawResponse 使用 status_code 而不是 code
        status_code = getattr(lark_resp, "status_code", getattr(lark_resp, "code", 200))
        logger.info(f"SDK Response Status Code: {status_code}")
        
        # 如果 SDK 返回 500，记录一下 body
        if status_code == 500:
            err_msg = lark_resp.body.decode('utf-8') if lark_resp.body else 'Empty'
            logger.error(f"SDK returned 500. Body: {err_msg}")
            # 返回 200 给飞书，避免飞书不断重试，但内容包含错误信息
            return Response(
                content=json.dumps({"status": "failed", "sdk_error": err_msg}),
                status_code=200, # 改为 200，停止飞书重试
                media_type="application/json"
            )

        return Response(
            content=lark_resp.body or b"", 
            status_code=status_code,
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
