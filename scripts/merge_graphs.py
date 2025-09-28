# scripts/merge_graphs.py

import os
import json
import sys
from google import genai
from google.genai import types
from dotenv import load_dotenv

# --- 配置 ---
load_dotenv()
CHECK_MODEL_NAME = "gemma-3-27b-it" # 用于合并和预检的轻量级模型
MERGE_MODEL_NAME = "gemini-2.5-flash"  # 用于复杂合并的高性能模型
DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data')
CONSOLIDATED_FILE_PATH = os.path.join(DATA_PATH, 'consolidated_graph.json')
PROCESSED_LOG_PATH = os.path.join(DATA_PATH, 'processed_files.log')


# --- 全新函数：使用LLM进行预检查 ---
def should_trigger_merge_llm(existing_node: dict, new_node: dict) -> bool:
    """
    调用LLM来判断新节点是否为现有节点提供了有价值的新信息。

    Returns:
        bool: 如果LLM认为需要合并，返回 True，否则返回 False。
    """
    
    system_prompt = f"""
你是一位高效的数据差异分析师。你的任务是判断“新JSON对象”是否为“现有JSON对象”提供了任何有价值的新信息。

什么是有价值的新信息？
1.  更具体的日期（例如，现有的是 '1990'，新的是 '1990-01-15'）。
2.  一个新的别名（alias）。
3.  一个之前为空或不存在，但现在有值的新属性。
4.  在描述性文本中增加了具体的、非冲突性的事实。

什么是无价值的信息？
1.  一个更模糊的日期（例如，现有的是 '1990-01-15'，新的是 '1990'）。
2.  对同一事实的不同措辞或微小修正。
3.  信息是现有信息的子集。

请严格按照以下格式回答，你的输出必须是且仅是一个单词：YES 或 NO。
- YES: 如果新对象提供了有价值的新信息。
- NO: 如果新对象没有提供任何有价值的新信息。
"""
    
    # 为了节约token，只比较两个对象的 'aliases' 和 'properties'
    # gemma-3-27b-it不支持system_instruction，故拼接上传
    comparison_prompt = (
        f"{system_prompt}\n"
        f"--- 现有JSON对象 (部分) ---\n"
        f"{json.dumps({'aliases': existing_node.get('aliases', []), 'properties': existing_node.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
        f"--- 新JSON对象 (部分) ---\n"
        f"{json.dumps({'aliases': new_node.get('aliases', []), 'properties': new_node.get('properties', {})}, indent=2, ensure_ascii=False)}\n\n"
        f"--- 新对象是否提供了有价值的新信息？ (回答 YES 或 NO) ---\n"
    )
    
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=CHECK_MODEL_NAME, 
            contents=comparison_prompt,
        )
        
        # 1. 检查 response.text 是否存在且为字符串
        if response.text and isinstance(response.text, str):
            answer = response.text.strip().upper()
            if answer == "YES":
                return True
            if answer == "NO":
                return False
        
        # 2. 如果 response.text 为 None、空字符串，或内容不是 "YES"/"NO"，则执行安全的回退策略
        warning_text = response.text if response.text else "空响应 (empty response)"
        print(f"[!] 警告：预检LLM返回了意外或空的内容 '{warning_text}'，将默认执行合并。", file=sys.stderr)
        return True # 安全默认：假设需要合并

    except Exception as e:
        print(f"[!] 错误：LLM预检失败 - {e}", file=sys.stderr)
        # 如果预检本身失败，最安全的做法是假设需要合并，以避免漏掉更新
        print("      - 备用策略：将继续执行合并流程。")
        return True


