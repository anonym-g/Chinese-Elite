# scripts/process_list.py

import os
import sys
import json
import re
from datetime import datetime
import urllib.parse
import random
import logging

from config import DATA_DIR, LIST_FILE_PATH, PROB_START_DAY, PROB_END_DAY, PROB_START_VALUE, PROB_END_VALUE, TIMEZONE
from utils import WikipediaClient
from parse_gemini import GeminiParser

logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """移除文件名中不合法或不推荐的字符。"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def get_last_local_process_time(item_name: str, category: str) -> datetime | None:
    """检查本地文件，获取该条目最后一次处理的时间。"""
    safe_item_name = sanitize_filename(item_name)
    item_dir = os.path.join(DATA_DIR, category, safe_item_name)

    if not os.path.isdir(item_dir):
        return None

    latest_time = None
    timestamp_regex = re.compile(r'_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.json$')

    for filename in os.listdir(item_dir):
        match = timestamp_regex.search(filename)
        if match:
            try:
                timestamp_str = match.group(1)
                dt_object = datetime.strptime(timestamp_str, '%Y-%m-%d-%H-%M-%S')
                localized_dt = TIMEZONE.localize(dt_object)
                if latest_time is None or localized_dt > latest_time:
                    latest_time = localized_dt
            except ValueError:
                continue
    return latest_time

def parse_list_file(file_path: str) -> dict[str, list]:
    """解析 LIST.txt 文件，返回一个按类别组织的字典。"""
    if not os.path.exists(file_path):
        logger.error(f"错误：列表文件不存在于 '{file_path}'")
        return {}
    
    logger.info(f"正在读取列表文件: {file_path}")
    categorized_items = {}
    current_category = None
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if line in ['person', 'organization', 'movement', 'event', 'document', 'location']:
                current_category = line
                if current_category not in categorized_items:
                    categorized_items[current_category] = []
            elif line in ['new']:
                return categorized_items
            elif current_category:
                categorized_items[current_category].append(line)
    
    return categorized_items

def process_item(item_name: str, category: str, wiki_client: WikipediaClient, parser: GeminiParser):
    """对单个条目执行完整的处理流程。"""
    logger.info(f"--- 开始处理 '{item_name}' (类别: {category}) ---")

    last_local_time = get_last_local_process_time(item_name, category)

    # 如果从未处理过，则直接继续处理
    if not last_local_time:
        logger.info("未在本地发现历史版本，将执行首次提取。")
    else:
        now = datetime.now(TIMEZONE)
        age_in_days = (now - last_local_time).days

        # 在冷静期内（如一周）处理过，直接跳过
        if age_in_days <= PROB_START_DAY:
            logger.info(f"'{item_name}' 在 {age_in_days} 天前刚处理过 (在 {PROB_START_DAY} 天冷静期内)，跳过。")
            return

        # 只有在冷静期过后，才进行网络请求检查Wiki更新时间
        latest_wiki_time = wiki_client.get_latest_revision_time(item_name)

        # 如果Wiki页面没有更新，跳过
        if latest_wiki_time and latest_wiki_time <= last_local_time:
            logger.info(f"'{item_name}' 的本地数据已是最新 (Wiki无更新)，跳过。")
            return

        # 在概率期内（如一周到一月），且Wiki有更新，按递增概率决定是否处理
        if PROB_START_DAY < age_in_days <= PROB_END_DAY:
            total_duration = PROB_END_DAY - PROB_START_DAY
            current_pos = age_in_days - PROB_START_DAY
            ratio = current_pos / total_duration if total_duration > 0 else 1
            probability = PROB_START_VALUE + (PROB_END_VALUE - PROB_START_VALUE) * ratio

            if random.random() < probability:
                logger.info(f"'{item_name}' 在 {age_in_days} 天前处理过，Wiki有更新，按概率 ({probability:.2%}) 重新提取。")
            else:
                logger.info(f"'{item_name}' 在 {age_in_days} 天前处理过，Wiki有更新，但按概率 ({probability:.2%}) 本次跳过。")
                return

        # 超过概率期（如一月以上），且Wiki有更新，则必须处理
        elif age_in_days > PROB_END_DAY:
            logger.info(f"'{item_name}' 在 {age_in_days} 天前处理过 (超过 {PROB_END_DAY} 天)，且Wiki有更新，将重新提取。")

    wikitext, _ = wiki_client.get_simplified_wikitext(item_name)
    
    if not wikitext:
        logger.warning(f"失败：未能获取 '{item_name}' 的Wikitext，跳过。")
        return
    
    # 开始解析
    structured_data = parser.parse(wikitext)
    if not structured_data:
        logger.warning(f"失败：LLM未能解析 '{item_name}' 的Wikitext，跳过。")
        return
        
    try:
        safe_item_name = sanitize_filename(item_name)
        output_dir = os.path.join(DATA_DIR, category, safe_item_name)
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(TIMEZONE).strftime('%Y-%m-%d-%H-%M-%S')
        file_name = f"{safe_item_name}_{timestamp}.json"
        output_path = os.path.join(output_dir, file_name)

        logger.info(f"正在将结果保存至: {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, indent=2, ensure_ascii=False)
        logger.info(f"成功：'{item_name}' 的处理结果已保存。")
    except Exception as e:
        logger.error(f"严重错误：在保存文件时发生异常 - {e}")

def main():
    """脚本主入口函数。"""
    items_to_process = parse_list_file(LIST_FILE_PATH)
    if not items_to_process:
        logger.info("列表文件为空或不存在，任务结束。")
        return

    # 初始化客户端和解析器
    wiki_client = WikipediaClient()
    parser = GeminiParser()
    
    total_items = sum(len(v) for v in items_to_process.values())
    logger.info(f"列表文件解析完成，共发现 {total_items} 个条目待处理。")
    
    processed_count = 0
    for category, items in items_to_process.items():
        for item in items:
            processed_count += 1
            logger.info(f"[{processed_count}/{total_items}] ====================================")
            process_item(item, category, wiki_client, parser)
    
    logger.info("所有条目处理完毕。")

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        stream=sys.stdout
    )
    
    main()
