# scripts/check_pageviews.py

import os
import sys
import asyncio
import aiohttp
import urllib.parse
from datetime import datetime, timedelta, timezone
import json
from collections import OrderedDict
from opencc import OpenCC
import logging
import random

from config import LIST_FILE_PATH, CACHE_DIR, PROB_START_DAY, PROB_END_DAY, PROB_START_VALUE, PROB_END_VALUE, WIKI_API_URL, PAGEVIEWS_API_BASE, USER_AGENT

# 初始化日志记录器
logger = logging.getLogger(__name__)

# --- 转换器 ---
s2t_converter = OpenCC('s2t') # 简转繁
t2s_converter = OpenCC('t2s') # 繁转简

# --- 其他全局常量 ---
PAGEVIEWS_DATA_START_DATE = datetime(2015, 7, 1)
CACHE_FILE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')
MAX_RETRIES = 2
BATCH_SIZE = 25

def load_pageviews_cache(file_path: str) -> dict:
    """加载页面访问量缓存文件。"""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return {}

def save_pageviews_cache(file_path: str, cache_data: dict):
    """保存页面访问量缓存文件。"""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"严重错误：无法写入页面访问量缓存文件 {file_path}。错误: {e}")

def parse_list_file(file_path: str) -> dict[str, list]:
    """解析 LIST.txt 文件，返回一个按类别组织的字典。"""
    if not os.path.exists(file_path):
        logger.error(f"错误：列表文件不存在于 '{file_path}'")
        return {}
    
    categorized_items = {}
    current_category = None
    PROCESS_CATEGORIES = {'person', 'organization', 'movement', 'event', 'document', 'location', 'new'}
    KNOWN_CATEGORIES = PROCESS_CATEGORIES
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            
            if line in KNOWN_CATEGORIES:
                current_category = line
                if current_category not in categorized_items:
                    categorized_items[current_category] = []
            elif current_category:
                categorized_items[current_category].append(line)
    return categorized_items

def batchify(data: list, batch_size: int):
    """将列表分割成指定大小的批次。"""
    for i in range(0, len(data), batch_size):
        yield data[i:i + batch_size]

async def make_api_request_async(session: aiohttp.ClientSession, url, params=None):
    """执行异步API请求，并在失败时自动重试。"""
    retries = 0
    timeout_obj = aiohttp.ClientTimeout(total=1.5)
    while retries < MAX_RETRIES:
        try:
            async with session.get(url, params=params, timeout=timeout_obj) as response:
                if response.status == 404: return None
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            retries += 1
            if retries >= MAX_RETRIES:
                logger.warning(f"API请求失败 (尝试 {retries}/{MAX_RETRIES}): {url}, 错误: {e}")
                return None
            await asyncio.sleep(retries)
    return None

async def get_article_creation_date_async(session: aiohttp.ClientSession, article_title: str) -> datetime | None:
    """通过MediaWiki API异步获取维基百科文章的创建日期。"""
    params = {"action": "query", "prop": "revisions", "titles": article_title, "rvlimit": "1", "rvdir": "newer", "format": "json", "formatversion": "2"}
    data = await make_api_request_async(session, WIKI_API_URL, params=params)
    if not data: return None
    try:
        page = data["query"]["pages"][0]
        if page.get("missing"): return None
        if "revisions" in page and page["revisions"]:
            timestamp_str = page["revisions"][0]["timestamp"]
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (KeyError, IndexError):
        return None
    return None

