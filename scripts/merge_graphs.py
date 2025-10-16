# scripts/merge_graphs.py

import os
import json
import sys
import logging

# 使用相对路径导入
from .config import DATA_DIR, PROCESSED_LOG_PATH, NON_DIRECTED_LINK_TYPES
from .clients.wikipedia_client import WikipediaClient
from .services.llm_service import LLMService
from .services import graph_io
from .utils import add_title_to_list

logger = logging.getLogger(__name__)

class GraphMerger:
    """封装了合并多个JSON图谱文件到主图谱的逻辑。"""

    def __init__(self, master_graph_path: str, log_path: str, llm_service: LLMService, wiki_client: WikipediaClient):
        self.master_graph_path = master_graph_path
        self.log_path = log_path
        self.llm_service = llm_service
        self.wiki_client = wiki_client
        
        self.master_graph = {"nodes": [], "relationships": []}
        self.processed_files = set()
        self.master_nodes_map = {}
        self.name_to_qcode_map = {}
        self.files_processed_this_run = []

    def _load_state(self):
        """加载主图谱和已处理文件日志。"""
        self.master_graph = graph_io.load_master_graph(self.master_graph_path)
        
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                self.processed_files = set(line.strip() for line in f)
        except FileNotFoundError:
            self.processed_files = set()
            
        self.name_to_qcode_map = {}
        for node in self.master_graph.get('nodes', []):
            node_id = node.get('id')
            if not node_id: continue
            for lang, names in node.get('name', {}).items():
                if isinstance(names, list):
                    for name in names:
                        if name: self.name_to_qcode_map[name] = node_id
        
        self.master_nodes_map = {node['id']: node for node in self.master_graph.get('nodes', []) if 'id' in node}

    def _get_canonical_rel_key(self, rel: dict) -> tuple | None:
        """为关系生成一个规范化的键，用于处理无向关系。"""
        source, target, rel_type = rel.get('source'), rel.get('target'), rel.get('type')
        if not (isinstance(source, str) and isinstance(target, str) and rel_type):
            return None
        return tuple(sorted((source, target))), rel_type if rel_type in NON_DIRECTED_LINK_TYPES else (source, target, rel_type)

    def _merge_and_update_names(self, new_node, qcode, existing_node=None, canonical_name_override=None, primary_lang=None):
        """合并多语言的 name 对象，并更新全局的 name-to-ID 映射。"""
        merged_name_obj = (existing_node.get('name') or {}).copy() if existing_node else {}
        all_langs = set(merged_name_obj.keys()) | set(new_node.get('name', {}).keys())

        for lang in all_langs:
            existing_names = merged_name_obj.get(lang, [])
            new_names = new_node.get('name', {}).get(lang, [])
            
            canonical_name = None
            if lang == primary_lang and canonical_name_override: canonical_name = canonical_name_override
            elif existing_names: canonical_name = existing_names[0]
            elif new_names: canonical_name = new_names[0]
            
            all_names_set = set(existing_names) | set(new_names)
            if canonical_name: all_names_set.add(canonical_name)

            if canonical_name:
                all_names_set.discard(canonical_name)
                final_name_list = [canonical_name] + sorted(list(all_names_set))
                merged_name_obj[lang] = final_name_list
            elif all_names_set:
                merged_name_obj[lang] = sorted(list(all_names_set))

        for lang, names in merged_name_obj.items():
            for name in names:
                if name not in self.name_to_qcode_map:
                    self.name_to_qcode_map[name] = qcode
        
        return merged_name_obj

    def _process_single_file(self, file_path: str, master_rels_map: dict) -> bool:
        """处理单个JSON文件的合并逻辑。"""
        logger.info(f"--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)
            if not isinstance(new_data, dict):
                logger.warning(f"文件内容不是字典，已跳过: {file_path}")
                return False

            local_name_to_final_id_map = {}

            # --- 步骤1: 处理和解析节点 ---
            for new_node in new_data.get('nodes', []):
                new_node_name_obj = new_node.get('name', {})
                if not new_node_name_obj: continue
                primary_lang = next(iter(new_node_name_obj), None)
                if not primary_lang: continue
                primary_name = new_node_name_obj[primary_lang][0] if new_node_name_obj.get(primary_lang) else None
                if not primary_name: continue

                final_id = None
                api_lang = 'zh' if 'zh' in primary_lang else primary_lang
                
                # 优先通过API获取Q-Code，以准确区分同名但不同实体的页面
                qcode = self.wiki_client.get_qcode(primary_name, lang=api_lang)
                
                if qcode:
                    final_id = qcode
                    # Case 1: API成功返回Q-Code，以此为准
                    if qcode in self.master_nodes_map:
                        # 该Q-Code已存在于主图中，执行合并
                        existing_node = self.master_nodes_map[qcode]
                        logger.info(f"  - 新节点 '{primary_name}' 解析为已存在Q-Code: {qcode}，进行合并...")
                        
                        status, detail = self.wiki_client.check_link_status(primary_name, lang=api_lang)
                        canonical_name_override = None
                        if status == "SIMP_TRAD_REDIRECT" and detail:
                            canonical_name_override = self.wiki_client.t2s_converter.convert(detail) if api_lang == 'zh' else detail
                        
                        existing_node['name'] = self._merge_and_update_names(new_node, qcode, existing_node=existing_node, primary_lang=primary_lang, canonical_name_override=canonical_name_override)
                        if self.llm_service.should_merge(existing_node, new_node):
                            merged_node_props = self.llm_service.merge_items(existing_node, new_node, "节点")
                            if merged_node_props: existing_node.update(merged_node_props)
                        self.master_nodes_map[qcode] = existing_node
                    else:
                        # 带有有效Q-Code的新节点
                        logger.info(f"  - 添加全新节点: '{primary_name}' -> {qcode}")
                        new_node['id'] = qcode
                        new_node['name'] = self._merge_and_update_names(new_node, qcode, primary_lang=primary_lang)
                        self.master_nodes_map[qcode] = new_node
                        add_title_to_list(f"({api_lang}) {primary_name}" if api_lang != 'zh' else primary_name)
                else:
                    # Case 2: API未能返回Q-Code，回退并检查本地名称映射
                    if primary_name in self.name_to_qcode_map:
                        qcode_from_map = self.name_to_qcode_map[primary_name]
                        final_id = qcode_from_map
                        existing_node = self.master_nodes_map[qcode_from_map]
                        logger.info(f"  - 发现已存在节点 (无API Q-Code，通过名称映射): '{primary_name}' -> {qcode_from_map}，进行合并...")
                        
                        existing_node['name'] = self._merge_and_update_names(new_node, qcode_from_map, existing_node=existing_node, primary_lang=primary_lang)
                        if self.llm_service.should_merge(existing_node, new_node):
                            merged_node_props = self.llm_service.merge_items(existing_node, new_node, "节点")
                            if merged_node_props: existing_node.update(merged_node_props)
                        self.master_nodes_map[qcode_from_map] = existing_node
                    else:
                        # Case 3: 无Q-Code节点，创建临时ID或丢弃
                        status, _ = self.wiki_client.check_link_status(primary_name, lang=api_lang)
                        if status in ["REDIRECT", "DISAMBIG"]:
                            logger.warning(f"  - [丢弃] 节点 '{primary_name}' 是一个非简繁重定向或消歧义页，已丢弃。")
                            continue

                        temp_id = f"BAIDU:{primary_name}" if status == "BAIDU" else (f"CDT:{primary_name}" if status == "CDT" else None)
                        if temp_id:
                            final_id = temp_id
                            logger.warning(f"  - 节点 '{primary_name}' 状态为 {status}。使用临时ID: {temp_id}")
                            new_node['id'] = temp_id
                            self.master_nodes_map[temp_id] = new_node
                        else:
                            logger.error(f"  - [失败] 节点 '{primary_name}' 在所有来源均未找到，已丢弃。")
                
                if final_id:
                    local_name_to_final_id_map[primary_name] = final_id

            # --- 步骤2: 处理关系 ---
            for new_rel in new_data.get('relationships', []):
                source_name, target_name = new_rel.get('source'), new_rel.get('target')
                source_id = local_name_to_final_id_map.get(source_name) or self.name_to_qcode_map.get(source_name)
                target_id = local_name_to_final_id_map.get(target_name) or self.name_to_qcode_map.get(target_name)

                if not source_id or not target_id:
                    logger.warning(f"  - 关系中的源/目标节点无法解析，已跳过: {source_name} -> {target_name}")
                    continue

                new_rel['source'], new_rel['target'] = source_id, target_id
                rel_key = self._get_canonical_rel_key(new_rel)
                if rel_key is None: continue

                if rel_key in master_rels_map:
                    if self.llm_service.should_merge(master_rels_map[rel_key], new_rel):
                        merged_rel = self.llm_service.merge_items(master_rels_map[rel_key], new_rel, "关系")
                        if merged_rel: master_rels_map[rel_key] = merged_rel
                else:
                    master_rels_map[rel_key] = new_rel
            
            return True
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"无法读取或解析文件 {file_path} - {e}")
            return False
        except Exception:
            logger.error(f"处理文件 {file_path} 时发生意外的逻辑错误。", exc_info=True)
            return False

    def run(self):
        """执行完整的合并流程。"""
        self._load_state()
        
        source_files_to_process = [os.path.join(r, f) for r, _, files in os.walk(DATA_DIR) for f in files if f.endswith('.json') and f not in self.processed_files]

        if not source_files_to_process:
            logger.info("未发现需要处理的新文件。")
        else:
            logger.info(f"发现 {len(source_files_to_process)} 个新的源JSON文件待处理。")
            master_rels_map = {self._get_canonical_rel_key(r): r for r in self.master_graph['relationships']}

            for file_path in source_files_to_process:
                if self._process_single_file(file_path, master_rels_map):
                    self.files_processed_this_run.append(os.path.basename(file_path))
            
            self.master_graph['relationships'] = list(master_rels_map.values())

        self.master_graph['nodes'] = list(self.master_nodes_map.values())
        graph_io.save_master_graph(self.master_graph_path, self.master_graph)

        if self.files_processed_this_run:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                for filename in self.files_processed_this_run:
                    f.write(filename + '\n')
            logger.info(f"{len(self.files_processed_this_run)} 个新文件名已添加到日志中。")

        self.wiki_client.save_caches()
