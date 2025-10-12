# scripts/generate_frontend_data.py

import os
import sys
import json
import shutil
from collections import defaultdict
import logging
import re

from config import (
    LIST_FILE_PATH, MASTER_GRAPH_PATH, CACHE_DIR,
    FRONTEND_DATA_DIR,
    NON_DIRECTED_LINK_TYPES, CORE_NETWORK_SIZE
)

logger = logging.getLogger(__name__)

FRONTEND_NODES_DIR = os.path.join(FRONTEND_DATA_DIR, 'nodes')
FRONTEND_INITIAL_PATH = os.path.join(FRONTEND_DATA_DIR, 'initial.json')
NAME_TO_ID_PATH = os.path.join(FRONTEND_DATA_DIR, 'name_to_id.json')
PAGEVIEWS_CACHE_PATH = os.path.join(CACHE_DIR, 'pageviews_cache.json')


def load_master_graph():
    """加载完整的主图谱文件。"""
    logger.info(f"正在加载主图谱文件: {os.path.basename(MASTER_GRAPH_PATH)}...")
    try:
        with open(MASTER_GRAPH_PATH, 'r', encoding='utf-8') as f:
            graph = json.load(f)
            return graph
    except FileNotFoundError:
        logger.critical(f"严重错误: 主图谱文件未找到于 '{MASTER_GRAPH_PATH}'")
        sys.exit(1)
    except json.JSONDecodeError:
        logger.critical(f"严重错误: 主图谱文件 '{MASTER_GRAPH_PATH}' 格式无效。")
        sys.exit(1)

def generate_name_to_id_map(master_graph):
    """从主图谱生成一个 名称 -> ID 的映射，用于前端全局搜索。"""
    logger.info(f"正在生成名称到ID的映射文件: {os.path.basename(NAME_TO_ID_PATH)}...")
    name_map = {}
    nodes = master_graph.get('nodes', [])
    for node in nodes:
        node_id = node.get('id')
        if not node_id:
            continue
        
        # 遍历 name 对象中的所有语言 ('zh-cn', 'en', etc.)
        for lang, names in node.get('name', {}).items():
            if isinstance(names, list):
                for name in names:
                    if name and name not in name_map:
                        name_map[name] = node_id
            
    try:
        with open(NAME_TO_ID_PATH, 'w', encoding='utf-8') as f:
            json.dump(name_map, f, ensure_ascii=False, indent=2)
        logger.info(f"成功将 {len(name_map)} 个名称映射写入文件。")
    except IOError as e:
        logger.error(f"严重错误: 无法写入名称映射文件 - {e}")


def select_important_nodes(master_graph):
    """ 根据 pageviews_cache.json 和 LIST.md 筛选出重要节点。"""
    logger.info(f"正在根据页面热度和LIST.md筛选至多 {CORE_NETWORK_SIZE} 个重要节点...")

    # 1. 加载页面热度缓存
    try:
        with open(PAGEVIEWS_CACHE_PATH, 'r', encoding='utf-8') as f:
            pageviews_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning(f"页面热度缓存 '{PAGEVIEWS_CACHE_PATH}' 不存在或无效。将无法按热度排序。")
        pageviews_cache = {}

    # 2. 从 LIST.md 读取有效候选实体
    valid_candidates = set()
    lang_pattern = re.compile(r'\((?P<lang>[a-z]{2})\)\s*')
    try:
        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
            current_category = None
            for line in f:
                line = line.strip()
                
                # 检查是否为类别标题
                if line.startswith('## '):
                    category_name = line[3:].strip().lower()
                    # 遇到 'new' 类别时，停止解析
                    if category_name == 'new':
                        break
                    current_category = category_name
                    continue
                
                # 跳过空行和注释
                if not line or line.startswith('//'):
                    continue

                # 添加实体到当前类别
                if current_category:
                    # 去除语言标签，只保留实体名称
                    match = lang_pattern.match(line)
                    item_name = line[match.end():].strip() if match else line
                    valid_candidates.add(item_name)
                    
    except FileNotFoundError:
        logger.warning("LIST.md 文件未找到。无法确定候选节点池。")

    # 3. 创建一个多语言的 名称 -> ID 的反向映射
    name_to_id_map = {}
    for node in master_graph.get('nodes', []):
        node_id = node.get('id')
        if not node_id: continue
        for lang, names in node.get('name', {}).items():
            if isinstance(names, list):
                for name in names:
                    if name not in name_to_id_map:
                        name_to_id_map[name] = node_id

    # 4. 结合热度数据对有效候选实体进行排序
    ranked_candidates = []
    for name in valid_candidates:
        avg_views = pageviews_cache.get(name, {}).get('avg_daily_views', -1)
        node_id = name_to_id_map.get(name)
        if node_id:
            ranked_candidates.append({'id': node_id, 'name': name, 'avg_views': avg_views})
    
    # 按浏览量降序排序
    ranked_candidates.sort(key=lambda x: x['avg_views'], reverse=True)
    
    # 5. 选取前 N 个节点的 ID
    important_node_ids = {item['id'] for item in ranked_candidates[:CORE_NETWORK_SIZE]}
    
    logger.info(f"筛选完成，共选出 {len(important_node_ids)} 个重要节点。")
    return important_node_ids