async def _fetch_stats_for_title_async(session: aiohttp.ClientSession, article_title: str) -> dict:
    """内部函数，为单个标题获取统计数据，并在成功时附带时间戳。"""
    creation_date = await get_article_creation_date_async(session, article_title)
    if not creation_date:
        return {'error': 'Page not found or creation date inaccessible'}

    effective_start_date = max(creation_date, PAGEVIEWS_DATA_START_DATE)
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    end_date = today - timedelta(days=1)
    days_since_creation = (end_date - effective_start_date).days

    if days_since_creation <= 0:
        return {'total_views': 0, 'avg_daily_views': 0, 'check_timestamp': datetime.now(timezone.utc).isoformat()}
    
    duration_days = min(days_since_creation, 365)
    start_date = end_date - timedelta(days=duration_days)
    start_str, end_str = start_date.strftime('%Y%m%d00'), end_date.strftime('%Y%m%d00')
    encoded_title = urllib.parse.quote(article_title.replace(" ", "_"))
    url = f"{PAGEVIEWS_API_BASE}zh.wikipedia.org/all-access/user/{encoded_title}/daily/{start_str}/{end_str}"
    
    data = await make_api_request_async(session, url)
    if not data:
        return {'error': 'Pageviews API request failed'}

    total_views = sum(item['views'] for item in data.get('items', []))
    avg_daily_views = total_views / duration_days if duration_days > 0 else 0
    
    return {
        'total_views': total_views,
        'avg_daily_views': avg_daily_views,
        'check_timestamp': datetime.now(timezone.utc).isoformat()
    }

async def get_pageviews_stats_async(session: aiohttp.ClientSession, article_title: str) -> tuple[str, dict]:
    """
    此函数负责网络请求和简繁体回退。
    无论请求成功还是失败，返回的字典中都包含 'total_views' 键，以防止排序时出错。
    """
    logger.info(f"[*] 正在网络查询: {article_title}")
    
    stats = await _fetch_stats_for_title_async(session, article_title)
    if 'error' not in stats:
        return article_title, stats

    # 后备查询逻辑
    candidate_titles = {
        s2t_converter.convert(article_title),
        t2s_converter.convert(article_title)
    }
    for candidate in candidate_titles:
        if candidate != article_title:
            logger.info(f"  ...原始查询失败，尝试备用标题: '{candidate}'")
            stats = await _fetch_stats_for_title_async(session, candidate)
            if 'error' not in stats:
                return article_title, stats

    final_result = {
        'error': 'API and cache lookup failed',
        'total_views': -1,
        'avg_daily_views': 0,
        'check_timestamp': datetime.now(timezone.utc).isoformat()
    }
    return article_title, final_result

