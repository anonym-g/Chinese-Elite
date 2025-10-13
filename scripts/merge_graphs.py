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
from api_rate_limiter import gemma_limiter, gemini_flash_limiter

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
            
        # --- 构建支持多语言的名称到Q-Code全局映射 ---
        self.name_to_qcode_map = {}
        for node in self.master_graph.get('nodes', []):
            node_id = node.get('id')
            if not node_id:
                continue
            # 遍历所有语言 ('zh-cn', 'en', etc.)
            for lang, names in node.get('name', {}).items():
                if isinstance(names, list):
                    for name in names:
                        if name:
                            # 允许覆盖，对于别名映射来说是安全的
                            self.name_to_qcode_map[name] = node_id
        
        self.master_nodes_map = {node['id']: node for node in self.master_graph.get('nodes', []) if 'id' in node}

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

    @gemma_limiter.limit # 应用 Gemma 装饰器
    def _should_trigger_merge_llm(self, existing_item: dict, new_item: dict) -> bool:
        """调用LLM判断新对象是否提供了有价值的新信息。"""
        
        # 定义所有可能的标识符字段
        keys_to_remove = {'id', 'name', 'source', 'target'}

        existing_props = {k: v for k, v in existing_item.items() if k not in keys_to_remove}
        new_props = {k: v for k, v in new_item.items() if k not in keys_to_remove}

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

    @gemini_flash_limiter.limit # 应用 Flash 装饰器
    def _call_llm_for_merge(self, existing_item: dict, new_item: dict, item_type: str) -> dict:
        """调用LLM执行两个冲突项的智能合并。"""
        
        # 定义所有可能的标识符字段
        keys_to_remove = {'id', 'name', 'source', 'target'}

        existing_props = {k: v for k, v in existing_item.items() if k not in keys_to_remove}
        new_props = {k: v for k, v in new_item.items() if k not in keys_to_remove}

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

    def _merge_and_update_names(self, new_node, qcode, existing_node=None, canonical_name_override=None, primary_lang=None):
        """
        合并多语言的 name 对象，并更新全局的 name-to-ID 映射。

        Args:
            new_node (dict): 从碎片文件中读取的新节点。
            qcode (str): 该实体最终确定的Q-Code。
            existing_node (dict, optional): 主图谱中已存在的节点。默认为None。
            canonical_name_override (str, optional): 强行指定的规范名称（用于处理重定向）。默认为None。
            primary_lang (str, optional): 新节点的主要语言。

        Returns:
            dict: 包含所有语言名称的完整 name 对象。
        """
        # 从已存在节点开始，或创建一个空字典
        merged_name_obj = (existing_node.get('name') or {}).copy() if existing_node else {}
        
        # 识别所有需要处理的语言键
        all_langs = set(merged_name_obj.keys()) | set(new_node.get('name', {}).keys())

        for lang in all_langs:
            existing_names = merged_name_obj.get(lang, [])
            new_names = new_node.get('name', {}).get(lang, [])

            # 1. 确定权威的规范名称 (canonical name)
            canonical_name = None
            # 优先级1: 重定向指定的新名称
            if lang == primary_lang and canonical_name_override:
                canonical_name = canonical_name_override
            # 优先级2: 已存在节点的首个名称 (这是最重要的，必须保留)
            elif existing_names:
                canonical_name = existing_names[0]
            # 优先级3: 新节点的首个名称 (仅当节点是全新的)
            elif new_names:
                canonical_name = new_names[0]
            
            # 2. 收集所有不重复的名称
            all_names_set = set(existing_names) | set(new_names)
            if canonical_name:
                all_names_set.add(canonical_name) # 确保规范名一定在集合里

            # 3. 构建最终的、顺序正确的列表
            if canonical_name:
                # 从集合中移除规范名，剩下的就是别名
                all_names_set.discard(canonical_name)
                # 将规范名放在首位，后面跟上排序后的别名列表
                final_name_list = [canonical_name] + sorted(list(all_names_set))
                merged_name_obj[lang] = final_name_list
            elif all_names_set:
                # 如果由于某种原因没有规范名，则退回为全部排序，防止数据丢失
                merged_name_obj[lang] = sorted(list(all_names_set))

        # 用所有合并后的名称更新全局映射
        for lang, names in merged_name_obj.items():
            for name in names:
                if name not in self.name_to_qcode_map:
                    self.name_to_qcode_map[name] = qcode
        
        return merged_name_obj

    def _process_single_file(self, file_path: str, master_rels_map: dict) -> bool:
        """处理单个JSON文件的合并逻辑 (Q-Code版本)。"""
        logger.info(f"--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)
            if not isinstance(new_data, dict):
                logger.warning(f"文件内容不是字典，已跳过: {file_path}")
                return False

            # 用于追踪本文件中名称到最终ID（Q-Code或临时名）的映射
            local_name_to_final_id_map = {}

            # --- 步骤1: 处理和解析节点 ---
            for new_node in new_data.get('nodes', []):
                new_node_name_obj = new_node.get('name', {})
                if not new_node_name_obj: continue

                # 动态确定新节点的主语言和主名称
                primary_lang = next(iter(new_node_name_obj), None)
                if not primary_lang: continue
                
                primary_name = new_node_name_obj[primary_lang][0] if new_node_name_obj.get(primary_lang) else None
                if not primary_name: continue

                final_id = None
                
                # Case 1: 节点主名称已存在于全局映射中
                if primary_name in self.name_to_qcode_map:
                    qcode = self.name_to_qcode_map[primary_name]
                    existing_node = self.master_nodes_map[qcode]
                    logger.info(f"  - 发现已存在节点: '{primary_name}' -> {qcode}，进行合并...")
                    
                    # 合并多语言名称
                    final_name_obj = self._merge_and_update_names(new_node, qcode, existing_node=existing_node, primary_lang=primary_lang)
                    existing_node['name'] = final_name_obj

                    # 决定是否用LLM合并properties
                    if self._should_trigger_merge_llm(existing_node, new_node):
                        logger.info(f"    - LLM预检: YES，启动智能合并properties。")
                        merged_node_props = self._call_llm_for_merge(existing_node, new_node, "节点")

                        # 用 if 过滤装饰器返回的特殊值 (None)
                        if merged_node_props:
                            existing_node.update(merged_node_props)
                    else:
                        logger.info(f"    - LLM预检: NO，跳过合并properties。")

                    self.master_nodes_map[qcode] = existing_node
                    final_id = qcode

                # Case 2: 节点主名称是新的，需要确定其状态和ID
                else:
                    api_lang = 'zh' if 'zh' in primary_lang else primary_lang
                    status, detail = self.wiki_client.check_link_status(primary_name, lang=api_lang)

                    # 规则：消歧义，或简繁重定向以外的任何类型重定向，直接丢弃节点。
                    if status in ["REDIRECT", "DISAMBIG"]:
                        logger.warning(f"  - [丢弃] 节点 '{primary_name}' 是一个非简繁重定向 (目标: {detail})，已按规则丢弃。")
                        continue # 直接跳到 for 循环的下一个节点

                    qcode = self.wiki_client.get_qcode(primary_name, lang=api_lang)
                    
                    # 场景A: 成功获取Q-Code
                    if qcode:
                        canonical_name_override = None
                        # 如果是简繁重定向，则将重定向目标设为规范名称，并将其加入列表
                        if status == "SIMP_TRAD_REDIRECT" and detail:
                            redirect_target_name = self.wiki_client.t2s_converter.convert(detail) if api_lang == 'zh' else detail
                            if redirect_target_name:
                                canonical_name_override = redirect_target_name
                                # 将新发现的重定向目标页加入处理列表
                                if api_lang == 'zh':
                                    add_title_to_list(canonical_name_override)
                                else:
                                    add_title_to_list(f"({api_lang}) {canonical_name_override}")

                        # Case 2a: Q-Code已存在 (别名指向了现有实体)
                        if qcode in self.master_nodes_map:
                            existing_node = self.master_nodes_map[qcode]
                            logger.info(f"  - 新节点 '{primary_name}' 解析为已存在Q-Code: {qcode}，进行合并...")
                            final_name_obj = self._merge_and_update_names(new_node, qcode, existing_node=existing_node, primary_lang=primary_lang, canonical_name_override=canonical_name_override)
                            existing_node['name'] = final_name_obj
                            if self._should_trigger_merge_llm(existing_node, new_node):
                                merged_node_props = self._call_llm_for_merge(existing_node, new_node, "节点")
                                if merged_node_props:
                                    existing_node.update(merged_node_props)
                            self.master_nodes_map[qcode] = existing_node
                            final_id = qcode
                        
                        # Case 2b: 全新节点
                        else:
                            # 规则：当一个全新的、非重定向的有效页面被创建时，也应将其加入列表
                            if status == "OK":
                                if api_lang == 'zh':
                                    add_title_to_list(primary_name)
                                else:
                                    add_title_to_list(f"({api_lang}) {primary_name}")

                            # 确定日志中显示的规范名
                            log_canonical_name = canonical_name_override or primary_name
                            if detail and status == "SIMP_TRAD_REDIRECT":
                                logger.info(f"  - 新节点名 '{primary_name}' 是简繁重定向，规范名称为 '{log_canonical_name}'")
                            logger.info(f"  - 添加全新节点: '{log_canonical_name}' -> {qcode}")
                            
                            final_name_obj = self._merge_and_update_names(new_node, qcode, canonical_name_override=canonical_name_override, primary_lang=primary_lang)
                            new_node['id'] = qcode
                            new_node['name'] = final_name_obj
                            self.master_nodes_map[qcode] = new_node
                            final_id = qcode
                    
                    # 场景B: 无法获取Q-Code (包括 status 为 NO_PAGE, ERROR 等)
                    else:
                        temp_id = None
                        if status == "BAIDU":
                            temp_id = f"BAIDU:{primary_name}"
                        elif status == "CDT":
                            temp_id = f"CDT:{primary_name}"
                        
                        if temp_id:
                            logger.warning(f"  - 节点 '{primary_name}' 状态为 {status}。使用临时ID: {temp_id}")
                            new_node['id'] = temp_id
                            for lang_key, names_list in new_node.get('name', {}).items():
                                new_node['name'][lang_key] = list(dict.fromkeys(names_list))
                            self.master_nodes_map[temp_id] = new_node
                            final_id = temp_id
                        else:
                            logger.error(f"  - [失败] 节点 '{primary_name}' 在所有来源均未找到 (状态: {status})。节点已丢弃。")
                            final_id = None
                    
                    if final_id:
                        local_name_to_final_id_map[primary_name] = final_id

            # --- 步骤2: 处理关系 ---
            for new_rel in new_data.get('relationships', []):
                source_name = new_rel.get('source')
                target_name = new_rel.get('target')

                # 使用本文件内建立的映射将名称转换为最终ID
                source_id = local_name_to_final_id_map.get(source_name) or self.name_to_qcode_map.get(source_name)
                target_id = local_name_to_final_id_map.get(target_name) or self.name_to_qcode_map.get(target_name)

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

                        # 用 if 过滤装饰器返回的特殊值 (None)
                        if merged_rel:
                            master_rels_map[rel_key] = merged_rel
                else:
                    master_rels_map[rel_key] = new_rel
            
            # 在try块的末尾表示成功
            return True
        
        # 将宽泛的 except Exception 拆分为文件读取错误和逻辑错误
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"无法读取或解析文件 {file_path} - {e}")
            return False
        except Exception as e:
            # exc_info=True 会自动记录详细的异常和堆栈跟踪，便于调试
            logger.error(f"处理文件 {file_path} 时发生意外的逻辑错误。", exc_info=True)
            return False

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
                success = self._process_single_file(file_path, master_rels_map)
                if success:
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
