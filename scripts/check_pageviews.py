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
import time
import re

# 使用相对路径导入
from .config import LIST_FILE_PATH, CACHE_DIR, PROB_START_DAY, PROB_END_DAY, PROB_START_VALUE, PROB_END_VALUE, WIKI_API_URL_TPL, PAGEVIEWS_API_BASE, USER_AGENT

# --- 日志记录器初始化 ---
logger = logging.getLogger(__name__)

# --- 转换器 ---
s2t_converter = OpenCC('s2t') # 简转繁
t2s_converter = OpenCC('t2s') # 繁转简

# --- 全局常量 ---
PAGEVIEWS_DATA_START_DATE = datetime(2015, 7, 1) # 维基媒体Pageviews API数据起始日期
PAGEVIEWS_CACHE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')
CREATION_DATE_CACHE_PATH = os.path.join(CACHE_DIR, 'creation_date_cache.json')
BATCH_SIZE = 120 # 并发处理的批次大小
MAX_NETWORK_CHECKS = 7000 # 单次运行最大处理规模

# --- 速率与并发控制 ---
IS_CI = os.getenv('GITHUB_ACTIONS') == 'true'
RATE_LIMIT = 120 if IS_CI else 180
PER_SECONDS = 60
CONCURRENCY_LIMIT = 32
CONCURRENCY_SEMAPHORE = asyncio.Semaphore(CONCURRENCY_LIMIT)

class AsyncLeakyBucket:
    """
    一个异步漏桶速率限制器，用于平滑请求速率。
    """
    def __init__(self, rate: int, per_seconds: int):
        self.rate_per_second = rate / per_seconds
        self._lock = asyncio.Lock()
        self._last_request_time = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait_period = 1 / self.rate_per_second
            wait_needed = wait_period - elapsed
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
            self._last_request_time = time.monotonic()

leaky_bucket = AsyncLeakyBucket(RATE_LIMIT, PER_SECONDS)
logger.info(f"速率限制配置: {RATE_LIMIT} 请求 / {PER_SECONDS} 秒")
logger.info(f"并发上限配置: {CONCURRENCY_LIMIT} 个同时请求")


def load_json_cache(file_path: str) -> dict:
    """通用JSON缓存加载函数"""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            logger.info(f"成功加载缓存文件: {os.path.basename(file_path)}")
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return {}

def save_json_cache(file_path: str, cache_data: dict):
    """通用JSON缓存保存函数"""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        logger.info(f"缓存已成功更新至: {os.path.basename(file_path)}")
    except IOError as e:
        logger.error(f"严重错误：无法写入缓存文件 {file_path}。错误: {e}")

def parse_list_file(file_path: str) -> dict[str, list]:
    """解析 LIST.md 文件，返回一个按类别组织的字典"""
    if not os.path.exists(file_path):
        logger.error(f"错误：列表文件不存在于 '{file_path}'")
        return {}
    
    logger.info(f"正在读取列表文件: {file_path}")
    categorized_items, current_category = {}, None
    lang_pattern = re.compile(r'\((?P<lang>[a-z]{2})\)\s*')

    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()

            # 检查是否为类别标题
            if line.startswith('## '):
                current_category = line[3:].strip().lower()
                if current_category not in categorized_items: categorized_items[current_category] = []
                continue
            if not line or line.startswith('//') or not current_category: continue

            lang, item_name = 'zh', line
            if match := lang_pattern.match(line):
                lang = match.group('lang')
                item_name = line[match.end():].strip()
            categorized_items[current_category].append({"original_line": line, "name": item_name, "lang": lang})
    return categorized_items

def batchify(data: list, batch_size: int):
    """将列表分割成指定大小的批次"""
    for i in range(0, len(data), batch_size):
        yield data[i:i + batch_size]