def call_llm_for_merge(existing_item: dict, new_item: dict, item_type: str, original_id: str) -> dict:
    """
    调用LLM来执行两个冲突项的智能合并。
    """
    
    system_prompt = f"""
你是一位严谨的数据整合专家。你的任务是合并两个描述同一个实体的JSON对象，生成一个最准确、最完整的版本。

合并规则:
1.  **优先保留更具体的信息**: 例如，'YYYY-MM-DD' 格式的日期优于 'YYYY'。带有具体职务的描述优于宽泛的描述。
2.  **优先保留非空值**: 如果一个对象的某个字段有值，而另一个是空的或不存在，保留有值的版本。
3.  **结合信息**: 对于 'description' 等文本字段，如果两边信息不冲突且互为补充，可以考虑结合它们。不要做冗余结合。
4.  **合并别名 (aliases)**: 对于 'aliases' 列表，合并两个列表并移除重复项。确保最终结果不包含节点自身的 'id'。
5.  **保持结构**: 最终输出必须是且仅是一个与输入结构完全相同的、单一的、有效的JSON对象。不要添加任何解释或代码块标记。
6.  **关于时间信息**: 若两边日期有明显差异，可能是不同时间段的记录，以数组形式保留。例如：a.“中华人民共和国宪法修正案”的"period"，为数组；b.毛泽东、蒋介石之间的"friend_of"（交好）、"enemy_of"（交恶）有多次震荡，故"start_date"为数组（通常只能靠对立关系判断end_date，如交恶时上一段交好关系终止；故交好关系中的end_date可能空缺；若存在则一并保留）。

示例：

A. 中国共产党和中华人民共和国中央军事委员会，通称中央军事委员会，简称中央军委、军委，是中国共产党和中华人民共和国武装力量最高军事领导机构的总称，包括中国共产党中央军事委员会、中华人民共和国中央军事委员会两个机构。1982年12月中华人民共和国中央军事委员会成立以来，中共中央军委和国家中央军委的组成人员基本相同，实际上是“一个机构两块牌子”。

B. 中国共产党和中华人民共和国中央军事委员会，中国共产党中央委员会和全国人民代表大会领导下的最高军事领导机构。分为中国共产党中央军事委员会（中共中央军委）和中华人民共和国中央军事委员会（国家中央军委）。

C. 中华人民共和国中央军事委员会（国家中央军委），1982年宪法设立，领导全国武装力量。前身机构是中央人民政府人民革命军事委员会。中华人民共和国的最高军事领导机关，与中国共产党中央军事委员会（中共中央军委）为“一个机构两块牌子”。

错误合并示范：
中国共产党和中华人民共和国中央军事委员会，通称中央军事委员会，简称中央军委、军委，是中国共产党和中华人民共和国武装力量最高军事领导机构的总称，包括中国共产党中央军事委员会、中华人民共和国中央军事委员会两个机构。1982年12月中华人民共和国中央军事委员会成立以来，中共中央军委和国家中央军委的组成人员基本相同，实际上是“一个机构两块牌子”。中国共产党中央委员会和全国人民代表大会领导下的最高军事领导机构。1982年宪法设立，领导全国武装力量。中国共产党领导下的军事工作决策机关。中央人民政府人民革命军事委员会的前身机构。中华人民共和国的最高军事领导机关，与中国共产党中央军事委员会为'一个机构两块牌子'。中国共产党中央军事委员会的前身之一。

正确合并示范：
中国共产党和中华人民共和国中央军事委员会，通称中央军事委员会，简称中央军委、军委，是中国共产党和中华人民共和国武装力量最高军事领导机构的总称，包括中国共产党中央军事委员会、中华人民共和国中央军事委员会两个机构。受中国共产党中央委员会和全国人民代表大会领导。1982年12月通过八二宪法，成立中华人民共和国中央军事委员会，其前身是中央人民政府人民革命军事委员会（该机构名义上不受中共领导）。此后，中共中央军委和国家中央军委的组成人员基本相同，实际上是“一个机构两块牌子”。
"""
    
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
        client = genai.Client()
        response = client.models.generate_content(
            model=MERGE_MODEL_NAME, 
            contents=full_prompt, 
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type='application/json',
            ),
        )
        if response.text:
            merged_item = json.loads(response.text)
            if 'aliases' in merged_item:
                main_id = merged_item.get('id')
                unique_aliases = sorted(list(set(alias for alias in merged_item['aliases'] if alias != main_id)))
                merged_item['aliases'] = unique_aliases
            
            print(f"      - LLM成功合并 {item_type}: {existing_item.get('id') or '关系'}")
            return merged_item
        else:
            raise ValueError("LLM returned an empty response.")
    except (Exception, ValueError) as e:
        print(f"[!] 错误：LLM合并失败 - {e}", file=sys.stderr)
        print(f"      - 备用策略：为保证数据质量，将保留现有的高质量数据。")
        return existing_item


