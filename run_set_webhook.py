# run_set_webhook.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from bot_app.set_webhook import main

if __name__ == '__main__':
    """
    这是设置 Telegram Webhook 的官方入口点。
    
    执行方式:
    在项目根目录下运行: python run_set_webhook.py
    """
    print("--- 正在启动 Webhook 设置程序 ---")
    asyncio.run(main())
    print("--- 程序执行完毕 ---")
