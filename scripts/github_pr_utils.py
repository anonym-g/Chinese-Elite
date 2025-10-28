# scripts/github_pr_utils.py

import os
import sys
import subprocess
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from opencc import OpenCC

# --- 路径配置 ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

# --- 日志与工具初始化 ---
logger = logging.getLogger(__name__)
t2s = OpenCC('t2s') # 繁转简

# --- 全局常量 ---
# 定义实体类别，用于解析和构建 LIST.md
ENTITY_CATEGORIES = ['Person', 'Organization', 'Movement', 'Event', 'Location', 'Document']

# --- 辅助函数 ---
def _run_command(command: list[str], check: bool = True, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """
    一个辅助函数，用于执行shell命令。
    接受一个可选的 `env` 参数，为子进程设置环境变量。
    """
    logger.info(f"正在执行命令: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=check,
            encoding='utf-8',
            cwd=ROOT_DIR,
            env=env
        )
        if result.stdout:
            logger.info(f"命令输出:\n{result.stdout.strip()}")
        if result.stderr:
            logger.warning(f"命令错误输出:\n{result.stderr.strip()}")
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"命令执行失败: {' '.join(command)}\n退出码: {e.returncode}\n标准错误:\n{e.stderr}")
        raise
    except FileNotFoundError:
        logger.critical(f"严重错误: 命令 '{command[0]}' 未找到。请确保 git 和 gh (GitHub CLI) 已安装并位于系统的 PATH 中。")
        raise

def _parse_list_md(file_path: str) -> Dict[str, set]:
    """
    解析 LIST.md 文件，用于智能去重。
    """
    all_entries: Dict[str, set] = {cat.lower(): set() for cat in ENTITY_CATEGORIES}
    all_entries['new'] = set()
    
    current_category: Optional[str] = None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('## '):
                    category_name = line[3:].strip().lower()
                    current_category = category_name if category_name in all_entries else None
                    continue
                
                if current_category and line and not line.startswith('//'):
                    simplified_line = t2s.convert(line)
                    all_entries[current_category].add(simplified_line)
    except FileNotFoundError:
        logger.error(f"LIST.md 文件未找到: {file_path}")
    return all_entries

