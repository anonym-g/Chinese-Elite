# scripts/scheduled_tasks.py

import os
import sys
import json
import asyncio
import logging
from datetime import date
from math import sqrt
import pytz
import telegram
import argparse
import urllib.parse
from dotenv import load_dotenv

from config import MASTER_GRAPH_PATH, CACHE_DIR

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- 加载环境变量 ---
# 从项目根目录的 .env 文件加载环境变量
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# --- 常量定义 ---
PAGEVIEWS_CACHE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')
TOP_N = 7 # 输出前7条
WEB_BASE_URL = "https://anonym-g.github.io/Chinese-Elite"

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
        qcode_to_name = {
            node['id']: node.get('name', {}).get('zh-cn', [node['id']])[0]
            for node in graph_data.get('nodes', [])
        }
        
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

def escape_markdown_v2(text: str) -> str:
    """转义 Telegram MarkdownV2 消息格式的特殊字符。"""
    if not text: return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{char}' if char in escape_chars else char for char in text)

def format_message(top_items: list, qcode_to_name: dict, pageviews_cache: dict, today: date) -> str:
    """将排序后的条目格式化为 Telegram 消息。"""
    if not top_items:
        return ""
        
    today_str = escape_markdown_v2(today.strftime('%Y年%m月%d日'))
    header = f"*历史上的今天 \\({today_str}\\)*\n\n"
    
    lines = [header]
    for item in top_items:
        years = item['anniversary_years']
        data = item['data']
        date_label = escape_markdown_v2(item['date_label'])
        
        # 构建主信息行
        line = f"*【{years}周年】* "
        desc_text = ""
        search_name = ""

        if item['type'] == 'node':
            name = escape_markdown_v2(qcode_to_name.get(data['id'], data['id']))
            desc_text = data.get('properties', {}).get('description', {}).get('zh-cn', '')
            search_name = qcode_to_name.get(data['id'], "")
            line += f"*{name}* \\({date_label}\\)"
        
        elif item['type'] == 'relationship':
            source_name = escape_markdown_v2(qcode_to_name.get(data['source'], ""))
            target_name = escape_markdown_v2(qcode_to_name.get(data['target'], ""))
            rel_type = escape_markdown_v2(data['type'])
            desc_text = data.get('properties', {}).get('description', {}).get('zh-cn', '')
            line += f"{source_name} *{rel_type}* {target_name} \\({date_label}\\)"

            # 智能选择热度更高的实体作为引流链接的目标
            s_name_raw = qcode_to_name.get(data['source'], "")
            t_name_raw = qcode_to_name.get(data['target'], "")
            s1 = pageviews_cache.get(s_name_raw, {}).get('avg_daily_views', 0)
            s2 = pageviews_cache.get(t_name_raw, {}).get('avg_daily_views', 0)
            search_name = s_name_raw if s1 >= s2 else t_name_raw
        
        # 构建描述行
        if desc_text:
            desc_label = "简介" if item['type'] == 'node' else "说明"
            line += f"\n> *{desc_label}*: {escape_markdown_v2(desc_text)}"
        
        lines.append(line)
        
    footer = (
        f"\n\n`数据由 @ChineseEliteTelegramBot 自动生成`\n"
        f"➡️➡️ [项目主页]({WEB_BASE_URL}/)"
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
        # 在 GitHub Actions 等生产环境中，使用 UTC 当天日期
        today = date.today()
    
    anniversary_items = find_anniversary_items(graph_data, today)
    logger.info(f"发现 {len(anniversary_items)} 个符合条件的周年纪念条目。")

    if not anniversary_items:
        logger.info("今日无周年纪念条目，任务结束。")
        return

    scored_items = calculate_scores(anniversary_items, pageviews_cache, qcode_to_name)
    sorted_items = sorted(scored_items, key=lambda x: x['score'], reverse=True)
    top_items = sorted_items[:TOP_N]
    
    message = format_message(top_items, qcode_to_name, pageviews_cache, today)
    
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
