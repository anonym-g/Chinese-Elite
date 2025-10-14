# scripts/clean_data.py

import os
import json
import sys
from datetime import datetime, timedelta
import logging

# 使用相对路径导入
from .config import MASTER_GRAPH_PATH
from .clients.wikipedia_client import WikipediaClient
from .services.llm_service import LLMService
from .services import graph_io

logger = logging.getLogger(__name__)

class GraphCleaner:
    """
    一个用于对主图谱进行定期深度维护的工具。
    """
    def __init__(self, master_graph_path: str, wiki_client: WikipediaClient, llm_service: LLMService):
        self.master_graph_path = master_graph_path
        self.wiki_client = wiki_client
        self.llm_service = llm_service
        self.redundancy_check_relations = {"INFLUENCED", "PUSHED", "BLOCKED", "FRIEND_OF", "ENEMY_OF", "MET_WITH"}

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

    def _clean_redundant_relationships(self, nodes: list, relationships: list) -> list:
        """使用LLM清理节点对之间的冗余关系。"""
        logger.info("\n--- 步骤 3/3: 清理冗余的关系 ---")
        
        id_to_name_map = {n['id']: n.get('name', {}).get('zh-cn', [n['id']])[0] for n in nodes}
            
        rels_by_pair = {}
        for rel in relationships:
            source, target = rel.get('source'), rel.get('target')
            if not source or not target: continue
            pair_key = tuple(sorted((source, target)))
            if pair_key not in rels_by_pair: rels_by_pair[pair_key] = []
            rels_by_pair[pair_key].append(rel)
            
        rels_to_delete, processed_pairs = set(), 0
        for pair_key, rels in rels_by_pair.items():
            check_rels = [r for r in rels if r.get('type') in self.redundancy_check_relations]
            if len(check_rels) >= 2:
                processed_pairs += 1
                readable_source = id_to_name_map.get(pair_key[0], pair_key[0])
                readable_target = id_to_name_map.get(pair_key[1], pair_key[1])
                logger.info(f"  - 检查节点对 ({readable_source} <-> {readable_target}) 之间的 {len(check_rels)} 条潜在冗余关系...")
                
                # 使用 LLMService
                types_to_delete = self.llm_service.clean_relations(check_rels, id_to_name_map)
                
                if types_to_delete:
                    temp_check_rels = list(check_rels)
                    for rel_type in types_to_delete:
                        for r in temp_check_rels:
                            if r.get('type') == rel_type:
                                logger.info(f"    -> LLM建议删除: {r['type']}")
                                rels_to_delete.add(id(r))
                                temp_check_rels.remove(r)
                                break
        
        final_relationships = [rel for rel in relationships if id(rel) not in rels_to_delete]
        if rels_to_delete:
            logger.info(f"在 {processed_pairs} 组关系中，LLM共清理了 {len(rels_to_delete)} 条冗余关系。")
        else:
            logger.info("未发现需要清理的冗余关系。")
        return final_relationships

    def run(self):
        """执行完整的端到端维护流水线。"""
        graph = graph_io.load_master_graph(self.master_graph_path)
        nodes, relationships = graph.get('nodes', []), graph.get('relationships', [])
        
        logger.info("================= 启动 Chinese-Elite 深度维护 =================")
        self._clean_stale_cache()
        nodes, relationships = self._resolve_temporary_nodes(nodes, relationships)
        relationships = self._clean_redundant_relationships(nodes, relationships)

        logger.info("\n[*] 正在保存所有变更...")
        final_graph = {'nodes': nodes, 'relationships': relationships}
        graph_io.save_master_graph(self.master_graph_path, final_graph)
        self.wiki_client.save_caches()
        
        logger.info("==============   深度维护执行完毕   =============")
