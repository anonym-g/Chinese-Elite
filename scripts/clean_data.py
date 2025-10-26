# scripts/clean_data.py

import os
import json
import sys
import random
import time
from datetime import datetime, timedelta, timezone
from collections import deque
import logging
import concurrent.futures
import re
from opencc import OpenCC

# 使用相对路径导入
from .config import (
    MASTER_GRAPH_PATH, FALSE_RELATIONS_CACHE_PATH, 
    RELATIONSHIP_TYPE_RULES,
    LIST_FILE_PATH,
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
        self.t2s_converter = OpenCC('t2s')

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

    def _validate_and_clean_schema(self, nodes: list, relationships: list) -> tuple[list, list]:
        """
        对图谱数据进行严格的模式验证和清理。
        - 移除不符合预设类型或格式的节点和关系。
        - 移除节点和关系内部不符合格式的属性。
        """
        # --- 节点清理 ---
        cleaned_nodes = []
        valid_node_ids = set()
        node_id_to_type_map = {}
        
        VALID_NODE_TYPES = {'Person', 'Organization', 'Movement', 'Event', 'Location', 'Document'}
        
        for node in nodes:
            is_valid = True
            if not isinstance(node, dict):
                continue

            node_id = node.get('id')
            node_type = node.get('type')
            if not (isinstance(node_id, str) and node_id and isinstance(node_type, str) and node_type in VALID_NODE_TYPES):
                logger.warning(f"  - [删除节点] 节点 {node_id or '(无ID)'} 因类型无效 ('{node_type}') 或ID缺失而被删除。")
                is_valid = False
            if not is_valid:
                continue

            allowed_node_keys = {'id', 'type', 'name', 'properties'}
            for key in list(node.keys()):
                if key not in allowed_node_keys: del node[key]

            if 'name' in node:
                if not isinstance(node['name'], dict):
                    del node['name']
                else:
                    for lang, names in list(node['name'].items()):
                        if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
                            del node['name'][lang]
            
            if 'properties' in node:
                if not isinstance(node['properties'], dict):
                    del node['properties']
                else:
                    props = node['properties']
                    for key in ['period']:
                        if key in props and not (isinstance(props[key], str) or (isinstance(props[key], list) and all(isinstance(i, str) for i in props[key]))):
                            del props[key]
                    for key in ['lifetime', 'gender']:
                         if key in props and not isinstance(props[key], str):
                            del props[key]
                    if 'gender' in props and props['gender'] not in ['Male', 'Female']:
                        del props['gender']
                    for key in ['location', 'birth_place', 'death_place', 'description']:
                        if key in props and isinstance(props[key], dict):
                            for lang, value in list(props[key].items()):
                                if not isinstance(value, str):
                                    del props[key][lang]
                        elif key in props:
                            del props[key]
            
            cleaned_nodes.append(node)
            valid_node_ids.add(node_id)
            node_id_to_type_map[node_id] = node_type
            
        nodes_deleted_count = len(nodes) - len(cleaned_nodes)
        if nodes_deleted_count > 0:
            logger.info(f"  - 节点清理完成，共删除了 {nodes_deleted_count} 个无效节点。")
        
        # --- 关系清理 ---
        cleaned_relationships = []
        
        for rel in relationships:
            is_valid = True
            if not isinstance(rel, dict): continue

            source_id, target_id, rel_type = rel.get('source'), rel.get('target'), rel.get('type')
            if not (isinstance(source_id, str) and source_id and isinstance(target_id, str) and target_id and isinstance(rel_type, str) and rel_type):
                is_valid = False
            elif not (source_id in valid_node_ids and target_id in valid_node_ids):
                is_valid = False
            elif rel_type not in RELATIONSHIP_TYPE_RULES:
                is_valid = False
            else:
                rule = RELATIONSHIP_TYPE_RULES[rel_type]
                source_type, target_type = node_id_to_type_map[source_id], node_id_to_type_map[target_id]
                if ('source' in rule and source_type not in rule['source']) or ('target' in rule and target_type not in rule['target']):
                    is_valid = False
            
            if not is_valid: continue
            
            for key in list(rel.keys()):
                if key not in {'source', 'target', 'type', 'properties'}: del rel[key]

            if 'properties' in rel:
                if not isinstance(rel['properties'], dict):
                    del rel['properties']
                else:
                    props = rel['properties']
                    for key in ['start_date', 'end_date']:
                        if key in props and not (isinstance(props[key], str) or (isinstance(props[key], list) and all(isinstance(i, str) for i in props[key]))):
                            del props[key]
                    for key in ['position', 'degree', 'description']:
                        if key in props and isinstance(props[key], dict):
                           for lang, value in list(props[key].items()):
                                if not isinstance(value, str):
                                    del props[key][lang]
                        elif key in props:
                           del props[key]
                                    
            cleaned_relationships.append(rel)

        rels_deleted_count = len(relationships) - len(cleaned_relationships)
        if rels_deleted_count > 0:
            logger.info(f"  - 关系清理完成，共删除了 {rels_deleted_count} 条无效关系。")
            
        logger.info("--- 数据模式验证与清理完成 ---")
        return cleaned_nodes, cleaned_relationships
    
    def _correct_node_types_from_list(self, nodes: list) -> list:
        """
        根据 LIST.md 中的分类，校对并修正节点的 'type' 属性。
        新逻辑只基于节点的首要规范名称进行匹配，以避免别名冲突。
        """
        # 步骤 1: 解析 LIST.md，构建一个从“规范名称”到“正确类型”的映射。
        name_to_correct_type = {}
        try:
            with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
                current_category = None
                for line in f:
                    line = line.strip()
                    if line.startswith('## '):
                        category_name = line[3:].strip().lower()
                        # 跳过无效或不处理的分类
                        if not category_name or category_name == 'new':
                            current_category = None
                            continue
                        current_category = category_name.capitalize()
                        continue
                    
                    if not line or line.startswith('//') or not current_category:
                        continue

                    entity_name = re.sub(r'\([a-z]{2}\)\s*', '', line).strip()
                    simplified_name = self.t2s_converter.convert(entity_name)
                    if simplified_name:
                        name_to_correct_type[simplified_name] = current_category
        except FileNotFoundError:
            logger.warning(f"LIST.md 文件未找到于 '{LIST_FILE_PATH}'，跳过节点类型修正步骤。")
            return nodes

        # 步骤 2: 遍历所有节点，使用其规范名称进行检查和修正。
        corrected_count = 0
        for node in nodes:
            name_obj = node.get('name')
            if not isinstance(name_obj, dict):
                continue

            # 确定节点的首要规范名称 (优先级: zh-cn[0], en[0])
            canonical_name = None
            zh_names = name_obj.get('zh-cn')
            if isinstance(zh_names, list) and zh_names:
                canonical_name = zh_names[0]
            else:
                en_names = name_obj.get('en')
                if isinstance(en_names, list) and en_names:
                    canonical_name = en_names[0]

            if not canonical_name:
                continue

            # 使用简体化的规范名称在映射中查找
            simplified_canonical_name = self.t2s_converter.convert(canonical_name)
            correct_type = name_to_correct_type.get(simplified_canonical_name)

            # 如果找到了正确类型，且与当前类型不符，则进行修正
            if correct_type and node.get('type') != correct_type:
                original_type = node.get('type')
                logger.info(f"  - [类型修正] 节点 '{canonical_name}' ({node.get('id')}) 的类型从 '{original_type}' 修正为 '{correct_type}'。")
                node['type'] = correct_type
                corrected_count += 1
        
        if corrected_count > 0:
            logger.info(f"  - 节点类型修正完成，共修正了 {corrected_count} 个节点的类型。")
        else:
            logger.info("  - 未发现需要修正类型的节点。")
        
        return nodes

    def _clean_stale_cache(self):
        """
        清理所有超过30天的链接状态缓存条目。
        """
        one_month_ago = datetime.now() - timedelta(days=30)
        pruned_cache = {}
        cleaned_count = 0
        
        for key, value in self.wiki_client.link_cache.items():
            try:
                # 检查所有条目，无论其 status 是什么
                timestamp_str = value.get('timestamp')
                if not timestamp_str:
                    cleaned_count += 1
                    continue # 如果没有时间戳，直接清理掉
                
                timestamp = datetime.fromisoformat(timestamp_str)
                # 如果时间戳在最近30天内，则保留
                if timestamp > one_month_ago:
                    pruned_cache[key] = value
                else:
                    # 否则，标记为已清理
                    cleaned_count += 1
            except (ValueError, TypeError):
                # 如果时间戳格式错误，也清理掉
                cleaned_count += 1
        
        if cleaned_count > 0:
            self.wiki_client.link_cache = pruned_cache
            self.wiki_client.link_cache_updated = True
            logger.info(f"清理了 {cleaned_count} 个过期的链接状态缓存条目。")
        else:
            logger.info("未发现过期的缓存条目。")
            
    def _resolve_temporary_nodes(self, nodes: list, relationships: list) -> tuple[list, list]:
        """遍历所有节点，尝试将临时ID升级为Q-Code。"""
        nodes_map = {n['id']: n for n in nodes}
        id_remap, nodes_to_delete = {}, set()
        
        temp_nodes = [n for n in nodes if n['id'].startswith(('BAIDU:', 'CDT:'))]
        logger.info(f"发现 {len(temp_nodes)} 个使用临时ID的节点待检查。")

        for node in temp_nodes:
            old_id = node['id']
            original_name = old_id.split(':', 1)[-1]
            # 正确解包 get_qcode 的返回元组，用 _ 忽略不需要的 final_title
            qcode, _ = self.wiki_client.get_qcode(original_name)
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

    def _prune_rels(self, relationships: list) -> list:
        """
        清理描述 (description) 为空的关系。
        一个关系在以下情况下会被删除：
        1. 'properties' 字段不存在。
        2. 'properties' 字段中 'description' 缺失、是一个空字典，或其所有值均为空/空白字符串。
        """
        kept_relationships = []
        
        for rel in relationships:
            properties = rel.get('properties')
            description = properties.get('description') if isinstance(properties, dict) else None
            
            # 如果描述不存在、不是字典、是空字典，或所有值都为空字符串，则跳过保存（删除）
            if not isinstance(description, dict) or not description or all(not str(val).strip() for val in description.values()):
                continue
            else:
                kept_relationships.append(rel)

        deleted_count = len(relationships) - len(kept_relationships)
        if deleted_count > 0:
            logger.info(f"  - 成功删除了 {deleted_count} 条描述为空的关系。")
        else:
            logger.info("  - 未发现描述为空的关系。")

        return kept_relationships

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
        """对随机抽样的单条关系进行有速率限制的并行LLM审查和清理。"""
        id_to_node_map = {n['id']: n for n in nodes}
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

                    if age_days <= REL_CLEAN_SKIP_DAYS: continue # 30天内，跳过
                    elif REL_CLEAN_PROB_START_DAYS < age_days <= REL_CLEAN_PROB_END_DAYS:
                        ratio = (age_days - REL_CLEAN_PROB_START_DAYS) / (REL_CLEAN_PROB_END_DAYS - REL_CLEAN_PROB_START_DAYS)
                        prob = REL_CLEAN_PROB_START_VALUE + (REL_CLEAN_PROB_END_VALUE - REL_CLEAN_PROB_START_VALUE) * ratio
                        
                        if random.random() < prob: candidates.append(rel) # 概率命中，成为候选
                    else: candidates.append(rel) # 90天以上，缓存失效
                except (ValueError, TypeError): candidates.append(rel) # 缓存时间戳格式错误，视为无效
            else: candidates.append(rel) # 不在缓存中，成为候选

        if not candidates:
            logger.info("未发现需要检查的关系。")
            # 清理掉临时ID后返回
            for r in relationships: r.pop('temp_id', None)
            return relationships

        # 2. 随机抽样
        sample_size = min(REL_CLEAN_NUM, len(candidates))
        rels_to_check = random.sample(candidates, sample_size)
        random.shuffle(rels_to_check)
        
        logger.info(f"从 {len(candidates)} 条候选关系中，随机抽取 {len(rels_to_check)} 条进行检查。")
        
        # 3. 多轮重试，包裹并行批处理
        BATCH_SIZE, MAX_ROUNDS, COOLDOWN_SECONDS = 30, 20, 30
        ids_to_delete = set()
        
        def process_relation(relation):
            return self.llm_service.is_relation_deletable(relation, id_to_node_map)

        for round_num in range(1, MAX_ROUNDS + 1):
            if not rels_to_check:
                break
            
            logger.info(f"\n--- 开始第 {round_num}/{MAX_ROUNDS} 轮检查 (待处理: {len(rels_to_check)} 条) ---")
            
            failed_this_round = []

            total_batches_this_round = (len(rels_to_check) + BATCH_SIZE - 1) // BATCH_SIZE
            
            # 并行批处理
            for i in range(0, len(rels_to_check), BATCH_SIZE):
                batch = rels_to_check[i:i + BATCH_SIZE]

                batch_num = (i // BATCH_SIZE) + 1
                logger.info(f"  - 正在处理批次 {batch_num}/{total_batches_this_round} (共 {len(batch)} 条关系)")
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                    future_to_rel = {executor.submit(process_relation, rel): rel for rel in batch}
                    for future in concurrent.futures.as_completed(future_to_rel):
                        rel = future_to_rel[future]
                        try:
                            decision = future.result()
                            if decision is True:
                                ids_to_delete.add(rel['temp_id'])
                            elif decision is False:
                                key = self._get_canonical_rel_key(rel)
                                if key:
                                    self.false_relations_cache[key] = {'timestamp': now.isoformat()}
                                    self.cache_updated = True
                            else: # 返回 None (API call failed)
                                failed_this_round.append(rel)
                        except Exception as exc:
                            logger.error(f"处理关系 {rel['temp_id']} 时发生异常: {exc}")
                            failed_this_round.append(rel)

            # 将本轮失败的任务作为下一轮的输入
            rels_to_check = failed_this_round
            
            if rels_to_check and round_num < MAX_ROUNDS:
                logger.warning(f"第 {round_num} 轮有 {len(rels_to_check)} 条关系处理失败，将在 {COOLDOWN_SECONDS} 秒后重试...")
                time.sleep(COOLDOWN_SECONDS)
        
        if rels_to_check:
            logger.error(f"在 {MAX_ROUNDS} 轮后，仍有 {len(rels_to_check)} 条关系未能成功处理。")
        
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
        
        # 步骤 1: 根据 LIST.md 修正节点类型
        logger.info("\n--- 步骤 1/6: 根据列表文件修正节点类型 ---")
        nodes = self._correct_node_types_from_list(nodes)

        # 步骤 2: 清理无描述关系
        logger.info("\n--- 步骤 2/6: 清理描述为空的关系 ---")
        relationships = self._prune_rels(relationships)

        # 步骤 3: 验证并清理数据格式
        logger.info("\n--- 步骤 3/6: 验证并清理数据格式 ---")
        nodes, relationships = self._validate_and_clean_schema(nodes, relationships)
        
        # 步骤 4: 使用 LLM 清理剩余关系
        logger.info("\n--- 步骤 4/6: 清理单条的错误/低质量关系 ---")
        relationships = self._clean_individual_relationships(nodes, relationships)

        # 步骤 5: 清理缓存
        logger.info("\n--- 步骤 5/6: 清理过期的链接状态缓存 ---")
        self._clean_stale_cache()
        
        # 步骤 6: 尝试升级临时节点
        logger.info("\n--- 步骤 6/6: 尝试升级临时ID节点 ---")
        nodes, relationships = self._resolve_temporary_nodes(nodes, relationships)

        logger.info("\n[*] 正在保存所有变更...")
        final_graph = {'nodes': nodes, 'relationships': relationships}
        graph_io.save_master_graph(self.master_graph_path, final_graph)
        self._save_caches()
        
        logger.info("==============   深度维护执行完毕   =============")
