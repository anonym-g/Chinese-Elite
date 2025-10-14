# bot_app/set_webhook.py

import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

# 指定 .env 文件在项目根目录。
# os.path.dirname(__file__) -> bot_app
# os.path.dirname(...)      -> Chinese-Elite/ (根目录)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.critical("TELEGRAM_BOT_TOKEN 未设置!")
        return

    # 创建一个临时 application 实例，用于设置 webhook
    application = Application.builder().token(token).build()
    
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.critical("WEBHOOK_URL 未设置!")
        return

    full_webhook_url = f"{webhook_url}/{token}"
    
    logger.info(f"正在设置 Webhook: {full_webhook_url}")
    await application.bot.set_webhook(
        url=full_webhook_url, allowed_updates=Update.ALL_TYPES
    )
    logger.info("Webhook 设置成功。")

if __name__ == '__main__':
    # 这段代码现在仅用于直接测试，实际调用将通过根目录的脚本进行
    asyncio.run(main())
