# scripts/clean_data.py

import os
import json
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai import types
import logging

from config import (
    MASTER_GRAPH_PATH, CACHE_DIR, MERGE_CHECK_MODEL,
    PROMPTS_DIR
)
from utils import WikipediaClient
from api_rate_limiter import gemma_limiter

logger = logging.getLogger(__name__)

load_dotenv()

class GraphCleaner:
    """
    一个用于对主图谱进行定期深度维护的工具。
    - 清理过期的链接状态缓存。
    - 尝试将临时ID节点（如BAIDU:, CDT:）升级为Q-Code节点。
    - 使用LLM清理节点间冗余的关系。
    """
    
    def __init__(self, master_graph_path: str, cache_dir: str):
        self.master_graph_path = master_graph_path
        self.wiki_client = WikipediaClient()
        try:
            self.llm_client = genai.Client()
        except Exception as e:
            logger.critical(f"错误：初始化Google GenAI Client失败，请检查API密钥。错误详情: {e}")
            sys.exit(1)
        
        # 加载关系清洗的Prompt
        prompt_path = os.path.join(PROMPTS_DIR, 'clean_relations.txt')
        try:
            with open(prompt_path, 'r', encoding='utf-8') as f:
                self.relation_cleaner_prompt = f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: '{prompt_path}'未找到关系清洗Prompt文件")
            sys.exit(1)

        # 定义需要进行冗余检查的关系类型
        self.redundancy_check_relations = {"INFLUENCED", "PUSHED", "BLOCKED", "FRIEND_OF", "ENEMY_OF", "MET_WITH"}

    def _clean_stale_cache(self):
        """清理超过一个月的BAIDU/CDT链接状态缓存。"""
        logger.info("\n--- 步骤 1/3: 清理过期的链接状态缓存 ---")
        one_month_ago = datetime.now() - timedelta(days=30)
        
        pruned_cache = {}
        cleaned_count = 0
        
        for key, value in self.wiki_client.link_cache.items():
            status = value.get('status')
            if status in ["BAIDU", "CDT"]:
                try:
                    timestamp_str = value.get('timestamp')
                    if not timestamp_str:
                        cleaned_count += 1 # 没有时间戳的旧条目，直接清理
                        continue
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if timestamp > one_month_ago:
                        pruned_cache[key] = value # 保留未过期的
                    else:
                        cleaned_count += 1 # 标记为已清理
                except (ValueError, TypeError):
                    cleaned_count += 1 # 格式错误或无时间戳的也一并清理
            else:
                pruned_cache[key] = value # 保留所有非BAIDU/CDT的条目
        
        if cleaned_count > 0:
            self.wiki_client.link_cache = pruned_cache
            self.wiki_client.link_cache_updated = True
            logger.info(f"清理了 {cleaned_count} 个过期的 BAIDU/CDT 缓存条目。")
        else:
            logger.info("未发现过期的缓存条目。")
            
    def _resolve_temporary_nodes(self, nodes: list, relationships: list) -> tuple[list, list]:
        """遍历所有节点，尝试将临时ID (BAIDU:, CDT:) 升级为Q-Code。"""
        logger.info("\n--- 步骤 2/3: 尝试升级临时ID节点 ---")
        nodes_map = {n['id']: n for n in nodes}
        id_remap = {} # 存储 old_temp_id -> new_qcode 的映射
        nodes_to_delete = set() # 存储因合并而被删除的临时节点的ID
        
        # 检查 BAIDU 和 CDT 节点
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
                
                # 如果新Q-Code已存在，则合并属性，否则直接更新ID
                if qcode in nodes_map and nodes_map[qcode] is not node:
                    logger.info(f"    -> [合并] Q-Code {qcode} 已存在，正在合并属性...")
                    existing_node = nodes_map[qcode]
                    
                    temp_props = node.get('properties', {})
                    if temp_props:
                        existing_props = existing_node.setdefault('properties', {})
                        for key, val in temp_props.items():
                            # 如果现有属性和新属性都是字典 (即我们的多语言对象), 则更新字典
                            if key in existing_props and isinstance(existing_props[key], dict) and isinstance(val, dict):
                                existing_props[key].update(val)
                            # 否则，直接赋值 (适用于新键或非字典值)
                            else:
                                existing_props[key] = val
                                
                else:
                    node['id'] = qcode
                    nodes_map[qcode] = node
        
        # 从图中移除已被合并的旧临时节点
        final_nodes = [n for n in nodes if n['id'] not in nodes_to_delete]
        
        # 更新关系列表中的所有ID
        if id_remap:
            for rel in relationships:
                if rel.get('source') in id_remap:
                    rel['source'] = id_remap[rel['source']]
                if rel.get('target') in id_remap:
                    rel['target'] = id_remap[rel['target']]
        
        logger.info(f"成功升级了 {len(id_remap)} 个节点。")
        return final_nodes, relationships

    def _clean_redundant_relationships(self, nodes: list, relationships: list) -> list:
        """使用LLM清理节点对之间的冗余关系。"""
        logger.info("\n--- 步骤 3/3: 清理冗余的关系 ---")
        
        # 步骤 1: 创建一个从节点ID到可读名称的映射
        id_to_name_map = {}
        # 定义语言偏好顺序
        lang_preference = ['zh-cn', 'en'] 

        for node in nodes:
            node_id = node.get('id')
            if not node_id: continue
            
            representative_name = node_id # 默认使用ID作为最终后备
            node_names = node.get('name', {})
            
            # 按照偏好顺序查找最佳名称
            found_name = False
            for lang in lang_preference:
                # 检查该语言的名称列表是否存在且不为空
                if node_names.get(lang) and node_names[lang] and node_names[lang][0]:
                    representative_name = node_names[lang][0]
                    found_name = True
                    break
            
            # 如果在偏好语言中都没有找到，则使用第一个可用的语言名称作为后备
            if not found_name and node_names:
                # 遍历所有可用的语言
                for lang_key, name_list in node_names.items():
                    if name_list and name_list[0]:
                        representative_name = name_list[0]
                        break
            
            # 将最终选出的代表性名称存入映射
            id_to_name_map[node_id] = representative_name
            
        rels_by_pair = {}
        for rel in relationships:
            source, target = rel.get('source'), rel.get('target')
            if not source or not target: continue
            pair_key = tuple(sorted((source, target)))
            if pair_key not in rels_by_pair:
                rels_by_pair[pair_key] = []
            rels_by_pair[pair_key].append(rel)
            
        rels_to_delete = set()
        processed_pairs = 0

        for pair_key, rels in rels_by_pair.items():
            check_rels = [r for r in rels if r.get('type') in self.redundancy_check_relations]
            
            if len(check_rels) >= 2:
                processed_pairs += 1
                # 将ID->名称的映射传递给LLM调用函数
                readable_source = id_to_name_map.get(pair_key[0], pair_key[0])
                readable_target = id_to_name_map.get(pair_key[1], pair_key[1])
                logger.info(f"  - 检查节点对 ({readable_source} <-> {readable_target}) 之间的 {len(check_rels)} 条潜在冗余关系...")
                
                types_to_delete = self._call_relation_cleaner_llm(check_rels, id_to_name_map)
                
                if types_to_delete:
                    temp_check_rels = list(check_rels) # 创建副本以安全地查找和标记
                    for rel_type in types_to_delete:
                        found = False
                        for r in temp_check_rels:
                            if r.get('type') == rel_type:
                                logger.info(f"    -> LLM建议删除: {r['type']}")
                                rels_to_delete.add(id(r))
                                temp_check_rels.remove(r) # 从副本中移除，防止同一类型被删除多次
                                found = True
                                break
                        if not found:
                            logger.warning(f"    -> LLM建议删除类型'{rel_type}'，但在列表中未找到可删除的实例。")

        # 构建最终的关系列表
        final_relationships = [rel for rel in relationships if id(rel) not in rels_to_delete]

        deleted_count = len(rels_to_delete)
        if deleted_count > 0:
            logger.info(f"在 {processed_pairs} 组关系中，LLM共清理了 {deleted_count} 条冗余关系。")
        else:
            logger.info("未发现需要清理的冗余关系。")
            
        return final_relationships

    @gemma_limiter.limit # 应用 Gemma 装饰器
    def _call_relation_cleaner_llm(self, rels_to_check: list, id_to_name_map: dict) -> list:
        """调用LLM来判断应删除哪些关系。"""
        # 步骤 2: 构建发送给LLM的负载，将ID替换为名称
        llm_payload = []
        for r in rels_to_check:
            # 创建一个副本，并移除临时的内部键
            rel_copy = {k: v for k, v in r.items() if k != 'temp_id'}
            # 使用映射将 source 和 target ID 转换为可读名称
            rel_copy['source'] = id_to_name_map.get(r.get('source'), r.get('source'))
            rel_copy['target'] = id_to_name_map.get(r.get('target'), r.get('target'))
            llm_payload.append(rel_copy)

        prompt = self.relation_cleaner_prompt + "\n" + json.dumps(llm_payload, indent=2, ensure_ascii=False)
        
        try:
            response = self.llm_client.models.generate_content(
                model=MERGE_CHECK_MODEL,
                contents=prompt,
                # config=types.GenerateContentConfig(response_mime_type='application/json'),
            )
            # 检查response.text是否存在且为字符串，防止对None调用.replace()
            if response.text and isinstance(response.text, str):
                cleaned_text = response.text.replace('```json', '').replace('```', '').strip()
                types_to_delete = json.loads(cleaned_text)
                if isinstance(types_to_delete, list):
                    return types_to_delete
        except Exception as e:
            logger.error(f"    [!] LLM关系清理失败: {e}")
        return []
    
    def run(self):
        """执行完整的端到端维护流水线。"""
        if not os.path.exists(self.master_graph_path):
            logger.error(f"错误: 主图谱文件不存在: {self.master_graph_path}")
            return

        with open(self.master_graph_path, 'r', encoding='utf-8') as f:
            graph = json.load(f)
        
        nodes = graph.get('nodes', [])
        relationships = graph.get('relationships', [])
        
        logger.info("==================================================")
        logger.info("==========  启动 Chinese-Elite 深度维护  ==========")
        logger.info("==================================================")

        # --- 步骤 1: 清理过期的缓存条目 ---
        self._clean_stale_cache()

        # --- 步骤 2: 尝试解析和升级临时ID节点 ---
        nodes, relationships = self._resolve_temporary_nodes(nodes, relationships)

        # --- 步骤 3: 清理冗余的关系 ---
        relationships = self._clean_redundant_relationships(nodes, relationships)

        # --- 步骤 4: 保存所有变更 ---
        logger.info("\n[*] 正在保存所有变更...")
        final_graph = {'nodes': nodes, 'relationships': relationships}
        with open(self.master_graph_path, 'w', encoding='utf-8') as f:
            json.dump(final_graph, f, indent=2, ensure_ascii=False)
        
        # 保存所有可能已更新的缓存 (qcode 和 link_status)
        self.wiki_client.save_caches()
        
        logger.info("\n==================================================")
        logger.info("=============   深度维护执行完毕   =============")
        logger.info("==================================================")

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO, 
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
        stream=sys.stdout
    )
    
    cleaner = GraphCleaner(
        master_graph_path=MASTER_GRAPH_PATH,
        cache_dir=CACHE_DIR
    )
    cleaner.run()
