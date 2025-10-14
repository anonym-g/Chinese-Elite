# scripts/validate_pr.py

import os
import sys
import subprocess
import json
import re
import logging

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

# 使用绝对路径导入
from scripts.config import MASTER_GRAPH_PATH
from scripts.services import graph_io
from scripts.services.llm_service import LLMService

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def get_pr_files_and_diff(pr_number):
    """获取 PR 修改的文件列表和完整 diff 内容。"""
    try:
        files_result = subprocess.run(['gh', 'pr', 'diff', pr_number, '--name-only'], capture_output=True, text=True, check=True, encoding='utf-8')
        files_changed = files_result.stdout.strip().splitlines()
        diff_result = subprocess.run(['gh', 'pr', 'diff', pr_number], capture_output=True, text=True, check=True, encoding='utf-8')
        return files_changed, diff_result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"使用 'gh' 命令失败: {e.stderr}")
        return None, None

def translate_diff_for_llm(diff_content, qcode_map):
    """将 diff 中的 Q-Code 替换为可读名称。"""
    if not qcode_map: return diff_content
    translated_lines = []
    for line in diff_content.splitlines():
        for code in set(re.findall(r'(Q\d+)', line)):
            name = qcode_map.get(code, "Unknown Entity")
            line = line.replace(code, f'{code}({name})')
        translated_lines.append(line)
    return '\n'.join(translated_lines)

def main():
    if len(sys.argv) < 2:
        logger.error("用法: python scripts/validate_pr.py <pr_number>")
        sys.exit(2)

    pr_number = sys.argv[1]
    
    files_changed, diff_content = get_pr_files_and_diff(pr_number)
    if not files_changed or not diff_content:
        logger.error("错误：未能获取到 PR 的文件变更列表或 diff 内容。")
        sys.exit(2)

    file_to_check = files_changed[0]
    final_diff_for_llm = diff_content
    logging.info(f"文件待检查: '{file_to_check}'")

    if file_to_check == "docs/master_graph_qcode.json":
        logger.info("检测到对主图谱的修改，正在翻译 Q-Code...")
        
        graph = graph_io.load_master_graph(MASTER_GRAPH_PATH)
        qcode_map = {node['id']: node.get('name', {}).get('zh-cn', [node['id']])[0] for node in graph.get('nodes', [])}
        final_diff_for_llm = translate_diff_for_llm(diff_content, qcode_map)
    elif file_to_check == "data/LIST.md":
        logger.info("检测到对列表文件的修改，直接评估。")
    
    llm_service = LLMService()
    decision = llm_service.validate_pr_diff(final_diff_for_llm, file_to_check)
    
    logger.info(f"模型评估结果: {decision}")
    if decision == "True": sys.exit(0)
    elif decision == "False": sys.exit(1)
    else: sys.exit(2)

if __name__ == "__main__":
    main()