def generate_main_data_file(master_graph, important_node_ids):
    """生成只包含重要节点及其关系的 initial.json 文件。"""
    logger.info(f"正在生成前端主数据文件: {FRONTEND_INITIAL_PATH}...")
    
    important_nodes = [node for node in master_graph['nodes'] if node.get('id') in important_node_ids]
    important_relationships = [
        rel for rel in master_graph['relationships']
        if rel.get('source') in important_node_ids and rel.get('target') in important_node_ids
    ]
    
    frontend_graph = {
        "nodes": important_nodes,
        "relationships": important_relationships
    }
    
    try:
        os.makedirs(os.path.dirname(FRONTEND_INITIAL_PATH), exist_ok=True)
        with open(FRONTEND_INITIAL_PATH, 'w', encoding='utf-8') as f:
            json.dump(frontend_graph, f, ensure_ascii=False)
        logger.info(f"成功将 {len(important_nodes)} 个节点和 {len(important_relationships)} 条关系写入主文件。")
    except IOError as e:
        logger.error(f"严重错误: 无法写入前端主数据文件 - {e}")

def generate_simple_database(master_graph):
    """为所有节点生成简单数据库至 docs/data/nodes/。"""
    logger.info(f"正在生成简单数据库至: {FRONTEND_NODES_DIR}...")
    
    if os.path.exists(FRONTEND_NODES_DIR):
        shutil.rmtree(FRONTEND_NODES_DIR)
    os.makedirs(FRONTEND_NODES_DIR)
    
    rels_by_node = defaultdict(list)
    for rel in master_graph.get('relationships', []):
        source = rel.get('source')
        target = rel.get('target')
        if source and target:
            rels_by_node[source].append(rel)
            if source != target:
                rels_by_node[target].append(rel)

    count = 0
    total = len(master_graph.get('nodes', []))
    for node in master_graph.get('nodes', []):
        node_id = node.get('id')
        if not node_id:
            continue
        
        node_info = node.copy()
        # 过滤节点属性，只保留 period 或 lifetime
        if 'properties' in node_info and isinstance(node_info.get('properties'), dict):
            original_props = node_info['properties']
            filtered_props = {}
            if 'period' in original_props:
                filtered_props['period'] = original_props['period']
            if 'lifetime' in original_props:
                filtered_props['lifetime'] = original_props['lifetime']
            
            if filtered_props:
                node_info['properties'] = filtered_props
            else:
                del node_info['properties']
            
        simplified_rels = []
        for rel in rels_by_node.get(node_id, []):
            simple_rel = {
                'source': rel.get('source'),
                'target': rel.get('target'),
                'type': rel.get('type')
            }
            
            allowed_props = {'start_date', 'end_date'}
            original_props = rel.get('properties', {})
            
            # 在调用.items()前，确保 original_props 是一个字典
            if isinstance(original_props, dict):
                new_props = {k: v for k, v in original_props.items() if k in allowed_props}
                if new_props:
                    simple_rel['properties'] = new_props
            
            simplified_rels.append(simple_rel)
            
        output_data = {"node": node_info, "relationships": simplified_rels}
        
        try:
            safe_node_id = node_id.replace(":", "_")
            node_dir = os.path.join(FRONTEND_NODES_DIR, safe_node_id)
            os.makedirs(node_dir)
            with open(os.path.join(node_dir, 'node.json'), 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False)
            count += 1
        except (IOError, OSError) as e:
            logger.warning(f"无法为节点 {node_id} 创建文件 - {e}")

        if count % 100 == 0 or count == total:
            # 将进度条信息改为普通的日志条目
            logger.info(f"-> 已处理 {count}/{total} 个节点...")

    logger.info(f"成功为 {count} 个节点生成了数据文件。")

def main():
    """脚本主入口函数。"""
    logger.info("==================================================")
    logger.info("====== 启动前端数据生成与数据库构建脚本 ======")
    logger.info("==================================================")

    master_graph = load_master_graph()

    if not master_graph or 'nodes' not in master_graph:
        logger.critical("因无法加载主图谱或图谱无节点，脚本终止。")
        return

    important_node_ids = select_important_nodes(master_graph)
    generate_main_data_file(master_graph, important_node_ids)
    generate_simple_database(master_graph)
    generate_name_to_id_map(master_graph)

    logger.info("==================================================")
    logger.info("=============  所有任务执行完毕  =============")
    logger.info("==================================================")


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        stream=sys.stdout
    )

    main()
