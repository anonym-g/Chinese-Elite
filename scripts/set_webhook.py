# scripts/set_webhook.py

import os
import asyncio
import logging
from dotenv import load_dotenv

# 确保在 setup_bot 之前加载环境变量
load_dotenv()

from bot import setup_bot

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """
    一个独立的一次性脚本，用于在部署时设置 Telegram Webhook。
    """
    # setup_bot() 会加载 token 等信息并返回配置好的 application 实例
    application = await setup_bot()
    bot_token = application.bot.token
    
    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        logger.critical("严重错误: 必须在环境变量中设置 WEBHOOK_URL。")
        return

    # 构建完整的 webhook URL，路径中包含 token 以增加安全性
    full_webhook_url = f"{webhook_url}/{bot_token}"
    
    logger.info(f"正在为机器人设置 Webhook，URL: {full_webhook_url}")
    try:
        await application.bot.set_webhook(
            url=full_webhook_url,
            allowed_updates=Update.ALL_TYPES
        )
        logger.info("Webhook 设置成功。")
    except Exception as e:
        logger.critical(f"设置 Webhook 失败: {e}")

if __name__ == '__main__':
    # 从 bot.py 导入 Update 类，仅为 set_webhook 调用
    from telegram import Update
    asyncio.run(main())
