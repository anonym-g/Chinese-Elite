# scripts/merge_graphs.py

import os
import json
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    DATA_DIR, MERGE_CHECK_MODEL, MERGE_EXECUTE_MODEL,
    MERGE_CHECK_PROMPT_PATH, MERGE_EXECUTE_PROMPT_PATH,
    NON_DIRECTED_LINK_TYPES
)

load_dotenv()

class GraphMerger:
    """封装了合并多个JSON图谱文件到主图谱的逻辑。"""

    def __init__(self, master_graph_path: str, log_path: str):
        self.master_graph_path = master_graph_path
        self.log_path = log_path
        self.client = genai.Client()
        self.check_prompt = self._load_prompt(MERGE_CHECK_PROMPT_PATH)
        self.execute_prompt = self._load_prompt(MERGE_EXECUTE_PROMPT_PATH)
        
        # 状态变量
        self.master_graph = {"nodes": [], "relationships": []}
        self.processed_files = set()
        self.master_nodes_map = {}
        self.master_aliases_map = {}
        self.files_processed_this_run = []

    def _load_prompt(self, path: str) -> str:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            print(f"[!] 严重错误: Prompt文件未找到于 '{path}'", file=sys.stderr)
            sys.exit(1)

    def _load_graph_and_log(self):
        """加载主图谱和已处理文件日志。"""
        if os.path.exists(self.master_graph_path):
            try:
                with open(self.master_graph_path, 'r', encoding='utf-8') as f:
                    self.master_graph = json.load(f)
                    # 确保基础结构存在
                    if 'nodes' not in self.master_graph: self.master_graph['nodes'] = []
                    if 'relationships' not in self.master_graph: self.master_graph['relationships'] = []
            except (json.JSONDecodeError, IOError) as e:
                 print(f"[!] 警告: 无法读取或解析主图谱文件，将创建一个新的。错误: {e}", file=sys.stderr)
        
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                self.processed_files = set(line.strip() for line in f)
        except FileNotFoundError:
            self.processed_files = set()
            
        self.master_nodes_map = {node['id']: node for node in self.master_graph.get('nodes', [])}
        for node in self.master_graph.get('nodes', []):
            for alias in node.get('aliases', []):
                self.master_aliases_map[alias] = node['id']

    def _get_canonical_rel_key(self, rel: dict) -> tuple | None:
        """为关系生成一个规范化的键，用于处理无向关系。"""
        source = rel.get('source')
        target = rel.get('target')
        rel_type = rel.get('type')

        if not (isinstance(source, str) and isinstance(target, str) and rel_type):
            return None

        # 对于无向关系，对 source 和 target 排序以确保唯一性
        if rel_type in NON_DIRECTED_LINK_TYPES:
            # 此时 Pylance 已知 source 和 target 都是字符串，所以 sorted() 不会报错
            return tuple(sorted((source, target))), rel_type
        # 对于有向关系，保持顺序
        else:
            return (source, target), rel_type

    def _deduplicate_internal_relationships(self):
        """
        对主图谱文件内部的关系进行去重和合并。
        处理 A->B 和 B->A (对于无向关系) 的情况。
        """
        print("[*] 开始对主图谱文件进行内部关系去重与合并...")
        deduplicated_rels = {}
        duplicates_found = 0
        
        for rel in self.master_graph.get('relationships', []):
            # 将 source 和 target 从完整的节点对象转换为ID字符串
            rel_with_ids = {
                **rel,
                'source': rel['source']['id'] if isinstance(rel.get('source'), dict) else rel.get('source'),
                'target': rel['target']['id'] if isinstance(rel.get('target'), dict) else rel.get('target')
            }
            
            key = self._get_canonical_rel_key(rel_with_ids)
            if key is None: continue

            if key in deduplicated_rels:
                duplicates_found += 1
                print(f"  - 发现内部重合关系: {key}，启动智能合并。")
                existing_rel = deduplicated_rels[key]
                # 使用与节点合并相同的LLM逻辑
                merged_rel = self._call_llm_for_merge(existing_rel, rel, "关系", "")
                deduplicated_rels[key] = merged_rel
            else:
                deduplicated_rels[key] = rel

        if duplicates_found > 0:
            self.master_graph['relationships'] = list(deduplicated_rels.values())
            print(f"[+] 内部关系清理完成，合并了 {duplicates_found} 组重合关系。")
        else:
            print("[*] 内部关系检查完成，未发现重合项。")


    def _should_trigger_merge_llm(self, existing_item: dict, new_item: dict) -> bool:
        """调用LLM判断新对象是否提供了有价值的新信息。"""
        comparison_prompt = (
            f"{self.check_prompt}\n"
            f"--- 现有JSON对象 (部分) ---\n"
            f"{json.dumps({'aliases': existing_item.get('aliases', []), 'properties': existing_item.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新JSON对象 (部分) ---\n"
            f"{json.dumps({'aliases': new_item.get('aliases', []), 'properties': new_item.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新对象是否提供了有价值的新信息？ (回答 YES 或 NO) ---\n"
        )
        try:
            response = self.client.models.generate_content(
                model=MERGE_CHECK_MODEL,
                contents=comparison_prompt,
            )
            if response.text and isinstance(response.text, str):
                answer = response.text.strip().upper()
                return answer == "YES"
            return True
        except Exception as e:
            print(f"[!] 错误：LLM预检失败 - {e}", file=sys.stderr)
            return True

    def _call_llm_for_merge(self, existing_item: dict, new_item: dict, item_type: str, original_id: str) -> dict:
        """调用LLM执行两个冲突项的智能合并。"""
        if item_type == "节点" and original_id and original_id != new_item['id']:
            new_item.setdefault('aliases', []).append(original_id)

        full_prompt = (
            f"--- 现有{item_type} ---\n"
            f"{json.dumps(existing_item, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新{item_type} ---\n"
            f"{json.dumps(new_item, indent=2, ensure_ascii=False)}\n\n"
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
                merged_item = json.loads(response.text)
                print(f"    - LLM成功合并 {item_type}: {existing_item.get('id') or '关系'}")
                return merged_item
            else:
                raise ValueError("LLM returned an empty response.")
        except (Exception, ValueError) as e:
            print(f"[!] 错误：LLM合并失败 - {e}", file=sys.stderr)
            return existing_item

    def _process_single_file(self, file_path: str, master_rels_map: dict):
        """处理单个JSON文件的合并逻辑 (关系部分重构)。"""
        print(f"\n--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)
            if not isinstance(new_data, dict):
                print(f"[!] 警告: 文件内容不是字典，已跳过。", file=sys.stderr)
                return

            # --- 步骤1: 处理节点 ---
            for new_node in new_data.get('nodes', []):
                original_id = new_node['id']
                canonical_id = self.master_aliases_map.get(original_id, original_id)
                new_node['id'] = canonical_id

                if canonical_id in self.master_nodes_map:
                    existing_node = self.master_nodes_map[canonical_id]
                    print(f"  - 发现已存在节点: '{canonical_id}'，正在进行LLM预检...")
                    if self._should_trigger_merge_llm(existing_node, new_node):
                        print(f"    - 预检结果: YES，启动智能合并。")
                        merged_node = self._call_llm_for_merge(existing_node, new_node, "节点", original_id)
                        self.master_nodes_map[canonical_id] = merged_node
                        for alias in merged_node.get('aliases', []): self.master_aliases_map[alias] = canonical_id
                    else:
                        print(f"    - 预检结果: NO，跳过合并。")
                else:
                    print(f"  - 添加新节点: {canonical_id}")
                    self.master_nodes_map[canonical_id] = new_node
                    for alias in new_node.get('aliases', []): self.master_aliases_map[alias] = canonical_id
            
            # --- 步骤2: 处理关系 ---
            for new_rel in new_data.get('relationships', []):
                # 解析别名，确保使用规范ID
                new_rel['source'] = self.master_aliases_map.get(new_rel['source'], new_rel['source'])
                new_rel['target'] = self.master_aliases_map.get(new_rel['target'], new_rel['target'])
                
                rel_key = self._get_canonical_rel_key(new_rel)
                if rel_key is None: 
                    print(f"  - 警告: 发现格式不完整的关系，已跳过: {new_rel}", file=sys.stderr)
                    continue

                if rel_key in master_rels_map:
                    existing_rel = master_rels_map[rel_key]
                    print(f"  - 发现重合关系: {rel_key}，正在进行LLM预检...")
                    if self._should_trigger_merge_llm(existing_rel, new_rel):
                        print(f"    - 预检结果: YES，启动智能合并。")
                        merged_rel = self._call_llm_for_merge(existing_rel, new_rel, "关系", "")
                        master_rels_map[rel_key] = merged_rel
                    else:
                        print(f"    - 预检结果: NO，跳过合并。")
                else:
                    print(f"  - 添加新关系: {rel_key}")
                    master_rels_map[rel_key] = new_rel

        except Exception as e:
            print(f"[!] 错误: 无法读取或解析文件 {file_path} - {e}", file=sys.stderr)
    
    def run(self):
        """执行完整的合并流程。"""
        self._load_graph_and_log()
        
        # 对加载的主图谱进行内部去重
        self._deduplicate_internal_relationships()
        
        source_files_to_process = []
        for root, _, files in os.walk(DATA_DIR):
            for filename in files:
                if filename.endswith('.json') and filename not in self.processed_files:
                    source_files_to_process.append(os.path.join(root, filename))

        if not source_files_to_process:
            print("\n[*] 未发现需要处理的新文件。")
        else:
            print(f"\n[*] 发现 {len(source_files_to_process)} 个新的源JSON文件待处理。")
            
            # 使用规范键构建主关系映射
            master_rels_map = {self._get_canonical_rel_key(r): r for r in self.master_graph['relationships']}

            for file_path in source_files_to_process:
                self._process_single_file(file_path, master_rels_map)
                self.files_processed_this_run.append(os.path.basename(file_path))
            
            # 从映射中写回最终的关系列表
            self.master_graph['relationships'] = list(master_rels_map.values())

        # 保存结果
        self.master_graph['nodes'] = list(self.master_nodes_map.values())
        print("\n[*] 合并完成，正在保存最终结果...")
        try:
            with open(self.master_graph_path, 'w', encoding='utf-8') as f:
                json.dump(self.master_graph, f, indent=2, ensure_ascii=False)
            print(f"[*] 主图谱已成功保存至: {self.master_graph_path}")
        except IOError as e:
            print(f"[!] 严重错误: 保存主图谱文件失败 - {e}", file=sys.stderr)


        if self.files_processed_this_run:
            print(f"[*] 正在更新已处理文件日志...")
            with open(self.log_path, 'a', encoding='utf-8') as f:
                for filename in self.files_processed_this_run:
                    f.write(filename + '\n')
            print(f"[*] {len(self.files_processed_this_run)} 个新文件名已添加到日志中。")
