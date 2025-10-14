# scripts/utils.py

import os
import re
import logging

# 使用相对路径导入
from .config import LIST_FILE_PATH

logger = logging.getLogger(__name__)

def add_title_to_list(title_to_add: str):
    """
    将一个新的标题添加到 LIST.md 文件的 'new' 类别下。
    添加前检查。若标题已存在于文件中，则不进行任何操作。
    """
    if not title_to_add:
        return

    try:
        # 使用 'r+' 模式，先读后写
        with open(LIST_FILE_PATH, 'r+', encoding='utf-8') as f:
            lines = f.readlines()
            
            # 构建一个临时集合用于快速查重，忽略类别标题和空行
            existing_entities = {line.strip() for line in lines if line.strip() and not line.strip().startswith('##')}
            if title_to_add.strip() in existing_entities:
                return # 如果已存在，则静默返回

            logger.info(f"正在将新标题 '{title_to_add}' 添加到 LIST.md 的 'new' 类别下...")
            
            # 寻找 '## new' 栏目的插入位置
            new_section_index = -1
            for i, line in enumerate(lines):
                if line.strip() == '## new':
                    new_section_index = i
                    break

            if new_section_index != -1:
                insert_pos = len(lines)
                for i in range(new_section_index + 1, len(lines)):
                    if lines[i].strip().startswith('## '):
                        insert_pos = i
                        break
                lines.insert(insert_pos, f"{title_to_add}\n")
            else:
                if lines and not lines[-1].endswith('\n'): lines.append('\n')
                lines.append("\n## new\n\n")
                lines.append(f"{title_to_add}\n")

            f.seek(0)
            f.truncate()
            f.writelines(lines)

    except FileNotFoundError:
        logger.error(f"严重错误: LIST.md 文件未找到于 '{LIST_FILE_PATH}'")
    except Exception as e:
        logger.error(f"严重错误: 向 LIST.md 添加标题时发生错误: {e}")

def sanitize_filename(name: str) -> str:
    """
    移除文件名中不合法或不推荐的字符。
    这是从 process_list.py 中移到此处的通用函数。
    """
    return re.sub(r'[\\/*?:"<>|]', '_', name)
