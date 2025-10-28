# bot_app/bot.py

import os
import sys
import logging
import asyncio
from dotenv import load_dotenv
from google import genai
from google.genai import types
from collections import deque
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, BotCommand
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes,
    ConversationHandler, CommandHandler, CallbackQueryHandler
)
from opencc import OpenCC

# 使用绝对路径导入
from scripts.config import BOT_QA_MODEL, ROOT_DIR, BOT_QA_PROMPT
from scripts.api_rate_limiter import gemini_flash_lite_preview_limiter
from scripts.github_pr_utils import create_list_update_pr
from scripts.clients.wikipedia_client import WikipediaClient

# --- 日志配置 ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 全局变量与锁 ---
_bot_app_instance = None
_bot_app_lock = asyncio.Lock()
GIT_OPERATION_LOCK = asyncio.Lock()  # 用于确保Git操作的原子性，防止并发冲突

# --- ConversationHandler 状态定义 ---
# 使用整数定义对话的不同阶段，便于管理和跳转
SELECTING_ACTION, AWAITING_ENTRIES, CONFIRM_SUBMISSION = range(3)

# --- 全局常量 ---
MAX_HISTORY = 24  # 问答功能保留的上下文历史数量
MAX_ENTRIES_PER_CATEGORY = 50  # 每个类别一次最多能提交的条目数
ENTITY_CATEGORIES = ['Person', 'Organization', 'Movement', 'Event', 'Location', 'Document']
t2s = OpenCC('t2s')  # 繁转简

# --- 全局辅助函数和工具定义 ---
def escape_markdown_v2(text: str) -> str:
    """
    安全地转义 Telegram MarkdownV2 消息格式的特殊字符。
    """
    if not text: 
        return ""
    # 根据 Telegram API 文档，这些是需要转义的字符
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

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
                    'file_path': types.Schema(
                        type=types.Type.STRING,
                        description='相对于项目根目录的文件路径, 例如 "scripts/config.py" 或 "README.md"'
                    )
                },
                required=['file_path']
            )
        )
    ]
)

# --- 辅助函数：构建键盘 ---
def build_main_menu(user_data) -> InlineKeyboardMarkup:
    """
    构建主菜单，显示各类别按钮和提交按钮。
    """
    submissions = user_data.get('submissions', {})
    keyboard = []
    for i in range(0, len(ENTITY_CATEGORIES), 2):
        row = [
            InlineKeyboardButton(
                f"{category} ({len(submissions.get(category, []))})" if category in submissions and submissions[category] else category,
                callback_data=f"category:{category}"
            ) for category in ENTITY_CATEGORIES[i:i + 2]
        ]
        keyboard.append(row)
    total_submissions = sum(len(s) for s in submissions.values())
    if total_submissions > 0:
        keyboard.append(
            [InlineKeyboardButton(f"✅ 提交全部 ({total_submissions})", callback_data="submit")]
        )
    return InlineKeyboardMarkup(keyboard)

