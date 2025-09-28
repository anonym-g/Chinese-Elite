# scripts/clean_data.py

import os
import json
import requests
import urllib.parse
from datetime import datetime
import re
from opencc import OpenCC

# --- 配置 ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SOURCE_FILE_PATH = os.path.join(PROJECT_ROOT, 'data', 'consolidated_graph.json')
CLEAN_BASE_DIR = os.path.join(PROJECT_ROOT, 'data_to_be_cleaned')

# --- 缓存配置 ---
CACHE_DIR = os.path.join(PROJECT_ROOT, '.cache')
CACHE_FILE_PATH = os.path.join(CACHE_DIR, 'wiki_link_status_cache.json')

HEADERS = {
    'User-Agent': 'ChineseEliteExplorer/1.0 (Data Cleaning Script)'
}

# --- 创建OpenCC实例 ---
# 't2s.json' 是从繁体(Traditional)到简体(Simplified)的标准配置
cc = OpenCC('t2s')

def load_cache():
    """从.cache目录加载缓存文件，如果不存在则返回空字典。"""
    if not os.path.exists(CACHE_FILE_PATH):
        return {}
    try:
        with open(CACHE_FILE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"[!] 警告：无法读取或解析缓存文件 {CACHE_FILE_PATH} - {e}")
        return {}