def rewrite_list_file(sorted_results: dict):
    """使用排序后的条目重写LIST.txt文件。"""
    logger.info("\n--- 正在用排序后的结果重写 LIST.txt ---")
    try:
        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
            original_lines = f.readlines()
        
        new_lines, current_category = [], None
        PROCESS_CATEGORIES = {'person', 'organization', 'movement', 'event', 'document', 'location', 'new'}
        KNOWN_CATEGORIES = PROCESS_CATEGORIES
        
        for line in original_lines:
            stripped_line = line.strip()
            if stripped_line in KNOWN_CATEGORIES:
                current_category = stripped_line
                new_lines.append(line)
                if current_category in sorted_results:
                    for item_name in sorted_results[current_category]:
                        new_lines.append(f"{item_name}\n")
            else:
                is_old_item = (current_category in sorted_results and stripped_line != "" and not stripped_line.startswith('#'))
                if not is_old_item:
                    new_lines.append(line)
        
        with open(LIST_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        logger.info("--- LIST.txt 文件更新成功 ---")
    except IOError as e:
        logger.error(f"严重错误：重写 LIST.txt 文件失败。错误: {e}")

async def main():
    """脚本主入口，main函数负责所有缓存的读写和决策逻辑。"""
    logger.info("--- 开始检查页面热度（智能跳过模式） ---")
    
    items_by_category = parse_list_file(LIST_FILE_PATH)
    if not items_by_category:
        logger.info("列表文件为空，任务结束。")
        return

    pageviews_cache = load_pageviews_cache(CACHE_FILE_PATH)
    to_check_by_category = {cat: [] for cat in items_by_category}
    now = datetime.now(timezone.utc)

    # --- 步骤 1: 预处理，决策哪些条目需要检查 ---
    logger.info("\n--- 步骤 1/3: 预处理所有条目，决定是否需要网络检查 ---")
    total_items = sum(len(i) for i in items_by_category.values())
    skipped_count = 0
    for category, items in items_by_category.items():
        for item in items:
            # 决策逻辑：读取缓存并判断
            if item not in pageviews_cache:
                to_check_by_category[category].append(item)
                continue
            
            cached_entry = pageviews_cache[item]
            timestamp_str = cached_entry.get('check_timestamp')

            if not timestamp_str or cached_entry.get('error'):
                to_check_by_category[category].append(item)
                continue
            
            try:
                check_time = datetime.fromisoformat(timestamp_str)
                age_in_days = (now.date() - check_time.date()).days
                
                if age_in_days <= PROB_START_DAY:
                    skipped_count += 1
                elif PROB_START_DAY <= age_in_days <= PROB_END_DAY:
                    total_duration_days = PROB_END_DAY - PROB_START_DAY
                    current_pos_days = age_in_days - PROB_START_DAY
                    ratio = current_pos_days / total_duration_days if total_duration_days > 0 else 1
                    probability = PROB_START_VALUE + (PROB_END_VALUE - PROB_START_VALUE) * ratio
                    if random.random() < probability:
                        to_check_by_category[category].append(item)
                    else:
                        skipped_count += 1
                else:
                    to_check_by_category[category].append(item)
            except (ValueError, TypeError):
                to_check_by_category[category].append(item)
    
    logger.info(f"预处理完成。共 {total_items} 项，其中 {skipped_count} 项将使用缓存，{total_items - skipped_count} 项需要网络检查。")
    
    # --- 步骤 2: 并发执行网络请求 ---
    if total_items > skipped_count:
        logger.info("\n--- 步骤 2/3: 开始并发执行网络检查 ---")
        headers = {'User-Agent': USER_AGENT}
        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            for category, items_to_check in to_check_by_category.items():
                if not items_to_check:
                    continue

                logger.info(f"\n--- 正在检查类别: {category} ---")
                total_batches = (len(items_to_check) + BATCH_SIZE - 1) // BATCH_SIZE
                for i, batch in enumerate(batchify(items_to_check, BATCH_SIZE)):
                    logger.info(f"--- 正在处理批次 {i+1}/{total_batches} (共 {len(batch)} 项) ---")
                    
                    tasks = [get_pageviews_stats_async(session, item) for item in batch]
                    batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                    # 决策逻辑：在这里将新获取的结果写入缓存
                    for result in batch_results:
                        if isinstance(result, Exception):
                            logger.error(f"一个查询任务在执行中发生异常: {result}")
                        else:
                            item, stats = result # type: ignore
                            pageviews_cache[item] = stats # 更新缓存
    else:
        logger.info("\n--- 步骤 2/3: 无需网络检查，跳过此步骤 ---")

    # --- 步骤 3: 整合所有数据并排序 ---
    logger.info("\n--- 步骤 3/3: 整合所有数据并排序 ---")
    final_results_by_category = {cat: [] for cat in items_by_category}
    for category, items in items_by_category.items():
        for item in items:
            # 这里的 cache 已经是最新版本
            stats = pageviews_cache.get(item)
            # 防止意外的空值
            if not stats or 'total_views' not in stats:
                stats = {'total_views': -1, 'avg_daily_views': 0}
            final_results_by_category[category].append({'item': item, 'stats': stats})

    sorted_results = OrderedDict()
    for category, results in final_results_by_category.items():
        if results:
            sorted_items = sorted(results, key=lambda x: x['stats']['avg_daily_views'], reverse=True)
            sorted_results[category] = [res['item'] for res in sorted_items]

    save_pageviews_cache(CACHE_FILE_PATH, pageviews_cache)
    logger.info(f"--- 页面访问量缓存已更新至 {CACHE_FILE_PATH} ---")
    
    rewrite_list_file(sorted_results)
    logger.info("\n--- 全部任务完成 ---")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        stream=sys.stdout
    )
    asyncio.run(main())
