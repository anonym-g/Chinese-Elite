# scripts/scheduled_tasks.py

import os
import sys
import json
import asyncio
import logging
from datetime import date, datetime
from math import sqrt
import pytz
import telegram
import argparse
import urllib.parse
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

# 使用绝对路径导入
from scripts.config import MASTER_GRAPH_PATH, CACHE_DIR

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- 加载环境变量 ---
# 明确指定.env文件相对于项目根目录的位置。
load_dotenv(os.path.join(ROOT_DIR, '.env'))

# --- 常量定义 ---
PAGEVIEWS_CACHE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')
TOP_N = 7 # 输出前7条
WEBSITE_URL = "https://anonym-g.github.io/Chinese-Elite"
REPO_URL = "https://github.com/anonym-g/Chinese-Elite"

def setup_arg_parser():
    """设置命令行参数解析器。"""
    parser = argparse.ArgumentParser(description='执行 Chinese-Elite 的定时任务，如发送周年纪念日消息。')
    parser.add_argument(
        '--date',
        type=str,
        help='指定一个模拟的日期来运行脚本，格式为 YYYY-MM-DD。用于测试。'
    )
    return parser

def load_data():
    """加载主图谱和页面热度缓存。"""
    try:
        with open(MASTER_GRAPH_PATH, 'r', encoding='utf-8') as f:
            graph_data = json.load(f)
        with open(PAGEVIEWS_CACHE_PATH, 'r', encoding='utf-8') as f:
            pageviews_cache = json.load(f)
        
        # 构建一个从 Q-Code 到主名称的映射，以提高后续查找效率
        qcode_to_name = {}
        for node in graph_data.get('nodes', []):
            node_id = node.get('id')
            if not node_id: continue
            name_obj = node.get('name', {})
            # 优先级: zh-cn -> en -> 第一个找到的 -> ID
            primary_name = (
                name_obj.get('zh-cn', [None])[0] or
                name_obj.get('en', [None])[0] or
                next((names[0] for names in name_obj.values() if names), node_id)
            )
            qcode_to_name[node_id] = primary_name
        
        return graph_data, pageviews_cache, qcode_to_name
    except FileNotFoundError as e:
        logger.critical(f"严重错误: 必需的数据文件未找到 - {e}")
        return None, None, None
    except json.JSONDecodeError as e:
        logger.critical(f"严重错误: JSON文件解析失败 - {e}")
        return None, None, None

def _is_anniversary(date_str: str, today: date) -> int | None:
    """检查日期字符串是否是今天的5周年倍数纪念日。"""
    try:
        # 为保证准确性，只处理精确到日的 "YYYY-MM-DD" 格式
        if not (date_str and len(date_str) == 10 and date_str.count('-') == 2):
            return None
            
        event_date = date.fromisoformat(date_str)
        if event_date.month == today.month and event_date.day == today.day:
            year_diff = today.year - event_date.year
            # 周年数必须是大于0的5的倍数
            if year_diff > 0 and year_diff % 5 == 0:
                return year_diff
    except (ValueError, TypeError):
        return None
    return None

def find_anniversary_items(graph_data: dict, today: date) -> list:
    """从图谱数据中筛选出所有符合周年纪念条件的节点和关系，并附带事件上下文。"""
    anniversary_items = []
    
    # 遍历所有节点
    for node in graph_data.get('nodes', []):
        props = node.get('properties', {})
        if not isinstance(props, dict): continue

        # 根据节点类型确定要检查的日期字段
        keys_to_check = []
        if node.get('type') == 'Person':
            keys_to_check.append('lifetime') # 人物节点只检查 lifetime
        else:
            keys_to_check.append('period') # 其他类型节点检查 period

        for key in keys_to_check:
            date_ranges = props.get(key) or []
            if isinstance(date_ranges, str): date_ranges = [date_ranges]

            for date_range in date_ranges:
                parts = [p.strip() for p in date_range.split(' - ')]
                for i, part in enumerate(parts):
                    if (years := _is_anniversary(part, today)):
                        # 根据字段确定事件标签
                        label = part # 默认标签为日期本身
                        if key == 'lifetime':
                            label = "诞辰" if i == 0 else "逝世"
                        
                        anniversary_items.append({
                            'type': 'node', 'data': node, 'anniversary_years': years,
                            'date_label': label, 'date_str': part
                        })

    # 遍历所有关系
    for rel in graph_data.get('relationships', []):
        # 过滤掉 BORN_IN 关系，因为它与人物的诞辰信息重复
        if rel.get('type') == 'BORN_IN':
            continue

        props = rel.get('properties')
        if not isinstance(props, dict): continue

        # 检查 start_date 和 end_date 字段
        for key in ['start_date', 'end_date']:
            dates = props.get(key) or []
            if isinstance(dates, str): dates = [dates]
            for d_str in dates:
                 if (years := _is_anniversary(d_str, today)):
                    anniversary_items.append({
                        'type': 'relationship', 'data': rel, 'anniversary_years': years,
                        'date_label': d_str, 'date_str': d_str # 标签即日期
                    })
                    
    # 基于实体ID和具体日期进行去重
    unique_items = []
    seen = set()
    for item in anniversary_items:
        item_id = item['data']['id'] if item['type'] == 'node' else f"{item['data']['source']}-{item['data']['target']}-{item['data']['type']}"
        seen_key = f"{item_id}-{item['date_str']}"
        if seen_key not in seen:
            unique_items.append(item)
            seen.add(seen_key)
            
    return unique_items

