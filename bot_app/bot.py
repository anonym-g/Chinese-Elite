# bot_app/bot.py

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

# 使用绝对路径导入
from scripts.config import BOT_QA_MODEL, ROOT_DIR, BOT_QA_PROMPT
from scripts.api_rate_limiter import gemini_flash_lite_preview_limiter

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 全局变量 ---
_bot_app_instance = None
_bot_app_lock = asyncio.Lock()

# --- 全局常量和辅助函数 ---
MAX_HISTORY = 24

def _generate_project_structure_text() -> str:
    """
    实时生成项目的文件和目录结构文本，用于注入到System Prompt。
    """
    logger.info("正在实时生成项目结构文本...")
    structure_lines = ["项目结构如下:"]
    ignore_dirs = {
        '.git', '__pycache__', 
        'logs', 
        'venv', '.venv', 'env', '.env', 
        'nodes', 
        'person', 'organization', 'movement', 'event', 'document', 'location'
    }
    ignore_files = {
        '.gitignore', 
        'processed_files.log'
    }
    for root, dirs, files in os.walk(ROOT_DIR):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        level = root.replace(ROOT_DIR, '').count(os.sep)
        if root == ROOT_DIR:
            sub_indent = '├── '
            for f in sorted(files):
                if f not in ignore_files:
                    structure_lines.append(f"{sub_indent}{f}")
            continue
        indent = '│   ' * (level - 1) + '├── '
        structure_lines.append(f"{indent}{os.path.basename(root)}/")
        sub_indent = '│   ' * level + '├── '
        for f in sorted(files):
            if f not in ignore_files:
                structure_lines.append(f"{sub_indent}{f}")
    logger.info("项目结构文本生成完毕。")
    return "\n".join(structure_lines)

def read_project_file(file_path: str) -> str:
    """
    安全地读取项目内指定文件的内容。这是提供给LLM的工具函数。
    """
    try:
        abs_file_path = os.path.abspath(os.path.join(ROOT_DIR, file_path))
        if not abs_file_path.startswith(ROOT_DIR):
            return f"错误：禁止访问项目目录之外的文件: {file_path}"
        if not os.path.exists(abs_file_path):
            return f"错误：文件不存在: {file_path}"
        if os.path.isdir(abs_file_path):
            return f"错误：这是一个目录，无法读取: {file_path}"
        with open(abs_file_path, 'r', encoding='utf-8') as f:
            content = f.read(5000)
            if len(content) == 5000:
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
            with open(BOT_QA_PROMPT, 'r', encoding='utf-8') as f:
                prompt_template = f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: Prompt 文件 '{BOT_QA_PROMPT}' 未找到。")
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
                    break
            
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
        full_user_message = message.text
        is_private_chat = chat_id > 0

        # --- 白名单 ---
        # https://t.me/ChineseEliteTeleGroup
        allowed_group_id = os.getenv("ALLOWED_GROUP_ID")
        allowed_user_id = os.getenv("ALLOWED_USER_ID")

        logger.info(f"--- [ID DISCOVERY] Received message from chat_id: {chat_id} ---")

        # 创建一个包含所有允许 ID 的列表
        allowed_ids = []
        if allowed_group_id:
            allowed_ids.append(allowed_group_id)
        if allowed_user_id:
            allowed_ids.append(allowed_user_id)

        # 如果白名单列表不为空，且当前用户的ID不在其中，则拒绝
        if allowed_ids and str(chat_id) not in allowed_ids:
            await message.reply_text("抱歉，我是专为下面这个群组设计的助手，请在那里与我互动（因为LLM API有用量限制）。\n\n群聊链接：https://t.me/ChineseEliteTeleGroup")

            # 如果您希望机器人在被拉入野群时自动退群，可以取消下面这行的注释
            # await context.bot.leave_chat(chat_id)

            logger.info(f"收到来自非授权群组或私聊 {chat_id} 的消息，已忽略。")
            return

        is_reply_to_bot = (
            message.reply_to_message and
            message.reply_to_message.from_user and
            message.reply_to_message.from_user.username == self.bot_username
        )
        is_mentioning_bot = f"@{self.bot_username}" in full_user_message

        if not (is_reply_to_bot or is_mentioning_bot or is_private_chat):
            return

        # 检查是否有回复引用的消息
        if message.reply_to_message and message.reply_to_message.text:
            replied_msg = message.reply_to_message
            quoted_message_text = replied_msg.text

            # 默认的被引用用户名
            quoted_user_name = "Someone"

            # 来自用户
            if replied_msg.from_user:
                quoted_user_name = replied_msg.from_user.first_name
            # 来自频道
            elif replied_msg.sender_chat:
                quoted_user_name = replied_msg.sender_chat.title

            # 构建一个更丰富的上下文给 LLM
            full_user_message = (
                f"用户回复了 '{quoted_user_name}' 的消息。\n"
                f"--- 被回复的消息 ---\n"
                f"{quoted_message_text}\n"
                f"--- 用户的新消息 ---\n"
                f"{full_user_message}"
            )
        
        logger.info(f"收到来自 Chat ID {chat_id} 的消息: '{full_user_message}'")

        if chat_id not in self.chat_histories:
            self.chat_histories[chat_id] = deque(maxlen=MAX_HISTORY * 2)

        history = self.chat_histories[chat_id]
        history.append(("user", full_user_message))

        bot_response = self._call_llm(list(history))

        if bot_response:
            history.append(("model", bot_response))
            await message.reply_text(bot_response)
        else:
            history.pop()
            await message.reply_text("服务器繁忙，请稍后再试。")

# --- 机器人设置函数 ---
def create_bot_app_sync():
    """
    同步地创建 Application 实例，但不初始化。
    这是为了让 Flask 应用在启动时可以调用。
    """
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not os.getenv("GEMINI_API_KEY"):
        logger.critical("严重错误: 必须在 .env 文件中设置 TELEGRAM_BOT_TOKEN 和 GEMINI_API_KEY。")
        sys.exit(1)
    
    return Application.builder().token(token).build()

async def ensure_bot_initialized(application: Application) -> None:
    """
    一个异步函数，确保 bot application 只被初始化一次。
    """
    global _bot_app_instance
    async with _bot_app_lock:
        if _bot_app_instance:
            return

        logger.info("正在首次初始化 Application...")
        await application.initialize()
        logger.info("Application 初始化成功。")

        if not application.bot.username:
            logger.critical("严重错误：无法获取机器人的用户名。")
            # 在实际运行中，我们不希望因为这个就让整个服务崩溃
            return
        
        logger.info(f"机器人已配置，用户名为: @{application.bot.username}")

        bot_handler = TelegramBotHandler(application.bot.username)
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_message))
        
        _bot_app_instance = application
