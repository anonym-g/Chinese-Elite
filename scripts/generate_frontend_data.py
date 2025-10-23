# scripts/generate_frontend_data.py

import os
import sys
import json
import shutil
from collections import defaultdict
import logging
import re
import random

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

    def _calculate_quotas(self, total_size, items_by_category, total_entities):
        """使用最大余数法为给定总规模分配各类别名额。"""
        quotas_info = []
        for category, names in items_by_category.items():
            if not names: continue
            proportion = len(names) / total_entities
            natural_quota = proportion * total_size
            quotas_info.append({
                'name': category, 'natural_quota': natural_quota,
                'final_quota': int(natural_quota), 'remainder': natural_quota - int(natural_quota)
            })

        allocated_sum = sum(cat['final_quota'] for cat in quotas_info)
        remaining_slots = total_size - allocated_sum
        quotas_info.sort(key=lambda x: x['remainder'], reverse=True)
        
        for i in range(remaining_slots):
            if quotas_info:
                quotas_info[i % len(quotas_info)]['final_quota'] += 1
        
        return {cat['name']: cat['final_quota'] for cat in quotas_info}

    def _select_important_nodes(self, master_graph):
        """
        根据页面热度和类别比例筛选节点，并引入少量随机节点。
        """
        RANDOM_NODE_RATIO = 0.05
        random_node_size = round(CORE_NETWORK_SIZE * RANDOM_NODE_RATIO)
        logger.info(f"正在筛选 {CORE_NETWORK_SIZE} 个热度节点和 {random_node_size} 个随机节点...")

        # --- 步骤 1: 加载数据与解析 LIST.md ---
        try:
            with open(self.pageviews_cache_path, 'r', encoding='utf-8') as f: pageviews_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError): pageviews_cache = {}; logger.warning("页面热度缓存文件缺失。")

        items_by_category = defaultdict(list)
        total_entities = 0
        current_category = None
        try:
            with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line.startswith('## new'): break
                    if stripped_line.startswith('## '): current_category = stripped_line[3:].strip().lower(); continue
                    if current_category and stripped_line and not stripped_line.startswith(('//')):
                        item_name = re.sub(r'\([a-z]{2}\)\s*', '', stripped_line)
                        items_by_category[current_category].append(item_name)
                        total_entities += 1
        except FileNotFoundError: logger.error("LIST.md 文件未找到。"); return set()
        if total_entities == 0: logger.warning("LIST.md 中无有效实体。"); return set()
        
        # --- 步骤 2: 计算名额 & 构建各类候选池 ---
        # 名额
        heat_quotas = self._calculate_quotas(CORE_NETWORK_SIZE, items_by_category, total_entities)
        random_quotas = self._calculate_quotas(random_node_size, items_by_category, total_entities)
        
        name_to_id_map = {name: node['id'] for node in master_graph.get('nodes', [])
                          if 'id' in node for lang, names in node.get('name', {}).items() for name in names}

        # 各类候选池
        all_nodes_by_type = defaultdict(list)
        for node in master_graph.get('nodes', []):
            primary_name = next((names[0] for names in node.get('name', {}).values() if names), None)
            if primary_name:
                avg_views = pageviews_cache.get(primary_name, {}).get('avg_daily_views', -1)
                all_nodes_by_type[node.get('type')].append({'id': node['id'], 'avg_views': avg_views})
        for node_type in all_nodes_by_type:
            all_nodes_by_type[node_type].sort(key=lambda x: x['avg_views'], reverse=True)

        # 构建基于 LIST.md 的候选池
        list_md_candidates_by_category = defaultdict(list)
        for category, names in items_by_category.items():
            unique_nodes_in_category = {}
            for name in names:
                node_id = name_to_id_map.get(name)
                if node_id:
                    avg_views = pageviews_cache.get(name, {}).get('avg_daily_views', -1)
                    if node_id not in unique_nodes_in_category or avg_views > unique_nodes_in_category[node_id]:
                        unique_nodes_in_category[node_id] = avg_views
            
            candidates = [{'id': node_id, 'avg_views': views} for node_id, views in unique_nodes_in_category.items()]
            candidates.sort(key=lambda x: x['avg_views'], reverse=True)
            list_md_candidates_by_category[category] = candidates

        # --- 步骤 3: 热度筛选，包含类别内递补 ---
        important_node_ids = set()
        logger.info("\n--- 第一阶段: 按热度筛选 (含类别内递补) ---")
        for category, quota in heat_quotas.items():
            if quota == 0: continue
            
            count_before = len(important_node_ids)
            
            # 1. 从优先候选池中选取
            priority_candidates = list_md_candidates_by_category.get(category, [])
            for item in priority_candidates:
                if len(important_node_ids) >= CORE_NETWORK_SIZE: break
                if len(important_node_ids) - count_before >= quota: break
                important_node_ids.add(item['id'])
            
            # 2. 检查有缺口，并从候选池中递补
            shortfall = quota - (len(important_node_ids) - count_before)
            if shortfall > 0:
                # 过滤
                replenishment_pool = [item for item in all_nodes_by_type.get(category.capitalize(), []) if item['id'] not in important_node_ids]
                for item in replenishment_pool:
                    if len(important_node_ids) >= CORE_NETWORK_SIZE: break
                    if len(important_node_ids) - count_before >= quota: break
                    important_node_ids.add(item['id'])

            actually_added = len(important_node_ids) - count_before
            logger.info(f"类别 '{category}': 成功选取 {actually_added} / {quota} 个热度节点。")

        # --- 步骤 4: 随机筛选 ---
        random_node_ids = set()
        logger.info("\n--- 第二阶段: 按比例随机筛选 ---")
        for category, quota in random_quotas.items():
            if quota == 0: continue
            
            # 过滤
            pool = [item for item in all_nodes_by_type.get(category.capitalize(), []) if item['id'] not in important_node_ids]
            
            if not pool:
                logger.warning(f"类别 '{category}': 无可用节点进行随机选取。")
                continue

            count_before = len(random_node_ids)
            random.shuffle(pool)
            
            for item in pool:
                if len(random_node_ids) - count_before >= quota: break
                random_node_ids.add(item['id'])
            
            actually_added = len(random_node_ids) - count_before
            logger.info(f"类别 '{category}': 成功选取 {actually_added} / {quota} 个随机节点。")
        
        # --- 步骤 5: 合并与返回 ---
        final_node_ids = important_node_ids.union(random_node_ids)
        logger.info(f"\n筛选完成: {len(important_node_ids)} 个热度节点 + {len(random_node_ids)} 个随机节点 = {len(final_node_ids)} 个总节点。")
        return final_node_ids

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
