# scripts/utils.py

import os
import re
import logging
import threading
from opencc import OpenCC

# 使用相对路径导入
from .config import LIST_FILE_PATH

logger = logging.getLogger(__name__)

t2s_converter = OpenCC('t2s') # 繁转简

# --- 并发控制锁 ---
LIST_MD_LOCK = threading.Lock()

def add_title_to_list(title_to_add: str):
    """
    将一个新的标题添加到 LIST.md 文件的 'new' 类别下。
    添加前检查。若标题已存在于文件中，则不进行任何操作。
    """
    if not title_to_add:
        return

    with LIST_MD_LOCK:
        try:
            # 使用 'r+' 模式，先读后写
            with open(LIST_FILE_PATH, 'r+', encoding='utf-8') as f:
                lines = f.readlines()
                
                # --- 预处理输入标题 ---
                original_title_to_write = title_to_add.replace('_', ' ').strip()
                
                # --- 准备查重 ---
                title_to_check_simplified = t2s_converter.convert(original_title_to_write)
                
                existing_entities_simplified = {
                    t2s_converter.convert(line.strip()) 
                    for line in lines 
                    if line.strip() and not line.strip().startswith('##')
                }

                if title_to_check_simplified in existing_entities_simplified:
                    return

                logger.info(f"正在将新标题 '{original_title_to_write}' 添加到 LIST.md 的 'new' 类别下...")
                
                # 寻找 '## new' 栏目的插入位置
                new_section_index = -1
                for i, line in enumerate(lines):
                    if line.strip() == '## new':
                        new_section_index = i
                        break

                if new_section_index != -1:
                    insert_pos = len(lines)
                    # 从 '## new' 标题后开始寻找下一个 '## ' 标题，以确定插入点
                    for i in range(new_section_index + 1, len(lines)):
                        if lines[i].strip().startswith('## '):
                            insert_pos = i
                            break
                    # 在找到的位置插入处理过的原始标题
                    lines.insert(insert_pos, f"{original_title_to_write}\n")
                else:
                    # 如果文件中没有 '## new' 区域，则在文件末尾创建它
                    if lines and not lines[-1].endswith('\n'): lines.append('\n')
                    lines.append("\n## new\n\n")
                    lines.append(f"{original_title_to_write}\n")

                # 重置文件指针到开头，清空文件，然后写入更新后的内容
                f.seek(0)
                f.truncate()
                f.writelines(lines)

        except FileNotFoundError:
            logger.error(f"严重错误: '{LIST_FILE_PATH}' 未找到 LIST.md 文件")
        except Exception as e:
            logger.error(f"严重错误: 向 LIST.md 添加标题时发生错误: {e}")

def update_title_in_list(old_title: str, new_title: str):
    """
    在 LIST.md 文件中查找一个旧标题并将其替换为新标题。
    
    此函数主要用于处理重定向。
    当 WikipediaClient 发现一个页面名因重定向而指向另一个页面名时，可以使用此函数更新列表。

    Args:
        old_title (str): 文件中当前存在的实体标题。
        new_title (str): 用于替换旧标题的新标题。
    """
    # --- 步骤 1: 预处理与验证 ---
    old_title_processed = old_title.strip()
    new_title_processed = new_title.replace('_', ' ').strip()

    # 如果任一为空或两者相同，则无需操作
    if not old_title_processed or not new_title_processed or old_title_processed == new_title_processed:
        return

    with LIST_MD_LOCK:
        try:
            # --- 步骤 2: 读取文件并进行预查重 ---
            with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # 创建一个包含所有现有条目（已处理）的集合，用于查重
            existing_entries = {line.strip() for line in lines if line.strip() and not line.strip().startswith(('##', '//'))}
            existing_simplified_entries = {t2s_converter.convert(entry) for entry in existing_entries}
            
            is_duplicate = (
                new_title_processed in existing_entries or
                t2s_converter.convert(new_title_processed) in existing_simplified_entries
            )

            # --- 步骤 3: 基于查重结果构建新文件内容 ---
            updated_lines = []
            was_updated = False # 标记是否找到了 old_title
            
            for line in lines:
                if line.strip() == old_title_processed:
                    was_updated = True
                    if not is_duplicate:
                        # new_title 不是重复项，执行替换
                        updated_lines.append(f"{new_title_processed}\n")
                    # else: 如果 is_duplicate == True，则不添加任何内容，从而实现删除
                else:
                    # 保留所有其他行
                    updated_lines.append(line)

            # --- 步骤 4: 写回文件并提供明确的日志 ---
            if was_updated:
                if is_duplicate:
                    logger.info(f"检测到重定向: '{old_title_processed}' -> '{new_title_processed}'。因目标已存在，将删除旧条目。")
                else:
                    logger.info(f"正在 LIST.md 中将 '{old_title_processed}' 更新为 '{new_title_processed}'...")
                
                with open(LIST_FILE_PATH, 'w', encoding='utf-8') as f:
                    f.writelines(updated_lines)
            else:
                logger.warning(f"未能在 LIST.md 中找到待更新的标题: '{old_title_processed}'")

        except FileNotFoundError:
            logger.error(f"严重错误: LIST.md 文件未找到于 '{LIST_FILE_PATH}'")
        except Exception as e:
            logger.error(f"严重错误: 更新 LIST.md 标题时发生错误: {e}")

def sanitize_filename(name: str) -> str:
    """
    移除文件名中不合法或不推荐的字符。
    """
    return re.sub(r'[\\/*?:"<>|]', '_', name)
