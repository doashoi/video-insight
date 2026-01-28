import logging
import sys
from pathlib import Path
import lark_oapi
from lark_oapi.ws import Client

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from video_insight.config import config
from video_insight.bot import handle_message, handle_card_action
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FeishuBot-WS")

def main():
    logger.info("Starting Feishu WebSocket Client (Local Mode)...")
    
    # 构建事件分发器
    event_handler = EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(handle_message) \
        .register_p2_card_action_trigger(handle_card_action) \
        .build()

    # 创建 WebSocket 客户端
    ws_client = Client(
        config.FEISHU_APP_ID, 
        config.FEISHU_APP_SECRET,
        event_handler=event_handler,
        log_level=lark_oapi.LogLevel.INFO
    )

    # 启动客户端
    ws_client.start()

if __name__ == "__main__":
    main()