def calculate_scores(items: list, pageviews_cache: dict, qcode_to_name: dict) -> list:
    """为每个纪念日条目计算热度得分。"""
    scored_items = []
    for item in items:
        score = 0
        if item['type'] == 'node':
            # 节点的得分即为其自身的日均浏览量
            node_name = qcode_to_name.get(item['data']['id'], "")
            score = pageviews_cache.get(node_name, {}).get('avg_daily_views', 0)
        elif item['type'] == 'relationship':
            # 关系的得分由其连接的两个节点的日均浏览量共同决定
            source_name = qcode_to_name.get(item['data']['source'], "")
            target_name = qcode_to_name.get(item['data']['target'], "")
            
            s1 = pageviews_cache.get(source_name, {}).get('avg_daily_views', 0)
            s2 = pageviews_cache.get(target_name, {}).get('avg_daily_views', 0)
            
            # 使用“发现分数”算法，倾向于突出至少一方很热门的关系
            # Score = max(s1, s2) + sqrt(min(s1, s2))
            if s1 > 0 or s2 > 0:
                score = max(s1, s2) + sqrt(min(s1, s2))
            else:
                score = 0 # 如果两个节点都没有热度数据，则关系得分为0

        item['score'] = score
        scored_items.append(item)
    return scored_items

def escape_markdown_v2(text: str | None) -> str:
    """安全地转义 Telegram MarkdownV2 消息格式的特殊字符。"""
    if not text: 
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def _format_bilingual_string(zh_text: str | None, en_text: str | None, separator: str = " / ") -> str:
    """将中英文文本格式化为 '中文 / 英文' 或单一语言。"""
    zh_esc = escape_markdown_v2(zh_text)
    en_esc = escape_markdown_v2(en_text)
    if zh_esc and en_esc and zh_esc != en_esc:
        return f"{zh_esc}{separator}{en_esc}"
    return zh_esc or en_esc or ""

def _get_node_details(node: dict) -> dict:
    """从节点数据中提取并格式化名称、别名和描述。"""
    name_obj = node.get('name', {})
    
    # 安全地获取中文和英文主名称
    zh_name_list = name_obj.get('zh-cn', [])
    en_name_list = name_obj.get('en', [])
    zh_name = zh_name_list[0] if zh_name_list else None
    en_name = en_name_list[0] if en_name_list else None
    
    # 格式化主名称
    primary_name = _format_bilingual_string(zh_name, en_name)

    # 提取别名 (切片操作对空列表是安全的)
    zh_aliases = zh_name_list[1:]
    en_aliases = en_name_list[1:]
    aliases = [escape_markdown_v2(alias) for alias in (zh_aliases + en_aliases) if alias][:3]
    aliases_str = ", ".join(aliases) if aliases else ""

    # 提取描述
    props = node.get('properties', {})
    desc_obj = props.get('description') # 先直接获取值

    # 确保 desc_obj 是一个字典
    zh_desc = None
    en_desc = None
    if isinstance(desc_obj, dict):
        zh_desc = desc_obj.get('zh-cn')
        en_desc = desc_obj.get('en')

    return {
        "primary_name": primary_name,
        "aliases_str": aliases_str,
        "zh_desc": zh_desc,
        "en_desc": en_desc,
    }

def _format_rel_participant(node: dict) -> str:
    """为关系中的参与节点格式化名称，格式为 '中文 (英文)'。"""
    name_obj = node.get('name', {})
    
    # 安全地获取中文和英文主名称
    zh_name_list = name_obj.get('zh-cn', [])
    en_name_list = name_obj.get('en', [])
    zh_name = zh_name_list[0] if zh_name_list else None
    en_name = en_name_list[0] if en_name_list else None
    
    if zh_name:
        zh_name_esc = escape_markdown_v2(zh_name)
        if en_name and en_name != zh_name:
            return f"{zh_name_esc} \\({escape_markdown_v2(en_name)}\\)"
        return zh_name_esc
    
    # 如果没有中文名，则直接返回英文名或ID
    return escape_markdown_v2(en_name) or escape_markdown_v2(node.get('id'))