def save_cache(cache_data):
    """将更新后的缓存数据保存到.cache目录。"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"[!] 警告：无法写入缓存文件 {CACHE_FILE_PATH} - {e}")


def check_wiki_link(node_id: str) -> tuple[str, str | None]:
    """
    检查维基百科页面的状态，并返回状态类型和详细信息。

    Returns:
        一个元组 (status, detail)，其中 status 的可能值包括:
        - "OK": 链接有效
        - "SIMP_TRAD_REDIRECT": 简繁重定向，视为有效
        - "REDIRECT": 实质性重定向, detail 为新页面标题
        - "NO_PAGE": 页面不存在
        - "DISAMBIG": 消歧义页
        - "ERROR": 其他错误
    """
    try:
        encoded_id = urllib.parse.quote(node_id.replace(" ", "_"))
        url = f"https://zh.wikipedia.org/w/index.php?title={encoded_id}&action=raw"
        response = requests.get(url, headers=HEADERS, timeout=15)

        if response.status_code == 404:
            return "NO_PAGE", None
        
        response.raise_for_status()
        content = response.text.strip()
        
        if not content:
            return "NO_PAGE", None

        normalized_content = content.lower().lstrip()
        if (normalized_content.startswith("#redirect") or 
            normalized_content.startswith("#重定向")):
            match = re.search(r'\[\[(.*?)\]\]', content)
            if match:
                redirect_target = match.group(1).strip().split('#')[0]
                
                # 1. 将重定向目标转换为简体中文
                simplified_target = cc.convert(redirect_target)

                # 2. 将转换后的简体目标与node_id进行比对 (统一处理空格和大小写)
                norm_simplified_target = simplified_target.replace('_', ' ').lower()
                norm_node_id = node_id.replace('_', ' ').lower()

                if norm_simplified_target == norm_node_id:
                    # 如果一致，说明是简繁重定向，返回一个特殊状态
                    return "SIMP_TRAD_REDIRECT", None
                else:
                    # 如果不一致，说明是真正的重定向
                    return "REDIRECT", redirect_target
                # --- 逻辑修改结束 ---
            else:
                return "ERROR", "Malformed redirect"

        if "{{disambig" in normalized_content or "{{hndis" in normalized_content:
            return "DISAMBIG", None

    except requests.exceptions.RequestException as e:
        print(f"     - 网络或HTTP错误: {e}")
        return "ERROR", str(e)
        
    return "OK", None

def main():
    """主执行函数"""
    print(f"[*] 正在加载数据文件: {SOURCE_FILE_PATH}")
    if not os.path.exists(SOURCE_FILE_PATH):
        print(f"[!] 错误: 源文件不存在: {SOURCE_FILE_PATH}")
        return

    with open(SOURCE_FILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    nodes = data.get('nodes', [])
    relationships = data.get('relationships', [])
    
    link_cache = load_cache()
    print(f"[*] 已加载 {len(link_cache)} 条缓存的链接状态。")
    
    print(f"[*] 共加载 {len(nodes)} 个节点，{len(relationships)} 个关系。")
    print("\n[*] 开始检查所有节点的维基百科链接状态...")

    redirect_map = {}
    no_page_ids = set()
    disambig_ids = set()
    error_ids = set()
    
    cache_updated = False

    for i, node in enumerate(nodes):
        node_id = node.get('id')
        if not node_id:
            continue
        
        if node.get('properties', {}).get('verified_node', False) is True:
            print(f"  ({i+1}/{len(nodes)}) 正在检查: '{node_id}'... [已验证, 跳过]")
            continue

        print(f"  ({i+1}/{len(nodes)}) 正在检查: '{node_id}'...", end='', flush=True)
        
        # --- 处理缓存命中和未命中的情况 ---
        is_cached = node_id in link_cache

        if is_cached:
            cached_entry = link_cache[node_id]
            status = cached_entry['status']
            detail = cached_entry.get('detail')
            print(f" [缓存: {status}]")
        else:
            status, detail = check_wiki_link(node_id)
            link_cache[node_id] = {'status': status}
            if detail:
                link_cache[node_id]['detail'] = detail
            cache_updated = True

        # --- 根据状态分类，并处理新的SIMP_TRAD_REDIRECT状态 ---
        if status == "OK":
            if not is_cached: print(" OK")
        elif status == "SIMP_TRAD_REDIRECT":
            if not is_cached: print(" -> 简繁重定向，跳过")
        elif status == "NO_PAGE":
            no_page_ids.add(node_id)
            if not is_cached: print(" -> 页面不存在 (404)")
        elif status == "REDIRECT":
            redirect_map[node_id] = detail
            if not is_cached: print(f" -> 发现重定向: '{detail}'")
        elif status == "DISAMBIG":
            disambig_ids.add(node_id)
            if not is_cached: print(" -> 发现消歧义页")
        elif status == "ERROR":
            error_ids.add(node_id)
            if not is_cached: print(f" -> 检查时发生错误: {detail}")

    all_bad_ids = set(redirect_map.keys()) | no_page_ids | disambig_ids | error_ids
    if not all_bad_ids:
        print("\n[+] 检查完成，未发现任何需要清理的节点ID。文件是干净的。")
        if cache_updated:
            print("[*] 正在更新链接状态缓存...")
            save_cache(link_cache)
        return

    print(f"\n[*] 检查完成，共发现 {len(all_bad_ids)} 个需要清理的节点ID。")
    print("[*] 正在按类别分离数据...")

    cleaned_data = {'nodes': [], 'relationships': []}
    redirect_data = {'nodes': [], 'relationships': [], 'redirect_map': redirect_map}
    no_page_data = {'nodes': [], 'relationships': []}
    disambig_data = {'nodes': [], 'relationships': []}
    no_page_data['nodes_with_errors'] = []

    for node in nodes:
        node_id = node.get('id')
        if node_id in redirect_map:
            redirect_data['nodes'].append(node)
        elif node_id in no_page_ids:
            no_page_data['nodes'].append(node)
        elif node_id in error_ids:
             no_page_data['nodes_with_errors'].append(node)
        elif node_id in disambig_ids:
            disambig_data['nodes'].append(node)
        else:
            cleaned_data['nodes'].append(node)
            
    for rel in relationships:
        source_id = rel.get('source')
        target_id = rel.get('target')
        if source_id not in all_bad_ids and target_id not in all_bad_ids:
            cleaned_data['relationships'].append(rel)
            continue
        
        if source_id in redirect_map or target_id in redirect_map:
            redirect_data['relationships'].append(rel)
        elif source_id in no_page_ids or target_id in no_page_ids or source_id in error_ids or target_id in error_ids:
            no_page_data['relationships'].append(rel)
        elif source_id in disambig_ids or target_id in disambig_ids:
            disambig_data['relationships'].append(rel)

    timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    output_dir = os.path.join(CLEAN_BASE_DIR, timestamp)
    os.makedirs(output_dir, exist_ok=True)
    print(f"\n[*] 正在创建输出目录: {output_dir}")

    files_to_save = {
        f'redirect_{timestamp}.json': redirect_data,
        f'no_page_{timestamp}.json': no_page_data,
        f'disambig_{timestamp}.json': disambig_data
    }

    for filename, data_content in files_to_save.items():
        if (data_content.get('nodes') or 
            data_content.get('relationships') or 
            data_content.get('nodes_with_errors')):
            filepath = os.path.join(output_dir, filename)
            print(f"  - 正在保存: {filename}")
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data_content, f, indent=2, ensure_ascii=False)
        
    print(f"\n[*] 正在用干净的数据覆盖原始文件: {SOURCE_FILE_PATH}")
    with open(SOURCE_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, indent=2, ensure_ascii=False)
        
    if cache_updated:
        print("[*] 正在更新链接状态缓存...")
        save_cache(link_cache)
        
    print("\n[+] 清理完成！")

if __name__ == '__main__':
    main()
