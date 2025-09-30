# scripts/utils.py

import requests
import json
import re
import sys
from urllib.parse import urlparse, urlunparse, quote
from opencc import OpenCC
from datetime import datetime
import time
import random
from curl_cffi import requests as cffi_requests

from config import WIKI_API_URL, USER_AGENT, BAIDU_BASE_URL, LIST_FILE_PATH

def add_title_to_list(title_to_add: str):
    """
    将一个新的标题添加到 LIST.txt 文件的 'new' 类别下。
    添加前检查。若标题已存在于文件中，则不进行任何操作。
    """
    if not title_to_add:
        return

    try:
        with open(LIST_FILE_PATH, 'r+', encoding='utf-8') as f:
            # 1. 读取所有行并检查是否存在
            lines = f.readlines()
            # 使用 strip() 来处理行尾的换行符和可能的空格，进行精确匹配
            if any(title_to_add.strip() == line.strip() for line in lines):
                return # 如果已存在，则静默返回

            # 2. 如果不存在，则准备添加
            print(f"[*] 正在将新标题 '{title_to_add}' 添加到 LIST.txt 的 'new' 类别下...")
            
            # 寻找 'new' 栏目的插入位置
            try:
                new_section_index = [i for i, line in enumerate(lines) if line.strip() == 'new'][0]
                lines.insert(new_section_index + 1, f"{title_to_add}\n")
            except IndexError:
                # 如果没有找到 'new' 栏目，则在文件末尾添加
                if lines and not lines[-1].endswith('\n'):
                    lines.append('\n')
                lines.append("new\n")
                lines.append(f"{title_to_add}\n")

            # 3. 将更新后的内容写回文件
            f.seek(0) # 回到文件开头
            f.truncate() # 清空文件
            f.writelines(lines) # 写入新内容

    except FileNotFoundError:
        print(f"[!] 严重错误: LIST.txt 文件未找到于 '{LIST_FILE_PATH}'", file=sys.stderr)
    except Exception as e:
        print(f"[!] 严重错误: 向 LIST.txt 添加标题时发生错误: {e}", file=sys.stderr)


class WikipediaClient:
    """
    用于与网络资源交互的客户端类。
    - 默认使用 'requests' 库
    - 针对特定网站 (如百度) 切换使用 'curl-cffi'
    """

    def __init__(self, user_agent=USER_AGENT):
        # 初始化两个Session：一个常规，一个用于特殊目标
        self.session = requests.Session()
        self.cffi_session = cffi_requests.Session()
        
        self.session.headers.update({'User-Agent': user_agent})
        self.cffi_session.headers.update({'User-Agent': user_agent})
        
        self.converter = OpenCC('t2s')

    def _build_raw_url(self, article_title: str) -> str:
        """构建稳定、统一的原始Wikitext获取URL。"""
        raw_url_parts = (
            'https', 'zh.wikipedia.org', '/w/index.php', '',
            f'title={quote(article_title)}&action=raw', ''
        )
        return urlunparse(raw_url_parts)

    def get_simplified_wikitext(self, article_title: str) -> tuple[str | None, str | None]:
        """
        获取给定维基百科文章标题的简体中文Wikitext。

        Returns:
            一个元组 (simplified_wikitext, article_title)，若失败则返回 (None, None)。
        """
        
        raw_url = self._build_raw_url(article_title)
        print(f"[*] 正在获取 '{article_title}' 的Wikitext源码: {raw_url}")

        try:
            response = self.session.get(raw_url, timeout=20)
            response.raise_for_status()
            traditional_wikitext = response.text
            simplified_wikitext = self.converter.convert(traditional_wikitext)
            print("[*] Wikitext已成功获取并转换为简体中文。")
            return simplified_wikitext, article_title
        except requests.exceptions.RequestException as e:
            print(f"[!] 错误：获取Wikitext失败 - {e}")
            return None, None

    def get_latest_revision_time(self, article_title: str) -> datetime | None:
        """通过API获取页面的最新修订时间（UTC）。"""
        params = {
            "action": "query", "prop": "revisions", "titles": article_title,
            "rvlimit": "1", "rvprop": "timestamp", "format": "json", "formatversion": "2"
        }
        try:
            response = self.session.get(WIKI_API_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            page = data["query"]["pages"][0]
            if "revisions" in page and page["revisions"]:
                timestamp_str = page["revisions"][0]["timestamp"]
                return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"[!] 警告：获取 '{article_title}' 的维基修订历史失败 - {e}")
        return None

    def check_link_status(self, node_id: str) -> tuple[str, str | None]:
        """
        检查维基百科页面的状态。
        - OK: 页面有效。
        - SIMP_TRAD_REDIRECT: 页面仅因简繁差异重定向，返回目标页面标题。
        - REDIRECT: 页面因其他原因重定向，返回目标页面标题。
        - DISAMBIG: 页面是消歧义页。
        - NO_PAGE: 页面不存在。
        - ERROR: 检查时出错。
        """
        try:
            encoded_id = quote(node_id.replace(" ", "_"))
            url = f"https://zh.wikipedia.org/w/index.php?title={encoded_id}&action=raw"
            response = self.session.get(url, timeout=15)

            if response.status_code == 404:
                return "NO_PAGE", None
            
            response.raise_for_status()
            content = response.text.strip()
            if not content: return "NO_PAGE", None
            normalized_content = content.lower().lstrip()
            if normalized_content.startswith(("#redirect", "#重定向")):
                match = re.search(r'\[\[(.*?)\]\]', content)
                if match:
                    redirect_target = match.group(1).strip().split('#')[0]
                    simplified_target = self.converter.convert(redirect_target)
                    norm_simplified_target = simplified_target.replace('_', ' ').lower()
                    norm_node_id = node_id.replace('_', ' ').lower()
                    
                    if norm_simplified_target == norm_node_id:
                        return "SIMP_TRAD_REDIRECT", redirect_target
                    else:
                        return "REDIRECT", redirect_target
                else:
                    return "ERROR", "Malformed redirect"

            if "{{disambig" in normalized_content or "{{hndis" in normalized_content:
                return "DISAMBIG", None

        except requests.exceptions.RequestException as e:
            return "ERROR", str(e)
            
        return "OK", None

    def check_generic_url(self, base_url: str, node_id: str) -> bool:
        """
        智能检查URL是否存在。
        - 如果是百度百科，则使用 curl-cffi。
        - 其他网站（如中国数字空间）使用常规的 requests。
        """
        url = f"{base_url}{quote(node_id.replace(' ', '_'))}"

        if BAIDU_BASE_URL in base_url:
            try:
                response = self.cffi_session.get(url, impersonate="chrome110", timeout=15, allow_redirects=True)

                # 在每次请求百度后增加一个随机延迟，以避免触发速率限制
                delay = random.uniform(1.0, 2.5)
                # print(f"(延迟 {delay:.1f}s) ", end="") # 可以取消这行注释来显示延时
                time.sleep(delay)

                return response.status_code < 400
            except Exception:
                return False
        else:
            response = None
            try:
                response = self.session.get(url, timeout=10, allow_redirects=True, stream=True)
                is_ok = response.status_code < 400
                return is_ok
            except requests.exceptions.RequestException:
                return False
            finally:
                if response:
                    response.close()
