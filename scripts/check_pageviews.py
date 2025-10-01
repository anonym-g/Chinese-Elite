# scripts/check_pageviews.py

import os
import sys
import requests
import urllib.parse
from datetime import datetime, timedelta, timezone
import time
from collections import OrderedDict
from config import LIST_FILE_PATH, DATA_DIR, WIKI_API_URL, USER_AGENT

# --- 全局常量 ---

# 维基媒体基金会提供的页面访问量API的基础URL
PAGEVIEWS_API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
# 页面访问量API的数据从2015年7月1日开始提供，这是我们能查询到的最早日期
PAGEVIEWS_DATA_START_DATE = datetime(2015, 7, 1)
# 日志文件的完整路径
LOG_FILE_PATH = os.path.join(DATA_DIR, 'pageviews.log')
# API请求失败时的最大重试次数
MAX_RETRIES = 3

# 定义需要被脚本处理（查询访问量、排序等）的类别集合
PROCESS_CATEGORIES = {'person', 'organization', 'movement', 'event', 'document', 'location'}
# 定义所有在LIST.txt中合法的类别标题，包括不处理的'new'类别
# 这样做是为了让文件解析器能正确识别'new'是一个分区，而不是某个类别下的条目
KNOWN_CATEGORIES = PROCESS_CATEGORIES | {'new'}


# 创建一个全局的requests.Session对象，以便在多次API请求中复用TCP连接，提高效率
session = requests.Session()
# 设置一个符合维基媒体API规范的用户代理（User-Agent）
session.headers.update({'User-Agent': USER_AGENT})

def parse_list_file(file_path: str) -> dict[str, list]:
    """
    解析 LIST.txt 文件，返回一个按类别组织的字典。

    Args:
        file_path (str): LIST.txt文件的路径。

    Returns:
        dict[str, list]: 一个字典，键是类别名称，值是该类别下的条目列表。
    """
    # 检查文件是否存在，如果不存在则打印错误并返回空字典
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
    """
    执行API请求，并在失败时自动重试。

    Args:
        url (str): 请求的目标URL。
        params (dict, optional): URL查询参数。默认为 None。

    Returns:
        dict or None: 成功时返回解析后的JSON数据（字典），失败时返回None。
    """
    retries = 0
    while retries < MAX_RETRIES:
        try:
            # 使用全局session对象发起GET请求，设置20秒超时
            response = session.get(url, params=params, timeout=20)
            # 特别处理404错误，因为用户假设页面都存在，这可能表示临时问题
            if response.status_code == 404:
                raise requests.exceptions.HTTPError(f"404 Client Error: Not Found for url: {response.url}")
            # 对于其他HTTP错误（如500, 503等），这个方法会抛出异常
            response.raise_for_status()
            # 如果请求成功，返回JSON格式的响应体
            return response.json()
        except requests.exceptions.RequestException as e:
            # 捕获所有requests相关的异常
            retries += 1
            print(f"  [警告] 请求失败 (尝试 {retries}/{MAX_RETRIES}): {e}")
            # 如果还未达到最大重试次数
            if retries < MAX_RETRIES:
                # 等待一段时间再重试，等待时间随重试次数增加（2秒，4秒）
                time.sleep(retries * 2)
            else:
                # 达到最大次数后，打印错误并放弃
                print(f"  [错误] 达到最大重试次数，放弃请求。")
                return None
    return None

def get_article_creation_date(article_title: str) -> datetime | None:
    """
    通过MediaWiki API获取维基百科文章的创建日期。

    Args:
        article_title (str): 文章的准确标题。

    Returns:
        datetime or None: 文章的创建日期对象，如果页面不存在或查询失败则返回None。
    """
    # 构建API查询参数
    params = {
        "action": "query",
        "prop": "revisions",       # 获取修订历史
        "titles": article_title,   # 目标页面
        "rvlimit": "1",            # 只获取1条记录
        "rvdir": "newer",          # 从最早的版本开始
        "format": "json",          # 返回JSON格式
        "formatversion": "2"       # 使用新版JSON格式，更简洁
    }
    # 发起带重试的API请求
    data = make_api_request(WIKI_API_URL, params=params)
    if not data:
        return None

    try:
        # 解析返回的JSON数据
        page = data["query"]["pages"][0]
        # 检查API是否明确返回页面“missing”
        if page.get("missing"):
            print(f"  [信息] API返回'missing'，页面 '{article_title}' 不存在。")
            return None
        # 如果页面存在且有修订历史
        if "revisions" in page and page["revisions"]:
            # 提取第一条修订记录的时间戳
            timestamp_str = page["revisions"][0]["timestamp"]
            # 将ISO 8601格式的时间戳字符串转换为datetime对象
            # .replace('Z', '+00:00')是为了兼容性，.replace(tzinfo=None)是得到一个“朴素”时间对象，便于后续计算
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00')).replace(tzinfo=None)
    except (KeyError, IndexError) as e:
        # 捕获解析数据时可能发生的错误
        print(f"  [错误] 解析 '{article_title}' 的创建日期失败: {e}")
    
    return None

