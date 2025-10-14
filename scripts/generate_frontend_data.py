# scripts/generate_frontend_data.py

import os
import sys
import json
import shutil
from collections import defaultdict
import logging
import re

# 使用相对路径导入
from .config import LIST_FILE_PATH, MASTER_GRAPH_PATH, CACHE_DIR, FRONTEND_DATA_DIR, CORE_NETWORK_SIZE
from .services import graph_io

logger = logging.getLogger(__name__)

class FrontendDataGenerator:
    """
    负责为前端可视化界面生成所有必需的数据文件。
    """
    def __init__(self):
        self.frontend_nodes_dir = os.path.join(FRONTEND_DATA_DIR, 'nodes')
        self.frontend_initial_path = os.path.join(FRONTEND_DATA_DIR, 'initial.json')
        self.name_to_id_path = os.path.join(FRONTEND_DATA_DIR, 'name_to_id.json')
        self.pageviews_cache_path = os.path.join(CACHE_DIR, 'pageviews_cache.json')
    
    def _generate_name_to_id_map(self, master_graph):
        """生成名称 -> ID 的映射，用于前端全局搜索。"""
        logger.info(f"正在生成名称到ID的映射文件...")
        name_map = {}
        for node in master_graph.get('nodes', []):
            if node_id := node.get('id'):
                for lang, names in node.get('name', {}).items():
                    if isinstance(names, list):
                        for name in names:
                            if name and name not in name_map:
                                name_map[name] = node_id
        try:
            with open(self.name_to_id_path, 'w', encoding='utf-8') as f:
                json.dump(name_map, f, ensure_ascii=False) # 紧凑格式
            logger.info(f"成功将 {len(name_map)} 个名称映射写入文件。")
        except IOError as e:
            logger.error(f"严重错误: 无法写入名称映射文件 - {e}")

    def _select_important_nodes(self, master_graph):
        """根据页面热度和LIST.md筛选出重要节点。"""
        logger.info(f"正在筛选至多 {CORE_NETWORK_SIZE} 个重要节点...")
        try:
            with open(self.pageviews_cache_path, 'r', encoding='utf-8') as f: pageviews_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pageviews_cache = {}

        valid_candidates = set()
        try:
            with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('## new'): break
                    if not line.strip() or line.strip().startswith(('##', '//')): continue
                    item_name = re.sub(r'\([a-z]{2}\)\s*', '', line.strip())
                    valid_candidates.add(item_name)
        except FileNotFoundError: pass

        name_to_id_map = {name: node['id'] for node in master_graph.get('nodes', []) if (node_id := node.get('id')) for lang, names in node.get('name', {}).items() for name in names}
        
        ranked_candidates = [{'id': name_to_id_map.get(name), 'avg_views': pageviews_cache.get(name, {}).get('avg_daily_views', -1)} for name in valid_candidates if name_to_id_map.get(name)]
        ranked_candidates.sort(key=lambda x: x['avg_views'], reverse=True)
        
        important_node_ids = {item['id'] for item in ranked_candidates[:CORE_NETWORK_SIZE]}
        logger.info(f"筛选完成，共选出 {len(important_node_ids)} 个重要节点。")
        return important_node_ids

    def _generate_main_data_file(self, master_graph, important_node_ids):
        """生成只包含重要节点及其关系的 initial.json 文件。"""
        logger.info(f"正在生成前端主数据文件...")
        important_nodes = [n for n in master_graph['nodes'] if n.get('id') in important_node_ids]
        important_rels = [r for r in master_graph['relationships'] if r.get('source') in important_node_ids and r.get('target') in important_node_ids]
        
        try:
            os.makedirs(os.path.dirname(self.frontend_initial_path), exist_ok=True)
            with open(self.frontend_initial_path, 'w', encoding='utf-8') as f:
                json.dump({"nodes": important_nodes, "relationships": important_rels}, f, ensure_ascii=False)
            logger.info(f"成功将 {len(important_nodes)} 个节点和 {len(important_rels)} 条关系写入主文件。")
        except IOError as e:
            logger.error(f"严重错误: 无法写入前端主数据文件 - {e}")

    def _generate_simple_database(self, master_graph):
        """为所有节点生成简单数据库。"""
        logger.info(f"正在生成简单数据库...")
        if os.path.exists(self.frontend_nodes_dir): shutil.rmtree(self.frontend_nodes_dir)
        os.makedirs(self.frontend_nodes_dir)
        
        rels_by_node = defaultdict(list)
        for rel in master_graph.get('relationships', []):
            if (source := rel.get('source')) and (target := rel.get('target')):
                rels_by_node[source].append(rel)
                if source != target: rels_by_node[target].append(rel)

        nodes = master_graph.get('nodes', [])
        for i, node in enumerate(nodes):
            if not (node_id := node.get('id')): continue
            
            node_info = node.copy()
            if isinstance(props := node_info.get('properties'), dict):
                node_info['properties'] = {k: props[k] for k in ['period', 'lifetime'] if k in props}
                if not node_info['properties']: del node_info['properties']

            simplified_rels = []
            for rel in rels_by_node.get(node_id, []):
                simple_rel = {'source': rel.get('source'), 'target': rel.get('target'), 'type': rel.get('type')}
                if isinstance(props := rel.get('properties'), dict) and (new_props := {k: props[k] for k in ['start_date', 'end_date'] if k in props}):
                    simple_rel['properties'] = new_props
                simplified_rels.append(simple_rel)

            try:
                node_dir = os.path.join(self.frontend_nodes_dir, node_id.replace(":", "_"))
                os.makedirs(node_dir)
                with open(os.path.join(node_dir, 'node.json'), 'w', encoding='utf-8') as f:
                    json.dump({"node": node_info, "relationships": simplified_rels}, f, ensure_ascii=False)
            except (IOError, OSError) as e:
                logger.warning(f"无法为节点 {node_id} 创建文件 - {e}")
            if (i + 1) % 500 == 0: logger.info(f"-> 已处理 {i+1}/{len(nodes)} 个节点...")
        logger.info(f"成功为 {len(nodes)} 个节点生成了数据文件。")

    def run(self):
        """脚本主入口函数。"""
        logger.info("====== 启动前端数据生成脚本 ======")
        master_graph = graph_io.load_master_graph(MASTER_GRAPH_PATH)
        if not master_graph or 'nodes' not in master_graph:
            logger.critical("主图谱加载失败或无节点，脚本终止。")
            return
            
        important_node_ids = self._select_important_nodes(master_graph)
        self._generate_main_data_file(master_graph, important_node_ids)
        self._generate_simple_database(master_graph)
        self._generate_name_to_id_map(master_graph)
        logger.info("=============  前端数据生成完毕  =============")
