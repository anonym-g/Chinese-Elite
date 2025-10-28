# scripts/github_pr_utils.py

import os
import sys
import subprocess
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
import re

from opencc import OpenCC

# --- 路径配置 ---
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

# --- 模块导入 ---
from scripts.clients.wikipedia_client import WikipediaClient

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
            env=env,
            timeout=300
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
    except subprocess.TimeoutExpired:
        logger.error(f"命令执行超时: {' '.join(command)}")
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

def create_list_update_pr(submissions: Dict[str, list], wiki_client: WikipediaClient) -> Optional[Dict[str, Any]]:
    """
    接收机器人收集的条目，执行验证、Git操作，并创建Pull Request。
    """
    branch_name: Optional[str] = None
    try:
        # --- 1. 初始化报告和最终待添加列表 ---
        report = {
            'accepted': [], 'corrected': [], 'rejected': [], 'skipped': []
        }
        final_additions: Dict[str, list] = {cat: [] for cat in ENTITY_CATEGORIES}
        
        # --- 2. 加载现有条目用于去重 ---
        list_md_path = os.path.join(ROOT_DIR, 'data', 'LIST.md')
        existing_entries = _parse_list_md(list_md_path)
        all_existing_simplified = {entry for entries in existing_entries.values() for entry in entries}

        # --- 3. 智能去重、验证和整理 ---
        logger.info("开始对提交的条目进行去重和维基百科验证...")
        for category, user_entries in submissions.items():
            for entry in user_entries:
                simplified_entry = t2s.convert(entry)
                if simplified_entry in all_existing_simplified:
                    report['skipped'].append(entry)
                    continue

                # --- 开始维基百科验证 ---
                lang_match = re.match(r'\((?P<lang>[a-z]{2})\)\s*', entry)
                lang = 'zh'
                name_to_check = entry
                if lang_match:
                    lang = lang_match.group('lang')
                    name_to_check = entry[lang_match.end():].strip()
                
                status, detail = wiki_client.check_link_status(name_to_check, lang=lang)

                if status in ["OK", "SIMP_TRAD_REDIRECT"]:
                    final_additions[category].append(entry)
                    report['accepted'].append(entry)
                    all_existing_simplified.add(simplified_entry)

                elif status == "REDIRECT":
                    if not detail:
                        report['rejected'].append((entry, "重定向目标为空"))
                        continue
                    
                    corrected_name = f"({lang}) {detail}" if lang != 'zh' else detail
                    if t2s.convert(corrected_name) in all_existing_simplified:
                        report['skipped'].append(entry)
                        continue

                    final_additions[category].append(corrected_name)
                    report['corrected'].append((entry, corrected_name))
                    all_existing_simplified.add(t2s.convert(corrected_name))
                
                elif status == "DISAMBIG":
                    report['rejected'].append((entry, "消歧义页"))
                elif status == "NO_PAGE":
                    report['rejected'].append((entry, "页面不存在"))
                else: # ERROR 或其他
                    report['rejected'].append((entry, f"验证错误({status})"))
        
        added_count = len(report['accepted']) + len(report['corrected'])
        if added_count == 0:
            logger.info("所有提交的条目都无效或已存在，无需创建PR。")
            return {'pr_url': None, 'report': report}

        # --- 4. 配置Git环境并同步最新的主分支 ---
        github_token = os.getenv("GITHUB_BOT_ACCOUNT_TOKEN")
        github_username = os.getenv("GITHUB_BOT_ACCOUNT_USERNAME")
        upstream_repo = os.getenv("UPSTREAM_REPO_URL")
        http_proxy = os.getenv("HTTP_PROXY")
        https_proxy = os.getenv("HTTPS_PROXY")

        if not all([github_token, github_username, upstream_repo]):
            logger.critical("错误: 缺少必要的GitHub环境变量。")
            return None

        assert github_token is not None
        assert github_username is not None
        assert upstream_repo is not None
        
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        branch_name = f"bot/add-batch-{timestamp}"
        
        custom_env = os.environ.copy()
        custom_env["GH_TOKEN"] = github_token
        if http_proxy: custom_env["HTTP_PROXY"] = http_proxy
        if https_proxy: custom_env["HTTPS_PROXY"] = https_proxy
        
        _run_command(['git', 'init'])
        _run_command(['git', 'config', '--local', 'user.name', github_username])
        _run_command(['git', 'config', '--local', 'user.email', f'{github_username}@users.noreply.github.com'])
        
        origin_url = f'https://{github_username}:{github_token}@github.com/{github_username}/Chinese-Elite.git'
        upstream_url = f"https://github.com/{upstream_repo}.git"
        
        _run_command(['git', 'remote', 'add', 'origin', origin_url])
        _run_command(['git', 'remote', 'add', 'upstream', upstream_url])
        _run_command(['git', 'fetch', 'upstream'], env=custom_env)
        _run_command(['git', 'checkout', '-b', 'main', 'upstream/main'])
        
        _run_command(['git', 'checkout', '-b', branch_name])

        # --- 5. 修改 LIST.md 文件 ---
        logger.info(f"正在使用验证后的条目重写 {list_md_path} ...")
        
        valid_categories = {cat.lower() for cat in ENTITY_CATEGORIES} | {'new'}
        
        with open(list_md_path, 'r+', encoding='utf-8') as f:
            lines = f.readlines()
            new_lines = []
            
            for line in lines:
                stripped_line = line.strip()
                new_lines.append(line)
                if stripped_line.startswith('## '):
                    category_name = stripped_line[3:].strip().lower()
                    category_key = next((cat for cat in final_additions if cat.lower() == category_name), None)
                    if category_key:
                        for new_entry in sorted(final_additions[category_key]):
                            new_lines.append(f"{new_entry}\n")
            
            f.seek(0)
            f.truncate()
            f.writelines(new_lines)
            
        # --- 6. Git提交、推送并创建PR ---
        commit_message = f"feat(list): Add {added_count} new entries via bot"
        pr_title = f"Contribution via Bot: Add {added_count} new entries"
        pr_body = (
            f"This PR was automatically generated by the bot via the `/list` command after validation.\n\n"
            f"**Summary:**\n"
            f"- Added/Corrected: {added_count}\n"
            f"- Skipped (duplicates): {len(report['skipped'])}\n"
            f"- Rejected (invalid): {len(report['rejected'])}\n"
        )
        
        _run_command(['git', 'add', list_md_path])
        _run_command(['git', 'commit', '-m', commit_message])
        _run_command(['git', 'push', '-u', 'origin', branch_name], env=custom_env)
        
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
        
        return {'pr_url': pr_url, 'report': report}

    except Exception as e:
        logger.error(f"创建PR过程中发生严重错误。", exc_info=True)
        try:
            # 在删除分支前，先切换回 main 分支
            _run_command(['git', 'checkout', 'main'], check=False)
            if branch_name is not None:
                _run_command(['git', 'branch', '-D', branch_name], check=False)
        except Exception as cleanup_e:
            logger.error(f"错误后清理失败: {cleanup_e}")
        return None
