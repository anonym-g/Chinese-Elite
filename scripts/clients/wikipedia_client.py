# scripts/clients/wikipedia_client.py

import requests
from requests.adapters import HTTPAdapter
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
from ..utils import add_title_to_list, update_title_in_list

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

        # --- 连接池优化 ---
        adapter = HTTPAdapter(pool_connections=200, pool_maxsize=200)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        
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
    def _fetch_qcode_from_api(self, article_title: str, lang: str = 'zh') -> tuple[str | None, str | None]:
        """
        内部辅助方法，执行API查询并解析结果。
        从 pages 对象获取最终标题。
        返回一个元组： (qcode, final_title)。
        """
        api_url = WIKI_API_URL_TPL.format(lang=lang)
        params = {
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "titles": article_title, "format": "json", "formatversion": "2", "redirects": "1",
        }
        try:
            response = self.session.get(api_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            query = data.get("query", {})

            if "pages" not in query: 
                return None, None

            page = query["pages"][0]
            if page.get("missing"): 
                return None, None
            
            page_props = page.get("pageprops", {})
            
            # --- 检查页面是否为消歧义页 ---
            if "disambiguation" in page_props:
                logger.warning(f"页面 '{article_title}' 被解析为消歧义页，已忽略。")
                return None, None

            qcode = page_props.get("wikibase_item")
            # 直接从 page 对象获取标题。
            # API在处理 redirects=1 时，page 对象里的 title 字段就是重定向链解析完成后的最终页面标题。
            final_title = page.get("title")
            
            return qcode, final_title
        except requests.exceptions.RequestException:
            return None, None

    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def get_authoritative_title_by_qcode(self, qcode: str, lang: str = 'zh') -> dict:
        """
        根据Q-Code反向查询其在指定语言维基中的正确页面标题，并检查是否为消歧义页。
        返回一个字典: {'title': str | None, 'status': 'OK'|'DISAMBIG'|'NOT_FOUND'|'ERROR'}
        """
        # 步骤1: 从Wikidata获取sitelink
        wikidata_api_url = "https://www.wikidata.org/w/api.php"
        params_wd = {
            "action": "wbgetentities", "ids": qcode, "props": "sitelinks",
            "format": "json", "formatversion": "2"
        }
        try:
            response_wd = self.session.get(wikidata_api_url, params=params_wd, timeout=15)
            response_wd.raise_for_status()
            data_wd = response_wd.json()
            sitelinks = data_wd.get("entities", {}).get(qcode, {}).get("sitelinks", {})
            wiki_key = f"{lang}wiki"
            
            if wiki_key not in sitelinks:
                return {'title': None, 'status': 'NOT_FOUND'}
            article_title = sitelinks[wiki_key].get("title")
            if not article_title:
                 return {'title': None, 'status': 'NOT_FOUND'}
        except requests.exceptions.RequestException:
            return {'title': None, 'status': 'ERROR'}

        # 步骤2: 验证维基百科页面的状态（处理重定向和消歧义）
        api_url = WIKI_API_URL_TPL.format(lang=lang)
        params_wiki = {
            "action": "query", "prop": "pageprops", "ppprop": "disambiguation",
            "titles": article_title, "format": "json", "formatversion": "2", "redirects": "1",
        }
        try:
            response_wiki = self.session.get(api_url, params=params_wiki, timeout=15)
            response_wiki.raise_for_status()
            data_wiki = response_wiki.json()
            page = data_wiki.get("query", {}).get("pages", [{}])[0]

            if page.get("missing"): return {'title': None, 'status': 'NOT_FOUND'}

            final_title = page.get("title")
            is_disambiguation = "disambiguation" in page.get("pageprops", {})
            status = "DISAMBIG" if is_disambiguation else "OK"
            return {'title': final_title, 'status': status}
        except (requests.exceptions.RequestException, IndexError, KeyError):
            return {'title': None, 'status': 'ERROR'}
    
    @wiki_sync_limiter.limit # 应用维基同步装饰器
    def get_authoritative_title_and_status(self, article_title: str, lang: str = 'zh') -> dict:
        """
        在指定语言维基中获取正确页面标题，并检查其是否为消歧义页。
        返回: {'title': str | None, 'status': 'OK'|'DISAMBIG'|'NOT_FOUND'|'ERROR'}
        """
        api_url = WIKI_API_URL_TPL.format(lang=lang)
        params = {
            "action": "query",
            "prop": "pageprops",
            "ppprop": "disambiguation",
            "titles": article_title,
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
        }
        try:
            response = self.session.get(api_url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            page = data.get("query", {}).get("pages", [{}])[0]

            if page.get("missing"):
                return {'title': None, 'status': 'NOT_FOUND'}

            final_title = page.get("title")
            is_disambiguation = "disambiguation" in page.get("pageprops", {})
            status = "DISAMBIG" if is_disambiguation else "OK"
            return {'title': final_title, 'status': status}
        except (requests.exceptions.RequestException, IndexError, KeyError):
            return {'title': None, 'status': 'ERROR'}

    def get_qcode(self, article_title: str, lang: str = 'zh', force_refresh: bool = False) -> tuple[str | None, str | None]:
        """
        根据维基百科文章标题获取其对应的Wikidata Q-Code及正确页面名。
        确保在多级重定向下正确更新LIST.md。
        返回元组： (qcode, final_title)
        """
        # # 0. 优先从内存中的反向映射缓存中快速查找
        # if not force_refresh and article_title in self._title_to_qcode_map:
        #     qcode = self._title_to_qcode_map[article_title]
        #     # 通过API获取权威标题。避免返回别名。
        #     canonical_title = self._fetch_title_by_qcode(qcode, lang)
        #     # 如果API查询失败，回退到原始标题。
        #     final_title = canonical_title or article_title
        #     logger.info(f"缓存命中: '{article_title}' -> {qcode}。权威标题: '{final_title}'")
        #     return qcode, final_title

        qcode, final_title = None, None
        
        # 1. 优先使用原始标题进行查询
        logger.info(f"正在通过API查询 ({lang}) '{article_title}'...")
        qcode, final_title = self._fetch_qcode_from_api(article_title, lang)

        # 2. 如果是中文且查询失败，尝试简繁转换
        traditional_title = ""
        if not qcode and lang == 'zh':
            traditional_title = self.s2t_converter.convert(article_title)
            if traditional_title != article_title:
                logger.info(f"简体查询失败，尝试后备查询 '{traditional_title}' (繁体)...")
                # 后备查询也可能发生重定向，所以同样接收 final_title
                qcode, final_title = self._fetch_qcode_from_api(traditional_title, lang)

        # 3. 如果最终找到了Q-Code和最终标题
        if qcode and final_title:
            logger.info(f"成功获取Q-Code: {qcode} (最终页面: '{final_title}')")

            # 只要API返回的最终标题与请求标题不同，就意味着发生了至少一次重定向。
            if final_title != article_title:
                logger.info(f"检测到页面重定向: '{article_title}' -> '{final_title}'。正在更新 LIST.md...")
                # 使用 final_title 更新列表
                update_title_in_list(article_title, final_title)
            
            # 获取或创建该Q-Code的标题列表
            titles_in_cache = self.qcode_cache.get(qcode, [])
            
            # 将原始标题和权威标题都加入缓存，指向同一个Q-Code
            titles_to_add = {article_title, final_title}
            if traditional_title: # 如果进行了繁体尝试，也加入
                titles_to_add.add(traditional_title)
            
            updated = False
            for title in titles_to_add:
                if title and title not in titles_in_cache:
                    titles_in_cache.append(title)
                    self._title_to_qcode_map[title] = qcode # 更新内存中的反向映射
                    updated = True

            if updated:
                self.qcode_cache[qcode] = sorted(list(set(titles_in_cache))) # 去重并排序
                self.qcode_cache_updated = True
            
            return qcode, final_title

        # 4. 如果所有尝试都失败了，则返回None
        return None, None

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
        获取给定维基百科文章标题的Wikitext，并确保返回的是重定向解析完成后的最终标题和内容。

        Returns:
            一个元组 (wikitext, final_article_title)，若失败则返回 (None, None)。
        """
        # 步骤 1: 使用 API 方法预先获取最终的权威页面标题
        _, final_title = self._fetch_qcode_from_api(article_title, lang=lang)
        
        if not final_title:
            logger.error(f"无法为 '{article_title}' ({lang}) 解析到有效的维基百科页面。")
            return None, None

        # 如果API返回的最终标题与请求的原始标题不同，说明发生了重定向
        if final_title != article_title:
            logger.info(f"页面 '{article_title}' 重定向至 '{final_title}'。将更新 LIST.md。")
            # 调用工具函数，将列表中的旧标题更新为新标题
            update_title_in_list(article_title, final_title)
        
        # 步骤 2: 使用获取到的权威标题抓取 Wikitext
        raw_url = self._build_raw_url(final_title, lang)
        logger.info(f"正在获取 ({lang}) '{final_title}' 的Wikitext源码: {raw_url}")
        
        try:
            response = self.session.get(raw_url, timeout=20)
            response.raise_for_status()
            
            content = response.text

            # 对中文维基内容进行简体转换
            final_wikitext = self.t2s_converter.convert(content) if lang == 'zh' else content
            
            logger.info(f"Wikitext已成功获取（最终标题: '{final_title}'）。")
            return final_wikitext, final_title

        except requests.exceptions.RequestException as e:
            logger.error(f"获取Wikitext失败 (最终标题: '{final_title}') - {e}")
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
