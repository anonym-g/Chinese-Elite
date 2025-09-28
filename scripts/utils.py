# scripts/utils.py

import requests
import json
import re
from urllib.parse import urlparse, urlunparse, quote
from opencc import OpenCC
from datetime import datetime

from config import WIKI_API_URL, USER_AGENT

class WikipediaClient:
    """用于与中文维基百科交互的客户端类。"""

    def __init__(self, user_agent=USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': user_agent})
        self.converter = OpenCC('t2s')  # 台湾正体 -> 简体

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
        # 清理可能不干净的ID
        
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
        """检查维基百科页面的状态。"""
        try:
            encoded_id = quote(node_id.replace(" ", "_"))
            url = f"https://zh.wikipedia.org/w/index.php?title={encoded_id}&action=raw"
            response = self.session.get(url, timeout=15)

            if response.status_code == 404:
                return "NO_PAGE", None
            
            response.raise_for_status()
            content = response.text.strip()
            
            if not content:
                return "NO_PAGE", None

            normalized_content = content.lower().lstrip()
            if normalized_content.startswith(("#redirect", "#重定向")):
                match = re.search(r'\[\[(.*?)\]\]', content)
                if match:
                    redirect_target = match.group(1).strip().split('#')[0]
                    simplified_target = self.converter.convert(redirect_target)
                    norm_simplified_target = simplified_target.replace('_', ' ').lower()
                    norm_node_id = node_id.replace('_', ' ').lower()
                    
                    if norm_simplified_target == norm_node_id:
                        return "SIMP_TRAD_REDIRECT", None
                    else:
                        return "REDIRECT", redirect_target
                else:
                    return "ERROR", "Malformed redirect"

            if "{{disambig" in normalized_content or "{{hndis" in normalized_content:
                return "DISAMBIG", None

        except requests.exceptions.RequestException as e:
            return "ERROR", str(e)
            
        return "OK", None
