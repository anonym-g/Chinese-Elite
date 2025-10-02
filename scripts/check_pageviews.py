# scripts/check_pageviews.py

import os
import sys
import requests
import urllib.parse
from datetime import datetime, timedelta, timezone
import time
import json
from collections import OrderedDict
from opencc import OpenCC

from config import LIST_FILE_PATH, CACHE_DIR, WIKI_API_URL, PAGEVIEWS_API_BASE, USER_AGENT

# --- 全局常量 ---
PAGEVIEWS_DATA_START_DATE = datetime(2015, 7, 1)
CACHE_FILE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')
MAX_RETRIES = 3

# 定义需要被脚本处理的类别集合
PROCESS_CATEGORIES = {'person', 'organization', 'movement', 'event', 'document', 'location', 'new'}
KNOWN_CATEGORIES = PROCESS_CATEGORIES # 以防将来需要新增不予处理的类别

# --- 全局会话和转换器 ---
session = requests.Session()
session.headers.update({'User-Agent': USER_AGENT})
s2t_converter = OpenCC('s2t') # 简转繁
t2s_converter = OpenCC('t2s') # 繁转简

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
        print(f"严重错误：无法写入页面访问量缓存文件 {file_path}。错误: {e}", file=sys.stderr)


def parse_list_file(file_path: str) -> dict[str, list]:
    """解析 LIST.txt 文件，返回一个按类别组织的字典。"""
    if not os.path.exists(file_path):
        print(f"[!] 错误：列表文件不存在于 '{file_path}'", file=sys.stderr)
        return {}
    
    categorized_items = {}
    current_category = None
    # 以只读模式打开文件，并指定UTF-8编码
    with open(file_path, 'r', encoding='utf-8') as f:
        # 逐行读取文件
        for line in f:
            # 去除行首尾的空白字符
            line = line.strip()
            # 跳过空行和以'#'开头的注释行
            if not line or line.startswith('#'): continue
            
            # 如果当前行是一个已知的类别标题
            if line in KNOWN_CATEGORIES:
                current_category = line
                # 如果是第一次遇到这个类别，在字典中为它创建一个空列表
                if current_category not in categorized_items:
                    categorized_items[current_category] = []
            # 如果当前行不是类别标题，且我们已经确定了当前类别
            elif current_category:
                # 将该行视为当前类别下的一个条目并添加
                categorized_items[current_category].append(line)
    return categorized_items

def make_api_request(url, params=None):
    """执行API请求，并在失败时自动重试。"""
    retries = 0
    while retries < MAX_RETRIES:
        try:
            # 使用全局session对象发起GET请求，设置20秒超时
            response = session.get(url, params=params, timeout=20)
            # 特别处理404错误
            if response.status_code == 404:
                raise requests.exceptions.HTTPError(f"404 Not Found for url: {response.url}")
            response.raise_for_status()
            # 如果请求成功，返回JSON格式的响应体
            return response.json()
        except requests.exceptions.RequestException as e:
            # 捕获所有requests相关的异常
            retries += 1
            if retries >= MAX_RETRIES:
                # 不打印警告，让上层函数处理
                return None
            time.sleep(retries)
    return None

def get_article_creation_date(article_title: str) -> datetime | None:
    """通过MediaWiki API获取维基百科文章的创建日期。"""
    params = {
        "action": "query", "prop": "revisions", "titles": article_title,
        "rvlimit": "1", "rvdir": "newer", "format": "json", "formatversion": "2"
    }
    # 发起带重试的API请求
    data = make_api_request(WIKI_API_URL, params=params)
    if not data: return None
    try:
        # 解析返回的JSON数据
        page = data["query"]["pages"][0]
        if page.get("missing"): return None
        if "revisions" in page and page["revisions"]:
            # 提取第一条修订记录的时间戳
            timestamp_str = page["revisions"][0]["timestamp"]
            # 将ISO 8601格式的时间戳字符串转换为datetime对象
            # .replace('Z', '+00:00')是为了兼容性，.replace(tzinfo=None)是得到一个“朴素”时间对象，便于后续计算
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (KeyError, IndexError):
        return None
    return None

def get_pageviews_stats(article_title: str, pageviews_cache: dict) -> dict:
    """
    获取单个维基百科页面的访问量统计，增加简繁体回退和缓存机制。
    """
    # 尝试原始标题
    stats = _fetch_stats_for_title(article_title)
    if 'error' not in stats:
        # 如果成功，更新缓存并返回
        pageviews_cache[article_title] = stats
        return stats

    # --- 后备查询逻辑 ---
    # 生成备选标题 (简转繁 & 繁转简)，使用set自动去重
    candidate_titles = {
        s2t_converter.convert(article_title),
        t2s_converter.convert(article_title)
    }
    
    # 遍历备选标题进行查询
    for candidate in candidate_titles:
        # 仅当备选标题与原始标题不同时才查询，避免重复请求
        if candidate != article_title:
            print(f"  ...原始查询失败，尝试备用标题: '{candidate}'")
            stats = _fetch_stats_for_title(candidate)
            if 'error' not in stats:
                # 如果成功，将结果缓存在原始标题下，并返回
                pageviews_cache[article_title] = stats
                return stats

    # 如果所有API查询都失败了，则回退到缓存
    if article_title in pageviews_cache:
        print(f"  ...API查询失败，从缓存中读取历史数据。")
        return pageviews_cache[article_title]
    else:
        return {'error': 'API and cache lookup failed'}

