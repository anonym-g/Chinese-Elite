# scripts/clean_data.py

import os
import json
import sys
import random
import time
from datetime import datetime, timedelta, timezone
from collections import deque
import logging

# 使用相对路径导入
from .config import (
    MASTER_GRAPH_PATH, FALSE_RELATIONS_CACHE_PATH, 
    REL_CLEAN_NUM, 
    REL_CLEAN_SKIP_DAYS, REL_CLEAN_PROB_START_DAYS, REL_CLEAN_PROB_END_DAYS, 
    REL_CLEAN_PROB_START_VALUE, REL_CLEAN_PROB_END_VALUE
)
from .clients.wikipedia_client import WikipediaClient
from .services.llm_service import LLMService
from .services import graph_io

logger = logging.getLogger(__name__)

class GraphCleaner:
    """
    一个用于对主图谱进行数据清洗的工具。
    """
    def __init__(self, master_graph_path: str, wiki_client: WikipediaClient, llm_service: LLMService):
        self.master_graph_path = master_graph_path
        self.wiki_client = wiki_client
        self.llm_service = llm_service
        self.false_relations_cache = self._load_json_cache(FALSE_RELATIONS_CACHE_PATH)
        self.cache_updated = False

    def _load_json_cache(self, path: str) -> dict:
        """通用JSON缓存加载函数。"""
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                logger.info(f"成功加载缓存文件: {os.path.basename(path)}")
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            logger.warning(f"无法读取或解析缓存文件 {path}，将返回空字典。")
            return {}

    def _save_caches(self):
        """统一保存所有已更新的缓存。"""
        if self.cache_updated:
            try:
                os.makedirs(os.path.dirname(FALSE_RELATIONS_CACHE_PATH), exist_ok=True)
                with open(FALSE_RELATIONS_CACHE_PATH, 'w', encoding='utf-8') as f:
                    json.dump(self.false_relations_cache, f, indent=2, ensure_ascii=False)
                logger.info(f"关系清洗缓存已成功更新到磁盘。")
                self.cache_updated = False
            except IOError:
                logger.error(f"严重错误: 无法写入关系清洗缓存文件 {FALSE_RELATIONS_CACHE_PATH}")
        # 调用其他需要保存的缓存
        self.wiki_client.save_caches()

    def _clean_stale_cache(self):
        """清理超过一个月的BAIDU/CDT链接状态缓存。"""
        logger.info("\n--- 步骤 1/3: 清理过期的链接状态缓存 ---")
        one_month_ago = datetime.now() - timedelta(days=30)
        pruned_cache = {}
        cleaned_count = 0
        
        for key, value in self.wiki_client.link_cache.items():
            if value.get('status') in ["BAIDU", "CDT"]:
                try:
                    timestamp = datetime.fromisoformat(value.get('timestamp'))
                    if timestamp > one_month_ago:
                        pruned_cache[key] = value
                    else:
                        cleaned_count += 1
                except (ValueError, TypeError):
                    cleaned_count += 1
            else:
                pruned_cache[key] = value
        
        if cleaned_count > 0:
            self.wiki_client.link_cache = pruned_cache
            self.wiki_client.link_cache_updated = True
            logger.info(f"清理了 {cleaned_count} 个过期的 BAIDU/CDT 缓存条目。")
        else:
            logger.info("未发现过期的缓存条目。")
            
    def _resolve_temporary_nodes(self, nodes: list, relationships: list) -> tuple[list, list]:
        """遍历所有节点，尝试将临时ID升级为Q-Code。"""
        logger.info("\n--- 步骤 2/3: 尝试升级临时ID节点 ---")
        nodes_map = {n['id']: n for n in nodes}
        id_remap, nodes_to_delete = {}, set()
        
        temp_nodes = [n for n in nodes if n['id'].startswith(('BAIDU:', 'CDT:'))]
        logger.info(f"发现 {len(temp_nodes)} 个使用临时ID的节点待检查。")

        for node in temp_nodes:
            old_id = node['id']
            original_name = old_id.split(':', 1)[-1]
            qcode = self.wiki_client.get_qcode(original_name)
            if qcode:
                logger.info(f"  - 成功升级: '{original_name}' -> {qcode}")
                id_remap[old_id] = qcode
                nodes_to_delete.add(old_id)
                
                if qcode in nodes_map and nodes_map[qcode] is not node:
                    logger.info(f"    -> [合并] Q-Code {qcode} 已存在，正在合并属性...")
                    existing_node, temp_props = nodes_map[qcode], node.get('properties', {})
                    if temp_props:
                        existing_props = existing_node.setdefault('properties', {})
                        for key, val in temp_props.items():
                            if key in existing_props and isinstance(existing_props.get(key), dict) and isinstance(val, dict):
                                existing_props[key].update(val)
                            else:
                                existing_props[key] = val
                else:
                    node['id'] = qcode
                    nodes_map[qcode] = node
        
        final_nodes = [n for n in nodes if n['id'] not in nodes_to_delete]
        if id_remap:
            for rel in relationships:
                if rel.get('source') in id_remap: rel['source'] = id_remap[rel['source']]
                if rel.get('target') in id_remap: rel['target'] = id_remap[rel['target']]
        
        logger.info(f"成功升级了 {len(id_remap)} 个节点。")
        return final_nodes, relationships
    
    def _get_canonical_rel_key(self, rel: dict) -> str | None:
        """为关系生成一个规范化的字符串键。"""
        source, target, rel_type = rel.get('source'), rel.get('target'), rel.get('type')
        if not (source and target and rel_type): return None
        return f"{source}-{target}-{rel_type}"
    
    def _get_primary_name(self, node: dict) -> str:
        """
        智能获取节点的主名称，用于向LLM展示。
        优先级: zh-cn -> en -> 其他语言 -> ID
        """
        node_id = node.get('id', '')
        name_obj = node.get('name')
        if not isinstance(name_obj, dict):
            return node_id
        
        # 1. 优先使用中文名
        zh_names = name_obj.get('zh-cn')
        if isinstance(zh_names, list) and zh_names:
            return zh_names[0]
            
        # 2. 其次使用英文名
        en_names = name_obj.get('en')
        if isinstance(en_names, list) and en_names:
            return en_names[0]
            
        # 3. 再次使用任何其他语言的名称
        for lang, names in name_obj.items():
            if isinstance(names, list) and names:
                return names[0]
                
        # 4. 最后回退到ID
        return node_id

    def _clean_individual_relationships(self, nodes: list, relationships: list) -> list:
        """对随机抽样的单条关系进行LLM审查和清理。"""
        logger.info("\n--- 步骤 3/3: 清理单条的错误/低质量关系 ---")
        
        id_to_name_map = {n['id']: self._get_primary_name(n) for n in nodes}
        now = datetime.now(timezone.utc)
        
        # 1. 筛选出可供抽样的候选关系
        candidates = []
        for i, rel in enumerate(relationships):
            rel['temp_id'] = i # 为关系添加临时唯一ID
            key = self._get_canonical_rel_key(rel)
            if not key: continue

            if key in self.false_relations_cache:
                try:
                    cache_time = datetime.fromisoformat(self.false_relations_cache[key]['timestamp'])
                    age_days = (now - cache_time).days

                    if age_days <= REL_CLEAN_SKIP_DAYS:
                        continue # 30天内，跳过
                    elif REL_CLEAN_PROB_START_DAYS < age_days <= REL_CLEAN_PROB_END_DAYS:
                        ratio = (age_days - REL_CLEAN_PROB_START_DAYS) / (REL_CLEAN_PROB_END_DAYS - REL_CLEAN_PROB_START_DAYS)
                        prob = REL_CLEAN_PROB_START_VALUE + (REL_CLEAN_PROB_END_VALUE - REL_CLEAN_PROB_START_VALUE) * ratio
                        if random.random() < prob:
                            candidates.append(rel) # 概率命中，成为候选
                    else: # 90天以上，缓存失效
                        candidates.append(rel)
                except (ValueError, TypeError):
                    candidates.append(rel) # 缓存时间戳格式错误，视为无效
            else:
                candidates.append(rel) # 不在缓存中，成为候选

        if not candidates:
            logger.info("未发现需要检查的关系。")
            return [r for r in relationships if 'temp_id' in r.pop('temp_id', None)]

        # 2. 随机抽样
        sample_size = min(REL_CLEAN_NUM, len(candidates))
        rels_to_check = random.sample(candidates, sample_size)
        random.shuffle(rels_to_check)
        
        logger.info(f"从 {len(candidates)} 条候选关系中，随机抽取 {len(rels_to_check)} 条进行检查。")
        
        # 3. 使用栈和多轮检查机制调用LLM
        processing_stack = deque(rels_to_check)
        ids_to_delete = set()
        max_rounds = 20
        cooldown_between_rounds_seconds = 30 # 每轮检查后的冷却时间（秒）

        for round_num in range(1, max_rounds + 1):
            if not processing_stack: break
            logger.info(f"--- 开始第 {round_num}/{max_rounds} 轮检查 (剩余: {len(processing_stack)} 条) ---")
            
            items_this_round = list(processing_stack)
            processing_stack.clear() # 清空栈，未处理的将被重新压入

            for rel in items_this_round:
                decision = self.llm_service.is_relation_deletable(rel, id_to_name_map)
                if decision is True:
                    ids_to_delete.add(rel['temp_id'])
                    logger.info(f"  - [删除] {id_to_name_map.get(rel['source'])} -> {id_to_name_map.get(rel['target'])} ({rel['type']})")
                elif decision is False:
                    key = self._get_canonical_rel_key(rel)
                    if key:
                        self.false_relations_cache[key] = {'timestamp': now.isoformat()}
                        self.cache_updated = True
                else: # API调用失败
                    processing_stack.append(rel) # 压回栈中，待下轮处理
            
            # 如果栈中仍有待处理项，且不是最后一轮，则进入冷却
            if processing_stack and round_num < max_rounds:
                logger.info(f"第 {round_num} 轮结束，进入 {cooldown_between_rounds_seconds} 秒冷却时间...")
                time.sleep(cooldown_between_rounds_seconds)
        
        if processing_stack:
            logger.warning(f"{len(processing_stack)} 条关系在 {max_rounds} 轮后仍未处理成功，将保留。")

        # 4. 根据结果构建最终的关系列表
        final_relationships = []
        for rel in relationships:
            temp_id = rel.pop('temp_id', None)
            if temp_id not in ids_to_delete:
                final_relationships.append(rel)

        logger.info(f"关系清洗完成，共删除了 {len(ids_to_delete)} 条关系。")
        return final_relationships

    def run(self):
        """执行完整的端到端维护流水线。"""
        graph = graph_io.load_master_graph(self.master_graph_path)
        nodes, relationships = graph.get('nodes', []), graph.get('relationships', [])
        
        logger.info("================= 启动 Chinese-Elite 深度维护 =================")
        self._clean_stale_cache()
        nodes, relationships = self._resolve_temporary_nodes(nodes, relationships)
        relationships = self._clean_individual_relationships(nodes, relationships)

        logger.info("\n[*] 正在保存所有变更...")
        final_graph = {'nodes': nodes, 'relationships': relationships}
        graph_io.save_master_graph(self.master_graph_path, final_graph)
        self._save_caches()
        
        logger.info("==============   深度维护执行完毕   =============")