def build_category_menu() -> InlineKeyboardMarkup:
    """
    构建在类别编辑模式下的菜单，只包含返回按钮。
    """
    keyboard = [
        [InlineKeyboardButton("⬅️ 返回主菜单", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 设置机器人指令 ---
async def _set_bot_commands(application: Application):
    """
    在机器人启动时，向Telegram注册支持的指令列表。
    """
    commands = [
        BotCommand("list", "批量添加新的实体条目到待处理列表"),
        BotCommand("cancel", "取消当前的批量添加操作"),
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("成功向Telegram注册了指令菜单。")
    except Exception as e:
        logger.error(f"注册指令菜单失败: {e}")

# --- 对话处理函数 ---
async def start_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /list 指令入口点，启动对话，显示主菜单。
    """
    if not (update.message and context.user_data is not None):
        return ConversationHandler.END

    context.user_data.clear()
    context.user_data['submissions'] = {}

    message = await update.message.reply_text(
        "欢迎使用批量添加功能！\n\n请点击下方按钮选择一个类别，然后发送您想添加的实体名称（每行一个）。",
        reply_markup=build_main_menu(context.user_data)
    )
    context.user_data['main_menu_message_id'] = message.message_id

    return SELECTING_ACTION

async def handle_action_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    处理主菜单的按钮点击：切换到类别编辑或开始提交。
    """
    query = update.callback_query
    if not (query and query.data and context.user_data is not None):
        return ConversationHandler.END

    await query.answer()
    action = query.data

    if action.startswith("category:"):
        category = action.split(":")[1]
        context.user_data['current_category'] = category
        submissions = context.user_data.get('submissions', {})
        submissions_for_category = submissions.get(category, set())
        message_text = f"您正在编辑 **{category}** 类别。\n请发送实体名称，每行一个（一次最多50个）。\n\n"
        if submissions_for_category:
            message_text += "目前已添加:\n" + "\n".join(f"`{item}`" for item in sorted(list(submissions_for_category)))

        try:
            await query.edit_message_text(
                message_text,
                reply_markup=build_category_menu(),
                parse_mode='MarkdownV2'
            )
        except telegram.error.BadRequest as e:
            logger.error(
                f"Telegram API BadRequest on edit_message_text (category selection): {e}\n"
                f"--- Offending message content ---\n{message_text}\n--- End of message content ---"
            )
            # --- 使用 isinstance 进行类型检查 ---
            if isinstance(query.message, Message):
                await query.message.reply_text("抱歉，格式化消息时出错，请重试。")

        return AWAITING_ENTRIES

    elif action == "submit":
        submissions = context.user_data.get('submissions', {})
        if not submissions or sum(len(s) for s in submissions.values()) == 0:
            await query.answer("您还没有添加任何条目。", show_alert=True)
            return SELECTING_ACTION

        summary_lines = ["您准备提交以下条目，请确认：\n"]
        for category, entries in sorted(submissions.items()):
            if entries:
                summary_lines.append(f"**{category}**:")
                summary_lines.extend(f"`{escape_markdown_v2(entry)}`" for entry in sorted(list(entries)))
                summary_lines.append("")
        
        message_text = "\n".join(summary_lines)
        keyboard = [
            [InlineKeyboardButton("✅ 确认提交", callback_data="confirm_submit")],
            [InlineKeyboardButton("⬅️ 返回修改", callback_data="back_to_main")]
        ]
        
        try:
            await query.edit_message_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='MarkdownV2'
            )
        except telegram.error.BadRequest as e:
            logger.error(
                f"Telegram API BadRequest on edit_message_text (submit confirmation): {e}\n"
                f"--- Offending message content ---\n{message_text}\n--- End of message content ---"
            )
            # --- 使用 isinstance 进行类型检查 ---
            if isinstance(query.message, Message):
                await query.message.reply_text("抱歉，格式化提交预览时出错，请检查您输入的条目。")
            
        return CONFIRM_SUBMISSION

    return SELECTING_ACTION

async def handle_entry_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    在类别编辑模式下，接收用户输入的文本并处理。
    """
    if not (update.message and update.message.text and context.user_data is not None):
        return AWAITING_ENTRIES

    current_category = context.user_data.get('current_category')
    if not current_category:
        await update.message.reply_text("发生错误，请返回主菜单重试。")
        return AWAITING_ENTRIES

    if 'submissions' not in context.user_data:
        context.user_data['submissions'] = {}
    if current_category not in context.user_data['submissions']:
        context.user_data['submissions'][current_category] = set()

    current_entries = context.user_data['submissions'][current_category]
    new_entries = [entry.strip() for entry in update.message.text.split('\n') if entry.strip()]
    added_count = 0

    for entry in new_entries:
        if len(current_entries) >= MAX_ENTRIES_PER_CATEGORY:
            await update.message.reply_text(f"'{current_category}' 类别已达到 {MAX_ENTRIES_PER_CATEGORY} 个条目上限。")
            break
        simplified_entry = t2s.convert(entry)
        if simplified_entry not in {t2s.convert(e) for e in current_entries}:
            current_entries.add(entry)
            added_count += 1

    main_menu_message_id = context.user_data.get('main_menu_message_id')
    if main_menu_message_id and update.effective_chat:
        try:
            await context.bot.edit_message_reply_markup(
                chat_id=update.effective_chat.id,
                message_id=main_menu_message_id,
                reply_markup=build_main_menu(context.user_data)
            )
        except Exception as e:
            logger.warning(f"更新主菜单失败: {e}")

    message_text = f"您正在编辑 **{current_category}** 类别。\n新增 {added_count} 条，跳过 {len(new_entries) - added_count} 条重复项。\n\n"
    message_text += "目前已添加:\n" + "\n".join(f"`{escape_markdown_v2(item)}`" for item in sorted(list(current_entries)))

    # --- 添加 try-except 块，以捕获并记录 Markdown 解析错误 ---
    try:
        await update.message.reply_text(
            message_text,
            reply_markup=build_category_menu(),
            parse_mode='MarkdownV2'
        )
    except telegram.error.BadRequest as e:
        # 当错误发生时，记录下导致错误的 message_text
        logger.error(
            f"Telegram API BadRequest on reply_text (likely MarkdownV2 parsing error): {e}\n"
            f"--- Offending message content ---\n"
            f"{message_text}\n"
            f"--- End of message content ---"
        )
        await update.message.reply_text(
            "错误：无法格式化您的输入。这可能是由于您输入的文本中包含了特殊的 Markdown 字符。\n"
            "您的条目已部分添加，但确认消息发送失败。请尝试修改或使用 /cancel 命令重试。"
        )

    return AWAITING_ENTRIES

async def handle_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    处理返回主菜单的请求。
    """
    query = update.callback_query
    if not (query and context.user_data is not None):
        return ConversationHandler.END

    await query.answer()
    context.user_data['current_category'] = None

    message = await query.edit_message_text(
        "您已返回主菜单。请选择一个类别继续添加，或点击“提交”。",
        reply_markup=build_main_menu(context.user_data)
    )
    if isinstance(message, Message):
        context.user_data['main_menu_message_id'] = message.message_id

    return SELECTING_ACTION

async def handle_submit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    处理最终的提交确认，包含条目验证和详细报告。
    """
    query = update.callback_query
    if not (query and query.data == "confirm_submit" and context.user_data is not None):
        return ConversationHandler.END

    await query.answer()
    submissions = context.user_data.get('submissions', {})
    serializable_submissions = {cat: list(entries) for cat, entries in submissions.items() if entries}

    if not serializable_submissions:
        await query.edit_message_text("没有可提交的条目。")
        return ConversationHandler.END

    await query.edit_message_text("正在对您提交的条目进行验证，请稍候...")

    # --- 实例化 WikipediaClient 并执行验证 ---
    wiki_client = WikipediaClient()
    
    async with GIT_OPERATION_LOCK:
        logger.info("获取到Git操作锁，开始验证条目并创建PR...")
        result = await asyncio.to_thread(create_list_update_pr, serializable_submissions, wiki_client)
        await asyncio.to_thread(wiki_client.save_caches)
        logger.info("Git操作完成，已释放锁。")

    chat_id = query.from_user.id
    final_report = ""

    if isinstance(result, dict) and 'report' in result:
        report_data = result['report']
        pr_url = result.get('pr_url')
        
        report_parts = ["*提交处理报告*"]
        
        if report_data.get('accepted'):
            lines = [f"`{escape_markdown_v2(e)}`" for e in sorted(report_data['accepted'])]
            report_parts.append(f"\n*✅ 已接受*:\n" + "\n".join(lines))
        
        if report_data.get('corrected'):
            lines = [f"`{escape_markdown_v2(orig)}` → `{escape_markdown_v2(corr)}`" for orig, corr in sorted(report_data['corrected'])]
            report_parts.append(f"\n*✏️ 已修正*:\n" + "\n".join(lines))

        if report_data.get('rejected'):
            lines = [f"`{escape_markdown_v2(entry)}` \\({escape_markdown_v2(reason)}\\)" for entry, reason in sorted(report_data['rejected'])]
            report_parts.append(f"\n*❌ 已拒绝*:\n" + "\n".join(lines))
        
        if report_data.get('skipped'):
            lines = [f"`{escape_markdown_v2(e)}`" for e in sorted(report_data['skipped'])]
            report_parts.append(f"\n*⏭️ 已跳过 \\(重复项\\)*:\n" + "\n".join(lines))

        final_report = "\n".join(report_parts)
        
        if pr_url:
            final_report += f"\n\n已成功创建 Pull Request: {pr_url}"
        elif len(report_parts) > 1: # 如果有处理结果但没有PR
             final_report += "\n\n由于没有新增有效条目，未创建 Pull Request。"
        else: # 如果没有任何处理结果（例如所有条目都是重复的）
            final_report = "所有提交的条目都无效或已存在，未执行任何操作。"
            
    else:
        final_report = "❌ 操作失败。\n创建 Pull Request 时发生严重错误，请联系管理员。"

    # --- 分割长消息 ---
    max_length = 4096
    if len(final_report) > max_length:
        for i in range(0, len(final_report), max_length):
            await context.bot.send_message(
                chat_id=chat_id, text=final_report[i:i + max_length], parse_mode='MarkdownV2'
            )
    else:
        await context.bot.send_message(chat_id=chat_id, text=final_report, parse_mode='MarkdownV2')

    if context.user_data:
        context.user_data.clear()

    return ConversationHandler.END

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    处理用户取消操作。
    """
    if update.message:
        await update.message.reply_text("批量添加操作已取消。")
    if context.user_data:
        context.user_data.clear()
    return ConversationHandler.END

# --- TelegramBotHandler 类 ---
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

            function_call = next((part.function_call for part in candidate.content.parts if part.function_call), None)
            if function_call and function_call.name == 'read_project_file':
                args = function_call.args
                if not args:
                    return "工具调用缺少参数。"
                file_path = args.get('file_path')
                if not isinstance(file_path, str):
                    return f"工具调用收到了无效的文件路径参数: {file_path}"

                logger.info(f"模型已成功发出指令，正在执行函数: read_project_file(file_path=\'{file_path}\')")
                file_content = read_project_file(file_path)

                response = self.client.models.generate_content(
                    model=f'models/{BOT_QA_MODEL}',
                    contents=[
                        *contents,
                        candidate.content,
                        types.Part(function_response=types.FunctionResponse(name='read_project_file', response={'content': file_content}))
                    ],
                    config=config
                )
            return response.text if response.text else "我用工具函数执行了操作，但未能生成进一步的文本。您还有其他问题吗？"
        except Exception as e:
            logger.error(f"LLM API 调用失败", exc_info=True)
            return "在处理您的请求时遇到了一个内部错误。"

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        # --- 提前检查 message 和 from_user 是否存在 ---
        if not (message and message.from_user):
            return

        # --- 自动广告检测与封禁 ---
        # 1. 检查：为群聊消息，且消息发送者是“已注销账户”
        if message.chat.type in ('group', 'supergroup') and message.from_user.is_deleted:  # type: ignore
            chat_id = message.chat.id
            user_id = message.from_user.id
            logger.warning(f"检测到来自已注销用户 (ID: {user_id}) 在群组 (ID: {chat_id}) 中发布的消息。")
            
            try:
                # 2. 检查机器人是否具有管理员权限
                bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
                if isinstance(bot_member, telegram.ChatMemberAdministrator) and bot_member.can_restrict_members:
                    # 3. 如果有权限，则执行封禁和删帖操作
                    await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                    await message.delete()
                    logger.info(f"成功封禁用户 {user_id} 并删除其在群组 {chat_id} 中发布的消息。")
                else:
                    logger.warning(f"在群组 {chat_id} 中检测到广告，但机器人缺少封禁成员的权限。")
            except Exception as e:
                logger.error(f"在尝试封禁用户 {user_id} 时发生错误: {e}")
            
            # 4. 无论成功与否，都终止后续处理，不再进行问答
            return

        # 如果不是广告，则执行问答逻辑
        if not message.text: return

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

            # 默认的被引用用户名
            quoted_user_name = "Someone"

            # 来自用户
            if replied_msg.from_user:
                quoted_user_name = replied_msg.from_user.first_name
            # 来自频道
            elif replied_msg.sender_chat:
                quoted_user_name = replied_msg.sender_chat.title

            # 给 LLM 构建一个更丰富的上下文
            full_user_message = (
                f"用户回复了 '{quoted_user_name}' 的消息。\n"
                f"--- 被回复的消息 ---\n{replied_msg.text}\n"
                f"--- 用户的新消息 ---\n{full_user_message}"
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

# --- 机器人设置与初始化 ---
def create_bot_app_sync():
    """
    同步地创建 Application 实例，但不进行网络初始化。
    """
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not (token and os.getenv("GEMINI_API_KEY")):
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
            return

        logger.info(f"机器人已配置，用户名为: @{application.bot.username}")

        # --- 注册指令菜单 ---
        await _set_bot_commands(application)

        # --- 设置 ConversationHandler，并添加超时 ---
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("list", start_list_command)],
            states={
                SELECTING_ACTION: [
                    CallbackQueryHandler(handle_action_selection, pattern="^category:"),
                    CallbackQueryHandler(handle_action_selection, pattern="^submit$"),
                ],
                AWAITING_ENTRIES: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_entry_input),
                    CallbackQueryHandler(handle_back_to_main, pattern="^back_to_main$"),
                ],
                CONFIRM_SUBMISSION: [
                    CallbackQueryHandler(handle_submit_confirm, pattern="^confirm_submit$"),
                    CallbackQueryHandler(handle_back_to_main, pattern="^back_to_main$"),
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel_command)],
            per_user=True,
            per_chat=True,
            # --- 设置5分钟超时，自动结束不活跃的对话 ---
            conversation_timeout=300,  # 单位：秒
            block=False
        )

        qa_bot_handler = TelegramBotHandler(application.bot.username)

        # 添加处理器到 application
        application.add_handler(conv_handler)
        # 问答处理器在 ConversationHandler 之后，确保指令优先被处理
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qa_bot_handler.handle_message))

        _bot_app_instance = application