async def make_api_request_async(session: aiohttp.ClientSession, url, params=None):
    """
    执行异步API请求，由信号量控制并发数，由漏桶平滑速率。
    """
    # 1. 获取一个并发名额，如果已满则在此等待
    async with CONCURRENCY_SEMAPHORE:
        # 2. 获取名额后，通过漏桶平滑发出请求的精确时间点
        await leaky_bucket.acquire()
        timeout_obj = aiohttp.ClientTimeout(total=20)
        try:
            async with session.get(url, params=params, timeout=timeout_obj) as response:
                if response.status == 404: return None
                if response.status == 429:
                    logger.warning(f"收到 429 错误: {url}。放弃本次请求。")
                    return None
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning(f"API请求失败: {url}, 错误: {e}")
            return None

async def get_article_creation_date_async(session: aiohttp.ClientSession, article_title: str, lang: str, creation_date_cache: dict) -> datetime | None:
    """通过MediaWiki API异步获取维基百科文章的创建日期，优先使用缓存"""
    # 1. 检查缓存
    if article_title in creation_date_cache and creation_date_cache[article_title]:
        try:
            return datetime.fromisoformat(creation_date_cache[article_title])
        except (ValueError, TypeError):
            pass # 缓存格式错误，将继续进行API查询

    # 2. 缓存未命中，执行API查询
    api_url = WIKI_API_URL_TPL.format(lang=lang)
    params = {"action": "query", "prop": "revisions", "titles": article_title, "rvlimit": "1", "rvdir": "newer", "format": "json", "formatversion": "2"}

    data = await make_api_request_async(session, api_url, params=params)
    
    if not data: return None
    try:
        page = data["query"]["pages"][0]
        if page.get("missing"): return None
        if "revisions" in page and page["revisions"]:
            timestamp_str = page["revisions"][0]["timestamp"]
            # 3. 查询成功，将结果存入缓存
            creation_date_cache[article_title] = timestamp_str
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (KeyError, IndexError):
        return None
    return None

async def _fetch_stats_for_title_async(session: aiohttp.ClientSession, article_title: str, lang: str, creation_date_cache: dict) -> dict:
    """内部函数，为单个标题并行获取统计数据"""
    # 创建两个异步任务，一个获取创建日期，一个获取浏览量
    creation_date_task = asyncio.create_task(get_article_creation_date_async(session, article_title, lang, creation_date_cache))
    
    # 构造 Pageviews API 请求
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    end_date = today - timedelta(days=1)
    # 先假设一个最长的查询周期（365天），获取到创建日期后再精确计算
    start_date_for_req = end_date - timedelta(days=365)
    start_str, end_str = start_date_for_req.strftime('%Y%m%d00'), end_date.strftime('%Y%m%d00')
    encoded_title = urllib.parse.quote(article_title.replace(" ", "_"))
    pageviews_url = f"{PAGEVIEWS_API_BASE}{lang}.wikipedia.org/all-access/user/{encoded_title}/daily/{start_str}/{end_str}"
    pageviews_task = asyncio.create_task(make_api_request_async(session, pageviews_url))

    # 使用 asyncio.gather 并行等待两个任务完成
    creation_date, pageviews_data = await asyncio.gather(creation_date_task, pageviews_task)

    if not creation_date:
        return {'error': 'Page not found or creation date inaccessible'}
    
    # 获取到创建日期后，进行精确计算
    effective_start_date = max(creation_date, PAGEVIEWS_DATA_START_DATE)
    if (days_since_creation := (end_date - effective_start_date).days) <= 0:
        return {'total_views': 0, 'avg_daily_views': 0}

    if not pageviews_data:
        return {'error': 'Pageviews API request failed'}

    # 过滤浏览量数据，只保留有效日期范围内的
    valid_items = [item for item in pageviews_data.get('items', []) 
                   if datetime.strptime(item['timestamp'], '%Y%m%d%H') >= effective_start_date]
    
    num_days_with_data = len(valid_items)
    if num_days_with_data == 0:
        return {'total_views': 0, 'avg_daily_views': 0}

    total_views = sum(item['views'] for item in valid_items)
    avg_daily_views = total_views / num_days_with_data
    
    return {'total_views': total_views, 'avg_daily_views': avg_daily_views}

