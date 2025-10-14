# bot_app/app.py

import os
import sys
import logging
from flask import Flask, request, Response
from telegram import Update
from asgiref.wsgi import WsgiToAsgi

# 使用相对路径导入
from .bot import create_bot_app_sync, ensure_bot_initialized

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 初始化 ---
# 同步地创建实例，不初始化
application = create_bot_app_sync()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# 创建 Flask web 应用实例，使用一个临时的名字
flask_app = Flask(__name__)

# --- Webhook 路由 ---
# URL 路径使用 Bot Token，确保只有 Telegram 能调用它
@flask_app.route(f'/{BOT_TOKEN}', methods=['POST'])
async def webhook():
    """这个函数处理所有来自 Telegram 的更新。"""
    try:
        # 开始处理请求时，确保机器人已初始化
        await ensure_bot_initialized(application)

        # 将收到的 JSON 数据转换为 Update 对象
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, application.bot)
        
        # 使用 application 实例来处理这个 update
        await application.process_update(update)
        
        # 向 Telegram 返回一个成功的响应
        return Response(status=200)
    except Exception as e:
        logger.error(f"处理 webhook 时出错: {e}")
        return Response(status=500)

# --- 健康检查路由 ---
@flask_app.route('/')
def hello_world():
    """一个简单的页面，用于 UptimeRobot 监控和 Render 的健康检查。"""
    return 'Bot is running...'

# 使用翻译器包装 Flask 实例
# Gunicorn 将会使用这个 ASGI 兼容的 `app` 实例
app = WsgiToAsgi(flask_app)
