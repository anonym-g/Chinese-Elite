# app.py

import os
import threading
import asyncio
from flask import Flask
from bot import main as run_bot

# 1. 创建一个 Flask web 应用实例
app = Flask(__name__)

# 2. 定义一个简单的网页路由，响应 Render 的健康检查
@app.route('/')
def hello_world():
    return 'Bot is running...'

# 3. 定义一个函数来启动 Telegram 机器人
def run_telegram_bot():
    """在一个新的事件循环中运行异步的机器人主函数"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except Exception as e:
        print(f"机器人线程出错: {e}")
    finally:
        loop.close()

# 4. 在程序启动时，创建一个新线程来专门运行机器人
#    这样可以确保 web 服务和 telegram 轮询互不阻塞
bot_thread = threading.Thread(target=run_telegram_bot)
bot_thread.start()

# 5. 主线程保持 Flask web 服务的运行
if __name__ == '__main__':
    # Gunicorn 会直接调用 app 对象，这部分主要用于本地测试
    # Render 会使用 Gunicorn 来启动，它需要知道主机和端口
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