def get_pageviews_stats(article_title: str) -> dict | None:
    """
    获取单个维基百科页面在特定时间范围内的访问量统计。
    - 如果页面历史<1年，则统计从创建至今的数据。
    - 如果页面历史>=1年，则统计最近一年的数据。

    Args:
        article_title (str): 文章的准确标题。

    Returns:
        dict or None: 包含统计信息的字典，或包含错误信息的字典。
    """
    # 1. 获取文章创建日期
    creation_date = get_article_creation_date(article_title)
    if not creation_date:
        return {'error': 'Page not found or creation date inaccessible'}

    # 2. 确定有效的查询起始日期（不能早于API有数据的日期）
    effective_start_date = max(creation_date, PAGEVIEWS_DATA_START_DATE)
    
    # 3. 确定查询的时间范围
    # 使用带时区的datetime.now(timezone.utc)来获取当前UTC时间，然后移除时区信息，以进行“朴素”时间计算
    today = datetime.now(timezone.utc).replace(tzinfo=None)
    end_date = today - timedelta(days=1) # 查询的结束日期是昨天
    days_since_creation = (end_date - effective_start_date).days

    # 如果页面太新，可能还没有任何完整的日访问数据
    if days_since_creation <= 0:
        return {'error': 'Page is too new to have data'}
    
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
    encoded_title = urllib.parse.quote(article_title) # 对标题进行URL编码，处理特殊字符
    url = (
        f"{PAGEVIEWS_API_BASE}zh.wikipedia.org/all-access/user/{encoded_title}/"
        f"daily/{start_str}/{end_str}"
    )
    
    # 5. 发起请求并处理数据
    data = make_api_request(url)
    if not data:
        return {'error': 'Pageviews API request failed after retries'}

    # 计算总访问量和日均访问量
    total_views = sum(item['views'] for item in data.get('items', []))
    avg_daily_views = total_views / duration_days if duration_days > 0 else 0
    
    # 6. 返回包含所有统计信息的字典
    return {
        'total_views': total_views,
        'start_date': start_date.strftime('%Y-%m-%d'),
        'end_date': end_date.strftime('%Y-%m-%d'),
        'duration_days': duration_days,
        'avg_daily_views': avg_daily_views
    }

