# scripts/process_list.py

import os
import sys
import json
from datetime import datetime
import pytz
import re
import requests
import urllib.parse
from config import DATA_PATH

# --- 检查并导入所需模块 ---
try:
    from utils import get_simplified_wikitext
    from parse_gemini import parse_wikitext_with_llm
except ImportError:
    print("[!] 严重错误：无法导入 'utils' 或 'parse_gemini' 模块。", file=sys.stderr)
    print("    请确保 'process_list.py' 与 'utils.py' 和 'parse_gemini.py' 位于同一个目录下。", file=sys.stderr)
    sys.exit(1)

# --- 配置 ---
LIST_FILE_PATH = os.path.join(DATA_PATH, 'LIST.txt')

WIKI_BASE_URL = "https://zh.wikipedia.org/zh-cn/"
# 维基百科API地址，用于获取修订历史
WIKI_API_URL = "https://zh.wikipedia.org/w/api.php"

TIMEZONE = pytz.timezone('Asia/Shanghai')

def sanitize_filename(name: str) -> str:
    """移除文件名中不合法或不推荐的字符。"""
    return re.sub(r'[\\/*?:"<>|]', '_', name)

def get_latest_wiki_revision_time(item_name: str) -> datetime | None:
    """
    通过维基百科API获取页面的最新修订时间（UTC）。
    """
    params = {
        "action": "query",
        "prop": "revisions",
        "titles": item_name,
        "rvlimit": "1",
        "rvprop": "timestamp",
        "format": "json",
        "formatversion": "2" # 使用新版格式，简化解析
    }
    headers = {
        'User-Agent': 'ChineseEliteExplorer/1.0 (https://github.com/anonym-g/Chinese-Elite)'
    }
    
    try:
        response = requests.get(WIKI_API_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # 解析返回的JSON
        page = data["query"]["pages"][0]
        if "revisions" in page and page["revisions"]:
            timestamp_str = page["revisions"][0]["timestamp"]
            # 维基百科API返回的是ISO 8601格式的UTC时间
            # fromisoformat可以直接解析，并将其设为UTC时区
            return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            
    except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"[!] 警告：获取 '{item_name}' 的维基修订历史失败 - {e}", file=sys.stderr)
        return None

def get_last_local_process_time(item_name: str, category: str) -> datetime | None:
    """
    检查本地文件，获取该条目最后一次处理的时间（GMT+8）。
    """
    safe_item_name = sanitize_filename(item_name)
    item_dir = os.path.join(DATA_PATH, category, safe_item_name)

    if not os.path.isdir(item_dir):
        return None # 目录不存在，说明从未处理过

    latest_time = None
    
    # 正则表达式匹配文件名中的时间戳
    timestamp_regex = re.compile(r'_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.json$')

    for filename in os.listdir(item_dir):
        match = timestamp_regex.search(filename)
        if match:
            try:
                timestamp_str = match.group(1)
                # 将字符串解析为无时区的datetime对象
                dt_object = datetime.strptime(timestamp_str, '%Y-%m-%d-%H-%M-%S')
                # 为其附加GMT+8时区信息
                localized_dt = TIMEZONE.localize(dt_object)
                
                if latest_time is None or localized_dt > latest_time:
                    latest_time = localized_dt
            except ValueError:
                continue # 时间戳格式不正确，跳过
    
    return latest_time

def parse_list_file(file_path: str) -> dict[str, list]:
    """解析 LIST.txt 文件，返回一个按类别组织的字典。"""
    if not os.path.exists(file_path):
        print(f"[!] 错误：列表文件不存在于 '{file_path}'", file=sys.stderr)
        return {}
    print(f"[*] 正在读取列表文件: {file_path}")
    categorized_items = {}
    current_category = None
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            if line in ['person', 'organization', 'movement', 'event', 'document', 'location']:
                current_category = line
                if current_category not in categorized_items:
                    categorized_items[current_category] = []
                print(f"[*] 发现类别: '{current_category}'")
            elif current_category:
                categorized_items[current_category].append(line)
                print(f"    - 添加条目 '{line}' 到 '{current_category}'")
            else:
                 print(f"[!] 警告：找到条目 '{line}' 但未发现其所属类别，已跳过。")
    return categorized_items

def process_item(item_name: str, category: str):
    """对单个条目执行完整的处理流程：检查更新、获取、解析、保存。"""
    print(f"\n--- 开始处理 '{item_name}' (类别: {category}) ---")

    # --- 修改部分：执行更新检查 ---
    last_local_time = get_last_local_process_time(item_name, category)
    latest_wiki_time = get_latest_wiki_revision_time(item_name)
    
    # 只有在两边时间都成功获取时才进行比较
    if last_local_time and latest_wiki_time:
        print(f"    - 本地最新版本: {last_local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        print(f"    - Wiki 最新修订: {latest_wiki_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        
        if latest_wiki_time <= last_local_time:
            print(f"[*] '{item_name}' 的本地数据已是最新，无需处理。跳过。")
            return # 结束当前条目的处理
        else:
            print(f"[*] 检测到维基页面有更新，将重新提取。")
    elif last_local_time is None:
        print("[*] 未在本地发现历史版本，将执行首次提取。")
    # -----------------------------

    # 1. 补全为wiki链接 (使用 urllib.parse.quote 确保URL安全)
    wiki_url = f"{WIKI_BASE_URL}{urllib.parse.quote(item_name)}"
    
    # 2. 调用 get_simplified_wikitext 获取源码
    wikitext = get_simplified_wikitext(wiki_url)
    if not wikitext:
        print(f"[!] 失败：未能获取 '{item_name}' 的Wikitext源码，跳过此条目。", file=sys.stderr)
        return

    # 3. 调用 parse_wikitext_with_llm 提取信息
    structured_data = parse_wikitext_with_llm(wikitext)
    if not structured_data:
        print(f"[!] 失败：LLM未能解析 '{item_name}' 的Wikitext，跳过此条目。", file=sys.stderr)
        return
        
    # 4. 按门类选择保存路径，并以处理时间命名
    try:
        safe_item_name = sanitize_filename(item_name)
        output_dir = os.path.join(DATA_PATH, category, safe_item_name)
        os.makedirs(output_dir, exist_ok=True)
        now_gmt8 = datetime.now(TIMEZONE)
        timestamp = now_gmt8.strftime('%Y-%m-%d-%H-%M-%S')
        file_name = f"{safe_item_name}_{timestamp}.json"
        output_path = os.path.join(output_dir, file_name)

        # 5. 保存JSON文件
        print(f"[*] 正在将结果保存至: {output_path}")
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(structured_data, f, indent=2, ensure_ascii=False)
        
        print(f"[+] 成功：'{item_name}' 的处理结果已保存。")

    except Exception as e:
        print(f"[!] 严重错误：在保存文件时发生异常 - {e}", file=sys.stderr)


def main():
    """脚本主入口函数。"""
    items_to_process = parse_list_file(LIST_FILE_PATH)
    if not items_to_process:
        print("\n[*] 列表文件为空或不存在，任务结束。")
        return
    total_items = sum(len(v) for v in items_to_process.values())
    print(f"\n[*] 列表文件解析完成，共发现 {total_items} 个条目待处理。")
    processed_count = 0
    for category, items in items_to_process.items():
        for item in items:
            processed_count += 1
            print(f"\n[{processed_count}/{total_items}] ====================================")
            process_item(item, category)
    print("\n\n[*] 所有条目处理完毕。")


if __name__ == '__main__':
    main()
