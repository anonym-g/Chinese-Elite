# scripts/clean_data.py

import os
import json
from datetime import datetime
from config import USER_AGENT
from utils import WikipediaClient

class GraphCleaner:
    """封装了清理图谱数据（验证维基链接）的逻辑。"""

    def __init__(self, graph_path: str, output_dir: str, cache_dir: str):
        self.graph_path = graph_path
        self.output_dir = output_dir
        self.cache_path = os.path.join(cache_dir, 'wiki_link_status_cache.json')
        self.wiki_client = WikipediaClient(user_agent=f"{USER_AGENT} (Cleaning Script)")
        self.link_cache = {}
        self.cache_updated = False

    def _load_cache(self):
        """加载链接状态缓存。"""
        if not os.path.exists(self.cache_path):
            return
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                self.link_cache = json.load(f)
            print(f"[*] 已加载 {len(self.link_cache)} 条缓存的链接状态。")
        except (IOError, json.JSONDecodeError) as e:
            print(f"[!] 警告：无法读取或解析缓存文件 - {e}")

    def _save_cache(self):
        """保存更新后的链接状态缓存。"""
        if not self.cache_updated:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self.link_cache, f, indent=2, ensure_ascii=False)
            print("[*] 链接状态缓存已更新。")
        except IOError as e:
            print(f"[!] 警告：无法写入缓存文件 - {e}")

    def _check_nodes(self, nodes: list) -> dict:
        """遍历所有节点，检查其维基链接状态。"""
        problematic_nodes = {
            "REDIRECT": {}, "NO_PAGE": set(), "DISAMBIG": set(), "ERROR": set()
        }
        
        for i, node in enumerate(nodes):
            node_id = node.get('id')
            if not node_id: continue

            if node.get('properties', {}).get('verified_node', False):
                print(f"  ({i+1}/{len(nodes)}) 正在检查: '{node_id}'... [已验证, 跳过]")
                continue

            print(f"  ({i+1}/{len(nodes)}) 正在检查: '{node_id}'...", end='', flush=True)

            if node_id in self.link_cache:
                status = self.link_cache[node_id]['status']
                print(f" [缓存: {status}]")
            else:
                status, detail = self.wiki_client.check_link_status(node_id)
                self.link_cache[node_id] = {'status': status, 'detail': detail}
                self.cache_updated = True
                print(f" -> {status}")

            status = self.link_cache[node_id]['status']
            detail = self.link_cache[node_id].get('detail')

            if status == "REDIRECT":
                problematic_nodes["REDIRECT"][node_id] = detail
            elif status == "NO_PAGE":
                problematic_nodes["NO_PAGE"].add(node_id)
            elif status == "DISAMBIG":
                problematic_nodes["DISAMBIG"].add(node_id)
            elif status == "ERROR":
                problematic_nodes["ERROR"].add(node_id)
        
        return problematic_nodes

    def run(self):
        """执行完整的清理流程。"""
        if not os.path.exists(self.graph_path):
            print(f"[!] 错误: 源文件不存在: {self.graph_path}")
            return

        with open(self.graph_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        nodes = data.get('nodes', [])
        relationships = data.get('relationships', [])

        self._load_cache()
        print(f"[*] 共加载 {len(nodes)} 个节点，{len(relationships)} 个关系。开始检查...")

        problem_nodes = self._check_nodes(nodes)
        all_bad_ids = set(problem_nodes["REDIRECT"].keys()) | problem_nodes["NO_PAGE"] | problem_nodes["DISAMBIG"] | problem_nodes["ERROR"]

        if not all_bad_ids:
            print("\n[+] 检查完成，未发现需要清理的节点。")
            self._save_cache()
            return

        print(f"\n[*] 检查完成，共发现 {len(all_bad_ids)} 个需要清理的节点ID。")
        
        cleaned_nodes = [n for n in nodes if n.get('id') not in all_bad_ids]
        cleaned_rels = [r for r in relationships if r.get('source') not in all_bad_ids and r.get('target') not in all_bad_ids]

        redirect_ids = set(problem_nodes["REDIRECT"].keys())
        no_page_ids = problem_nodes["NO_PAGE"] | problem_nodes["ERROR"]
        disambig_ids = problem_nodes["DISAMBIG"]

        problematic_data = {
            'redirect': {
                'nodes': [n for n in nodes if n.get('id') in redirect_ids],
                'relationships': [r for r in relationships if r.get('source') in redirect_ids or r.get('target') in redirect_ids],
                'redirect_map': problem_nodes["REDIRECT"]
            },
            'no_page': {
                'nodes': [n for n in nodes if n.get('id') in no_page_ids],
                'relationships': [r for r in relationships if r.get('source') in no_page_ids or r.get('target') in no_page_ids]
            },
            'disambig': {
                'nodes': [n for n in nodes if n.get('id') in disambig_ids],
                'relationships': [r for r in relationships if r.get('source') in disambig_ids or r.get('target') in disambig_ids]
            }
        }
        ### --- 修正结束 --- ###

        # Save results
        timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        current_output_dir = os.path.join(self.output_dir, timestamp)
        os.makedirs(current_output_dir, exist_ok=True)
        print(f"\n[*] 正在创建输出目录: {current_output_dir}")

        for key, content in problematic_data.items():
            # 只有当包含节点或关系时才保存文件
            if content.get('nodes') or content.get('relationships'):
                filepath = os.path.join(current_output_dir, f'{key}_{timestamp}.json')
                print(f"  - 正在保存: {os.path.basename(filepath)}")
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(content, f, indent=2, ensure_ascii=False)

        print(f"\n[*] 正在用干净的数据覆盖原始文件: {self.graph_path}")
        with open(self.graph_path, 'w', encoding='utf-8') as f:
            json.dump({'nodes': cleaned_nodes, 'relationships': cleaned_rels}, f, indent=2, ensure_ascii=False)

        self._save_cache()
        print("\n[+] 清理完成！")
