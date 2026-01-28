import logging
import lark_oapi
from lark_oapi.ws import Client
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from config import config
from common.bot_actions import handle_message, handle_card_action

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("FeishuBotRunner")

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
