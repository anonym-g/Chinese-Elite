# scripts/merge_graphs.py

import os
import json
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    DATA_DIR, MERGE_CHECK_MODEL, MERGE_EXECUTE_MODEL,
    MERGE_CHECK_PROMPT_PATH, MERGE_EXECUTE_PROMPT_PATH
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
            with open(self.master_graph_path, 'r', encoding='utf-8') as f:
                self.master_graph = json.load(f)
        
        try:
            with open(self.log_path, 'r', encoding='utf-8') as f:
                self.processed_files = set(line.strip() for line in f)
        except FileNotFoundError:
            self.processed_files = set()
            
        self.master_nodes_map = {node['id']: node for node in self.master_graph['nodes']}
        for node in self.master_graph['nodes']:
            for alias in node.get('aliases', []):
                self.master_aliases_map[alias] = node['id']

    def _should_trigger_merge_llm(self, existing_node: dict, new_node: dict) -> bool:
        """调用LLM判断新节点是否提供了有价值的新信息。"""
        comparison_prompt = (
            f"{self.check_prompt}\n"
            f"--- 现有JSON对象 (部分) ---\n"
            f"{json.dumps({'aliases': existing_node.get('aliases', []), 'properties': existing_node.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
            f"--- 新JSON对象 (部分) ---\n"
            f"{json.dumps({'aliases': new_node.get('aliases', []), 'properties': new_node.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
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
            return True  # 安全默认
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
                print(f"     - LLM成功合并 {item_type}: {existing_item.get('id') or '关系'}")
                return merged_item
            else:
                raise ValueError("LLM returned an empty response.")
        except (Exception, ValueError) as e:
            print(f"[!] 错误：LLM合并失败 - {e}", file=sys.stderr)
            return existing_item

    def _process_single_file(self, file_path: str):
        """处理单个JSON文件的合并逻辑。"""
        print(f"\n--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                new_data = json.load(f)
            if not isinstance(new_data, dict):
                print(f"[!] 警告: 文件内容不是字典，已跳过。", file=sys.stderr)
                return

            # 处理节点、映射别名
            for new_node in new_data.get('nodes', []):
                original_id = new_node['id']
                canonical_id = self.master_aliases_map.get(original_id, original_id)
                new_node['id'] = canonical_id

                if canonical_id in self.master_nodes_map:
                    existing_node = self.master_nodes_map[canonical_id]
                    print(f"   - 发现已存在节点: '{canonical_id}'，正在进行LLM预检...")
                    if self._should_trigger_merge_llm(existing_node, new_node):
                        print(f"     - 预检结果: YES，启动智能合并。")
                        merged_node = self._call_llm_for_merge(existing_node, new_node, "节点", original_id)
                        self.master_nodes_map[canonical_id] = merged_node
                        for alias in merged_node.get('aliases', []): self.master_aliases_map[alias] = canonical_id
                    else:
                        print(f"     - 预检结果: NO，跳过合并。")
                else:
                    print(f"   - 添加新节点: {canonical_id}")
                    self.master_nodes_map[canonical_id] = new_node
                    for alias in new_node.get('aliases', []): self.master_aliases_map[alias] = canonical_id
            
            # 处理关系
            temp_rels = {(r['source'], r['target'], r['type']): r for r in self.master_graph.get('relationships', [])}
            for new_rel in new_data.get('relationships', []):
                new_rel['source'] = self.master_aliases_map.get(new_rel['source'], new_rel['source'])
                new_rel['target'] = self.master_aliases_map.get(new_rel['target'], new_rel['target'])
                rel_key = (new_rel['source'], new_rel['target'], new_rel['type'])
                if rel_key not in temp_rels:
                    print(f"   - 添加新关系: {new_rel['source']} -> {new_rel['target']}")
                    temp_rels[rel_key] = new_rel
            self.master_graph['relationships'] = list(temp_rels.values())

        except Exception as e:
            print(f"[!] 错误: 无法读取或解析文件 {file_path} - {e}", file=sys.stderr)
    
    def run(self):
        """执行完整的合并流程。"""
        self._load_graph_and_log()
        
        source_files_to_process = []
        for root, _, files in os.walk(DATA_DIR):
            for filename in files:
                if filename.endswith('.json') and filename not in self.processed_files:
                    source_files_to_process.append(os.path.join(root, filename))

        if not source_files_to_process:
            print("\n[*] 未发现需要处理的新文件。")
            return
            
        print(f"\n[*] 发现 {len(source_files_to_process)} 个新的源JSON文件待处理。")
        for file_path in source_files_to_process:
            self._process_single_file(file_path)
            self.files_processed_this_run.append(os.path.basename(file_path))

        # 保存结果
        self.master_graph['nodes'] = list(self.master_nodes_map.values())
        print("\n[*] 合并完成，正在保存最终结果...")
        with open(self.master_graph_path, 'w', encoding='utf-8') as f:
            json.dump(self.master_graph, f, indent=2, ensure_ascii=False)
        print(f"[*] 主图谱已成功保存至: {self.master_graph_path}")

        if self.files_processed_this_run:
            print(f"[*] 正在更新已处理文件日志...")
            with open(self.log_path, 'a', encoding='utf-8') as f:
                for filename in self.files_processed_this_run:
                    f.write(filename + '\n')
            print(f"[*] {len(self.files_processed_this_run)} 个新文件名已添加到日志中。")
