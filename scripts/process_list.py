# scripts/process_list.py

import os
import sys
import json
import re
from datetime import datetime
import random
import logging

# 使用相对路径导入
from .config import DATA_DIR, LIST_FILE_PATH, PROB_START_DAY, PROB_END_DAY, PROB_START_VALUE, PROB_END_VALUE, TIMEZONE
from .clients.wikipedia_client import WikipediaClient
from .services.llm_service import LLMService
from .utils import sanitize_filename

logger = logging.getLogger(__name__)

class ListProcessor:
    """
    负责处理 `LIST.md` 中的实体列表，执行从维基百科提取、解析、
    并保存为结构化 JSON 文件的核心流程。
    """
    def __init__(self, wiki_client: WikipediaClient, llm_service: LLMService):
        """
        初始化 ListProcessor。

        Args:
            wiki_client: 用于与维基百科交互的客户端实例。
            llm_service: 用于调用大语言模型的服务实例。
        """
        self.wiki_client = wiki_client
        self.llm_service = llm_service
        self.items_to_process = {}

    def _parse_list_file(self) -> bool:
        """解析 LIST.md 文件，将待处理条目加载到 self.items_to_process。"""
        if not os.path.exists(LIST_FILE_PATH):
            logger.error(f"错误：列表文件不存在于 '{LIST_FILE_PATH}'")
            return False
        
        logger.info(f"正在读取列表文件: {LIST_FILE_PATH}")
        categorized_items = {}
        current_category = None
        lang_pattern = re.compile(r'\((?P<lang>[a-z]{2})\)\s*')

        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('## '):
                    category_name = line[3:].strip().lower()
                    if category_name == 'new': break
                    current_category = category_name
                    if current_category not in categorized_items:
                        categorized_items[current_category] = []
                    continue
                if not line or line.startswith('//'): continue
                if current_category:
                    lang = 'zh'
                    item_name = line
                    match = lang_pattern.match(line)
                    if match:
                        lang = match.group('lang')
                        item_name = line[match.end():].strip()
                    categorized_items[current_category].append((item_name, lang))
        
        self.items_to_process = categorized_items
        return True

    def _get_last_local_process_time(self, item_name: str, category: str) -> datetime | None:
        """检查本地文件，获取该条目最后一次处理的时间。"""
        safe_item_name = sanitize_filename(item_name)
        item_dir = os.path.join(DATA_DIR, category, safe_item_name)
        if not os.path.isdir(item_dir): return None

        latest_time = None
        timestamp_regex = re.compile(r'_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.json$')
        for filename in os.listdir(item_dir):
            match = timestamp_regex.search(filename)
            if match:
                try:
                    dt_object = datetime.strptime(match.group(1), '%Y-%m-%d-%H-%M-%S')
                    localized_dt = TIMEZONE.localize(dt_object)
                    if latest_time is None or localized_dt > latest_time:
                        latest_time = localized_dt
                except ValueError: continue
        return latest_time

    def _process_item(self, item_tuple: tuple, category: str):
        """对单个条目执行完整的处理流程。"""
        item_name, lang = item_tuple
        logger.info(f"--- 开始处理 '{item_name}' (类别: {category}, 语言: {lang}) ---")

        last_local_time = self._get_last_local_process_time(item_name, category)

        if not last_local_time:
            logger.info("未在本地发现历史版本，将执行首次提取。")
        else:
            now = datetime.now(TIMEZONE)
            age_in_days = (now - last_local_time).days
            if age_in_days <= PROB_START_DAY:
                logger.info(f"'{item_name}' 在 {age_in_days} 天前刚处理过，跳过。")
                return
            
            latest_wiki_time = self.wiki_client.get_latest_revision_time(item_name, lang=lang)
            if latest_wiki_time and latest_wiki_time <= last_local_time:
                logger.info(f"'{item_name}' 的本地数据已是最新，跳过。")
                return

            if PROB_START_DAY < age_in_days <= PROB_END_DAY:
                ratio = (age_in_days - PROB_START_DAY) / (PROB_END_DAY - PROB_START_DAY)
                probability = PROB_START_VALUE + (PROB_END_VALUE - PROB_START_VALUE) * ratio
                if random.random() >= probability:
                    logger.info(f"'{item_name}' 在概率期内，按概率 ({probability:.2%}) 本次跳过。")
                    return
                logger.info(f"'{item_name}' 在概率期内，按概率 ({probability:.2%}) 重新提取。")
            else: # age_in_days > PROB_END_DAY
                logger.info(f"'{item_name}' 已超过 {PROB_END_DAY} 天未更新，将重新提取。")

        wikitext, _ = self.wiki_client.get_wikitext(item_name, lang=lang)
        if not wikitext:
            logger.warning(f"失败：未能获取 '{item_name}' 的Wikitext，跳过。")
            return
        
        structured_data = self.llm_service.parse_wikitext(wikitext)
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

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(structured_data, f, indent=2, ensure_ascii=False)
            logger.info(f"成功：'{item_name}' 的处理结果已保存至: {output_path}")

            for old_filename in os.listdir(output_dir):
                if old_filename.endswith('.json') and old_filename != file_name:
                    os.remove(os.path.join(output_dir, old_filename))
                    logger.info(f"已删除旧版本: {old_filename}")
        except Exception as e:
            logger.error(f"严重错误：在保存文件时发生异常 - {e}")

    def run(self):
        """脚本主入口函数。"""
        if not self._parse_list_file():
            logger.info("列表文件为空或不存在，任务结束。")
            return

        total_items = sum(len(v) for v in self.items_to_process.values())
        logger.info(f"列表文件解析完成，共发现 {total_items} 个条目待处理。")
        
        processed_count = 0
        for category, items in self.items_to_process.items():
            for item_tuple in items:
                processed_count += 1
                logger.info(f"[{processed_count}/{total_items}] ====================================")
                self._process_item(item_tuple, category)
        
        logger.info("所有条目处理完毕。")