async def get_pageviews_stats_async(session: aiohttp.ClientSession, item_obj: dict, creation_date_cache: dict) -> tuple[str, dict]:
    """负责网络请求和简繁体回退逻辑"""
    article_title, lang = item_obj['name'], item_obj['lang']
    logger.info(f"[*] 正在网络查询: {article_title} ({lang})")
    
    stats = await _fetch_stats_for_title_async(session, article_title, lang, creation_date_cache)
    if 'error' not in stats:
        stats['check_timestamp'] = datetime.now(timezone.utc).isoformat()
        return article_title, stats

    # 后备查询逻辑 (仅对中文)
    if lang == 'zh':
        for candidate in {s2t_converter.convert(article_title), t2s_converter.convert(article_title)}:
            if candidate != article_title:
                logger.info(f"  ...原始查询失败，尝试备用标题: '{candidate}'")
                stats = await _fetch_stats_for_title_async(session, candidate, lang, creation_date_cache)
                if 'error' not in stats:
                    stats['check_timestamp'] = datetime.now(timezone.utc).isoformat()
                    return article_title, stats

    return article_title, {
        'error': 'API and fallback failed', 
        'total_views': -1, 
        'avg_daily_views': 0, 
        'check_timestamp': datetime.now(timezone.utc).isoformat()
    }

