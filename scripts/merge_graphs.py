# scripts/merge_graphs.py

import os
import json
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types
import logging

from config import (
    DATA_DIR, MERGE_CHECK_MODEL, MERGE_EXECUTE_MODEL,
    BAIDU_BASE_URL, CDSPACE_BASE_URL,
    MERGE_CHECK_PROMPT_PATH, MERGE_EXECUTE_PROMPT_PATH,
    NON_DIRECTED_LINK_TYPES, MASTER_GRAPH_PATH
)
from utils import WikipediaClient, add_title_to_list

logger = logging.getLogger(__name__)

load_dotenv()

class GraphMerger:
    """封装了合并多个JSON图谱文件到主图谱的逻辑（Q-Code版本）。"""

    def __init__(self, master_graph_path: str, log_path: str):
        self.master_graph_path = master_graph_path
        self.log_path = log_path
        self.client = genai.Client()
        self.wiki_client = WikipediaClient() # 初始化 WikipediaClient
        self.check_prompt = self._load_prompt(MERGE_CHECK_PROMPT_PATH)
        self.execute_prompt = self._load_prompt(MERGE_EXECUTE_PROMPT_PATH)
        
        # 状态变量
        self.master_graph = {"nodes": [], "relationships": []}
        self.processed_files = set()
        self.master_nodes_map = {} # qcode -> node object
        self.name_to_qcode_map = {} # name -> qcode
        self.files_processed_this_run = []

    def _load_prompt(self, path: str) -> str:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: '{path}'未找到Prompt文件")
            sys.exit(1)

    def _load_graph_and_log(self):
        """加载主图谱和已处理文件日志。"""
        if os.path.exists(self.master_graph_path):
            try:
                with open(self.master_graph_path, 'r', encoding='utf-8') as f:
                    self.master_graph = json.load(f)
                    if 'nodes' not in self.master_graph: self.master_graph['nodes'] = []
                    if 'relationships' not in self.master_graph: self.master_graph['relationships'] = []
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"无法读取或解析主图谱文件，将创建一个新的。错误: {e}")
        
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                self.processed_files = set(line.strip() for line in f)
        except FileNotFoundError:
            self.processed_files = set()
            
        # --- 构建基于Q-Code的索引 ---
        self.master_nodes_map = {node['id']: node for node in self.master_graph.get('nodes', [])}
        for node in self.master_graph.get('nodes', []):
            primary_name = node.get('name', {}).get('zh-cn', [None])[0]
            if primary_name:
                self.name_to_qcode_map[primary_name] = node['id']

    def _get_canonical_rel_key(self, rel: dict) -> tuple | None:
        """为关系生成一个规范化的键，用于处理无向关系。"""
        source = rel.get('source')
        target = rel.get('target')
        rel_type = rel.get('type')

        if not (isinstance(source, str) and isinstance(target, str) and rel_type):
            return None

        if rel_type in NON_DIRECTED_LINK_TYPES:
            return tuple(sorted((source, target))), rel_type
        else:
            return (source, target), rel_type

    def _should_trigger_merge_llm(self, existing_item: dict, new_item: dict) -> bool:
        """调用LLM判断新对象是否提供了有价值的新信息。"""
        # 从比较对象中移除ID和name字段，只比较properties
        existing_props = {k: v for k, v in existing_item.items() if k not in ['id', 'name']}
        new_props = {k: v for k, v in new_item.items() if k not in ['id', 'name']}

        comparison_prompt = (
            f"{self.check_prompt}\n"
            f"--- 现有JSON对象 (部分) ---\n"
            f"{json.dumps(existing_props, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新JSON对象 (部分) ---\n"
            f"{json.dumps(new_props, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新对象是否提供了有价值的新信息？ (回答 YES 或 NO) ---\n"
        )
        try:
            response = self.client.models.generate_content(
                model=MERGE_CHECK_MODEL, contents=comparison_prompt
            )
            if response.text and isinstance(response.text, str):
                return response.text.strip().upper() == "YES"
            return True
        except Exception as e:
            logger.error(f"LLM预检失败 - {e}")
            return True

    def _call_llm_for_merge(self, existing_item: dict, new_item: dict, item_type: str) -> dict:
        """调用LLM执行两个冲突项的智能合并。"""
        # 移除ID和name字段，避免LLM混淆
        existing_props = {k: v for k, v in existing_item.items() if k not in ['id', 'name']}
        new_props = {k: v for k, v in new_item.items() if k not in ['id', 'name']}

        full_prompt = (
            f"--- 现有{item_type} ---\n"
            f"{json.dumps(existing_props, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新{item_type} ---\n"
            f"{json.dumps(new_props, indent=2, ensure_ascii=False)}\n\n"
            f"--- 合并后的最终JSON ---\n"
        )
        try:
            response = self.client.models.generate_content(
                model=MERGE_EXECUTE_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.execute_prompt,
                    response_mime_type='application/json',
                ),
            )
            if response.text:
                merged_props = json.loads(response.text)
                # 将合并后的属性与原始ID和name重新组合
                final_item = existing_item.copy()
                final_item.update(merged_props)
                logger.info(f"LLM成功合并: {existing_item.get('id') or '关系'}")
                return final_item
            else:
                raise ValueError("LLM returned an empty response.")
        except (Exception, ValueError) as e:
            logger.error(f"LLM合并失败 - {e}")
            return existing_item

    def _merge_and_update_names(self, new_node, qcode, existing_node=None, canonical_name_override=None):
        """
        合并新旧节点的名称列表(name.zh-cn)，并为新发现的别名更新全局名称映射。

        Args:
            new_node (dict): 从碎片文件中读取的新节点。
            qcode (str): 该实体最终确定的Q-Code。
            existing_node (dict, optional): 主图谱中已存在的节点。默认为None。
            canonical_name_override (str, optional): 强行指定的规范名称（用于处理重定向）。默认为None。

        Returns:
            list: 合并、去重并排序后的最终 name.zh-cn 列表。
        """
        # 步骤1：从新旧节点中收集所有已知的名称
        all_known_names = set(new_node.get('name', {}).get('zh-cn', []))
        if existing_node:
            all_known_names.update(existing_node.get('name', {}).get('zh-cn', []))

        # 步骤2：确定规范名称
        if canonical_name_override:
            # 优先使用重定向检查后指定的规范名称
            canonical_name = canonical_name_override
            all_known_names.add(canonical_name)
        elif existing_node and existing_node.get('name', {}).get('zh-cn'):
            # 其次，使用已存在节点的首个名称
            canonical_name = existing_node['name']['zh-cn'][0]
        else:
            # 最后，对于一个全新的节点，使用它自己的首个名称
            canonical_name = new_node.get('name', {}).get('zh-cn', [None])[0]
        
        if not canonical_name: return []

        # 步骤3：构建最终的节点名列表，确保规范名称在第一位
        final_name_list = [canonical_name] + [name for name in sorted(list(all_known_names)) if name != canonical_name]
        final_name_list = list(dict.fromkeys(final_name_list))  # 去重，同时保持顺序

        # 步骤4：为所有收集到的名称更新全局映射，确保它们都指向唯一的Q-Code
        for name in all_known_names:
            if name not in self.name_to_qcode_map:
                self.name_to_qcode_map[name] = qcode
        
        return final_name_list

    def _process_single_file(self, file_path: str, master_rels_map: dict):
        """处理单个JSON文件的合并逻辑 (Q-Code版本)。"""
        logger.info(f"--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)
            if not isinstance(new_data, dict):
                logger.warning(f"文件内容不是字典，已跳过: {file_path}")
                return

            # 用于追踪本文件中中文名到最终ID（Q-Code或临时名）的映射
            local_name_to_final_id_map = {}

            # --- 步骤1: 处理和解析节点 ---
            for new_node in new_data.get('nodes', []):
                new_node_name = new_node.get('name', {}).get('zh-cn', [None])[0]
                if not new_node_name: continue

                final_id = None
                
                # Case 1: 节点名已存在于主图的名称映射中
                if new_node_name in self.name_to_qcode_map:
                    qcode = self.name_to_qcode_map[new_node_name]
                    existing_node = self.master_nodes_map[qcode]
                    logger.info(f"  - 发现已存在节点: '{new_node_name}' -> {qcode}，进行合并...")
                    
                    # 合并别名
                    final_name_list = self._merge_and_update_names(new_node, qcode, existing_node=existing_node)
                    existing_node['name']['zh-cn'] = final_name_list

                    # 决定是否用LLM合并properties
                    if self._should_trigger_merge_llm(existing_node, new_node):
                        logger.info(f"    - LLM预检: YES，启动智能合并properties。")
                        merged_node_props = self._call_llm_for_merge(existing_node, new_node, "节点")
                        existing_node.update(merged_node_props)
                    else:
                        logger.info(f"    - LLM预检: NO，跳过合并properties。")

                    self.master_nodes_map[qcode] = existing_node
                    final_id = qcode

                # Case 2: 节点名是新的，需要确定其状态和ID
                else:
                    # 调用 check_link_status
                    status, detail = self.wiki_client.check_link_status(new_node_name)
                    qcode = None

                    # 只有当页面有效时才尝试获取Q-Code
                    if status in ["OK", "REDIRECT", "SIMP_TRAD_REDIRECT"]:
                        qcode = self.wiki_client.get_qcode(new_node_name)
                    
                    # 场景A: 成功获取Q-Code
                    if qcode:
                        canonical_name = self.wiki_client.t2s_converter.convert(detail) if detail else new_node_name
                        add_title_to_list(canonical_name)

                        # Case 2a: Q-Code已存在
                        if qcode in self.master_nodes_map:
                            existing_node = self.master_nodes_map[qcode]
                            logger.info(f"  - 新节点 '{new_node_name}' 解析为已存在Q-Code: {qcode}，进行合并...")
                            final_name_list = self._merge_and_update_names(new_node, qcode, existing_node=existing_node)
                            existing_node['name']['zh-cn'] = final_name_list
                            if self._should_trigger_merge_llm(existing_node, new_node):
                                merged_node_props = self._call_llm_for_merge(existing_node, new_node, "节点")
                                existing_node.update(merged_node_props)
                            self.master_nodes_map[qcode] = existing_node
                            final_id = qcode
                        
                        # Case 2b: 全新节点
                        else:
                            if detail:
                                logger.info(f"  - 新节点名 '{new_node_name}' 是重定向，规范名称为 '{canonical_name}'")
                            logger.info(f"  - 添加全新节点: '{canonical_name}' -> {qcode}")
                            final_name_list = self._merge_and_update_names(new_node, qcode, canonical_name_override=canonical_name)
                            new_node['id'] = qcode
                            new_node['name']['zh-cn'] = final_name_list
                            self.master_nodes_map[qcode] = new_node
                            final_id = qcode
                    
                    # 场景B: 无法获取Q-Code，根据已查明的状态赋临时ID
                    else:
                        temp_id = None
                        if status == "BAIDU":
                            temp_id = f"BAIDU:{new_node_name}"
                        elif status == "CDT":
                            temp_id = f"CDT:{new_node_name}"
                        
                        if temp_id:
                            logger.warning(f"  - 节点 '{new_node_name}' 状态为 {status}。使用临时ID: {temp_id}")
                            new_node['id'] = temp_id
                            new_node['name']['zh-cn'] = list(dict.fromkeys(new_node.get('name', {}).get('zh-cn', [])))
                            self.master_nodes_map[temp_id] = new_node
                            final_id = temp_id
                        else:
                            logger.error(f"  - [失败] 节点 '{new_node_name}' 在所有来源均未找到 (状态: {status})。节点已丢弃。")
                            final_id = None
                
                if final_id:
                    local_name_to_final_id_map[new_node_name] = final_id

            # --- 步骤2: 处理关系 ---
            for new_rel in new_data.get('relationships', []):
                source_name = new_rel.get('source')
                target_name = new_rel.get('target')

                # 使用本文件内建立的映射将中文名转换为最终ID
                source_id = local_name_to_final_id_map.get(source_name)
                target_id = local_name_to_final_id_map.get(target_name)

                if not source_id or not target_id:
                    logger.warning(f"  - 关系中的源/目标节点无法解析，已跳过: {source_name} -> {target_name}")
                    continue

                new_rel['source'] = source_id
                new_rel['target'] = target_id
                
                rel_key = self._get_canonical_rel_key(new_rel)
                if rel_key is None: continue

                if rel_key in master_rels_map:
                    existing_rel = master_rels_map[rel_key]
                    if self._should_trigger_merge_llm(existing_rel, new_rel):
                        merged_rel = self._call_llm_for_merge(existing_rel, new_rel, "关系")
                        master_rels_map[rel_key] = merged_rel
                else:
                    master_rels_map[rel_key] = new_rel

        except Exception as e:
            logger.error(f"无法读取或解析文件 {file_path} - {e}")

    def run(self):
        """执行完整的合并流程。"""
        self._load_graph_and_log()
        
        source_files_to_process = []
        for root, _, files in os.walk(DATA_DIR):
            for filename in files:
                if filename.endswith('.json') and filename not in self.processed_files:
                    source_files_to_process.append(os.path.join(root, filename))

        if not source_files_to_process:
            logger.info("未发现需要处理的新文件。")
        else:
            logger.info(f"发现 {len(source_files_to_process)} 个新的源JSON文件待处理。")
            
            master_rels_map = {self._get_canonical_rel_key(r): r for r in self.master_graph['relationships']}

            for file_path in source_files_to_process:
                self._process_single_file(file_path, master_rels_map)
                self.files_processed_this_run.append(os.path.basename(file_path))
            
            self.master_graph['relationships'] = list(master_rels_map.values())

        # 保存结果
        self.master_graph['nodes'] = list(self.master_nodes_map.values())
        logger.info("合并完成，正在保存最终结果...")
        try:
            with open(self.master_graph_path, 'w', encoding='utf-8') as f:
                json.dump(self.master_graph, f, indent=2, ensure_ascii=False)
            logger.info(f"主图谱已成功保存至: {self.master_graph_path}")
        except IOError as e:
            logger.critical(f"保存主图谱文件失败 - {e}")

        if self.files_processed_this_run:
            logger.info("正在更新已处理文件日志...")
            with open(self.log_path, 'a', encoding='utf-8') as f:
                for filename in self.files_processed_this_run:
                    f.write(filename + '\n')
            logger.info(f"{len(self.files_processed_this_run)} 个新文件名已添加到日志中。")

        # 在所有操作结束后，保存Q-Code缓存
        self.wiki_client.save_caches()