def rewrite_list_file(sorted_results: dict):
    """
    使用排序后的条目重写LIST.txt文件，同时保持原有的注释、空行和未处理的类别。

    Args:
        sorted_results (dict): 一个有序字典，键是类别名，值是按访问量排序后的条目列表。
    """
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
                new_lines.append(line) # 将类别标题行加入新内容
                
                # 如果这个类别是被我们处理和排序过的
                if current_category in sorted_results:
                    # 写入排序后的所有条目
                    for item_name in sorted_results[current_category]:
                        new_lines.append(f"{item_name}\n")
            # 如果该行不是类别标题
            else:
                # 判断这一行是不是一个需要被替换的旧条目
                is_old_item_from_sorted_category = (
                    current_category in sorted_results and
                    stripped_line != "" and                  # 排除空行
                    not stripped_line.startswith('#')      # 排除注释
                )
                
                if is_old_item_from_sorted_category:
                    # 如果是，就跳过它，因为它已经被新的排序列表取代了
                    continue
                else:
                    # 否则，保留这一行（它是空行、注释，或属于'new'等未处理类别）
                    new_lines.append(line)
        
        # 将构建好的新内容完全覆盖写入原始文件
        with open(LIST_FILE_PATH, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        
        print("--- LIST.txt 文件更新成功 ---")

    except IOError as e:
        print(f"严重错误：重写 LIST.txt 文件失败。错误: {e}")

def main():
    """脚本主入口函数，协调整个流程。"""
    print("--- 开始检查 LIST.txt 中各项的维基百科页面日访问频次 ---")
    
    # 1. 解析LIST.txt文件
    items_to_process = parse_list_file(LIST_FILE_PATH)
    if not items_to_process:
        print("列表文件为空或不存在，任务结束。")
        return

    # 2. 清理每个类别中的重复条目
    print("--- 正在清理输入列表中的重复项 ---")
    for category, items in items_to_process.items():
        if category in PROCESS_CATEGORIES:
            original_count = len(items)
            # 使用dict.fromkeys方法高效去重，并保持条目首次出现的顺序
            unique_items = list(dict.fromkeys(items))
            if len(unique_items) < original_count:
                print(f"  类别 '{category}': 发现了 {original_count - len(unique_items)} 个重复项，已清理。")
            items_to_process[category] = unique_items
    
    # 初始化用于存储结果的数据结构
    results_by_category = {cat: [] for cat in PROCESS_CATEGORIES if cat in items_to_process}
    log_entries = []
    
    # 3. 遍历所有需要处理的类别和条目，获取数据
    for category, items in items_to_process.items():
        # 跳过不在PROCESS_CATEGORIES中的类别（如'new'）
        if category not in PROCESS_CATEGORIES:
            continue

        print(f"\n--- 正在处理类别: {category} ---")
        for i, item in enumerate(items):
            print(f"[*] 正在查询 ({i+1}/{len(items)}): {item}")
            stats = get_pageviews_stats(item)
            
            # 处理成功的情况
            if stats and 'error' not in stats:
                result_str = (
                    f"  -> 结果: 时段 [{stats['start_date']} to {stats['end_date']}] ({stats['duration_days']}天), "
                    f"总访问量: {stats['total_views']:,}, "
                    f"日均访问: {stats['avg_daily_views']:.2f}"
                )
                log_entry = (
                    f"ITEM: {item} | "
                    f"PERIOD: {stats['start_date']} to {stats['end_date']} ({stats['duration_days']} days) | "
                    f"TOTAL: {stats['total_views']} | "
                    f"AVG_DAILY: {stats['avg_daily_views']:.2f}\n"
                )
                # 存储完整统计结果，而不仅仅是总浏览量
                results_by_category[category].append({'item': item, 'stats': stats})
            # 处理失败的情况
            else:
                error_msg = stats.get('error', '未知错误') if stats else '未知错误'
                result_str = f"  -> 失败: {error_msg}"
                log_entry = f"ITEM: {item} | STATUS: FAILED | REASON: {error_msg}\n"
                # 对于失败的条目，存储一个标记，以便排序和统计时能识别
                results_by_category[category].append({'item': item, 'stats': {'total_views': -1, 'avg_daily_views': 0}})

            print(result_str)
            log_entries.append(log_entry)

    # 4. 计算并输出每个类别的平均访问频次
    print("\n--- 各类别平均访问频次统计 ---")
    summary_log_entries = ["\n--- CATEGORY SUMMARY ---\n"]
    for category, results in results_by_category.items():
        # 筛选出该类别下所有成功获取到数据的条目
        successful_items = [res for res in results if res['stats']['total_views'] != -1]
        
        if not successful_items:
            # 如果该类别下没有成功获取数据的条目
            category_average = 0
            count = 0
        else:
            # 计算该类别下所有条目“日均访问量”的总和
            total_avg_views = sum(res['stats']['avg_daily_views'] for res in successful_items)
            count = len(successful_items)
            # 计算“日均访问量”的平均值
            category_average = total_avg_views / count

        summary_str = f"  类别 '{category}': {count}个有效条目, 平均每日访问频次: {category_average:.2f}"
        print(summary_str)
        summary_log_entries.append(summary_str.strip() + '\n')


    # 5. 对每个类别内部的条目按总访问量进行排序
    print("\n--- 所有项目查询完毕，正在排序... ---")
    # 使用OrderedDict确保在重写文件时能保持LIST.txt中类别的原始顺序
    sorted_results = OrderedDict()
    for category in items_to_process.keys():
        if category in results_by_category:
            # 使用lambda函数按每个条目的总访问量（'total_views'）进行降序排序
            sorted_items = sorted(results_by_category[category], key=lambda x: x['stats']['total_views'], reverse=True)
            # 提取排序后的条目名称列表
            sorted_results[category] = [res['item'] for res in sorted_items]

    # 6. 将所有结果写入日志文件
    try:
        # 获取当前本地时区的日期和时间
        run_timestamp = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        with open(LOG_FILE_PATH, 'a', encoding='utf-8') as log_file:
            log_file.write(f"\n--- 运行开始于: {run_timestamp} ---\n")
            log_file.writelines(log_entries) # 写入每个条目的详细日志
            log_file.writelines(summary_log_entries) # 写入类别总结
        print(f"--- 运行结果已追加到 {LOG_FILE_PATH} ---")
    except IOError as e:
        print(f"\n严重错误：无法写入日志文件 {LOG_FILE_PATH}。错误: {e}")

    # 7. 用排序后的结果重写LIST.txt文件
    rewrite_list_file(sorted_results)

    print("\n--- 全部任务完成 ---")


# 当该脚本被直接执行时，调用main()函数
if __name__ == "__main__":
    main()
