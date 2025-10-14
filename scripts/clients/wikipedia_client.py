# scripts/clients/wikipedia_client.py

import requests
import json
import re
import sys
import os
from urllib.parse import urlparse, urlunparse, quote
from opencc import OpenCC
from datetime import datetime
import time
import random
import logging
from curl_cffi import requests as cffi_requests

# 使用相对路径导入
from ..config import WIKI_API_URL_TPL, USER_AGENT, BAIDU_BASE_URL, CDSPACE_BASE_URL, LIST_FILE_PATH, CACHE_DIR
from ..api_rate_limiter import wiki_sync_limiter
from ..utils import add_title_to_list

logger = logging.getLogger(__name__)

class WikipediaClient:
    """
    用于与网络资源交互的客户端类，主要负责处理维基百科的数据获取和缓存管理。

    功能包括:
    - 获取维基百科页面的 Wikidata Q-Code，并进行缓存。
    - 获取页面的 Wikitext 源码，并处理简繁重定向。
    - 检查页面的最新修订时间。
    - 检查一个实体名称的链接状态（是否存在于维基、百度百科、中国数字时代等）。
    - 统一管理所有相关的缓存文件。
    """

    def __init__(self, user_agent=USER_AGENT):
        # 初始化两个Session：一个常规，一个用于特殊目标 (e.g., BAIDU)
        self.session = requests.Session()
        self.session.trust_env = True
        self.cffi_session = cffi_requests.Session()
        
        self.session.headers.update({'User-Agent': user_agent})
        self.cffi_session.headers.update({'User-Agent': user_agent})
        
        self.t2s_converter = OpenCC('t2s') # 繁转简
        self.s2t_converter = OpenCC('s2t') # 简转繁

        # Wikidata: Q-Code缓存
        self.qcode_cache_path = os.path.join(CACHE_DIR, 'qcode_cache.json')
        self.qcode_cache = self._load_cache(self.qcode_cache_path)
        self.qcode_cache_updated = False

        # 链接状态缓存
        self.link_cache_path = os.path.join(CACHE_DIR, 'wiki_link_status_cache.json')
        self.link_cache = self._load_cache(self.link_cache_path)
        self.link_cache_updated = False

        # 为加速缓存查询，在内存中创建一个反向映射
        self._title_to_qcode_map = self._build_reverse_cache()

    def _load_cache(self, path: str) -> dict:
        """通用缓存加载函数。"""
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                logger.info(f"成功加载缓存文件: {os.path.basename(path)}")
                return json.load(f)
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"无法读取或解析缓存文件 {path} - {e}")
            return {}

    def save_caches(self):
        """统一保存所有已更新的缓存。"""
        if self.qcode_cache_updated:
            self._save_cache(self.qcode_cache_path, self.qcode_cache, "Q-Code")
            self.qcode_cache_updated = False
        if self.link_cache_updated:
            self._save_cache(self.link_cache_path, self.link_cache, "链接状态")
            self.link_cache_updated = False

    def _save_cache(self, path: str, data: dict, cache_name: str):
        """通用缓存保存函数。"""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info(f"{cache_name}缓存已成功更新到磁盘。")
        except IOError as e:
            logger.warning(f"无法写入{cache_name}缓存文件 - {e}")
    
    def _build_reverse_cache(self) -> dict:
        """根据已加载的缓存创建 title -> qcode 的反向映射以提速"""
        reverse_map = {}
        for qcode, titles in self.qcode_cache.items():
            for title in titles:
                reverse_map[title] = qcode
        return reverse_map
    
    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def _fetch_qcode_from_api(self, article_title: str, lang: str = 'zh') -> str | None:
        """内部辅助方法，仅负责执行一次API查询并解析结果。"""
        api_url = WIKI_API_URL_TPL.format(lang=lang)
        params = {
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "titles": article_title, "format": "json", "formatversion": "2", "redirects": "1",
        }
        try:
            response = self.session.get(api_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if "pages" not in data.get("query", {}): return None
            page = data["query"]["pages"][0]
            if page.get("missing"): return None
            
            return page.get("pageprops", {}).get("wikibase_item")
        except requests.exceptions.RequestException:
            return None

    def get_qcode(self, article_title: str, lang: str = 'zh') -> str | None:
        """
        根据维基百科文章标题获取其对应的Wikidata Q-Code。
        新增逻辑：当简体查询失败时，自动尝试使用繁体进行后备查询 (仅对中文)。
        """
        # 0. 优先从内存中的反向映射缓存中快速查找
        if article_title in self._title_to_qcode_map:
            return self._title_to_qcode_map[article_title]

        qcode = None
        
        # 1. 优先使用原始标题进行查询
        logger.info(f"正在通过API查询 ({lang}) '{article_title}'...")
        qcode = self._fetch_qcode_from_api(article_title, lang)

        # 2. 如果是中文且查询失败，尝试简繁转换
        traditional_title = ""
        if not qcode and lang == 'zh':
            traditional_title = self.s2t_converter.convert(article_title)
            if traditional_title != article_title:
                logger.info(f"简体查询失败，尝试后备查询 '{traditional_title}' (繁体)...")
                qcode = self._fetch_qcode_from_api(traditional_title, lang)

        # 3. 如果最终找到了Q-Code，则更新缓存
        if qcode:
            logger.info(f"成功获取Q-Code: {qcode}")
            
            # 获取或创建该Q-Code的标题列表
            titles_in_cache = self.qcode_cache.get(qcode, [])
            
            # 将本次查询涉及的标题都加入缓存，并去重
            titles_to_add = [article_title]
            if traditional_title and traditional_title != article_title:
                titles_to_add.append(traditional_title)
            
            updated = False
            for title in titles_to_add:
                if title not in titles_in_cache:
                    titles_in_cache.append(title)
                    self._title_to_qcode_map[title] = qcode # 更新内存中的反向映射
                    updated = True

            if updated:
                self.qcode_cache[qcode] = titles_in_cache
                self.qcode_cache_updated = True
            
            return qcode

        # 4. 如果两种尝试都失败了，则返回None
        return None

    def _build_raw_url(self, article_title: str, lang: str = 'zh') -> str:
        """构建稳定、统一的原始Wikitext获取URL。"""
        host = f"{lang}.wikipedia.org"
        raw_url_parts = (
            'https', host, '/w/index.php', '',
            f'title={quote(article_title)}&action=raw', ''
        )
        return urlunparse(raw_url_parts)

    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def get_wikitext(self, article_title: str, lang: str = 'zh') -> tuple[str | None, str | None]:
        """
        获取给定维基百科文章标题的Wikitext。如果语言是中文，则会进行简繁重定向检查和最终内容的简体转换。

        Returns:
            一个元组 (wikitext, final_article_title)，若失败则返回 (None, None)。
        """
        
        current_title_to_fetch = article_title
        raw_url = self._build_raw_url(current_title_to_fetch, lang)
        logger.info(f"正在获取 ({lang}) '{current_title_to_fetch}' 的Wikitext源码: {raw_url}")

        try:
            response = self.session.get(raw_url, timeout=20)
            response.raise_for_status()
            
            content = response.text
            
            # 当语言为中文时，进行重定向检查和简繁转换
            if lang == 'zh':
                normalized_content = content.strip().lower()
                if normalized_content.startswith(("#redirect", "#重定向")):
                    match = re.search(r'\[\[(.*?)\]\]', content)
                    if match:
                        redirect_target = match.group(1).strip().split('#')[0]
                        
                        simplified_target = self.t2s_converter.convert(redirect_target)
                        simplified_original = self.t2s_converter.convert(article_title)

                        norm_simplified_target = simplified_target.replace('_', ' ').lower()
                        norm_simplified_original = simplified_original.replace('_', ' ').lower()
                        
                        if norm_simplified_target == norm_simplified_original:
                            logger.info(f"页面 '{article_title}' 是一个简繁重定向，目标为 '{redirect_target}'。将使用目标页面获取内容。")
                            
                            current_title_to_fetch = redirect_target
                            new_raw_url = self._build_raw_url(current_title_to_fetch, lang)
                            logger.info(f"重新获取 '{current_title_to_fetch}' 的Wikitext源码: {new_raw_url}")
                            
                            response = self.session.get(new_raw_url, timeout=20)
                            response.raise_for_status()
                            content = response.text

                # 对最终内容进行简体转换
                final_wikitext = self.t2s_converter.convert(content)
                logger.info(f"Wikitext已成功获取并转换为简体（标题: '{current_title_to_fetch}'）。")
                return final_wikitext, current_title_to_fetch
            else:
                # 对于非中文，直接返回原文
                logger.info(f"Wikitext已成功获取（标题: '{current_title_to_fetch}'）。")
                return content, current_title_to_fetch

        except requests.exceptions.RequestException as e:
            logger.error(f"获取Wikitext失败 (标题: '{current_title_to_fetch}') - {e}")
            return None, None

    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def get_latest_revision_time(self, article_title: str, lang: str = 'zh') -> datetime | None:
        """通过API获取页面的最新修订时间（UTC）。"""
        api_url = WIKI_API_URL_TPL.format(lang=lang)
        params = {
            "action": "query", "prop": "revisions", "titles": article_title,
            "rvlimit": "1", "rvprop": "timestamp", "format": "json", "formatversion": "2"
        }
        try:
            response = self.session.get(api_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            page = data["query"]["pages"][0]
            if "revisions" in page and page["revisions"]:
                timestamp_str = page["revisions"][0]["timestamp"]
                return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning(f"获取 '{article_title}' ({lang}) 的维基修订历史失败 - {e}")
        return None

    def check_link_status(self, node_id: str, lang: str = 'zh') -> tuple[str, str | None]:
        """
        检查节点名称的状态，内置缓存和多源回退逻辑。
        """
        if node_id in self.link_cache:
            cached = self.link_cache[node_id]
            return cached['status'], cached.get('detail')

        status, detail = self._check_wiki_status_api(node_id, lang=lang)

        if status in ["NO_PAGE", "ERROR"] and lang == 'zh':
            if self.check_generic_url(BAIDU_BASE_URL, node_id):
                status = "BAIDU"
            elif self.check_generic_url(CDSPACE_BASE_URL, node_id):
                status = "CDT"
        
        if status not in ["NO_PAGE", "ERROR"]:
            self.link_cache[node_id] = {
                'status': status, 
                'detail': detail,
                'timestamp': datetime.now().isoformat()
            }
            self.link_cache_updated = True
            
        return status, detail

    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def _check_wiki_status_api(self, node_id: str, lang: str = 'zh') -> tuple[str, str | None]:
        """执行维基百科API检查。"""
        try:
            encoded_id = quote(node_id.replace(" ", "_"))
            url = f"https://{lang}.wikipedia.org/w/index.php?title={encoded_id}&action=raw"
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
                    
                    if lang == 'zh':
                        simplified_target = self.t2s_converter.convert(redirect_target)
                        norm_simplified_target = simplified_target.replace('_', ' ').lower()
                        norm_node_id = node_id.replace('_', ' ').lower()
                        
                        if norm_simplified_target == norm_node_id:
                            return "SIMP_TRAD_REDIRECT", redirect_target
                        else:
                            return "REDIRECT", redirect_target
                    else:
                        return "REDIRECT", redirect_target
                else:
                    return "ERROR", "Malformed redirect"

            if "{{disambig" in normalized_content or "{{hndis" in normalized_content:
                return "DISAMBIG", None

        except requests.exceptions.RequestException as e:
            return "ERROR", str(e)
            
        return "OK", None

    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def check_generic_url(self, base_url: str, node_id: str) -> bool:
        """智能检查URL是否存在。"""
        url = f"{base_url}{quote(node_id.replace(' ', '_'))}"

        if BAIDU_BASE_URL in base_url:
            try:
                response = self.cffi_session.get(url, impersonate="chrome110", timeout=15, allow_redirects=True)
                delay = random.uniform(1.0, 2.5)
                time.sleep(delay)
                return response.status_code < 400
            except Exception:
                return False
        else:
            response = None
            try:
                response = self.session.get(url, timeout=10, allow_redirects=True, stream=True)
                return response.status_code < 400
            except requests.exceptions.RequestException:
                return False
            finally:
                if response:
                    response.close()
