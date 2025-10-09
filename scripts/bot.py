# scripts/bot.py

import os
import sys
import logging
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from collections import deque

from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

from config import BOT_QA_MODEL
from api_rate_limiter import gemini_flash_lite_preview_limiter

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 全局常量 ---
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompts', 'bot_rag.txt')
# 项目根目录是 a/b/scripts/.. -> a/b/
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
MAX_HISTORY = 24

def _generate_project_structure_text() -> str:
    """
    实时生成项目的文件和目录结构文本，用于注入到System Prompt。
    """
    logger.info("正在实时生成项目结构文本...")
    structure_lines = ["项目结构如下:"]
    
    ignore_dirs = {
        '.git', '__pycache__', 'logs', 
        'venv', '.venv', 'env', '.env',
        'nodes', 'person', 'organization', 'movement',
        'event', 'document', 'location'
    }
    
    ignore_files = {'.gitignore', 'processed_files.log'}

    for root, dirs, files in os.walk(PROJECT_ROOT):
        # 过滤掉需要忽略的目录
        # dirs[:] = ... 是一种原地修改列表的技巧，可以影响 os.walk 的后续遍历
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        
        level = root.replace(PROJECT_ROOT, '').count(os.sep)
        
        # 跳过根目录本身，只显示子目录和文件
        if root == PROJECT_ROOT:
            # 只处理根目录下的文件
            sub_indent = '├── '
            for f in sorted(files):
                if f not in ignore_files:
                    structure_lines.append(f"{sub_indent}{f}")
            continue # 继续到下一个循环，处理子目录

        # 处理子目录
        indent = '│   ' * (level - 1) + '├── '
        structure_lines.append(f"{indent}{os.path.basename(root)}/")
        
        sub_indent = '│   ' * level + '├── '
        for f in sorted(files):
            if f not in ignore_files:
                structure_lines.append(f"{sub_indent}{f}")

    logger.info("项目结构文本生成完毕。")
    return "\n".join(structure_lines)

# --- 工具定义 ---

def read_project_file(file_path: str) -> str:
    """
    安全地读取项目内指定文件的内容。这是提供给LLM的工具函数。
    """
    try:
        # 构建绝对路径
        abs_file_path = os.path.abspath(os.path.join(PROJECT_ROOT, file_path))
        
        # 安全检查：确保请求的文件路径在项目根目录之内
        if not abs_file_path.startswith(PROJECT_ROOT):
            return f"错误：禁止访问项目目录之外的文件: {file_path}"
            
        if not os.path.exists(abs_file_path):
            return f"错误：文件不存在: {file_path}"
            
        if os.path.isdir(abs_file_path):
            return f"错误：这是一个目录，无法读取: {file_path}"

        with open(abs_file_path, 'r', encoding='utf-8') as f:
            # 读取文件前 15000 字符，防止文件过大、消耗过多资源
            content = f.read(15000) 
            if len(content) == 15000:
                return content + "\n... (文件内容过长，已截断)"
            return content
            
    except Exception as e:
        return f"读取文件时发生错误: {e}"

# 向 Google GenAI 声明这个工具
file_reader_tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name='read_project_file',
            description='读取并返回项目内指定文件的文本内容。用于回答关于代码实现、配置细节、Prompt内容等需要查看文件源码的问题。',
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    'file_path': types.Schema(type=types.Type.STRING, description='相对于项目根目录的文件路径, 例如 "scripts/config.py" 或 "README.md"')
                },
                required=['file_path']
            )
        )
    ]
)