def create_list_update_pr(submissions: Dict[str, list]) -> Optional[Dict[str, Any]]:
    """
    接收机器人收集的条目，执行Git操作，并创建Pull Request。
    """
    branch_name: Optional[str] = None
    try:
        # --- 1. 加载环境变量 ---
        github_token = os.getenv("GITHUB_BOT_ACCOUNT_TOKEN")
        github_username = os.getenv("GITHUB_BOT_ACCOUNT_USERNAME")
        upstream_repo = os.getenv("UPSTREAM_REPO_URL")

        if not all([github_token, github_username, upstream_repo]):
            logger.critical("错误: 缺少必要的GitHub环境变量 (GITHUB_BOT_ACCOUNT_TOKEN, GITHUB_BOT_ACCOUNT_USERNAME, UPSTREAM_REPO_URL)。")
            return None

        assert github_token is not None
        assert github_username is not None
        assert upstream_repo is not None
        
        # --- 2. 准备Git操作所需的基本信息 ---
        list_md_path = os.path.join(ROOT_DIR, 'data', 'LIST.md')
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        branch_name = f"bot/add-batch-{timestamp}"
        
        # --- 3. 智能去重和条目整理 ---
        existing_entries = _parse_list_md(list_md_path)
        main_categories_entries = {entry for category, entries in existing_entries.items() if category != 'new' for entry in entries}
        
        final_additions: Dict[str, list] = {cat: [] for cat in ENTITY_CATEGORIES}
        entries_to_remove_from_new = set()
        added_count, skipped_count = 0, 0

        for category, user_entries in submissions.items():
            for entry in user_entries:
                simplified_entry = t2s.convert(entry)
                if simplified_entry in main_categories_entries:
                    skipped_count += 1
                    continue
                if simplified_entry in existing_entries.get('new', set()):
                    entries_to_remove_from_new.add(simplified_entry)
                final_additions[category].append(entry)
                added_count += 1
        
        if added_count == 0:
            logger.info("所有提交的条目都已存在，无需创建PR。")
            return {'pr_url': None, 'added_count': 0, 'skipped_count': skipped_count}

        # --- 4. 配置Git环境并同步最新的主分支 ---
        custom_env = os.environ.copy()
        custom_env["GH_TOKEN"] = github_token
        
        _run_command(['git', 'config', '--local', 'user.name', github_username])
        _run_command(['git', 'config', '--local', 'user.email', f'{github_username}@users.noreply.github.com'])
        _run_command(['git', 'remote', 'set-url', 'origin', f'https://{github_username}:{github_token}@github.com/{github_username}/Chinese-Elite.git'])
        _run_command(['git', 'checkout', 'main'])
        
        upstream_url = f"https://github.com/{upstream_repo}"
        _run_command(['git', 'remote', 'remove', 'upstream'], check=False)
        _run_command(['git', 'remote', 'add', 'upstream', upstream_url])
        _run_command(['git', 'fetch', 'upstream'])
        _run_command(['git', 'reset', '--hard', 'upstream/main'])
        
        _run_command(['git', 'checkout', '-b', branch_name])

        # --- 5. 修改 LIST.md 文件 ---
        logger.info(f"正在重写 {list_md_path} ...")
        with open(list_md_path, 'r+', encoding='utf-8') as f:
            lines = f.readlines()
            new_lines = []
            current_category: Optional[str] = None
            
            for line in lines:
                stripped_line = line.strip()
                if stripped_line.startswith('## '):
                    category_name = stripped_line[3:].strip().lower()
                    new_lines.append(line)
                    category_key = next((cat for cat in final_additions if cat.lower() == category_name), None)
                    if category_key:
                        for new_entry in sorted(final_additions[category_key]):
                            new_lines.append(f"{new_entry}\n")
                    current_category = category_name if category_name in existing_entries else None
                elif current_category == 'new':
                    if t2s.convert(stripped_line) not in entries_to_remove_from_new:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            
            f.seek(0)
            f.truncate()
            f.writelines(new_lines)
            
        # --- 6. Git提交、推送并创建PR ---
        commit_message = f"feat(list): Add {added_count} new entries via bot"
        pr_title = f"Contribution via Bot: Add {added_count} new entries"
        pr_body = (
            f"This PR was automatically generated by the bot via the `/list` command.\n\n"
            f"**Summary:**\n"
            f"- Successfully added: {added_count}\n"
            f"- Skipped (duplicates): {skipped_count}\n\n"
            f"**Details:**\n" +
            "\n".join(f"**{cat}**:\n" + "\n".join(f"- `{entry}`" for entry in entries) 
                      for cat, entries in final_additions.items() if entries)
        )
        
        _run_command(['git', 'add', list_md_path])
        _run_command(['git', 'commit', '-m', commit_message])
        _run_command(['git', 'push', '-u', 'origin', branch_name])
        
        pr_result = _run_command([
            'gh', 'pr', 'create',
            '--repo', upstream_repo,
            '--title', pr_title,
            '--body', pr_body,
            '--head', f'{github_username}:{branch_name}'
        ], env=custom_env)
        
        pr_url = pr_result.stdout.strip()

        # --- 7. 清理本地环境 ---
        _run_command(['git', 'checkout', 'main'])
        if branch_name:
            _run_command(['git', 'branch', '-D', branch_name], check=False)
        
        return {'pr_url': pr_url, 'added_count': added_count, 'skipped_count': skipped_count}

    except Exception as e:
        logger.error(f"创建PR过程中发生严重错误。", exc_info=True)
        try:
            _run_command(['git', 'checkout', 'main'], check=False)
            if branch_name is not None:
                _run_command(['git', 'branch', '-D', branch_name], check=False)
        except Exception as cleanup_e:
            logger.error(f"错误后清理失败: {cleanup_e}")
        return None
