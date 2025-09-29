# scripts/clean_data.py

import os
import json
from datetime import datetime
from config import USER_AGENT, RELATIONSHIP_TYPE_RULES
from utils import WikipediaClient

class GraphCleaner:
    """封装了清理图谱数据（验证维基链接、清理无效关系）的逻辑。"""

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
        
        total_nodes = len(nodes)
        print(f"\n[*] 开始检查 {total_nodes} 个节点的链接状态...")
        for i, node in enumerate(nodes):
            node_id = node.get('id')
            if not node_id: continue

            if node.get('properties', {}).get('verified_node', False):
                print(f"  ({i+1}/{total_nodes}) 正在检查: '{node_id}'... [已验证, 跳过]")
                continue

            print(f"  ({i+1}/{total_nodes}) 正在检查: '{node_id}'...", end='', flush=True)

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
        print(f"[*] 共加载 {len(nodes)} 个节点，{len(relationships)} 个关系。")

        # --- 步骤 1: 检查节点链接状态并分离问题节点 ---
        problem_nodes_by_category = self._check_nodes(nodes)
        all_bad_node_ids = set(problem_nodes_by_category["REDIRECT"].keys()) | problem_nodes_by_category["NO_PAGE"] | problem_nodes_by_category["DISAMBIG"] | problem_nodes_by_category["ERROR"]

        if all_bad_node_ids:
            print(f"\n[*] 节点检查完成，共发现 {len(all_bad_node_ids)} 个需要清理的节点ID。")
        else:
            print("\n[+] 节点检查完成，未发现链接状态异常的节点。")
            
        good_nodes = [n for n in nodes if n.get('id') not in all_bad_node_ids]

        # --- 步骤 2: 基于有效的节点列表，清洗关系 ---
        good_node_ids = {n['id'] for n in good_nodes}
        node_type_map = {n['id']: n.get('type') for n in good_nodes}
        good_relationships = []
        invalid_relationships_log = []

        print(f"\n[*] 基于 {len(good_nodes)} 个有效节点，开始清理 {len(relationships)} 个关系...")
        for rel in relationships:
            source_id = rel.get('source')
            target_id = rel.get('target')
            rel_type = rel.get('type')

            # 规则 1: 清理悬空关系 (节点不存在)
            if not source_id or not target_id or source_id not in good_node_ids or target_id not in good_node_ids:
                reason = f"悬空关系：源节点 '{source_id}' 或目标节点 '{target_id}' 不存在于有效节点列表中。"
                invalid_relationships_log.append({'relationship': rel, 'reason': reason})
                continue

            # 规则 2: 清理类型不匹配的关系
            if rel_type and rel_type in RELATIONSHIP_TYPE_RULES:
                rule = RELATIONSHIP_TYPE_RULES[rel_type]
                source_type = node_type_map.get(source_id)
                target_type = node_type_map.get(target_id)
                
                allowed_sources = rule.get('source')
                allowed_targets = rule.get('target')

                source_ok = not allowed_sources or source_type in allowed_sources
                target_ok = not allowed_targets or target_type in allowed_targets
                
                if not source_ok or not target_ok:
                    reason = f"类型不匹配：关系 '{rel_type}' (源: {source_type}, 目标: {target_type}) 不符合规则 (允许源: {allowed_sources or 'Any'}, 允许目标: {allowed_targets or 'Any'})。"
                    invalid_relationships_log.append({'relationship': rel, 'reason': reason})
                    continue

            good_relationships.append(rel)
        
        print(f"[*] 关系清理完成。保留了 {len(good_relationships)} 个有效关系，移除了 {len(invalid_relationships_log)} 个无效关系。")
        
        # --- 步骤 3: 整理并保存所有待清理的数据 ---
        if not all_bad_node_ids and not invalid_relationships_log:
            print("\n[+] 整体检查完成，图谱数据健康，无需执行清理操作。")
            self._save_cache()
            return
            
        timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        current_output_dir = os.path.join(self.output_dir, timestamp)
        os.makedirs(current_output_dir, exist_ok=True)
        print(f"\n[*] 正在创建输出目录: {current_output_dir}")

        # 保存问题节点
        redirect_ids = set(problem_nodes_by_category["REDIRECT"].keys())
        no_page_ids = problem_nodes_by_category["NO_PAGE"] | problem_nodes_by_category["ERROR"]
        disambig_ids = problem_nodes_by_category["DISAMBIG"]

        problematic_nodes_data = {
            'redirect': {
                'nodes': [n for n in nodes if n.get('id') in redirect_ids],
                'relationships': [r for r in relationships if r.get('source') in redirect_ids or r.get('target') in redirect_ids],
                'redirect_map': problem_nodes_by_category["REDIRECT"]
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
        for key, content in problematic_nodes_data.items():
            if content.get('nodes'):
                filepath = os.path.join(current_output_dir, f'problem_nodes_{key}_{timestamp}.json')
                print(f"  - 正在保存问题节点: {os.path.basename(filepath)}")
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(content, f, indent=2, ensure_ascii=False)

        # 保存无效关系
        if invalid_relationships_log:
            filepath = os.path.join(current_output_dir, f'invalid_relationships_{timestamp}.json')
            print(f"  - 正在保存无效关系日志: {os.path.basename(filepath)}")
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(invalid_relationships_log, f, indent=2, ensure_ascii=False)

        # --- 步骤 4: 覆写主图谱文件 ---
        print(f"\n[*] 正在用干净的数据覆盖原始文件: {self.graph_path}")
        with open(self.graph_path, 'w', encoding='utf-8') as f:
            json.dump({'nodes': good_nodes, 'relationships': good_relationships}, f, indent=2, ensure_ascii=False)

        self._save_cache()
        print("\n[+] 清理完成！")