def main():
    if os.path.exists(CONSOLIDATED_FILE_PATH):
        with open(CONSOLIDATED_FILE_PATH, 'r', encoding='utf-8') as f: master_graph = json.load(f)
    else:
        master_graph = {"nodes": [], "relationships": []}

    try:
        with open(PROCESSED_LOG_PATH, 'r', encoding='utf-8') as f: processed_files = set(line.strip() for line in f)
    except FileNotFoundError:
        processed_files = set()

    master_nodes_map = {node['id']: node for node in master_graph['nodes']}
    master_aliases_map = {}
    for node in master_graph['nodes']:
        for alias in node.get('aliases', []):
            master_aliases_map[alias] = node['id']

    source_files_to_process = []
    for root, _, files in os.walk(DATA_PATH):
        for filename in files:
            if filename.endswith('.json') and filename not in processed_files and filename != 'consolidated_graph.json':
                source_files_to_process.append(os.path.join(root, filename))

    if not source_files_to_process:
        print("\n[*] 未发现需要处理的新文件。任务完成。")
        sys.exit(0)
    
    print(f"\n[*] 发现 {len(source_files_to_process)} 个新的源JSON文件待处理。")
    files_processed_this_run = []

    for file_path in source_files_to_process:
        print(f"\n--- 正在处理: {os.path.basename(file_path)} ---")
        try:
            with open(file_path, 'r', encoding='utf-8') as f: new_data = json.load(f)

            # --- 数据有效性检查 ---
            if not isinstance(new_data, dict):
                print(f"[!] 警告: 文件 {os.path.basename(file_path)} 的内容不是一个字典，而是一个 {type(new_data).__name__}。已跳过此文件。", file=sys.stderr)
                # 将处理过的文件名依然计入日志，避免重复报错
                files_processed_this_run.append(os.path.basename(file_path)) 
                continue # 跳过这个格式错误的文件，继续处理下一个

        except Exception as e:
            print(f"[!] 错误: 无法读取或解析文件 {file_path} - {e}", file=sys.stderr)
            continue

        id_remap = {}
        for node in new_data.get('nodes', []):
            node_id = node['id']
            if node_id not in master_nodes_map and node_id in master_aliases_map:
                canonical_id = master_aliases_map[node_id]
                id_remap[node_id] = canonical_id
                print(f"   - 别名发现: '{node_id}' 将被映射到 '{canonical_id}'")

        if id_remap:
            for rel in new_data.get('relationships', []):
                if rel['source'] in id_remap: rel['source'] = id_remap[rel['source']]
                if rel['target'] in id_remap: rel['target'] = id_remap[rel['target']]

        for new_node in new_data.get('nodes', []):
            original_id = new_node['id']
            canonical_id = id_remap.get(original_id, original_id)
            new_node['id'] = canonical_id

            if canonical_id in master_nodes_map:
                existing_node = master_nodes_map[canonical_id]
                
                # --- 修改部分：调用新的LLM预检函数 ---
                print(f"   - 发现已存在节点: '{canonical_id}'，正在进行LLM预检...")
                if should_trigger_merge_llm(existing_node, new_node):
                    print(f"      - 预检结果: YES (检测到新信息)，启动智能合并。")
                    merged_node = call_llm_for_merge(existing_node, new_node, "节点", original_id)
                    master_nodes_map[canonical_id] = merged_node
                    for alias in merged_node.get('aliases', []): master_aliases_map[alias] = canonical_id
                else:
                    print(f"      - 预检结果: NO (无有价值的新信息)，跳过合并。")
                # -----------------------------
            else:
                print(f"   - 添加新节点: {canonical_id}")
                master_nodes_map[canonical_id] = new_node
                for alias in new_node.get('aliases', []): master_aliases_map[alias] = canonical_id

        temp_rels = {(r['source'], r['target'], r['type']): r for r in master_graph.get('relationships', [])}
        for new_rel in new_data.get('relationships', []):
            rel_key = (new_rel['source'], new_rel['target'], new_rel['type'])
            if rel_key not in temp_rels:
                print(f"   - 添加新关系: {new_rel['source']} -> {new_rel['target']}")
                temp_rels[rel_key] = new_rel
        master_graph['relationships'] = list(temp_rels.values())
        
        files_processed_this_run.append(os.path.basename(file_path))

    master_graph['nodes'] = list(master_nodes_map.values())
    print("\n[*] 合并完成，正在保存最终结果...")
    with open(CONSOLIDATED_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(master_graph, f, indent=2, ensure_ascii=False)
    print(f"[*] 主图谱已成功保存至: {CONSOLIDATED_FILE_PATH}")

    if files_processed_this_run:
        print(f"[*] 正在更新已处理文件日志...")
        with open(PROCESSED_LOG_PATH, 'a', encoding='utf-8') as f:
            for filename in files_processed_this_run:
                f.write(filename + '\n')
        print(f"[*] {len(files_processed_this_run)} 个新文件名已添加到日志中。")

if __name__ == '__main__':
    main()