class TelegramBotHandler:
    """
    封装了处理Telegram消息和与LLM交互的所有逻辑, 增加了动态注入项目结构和工具使用的能力。
    """
    def __init__(self, bot_username: str):
        self.bot_username = bot_username
        self.client: genai.Client | None = None
        self.chat_histories = {}
        self._initialize_llm_client()
        self.system_prompt = self._build_system_prompt()

    def _initialize_llm_client(self):
        try:
            self.client = genai.Client()
            logger.info("Gemini Client 初始化成功。")
        except Exception as e:
            logger.critical(f"严重错误: 初始化 Gemini Client 失败。请检查 GEMINI_API_KEY。", exc_info=True)
            sys.exit(1)
            
    def _build_system_prompt(self) -> str:
        """
        读取静态prompt模板，并动态注入实时生成的项目结构。
        """
        try:
            with open(PROMPT_PATH, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: Prompt 文件 '{PROMPT_PATH}' 未找到。")
            sys.exit(1)
            
        project_structure = _generate_project_structure_text()
        return prompt_template.format(project_structure=project_structure)

    @gemini_flash_lite_preview_limiter.limit
    def _call_llm(self, chat_history: list) -> str | None:
        """
        调用LLM，并处理可能的工具调用（Function Calling）流程。
        """
        if not self.client:
            logger.error("LLM客户端未初始化, 无法调用。")
            return None
        
        try:
            contents = [
                {"role": "user" if role == "user" else "model", "parts": [{"text": text}]}
                for role, text in chat_history
            ]

            logger.info(f"正在调用模型 {BOT_QA_MODEL} (支持工具调用)...")
            
            config = types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=[file_reader_tool]
            )
            
            # 发送第一次请求
            response = self.client.models.generate_content(
                model=f'models/{BOT_QA_MODEL}',
                contents=contents,
                config=config
            )

            if not response.candidates:
                return "模型返回了空的候选结果。"

            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                return response.text if hasattr(response, 'text') and response.text else "模型返回了没有内容的结果。"
            
            # --- 遍历所有 parts 来查找 function_call ---
            function_call = None
            for part in candidate.content.parts:
                if part.function_call:
                    function_call = part.function_call
                    break # 找到一个就停止
            
            if function_call:
                if function_call.name == 'read_project_file':
                    args = function_call.args
                    if not args:
                        return "工具调用缺少参数。"
                    
                    file_path = args.get('file_path')
                    if not isinstance(file_path, str):
                        return f"工具调用收到了无效的文件路径参数: {file_path}"
                    
                    logger.info(f"模型已成功发出指令，正在执行函数: read_project_file(file_path=\'{file_path}\')")
                    file_content = read_project_file(file_path)
                    
                    # 将函数执行结果返回给模型，让它继续生成回答
                    response = self.client.models.generate_content(
                        model=f'models/{BOT_QA_MODEL}',
                        contents=[
                            *contents,
                            candidate.content,
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name='read_project_file',
                                    response={'content': file_content}
                                )
                            )
                        ],
                        config=config
                    )

            # 使用 .text 属性安全地获取最终文本
            final_text = response.text
            return final_text if final_text else "我用工具函数执行了操作，但未能生成进一步的文本。您还有其他问题吗？"

        except Exception as e:
            logger.error(f"LLM API 调用失败", exc_info=True)
            return "在处理您的请求时遇到了一个内部错误。"

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if not message or not message.text: return

        chat_id = message.chat_id
        final_user_message = message.text

        # 在 python-telegram-bot 中, 私聊的 chat_id 是正数 (等于用户ID)
        is_private_chat = chat_id > 0

        # --- 白名单 ---
        # https://t.me/ChineseEliteTeleGroup
        allowed_group_id = os.getenv("ALLOWED_GROUP_ID")

        logger.info(f"--- [ID DISCOVERY] Received message from chat_id: {chat_id} ---")

        # 检查 .env 文件中是否配置了群组ID
        if not allowed_group_id:
            logger.warning("警告：未在 .env 文件中设置 ALLOWED_GROUP_ID。为安全起见，机器人将拒绝所有群聊的呼叫。")
            if chat_id < 0: # 如果是任何群聊
                return
        
        # --- 禁用私聊 ---
        if is_private_chat:
            await message.reply_text("抱歉，我是一个专为群组设计的助手，请在指定群组中与我互动（因为LLM API有用量限制）。\n\n群聊链接：https://t.me/ChineseEliteTeleGroup")
            return

        # 如果配置了ID，则检查当前聊天ID是否匹配
        if allowed_group_id and str(chat_id) != allowed_group_id:
            logger.info(f"收到来自非授权群组或私聊 {chat_id} 的消息，已忽略。")
            # 如果您希望机器人在被拉入野群时自动退群，可以取消下面这行的注释
            # await context.bot.leave_chat(chat_id)
            return

        is_reply_to_bot = (
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.username == self.bot_username
        )
        is_mentioning_bot = f"@{self.bot_username}" in final_user_message

        # 如果在群聊中，但没有明确呼叫机器人，则忽略
        if not (is_reply_to_bot or is_mentioning_bot):
            return

        # 检查是否是一条回复消息
        if message.reply_to_message and message.reply_to_message.text:
            original_message = message.reply_to_message
            
            # 获取原消息作者的名字
            if original_message.from_user:
                original_author_name = original_message.from_user.first_name
            else:
                original_author_name = "A User"
            original_text = original_message.text  # 获取原消息的文本

            quoted_context = (
                f"用户正在回复 {original_author_name} 的消息:\n"
                f">>>\n"
                f"{original_text}\n"
                f"<<<\n\n"
            )
            final_user_message = quoted_context + final_user_message

        logger.info(f"收到来自 Chat ID {chat_id} 的消息: '{final_user_message}'")
        
        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = deque(maxlen=MAX_HISTORY * 2)

        history = self.chat_histories[chat_id]
        history.append(("user", final_user_message))
        
        bot_response = self._call_llm(list(history))

        if bot_response:
            history.append(("model", bot_response))
            await message.reply_text(bot_response)
        else:
            history.pop()
            await message.reply_text("服务器繁忙，请稍后再试。")

async def main() -> None:
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not os.getenv("GEMINI_API_KEY"):
        logger.critical("严重错误: 必须在 .env 文件中设置 TELEGRAM_BOT_TOKEN 和 GEMINI_API_KEY。")
        sys.exit(1)

    application = Application.builder().token(token).build()
    
    bot_info = await application.bot.get_me()
    if not bot_info.username:
        logger.critical("严重错误：无法获取机器人的用户名。")
        sys.exit(1)
        
    logger.info(f"机器人已启动，用户名为: @{bot_info.username}")

    bot_handler = TelegramBotHandler(bot_info.username)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_message))

    try:
        logger.info("机器人正在初始化...")
        await application.initialize()
        
        await application.start()
        if application.updater:
            logger.info("机器人开始轮询...")
            await application.updater.start_polling()
        else:
            logger.error("错误：Application.updater 未被初始化。")
            return

        while True:
            await asyncio.sleep(3600)

    except (KeyboardInterrupt, SystemExit):
        logger.info("收到中断信号，正在关闭机器人...")
    finally:
        if application.running:
            logger.info("正在关闭...")
            await application.shutdown()
        logger.info("机器人已成功关闭。")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"启动机器人时发生致命错误: {e}")
