import os
import sys
import subprocess
import json
import re
from google import genai
import logging

from config import MASTER_GRAPH_PATH, VALIDATE_PR_MODEL, VALIDATE_PR_PROMPT_PATH
from api_rate_limiter import gemini_flash_lite_limiter

# --- 日志配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

try:
    with open(VALIDATE_PR_PROMPT_PATH, 'r', encoding='utf-8') as f:
        PROMPT_TEMPLATE = f.read()
except FileNotFoundError:
    logger.critical(f"严重错误: '{VALIDATE_PR_PROMPT_PATH}' 未找到 Prompt 文件")
    sys.exit(2)

def load_qcode_to_name_map():
    """从主图谱加载 Q-Code 到实体主名称的映射字典。"""
    try:
        with open(MASTER_GRAPH_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        qcode_map = {
            node['id']: node.get('name', {}).get('zh-cn', [node['id']])[0]
            for node in data.get('nodes', []) if 'id' in node
        }
        logger.info(f"成功加载 {len(qcode_map)} 个 Q-Code 映射。")
        return qcode_map
    except Exception as e:
        logger.warning(f"无法加载 Q-Code 映射文件: {e}")
        return {}

def get_pr_files_and_diff(pr_number):
    """获取 PR 修改的文件列表和完整 diff 内容。"""
    try:
        files_result = subprocess.run(
            ['gh', 'pr', 'diff', pr_number, '--name-only'],
            capture_output=True, text=True, check=True, encoding='utf-8'
        )
        files_changed = files_result.stdout.strip().splitlines()
        
        diff_result = subprocess.run(
            ['gh', 'pr', 'diff', pr_number],
            capture_output=True, text=True, check=True, encoding='utf-8'
        )
        return files_changed, diff_result.stdout
    except subprocess.CalledProcessError as e:
        logger.error(f"使用 'gh' 命令失败: {e.stderr}")
        return None, None

def translate_diff_for_llm(diff_content, qcode_map):
    """将 diff 中的 Q-Code 替换为可读名称。"""
    if not qcode_map:
        return diff_content
    
    translated_lines = []
    for line in diff_content.splitlines():
        q_codes_found = re.findall(r'(Q\d+)', line)
        for code in set(q_codes_found):
            name = qcode_map.get(code, "Unknown Entity")
            line = line.replace(code, f'{code}({name})')
        translated_lines.append(line)
    return '\n'.join(translated_lines)

@gemini_flash_lite_limiter.limit
def evaluate_diff_with_gemini(final_diff_content, file_name):
    """调用 Gemini API 评估处理后的 diff。"""
    try:
        client = genai.Client()
        
        prompt = PROMPT_TEMPLATE.format(
            file_name=file_name,
            diff_content=final_diff_content[:15000] # 限制内容长度
        )
        
        response = client.models.generate_content(
            model=f'models/{VALIDATE_PR_MODEL}',
            contents=prompt
        )
        decision_text = getattr(response, 'text', '')
        decision = decision_text.strip()
        
        if decision not in ["True", "False"]:
            logger.warning(f"模型返回了意外的结果: '{decision}'")
            return None
            
        return decision
    except Exception as e:
        logger.error(f"调用 Gemini API 时发生异常: {e}")
        return None

def main():
    if len(sys.argv) < 2:
        logger.error("用法: python validate_pr.py <pr_number>")
        sys.exit(2)

    pr_number = sys.argv[1]
    
    result = get_pr_files_and_diff(pr_number)
    if result is None or not result[0]:
        sys.exit(2)
    
    files_changed, diff_content = result

    if not files_changed or not diff_content:
        logger.error("错误：未能获取到 PR 的文件变更列表或 diff 内容。")
        sys.exit(2)

    file_to_check = files_changed[0]
    final_diff_for_llm = diff_content

    logging.info(f"File to check: '{file_to_check}'")

    # 使用相对路径字符串进行比较
    if file_to_check == "docs/master_graph_qcode.json":
        logger.info("检测到对主图谱 (master_graph_qcode.json) 的修改，正在翻译 Q-Code...")
        qcode_map = load_qcode_to_name_map()
        final_diff_for_llm = translate_diff_for_llm(diff_content, qcode_map)
    elif file_to_check == "data/LIST.txt":
        logger.info("检测到对列表文件 (LIST.txt) 的修改，直接评估。")
    
    decision = evaluate_diff_with_gemini(final_diff_for_llm, file_to_check)
    
    logger.info(f"模型评估结果: {decision}")

    if decision == "True":
        sys.exit(0)
    elif decision == "False":
        sys.exit(1)
    else:
        sys.exit(2)

if __name__ == "__main__":
    main()