def _fetch_stats_for_title(article_title: str) -> dict:
    """内部函数，为单个标题获取统计数据。"""
    # 1. 获取文章创建日期
    creation_date = get_article_creation_date(article_title)
    if not creation_date:
        return {'error': 'Page not found or creation date inaccessible'}

    # 2. 确定有效的查询起始日期（不能早于API有数据的日期）
    effective_start_date = max(creation_date, PAGEVIEWS_DATA_START_DATE)
    
    # 3. 确定查询的时间范围
    # 使用带时区的datetime.now(timezone.utc)来获取当前UTC时间，然后移除时区信息，以进行朴素时间计算
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    end_date = today - timedelta(days=1)
    days_since_creation = (end_date - effective_start_date).days

    # 如果页面太新，可能还没有任何完整的日访问数据
    if days_since_creation <= 0:
        return {'total_views': 0, 'avg_daily_views': 0}
    
    # 根据页面历史长度决定查询范围
    if days_since_creation < 365:
        start_date = effective_start_date
        duration_days = days_since_creation
    else:
        start_date = end_date - timedelta(days=365)
        duration_days = 365

    # 4. 构建Pageviews API的URL
    start_str = start_date.strftime('%Y%m%d00')
    end_str = end_date.strftime('%Y%m%d00')
    encoded_title = urllib.parse.quote(article_title)
    url = f"{PAGEVIEWS_API_BASE}zh.wikipedia.org/all-access/user/{encoded_title}/daily/{start_str}/{end_str}"
    
    # 5. 发起请求并处理数据
    data = make_api_request(url)
    if not data:
        return {'error': 'Pageviews API request failed'}

    # 计算总访问量和日均访问量
    total_views = sum(item['views'] for item in data.get('items', []))
    avg_daily_views = total_views / duration_days if duration_days > 0 else 0
    
    # 6. 返回统计结果
    return {
        'total_views': total_views,
        'avg_daily_views': avg_daily_views
    }

def rewrite_list_file(sorted_results: dict):
    """使用排序后的条目重写LIST.txt文件。"""
    print("\n--- 正在用排序后的结果重写 LIST.txt ---")
    try:
        # 首先完整读取原始文件内容到内存
        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
            original_lines = f.readlines()
        
        new_lines = []
        current_category = None
        
        # 逐行遍历原始文件内容
        for line in original_lines:
            stripped_line = line.strip()
            
            # 如果该行是一个类别标题
            if stripped_line in KNOWN_CATEGORIES:
                current_category = stripped_line
                new_lines.append(line)
                
                # 如果这个类别是被我们处理和排序过的
                if current_category in sorted_results:
                    # 写入排序后的所有条目
                    for item_name in sorted_results[current_category]:
                        new_lines.append(f"{item_name}\n")
            # 如果该行不是类别标题
            else:
                # 判断这一行是不是一个需要被替换的旧条目
                is_old_item = (
                    current_category in sorted_results and
                    stripped_line != "" and
                    not stripped_line.startswith('#')
                )
                
                if is_old_item:
                    # 如果是，就跳过它，因为它已经被新的排序列表取代了
                    continue
                else:
                    # 否则，保留这一行（它是空行、注释，或属于其他未处理类别）
                    new_lines.append(line)
        
        # 将构建好的新内容完全覆盖写入原始文件
        with open(LIST_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        print("--- LIST.txt 文件更新成功 ---")
    except IOError as e:
        print(f"严重错误：重写 LIST.txt 文件失败。错误: {e}")

def main():
    """脚本主入口函数。"""
    print("--- 开始检查 LIST.txt 中各项的维基百科页面日访问频次 ---")
    
    items_to_process = parse_list_file(LIST_FILE_PATH)
    if not items_to_process:
        print("列表文件为空或不存在，任务结束。")
        return

    pageviews_cache = load_pageviews_cache(CACHE_FILE_PATH)
    
    results_by_category = {cat: [] for cat in PROCESS_CATEGORIES if cat in items_to_process}
    
    for category, items in items_to_process.items():
        if category not in PROCESS_CATEGORIES: continue

        print(f"\n--- 正在处理类别: {category} ---")
        unique_items = list(dict.fromkeys(items)) # 去重

        for i, item in enumerate(unique_items):
            print(f"[*] 正在查询 ({i+1}/{len(unique_items)}): {item}")
            stats = get_pageviews_stats(item, pageviews_cache)
            
            if 'error' not in stats:
                result_str = f"  -> 结果: 总访问量: {stats['total_views']:,}, 日均访问: {stats['avg_daily_views']:.2f}"
                results_by_category[category].append({'item': item, 'stats': stats})
            else:
                result_str = f"  -> 失败: {stats['error']}"
                results_by_category[category].append({'item': item, 'stats': {'total_views': -1, 'avg_daily_views': 0}})
            print(result_str)

    print("\n--- 所有项目查询完毕，正在排序... ---")
    sorted_results = OrderedDict()
    for category in items_to_process.keys():
        if category in results_by_category:
            sorted_items = sorted(
                results_by_category[category], 
                key=lambda x: x['stats']['total_views'], 
                reverse=True
            )
            sorted_results[category] = [res['item'] for res in sorted_items]

    save_pageviews_cache(CACHE_FILE_PATH, pageviews_cache)
    print(f"--- 页面访问量缓存已更新至 {CACHE_FILE_PATH} ---")
    
    rewrite_list_file(sorted_results)

    print("\n--- 全部任务完成 ---")


if __name__ == "__main__":
    main()