def rewrite_list_file(sorted_results: dict):
    """使用排序后的条目重写 LIST.md 文件，保持 Markdown 格式。"""
    logger.info("\n--- 正在用排序后的结果重写 LIST.md ---")
    
    # 将排序结果的键转换为小写，以匹配解析逻辑
    # 同时保留原始大小写用于输出
    sorted_categories_lower = {k.lower(): k for k in sorted_results.keys()}
    
    try:
        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f: original_lines = f.readlines()
        new_lines, is_in_sorted_category = [], False
        for line in original_lines:
            stripped_line = line.strip()
            
            # 检查是否是类别标题行
            if stripped_line.startswith('## '):
                category_name_original = stripped_line[3:].strip()
                category_name_lower = category_name_original.lower()

                # 如果类别在排序结果里
                if category_name_lower in sorted_categories_lower:
                    is_in_sorted_category = True
                    # 写入格式化的标题和排序后的内容
                    original_case_category = sorted_categories_lower[category_name_lower]
                    if original_case_category != 'person': new_lines.append("\n")
                    new_lines.append(f"## {original_case_category}\n")
                    for item_name in sorted_results[original_case_category]: new_lines.append(f"{item_name}\n")
                else:
                    # 如果类别不在排序结果里（比如 ## new），则照常保留
                    is_in_sorted_category = False
                    new_lines.append(line)
            # 如果当前行不属于需要重写的类别，则保留它（包括空行和注释）
            elif not is_in_sorted_category:
                new_lines.append(line)
        
        with open(LIST_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
            
        logger.info("--- LIST.md 文件更新成功 ---")
    except IOError as e:
        logger.error(f"严重错误：重写 LIST.md 文件失败。错误: {e}")

async def main():
    """脚本主入口，负责所有缓存读写和决策逻辑"""
    logger.info("--- 开始检查页面热度 ---")
    
    if not (items_by_category := parse_list_file(LIST_FILE_PATH)):
        logger.info("列表文件为空，任务结束。")
        return

    # --- 步骤 0: 加载缓存 ---
    pageviews_cache = load_json_cache(PAGEVIEWS_CACHE_PATH)
    creation_date_cache = load_json_cache(CREATION_DATE_CACHE_PATH)
    
    to_check_this_run, now, skipped_count = [], datetime.now(timezone.utc), 0

    # --- 步骤 1: 预处理，决策哪些条目需要检查 ---
    logger.info("\n--- 步骤 1/3: 筛选需要网络检查的条目 ---")
    all_items = [item for items in items_by_category.values() for item in items]
    for item_obj in all_items:
        item_name = item_obj['name']
        if item_name not in pageviews_cache or (cached_entry := pageviews_cache[item_name]).get('error') or not (timestamp_str := cached_entry.get('check_timestamp')):
            to_check_this_run.append(item_obj); continue
        try:
            age_in_days = (now.date() - datetime.fromisoformat(timestamp_str).date()).days
            if age_in_days <= PROB_START_DAY: skipped_count += 1
            elif PROB_START_DAY < age_in_days <= PROB_END_DAY:
                ratio = (age_in_days - PROB_START_DAY) / (PROB_END_DAY - PROB_START_DAY)
                probability = PROB_START_VALUE + (PROB_END_VALUE - PROB_START_VALUE) * ratio
                if random.random() < probability: to_check_this_run.append(item_obj)
                else: skipped_count += 1
            else: to_check_this_run.append(item_obj)
        except (ValueError, TypeError): to_check_this_run.append(item_obj)
    
    logger.info(f"预处理完成。共 {len(all_items)} 项，其中 {skipped_count} 项将使用缓存，{len(to_check_this_run)} 项符合网络检查条件。")

    if len(to_check_this_run) > MAX_NETWORK_CHECKS:
        logger.info(f"需要检查的条目过多({len(to_check_this_run)}), 将随机抽样 {MAX_NETWORK_CHECKS} 项进行处理。")
        random.shuffle(to_check_this_run)
        items_for_network_check = to_check_this_run[:MAX_NETWORK_CHECKS]
    else: items_for_network_check = to_check_this_run

    # --- 步骤 2: 并发执行网络请求 ---
    if items_for_network_check:
        logger.info(f"\n--- 步骤 2/3: 开始对 {len(items_for_network_check)} 个条目进行并发网络检查 ---")
        headers = {'User-Agent': USER_AGENT}
        async with aiohttp.ClientSession(headers=headers, trust_env=True) as session:
            total_batches = (len(items_for_network_check) + BATCH_SIZE - 1) // BATCH_SIZE
            for i, batch in enumerate(batchify(items_for_network_check, BATCH_SIZE)):
                logger.info(f"--- 正在处理批次 {i+1}/{total_batches} (共 {len(batch)} 项) ---")
                tasks = [get_pageviews_stats_async(session, item_obj, creation_date_cache) for item_obj in batch]

                for result in await asyncio.gather(*tasks, return_exceptions=True):
                    if isinstance(result, Exception): 
                        logger.error(f"一个查询任务在执行中发生异常: {str(result)}")
                    elif isinstance(result, tuple): 
                        pageviews_cache[result[0]] = result[1]
    else:
        logger.info("\n--- 步骤 2/3: 无需网络检查，跳过此步骤 ---")

    # --- 步骤 3: 整合所有数据、排序、保存缓存并重写文件 ---
    logger.info("\n--- 步骤 3/3: 整合所有数据、排序、保存并重写文件 ---")
    final_results_by_category = {cat: [] for cat in items_by_category}
    for category, items in items_by_category.items():
        for item_obj in items:
            stats = pageviews_cache.get(item_obj['name']) or {'total_views': -1, 'avg_daily_views': 0}
            final_results_by_category[category].append({'item': item_obj, 'stats': stats})

    sorted_results = OrderedDict()
    for category, results in final_results_by_category.items():
        if results:
            sorted_items = sorted(results, key=lambda x: x['stats']['avg_daily_views'], reverse=True)
            # 提取 original_line 用于文件重写
            sorted_results[category] = [res['item']['original_line'] for res in sorted_items]

    sorted_cache_items = sorted(
        pageviews_cache.items(),
        key=lambda item: item[1].get('avg_daily_views', 0),
        reverse=True
    )
    sorted_pageviews_cache = dict(sorted_cache_items)

    # 保存两个缓存文件
    save_json_cache(PAGEVIEWS_CACHE_PATH, sorted_pageviews_cache)
    save_json_cache(CREATION_DATE_CACHE_PATH, creation_date_cache)
    
    rewrite_list_file(sorted_results)
    logger.info("\n--- 全部任务完成 ---")

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        stream=sys.stdout
    )
    asyncio.run(main())