def format_message(top_items: list, qcode_to_name: dict, pageviews_cache: dict, graph_data: dict, today: date) -> str:
    """将排序后的条目格式化为 Telegram 消息。"""
    if not top_items:
        return ""
    
    # 构建一个快速查找节点完整数据的映射
    node_map = {node['id']: node for node in graph_data.get('nodes', [])}
        
    today_str = escape_markdown_v2(today.strftime('%Y年%m月%d日'))
    header = f"*历史上的今天 \\({today_str}\\)*\n"
    
    lines = [header]
    for item in top_items:
        years = item['anniversary_years']
        data = item['data']
        date_label = escape_markdown_v2(item['date_label'])
        
        line_parts = [f"\n*【{years}周年】* "]
        search_name = ""

        if item['type'] == 'node':
            details = _get_node_details(data)
            search_name = qcode_to_name.get(data['id'], "")
            
            line_parts.append(f"*{details['primary_name']}* \\({date_label}\\)")
            if details['aliases_str']:
                line_parts.append(f"\n> *别名*: {details['aliases_str']}")
            # 在 zh_desc 存在且不为空时打印
            if details['zh_desc']:
                line_parts.append(f"\n> *简介*: {escape_markdown_v2(details['zh_desc'])}")
            if details['en_desc']:
                line_parts.append(f"\n> *Desc*: {escape_markdown_v2(details['en_desc'])}")

        elif item['type'] == 'relationship':
            source_node = node_map.get(data['source'])
            target_node = node_map.get(data['target'])
            
            if not source_node or not target_node: continue

            source_formatted = _format_rel_participant(source_node)
            target_formatted = _format_rel_participant(target_node)
            rel_type = escape_markdown_v2(data['type'])
            
            line_parts.append(f"{source_formatted} *{rel_type}* {target_formatted} \\({date_label}\\)")
            
            props = data.get('properties', {})
            desc_obj = props.get('description') # 先直接获取值
            
            # 确保 desc_obj 是一个字典
            if isinstance(desc_obj, dict):
                zh_desc = desc_obj.get('zh-cn')
                en_desc = desc_obj.get('en')
                # 只有在描述确实存在且不为空时才打印
                if zh_desc:
                    line_parts.append(f"\n> *说明*: {escape_markdown_v2(zh_desc)}")
                if en_desc:
                    line_parts.append(f"\n> *Desc*: {escape_markdown_v2(en_desc)}")

        lines.append("".join(line_parts))
        
    footer = (
        f"\n\n`数据由 @ChineseEliteTelegramBot 自动生成`\n"
        f"➡️ [项目主页]({WEBSITE_URL})\n"
        f"▪️ [代码仓库]({REPO_URL})"
    )
    lines.append(footer)
    return "\n".join(lines)

async def main():
    """主执行函数。"""
    parser = setup_arg_parser()
    args = parser.parse_args()

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    if not bot_token or not channel_id:
        logger.critical("错误: 未设置 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHANNEL_ID 环境变量。")
        return

    logger.info("开始执行“历史上的今天”任务...")
    
    graph_data, pageviews_cache, qcode_to_name = load_data()
    # 明确检查每个可能为 None 的变量，确保类型安全
    if graph_data is None or pageviews_cache is None or qcode_to_name is None:
        logger.critical("数据加载失败，任务终止。")
        return

    # 如果命令行提供了日期，则使用该日期；否则，使用当天的真实日期
    if args.date:
        try:
            today = date.fromisoformat(args.date)
            logger.info(f"*** 使用命令行指定的模拟日期进行测试: {today} ***")
        except ValueError:
            logger.critical(f"错误: 日期格式无效。请使用 YYYY-MM-DD 格式。")
            return
    else:
        BEIJING_TZ = pytz.timezone('Asia/Shanghai')
        # 1. 获取当前UTC时间
        utc_now = datetime.now(pytz.utc)
        # 2. 将其转换为北京时间
        beijing_now = utc_now.astimezone(BEIJING_TZ)
        # 3. 从北京时间的datetime对象中提取日期部分
        today = beijing_now.date()
        logger.info(f"当前UTC时间: {utc_now.strftime('%Y-%m-%d %H:%M:%S')}, 对应的北京日期为: {today}")
    
    anniversary_items = find_anniversary_items(graph_data, today)
    logger.info(f"发现 {len(anniversary_items)} 个符合条件的周年纪念条目。")

    if not anniversary_items:
        logger.info("今日无周年纪念条目，任务结束。")
        return

    scored_items = calculate_scores(anniversary_items, pageviews_cache, qcode_to_name)
    sorted_items = sorted(scored_items, key=lambda x: x['score'], reverse=True)
    top_items = sorted_items[:TOP_N]
    
    message = format_message(top_items, qcode_to_name, pageviews_cache, graph_data, today)
    
    if message:
        try:
            bot = telegram.Bot(token=bot_token)
            logger.info("正在发送消息到 Telegram 频道...")
            await bot.send_message(
                chat_id=channel_id,
                text=message,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True # 建议禁用链接预览，保持排版整洁
            )
            logger.info("消息发送成功！")
        except Exception as e:
            logger.error(f"发送 Telegram 消息失败: {e}")

if __name__ == "__main__":
    asyncio.run(main())
