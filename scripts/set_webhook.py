# scripts/set_webhook.py

import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application

load_dotenv()

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
    asyncio.run(main())
