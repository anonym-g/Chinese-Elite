# scripts/utils.py

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

from config import WIKI_API_URL, USER_AGENT, BAIDU_BASE_URL, CDSPACE_BASE_URL, LIST_FILE_PATH, CACHE_DIR

logger = logging.getLogger(__name__)

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
            logger.info(f"正在将新标题 '{title_to_add}' 添加到 LIST.txt 的 'new' 类别下...")
            
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
        logger.error(f"严重错误: LIST.txt 文件未找到于 '{LIST_FILE_PATH}'")
    except Exception as e:
        logger.error(f"严重错误: 向 LIST.txt 添加标题时发生错误: {e}")


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
    
    def _fetch_qcode_from_api(self, article_title: str) -> str | None:
        """内部辅助方法，仅负责执行一次API查询并解析结果。"""
        params = {
            "action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
            "titles": article_title, "format": "json", "formatversion": "2", "redirects": "1",
        }
        try:
            response = self.session.get(WIKI_API_URL, params=params, timeout=15)
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
        新增逻辑：当简体查询失败时，自动尝试使用繁体进行后备查询。
        """
        # 0. 优先从内存中的反向映射缓存中快速查找
        if article_title in self._title_to_qcode_map:
            return self._title_to_qcode_map[article_title]

        qcode = None
        
        # 1. 优先使用原始标题（简体）进行查询
        logger.info(f"正在通过API查询 '{article_title}' (简体)...")
        qcode = self._fetch_qcode_from_api(article_title)

        # 2. 如果简体查询失败，尝试转换为繁体进行后备查询
        traditional_title = ""
        if not qcode:
            traditional_title = self.s2t_converter.convert(article_title)
            if traditional_title != article_title:
                logger.info(f"简体查询失败，尝试后备查询 '{traditional_title}' (繁体)...")
                qcode = self._fetch_qcode_from_api(traditional_title)

        # 3. 如果最终找到了Q-Code，则更新缓存
        if qcode:
            logger.info(f"成功获取Q-Code: {qcode}")
            
            # 获取或创建该Q-Code的标题列表
            titles_in_cache = self.qcode_cache.get(qcode, [])
            
            # 将本次查询涉及的简体和繁体标题都加入缓存，并去重
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
        logger.info(f"正在获取 '{article_title}' 的Wikitext源码: {raw_url}")

        try:
            response = self.session.get(raw_url, timeout=20)
            response.raise_for_status()
            traditional_wikitext = response.text
            simplified_wikitext = self.t2s_converter.convert(traditional_wikitext)
            logger.info("Wikitext已成功获取并转换为简体中文。")
            return simplified_wikitext, article_title
        except requests.exceptions.RequestException as e:
            logger.error(f"获取Wikitext失败 - {e}")
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
            logger.warning(f"获取 '{article_title}' 的维基修订历史失败 - {e}")
        return None

    def check_link_status(self, node_id: str) -> tuple[str, str | None]:
        """
        检查节点名称的状态，内置缓存和多源回退逻辑。
        """
        # 步骤1：检查缓存
        if node_id in self.link_cache:
            cached = self.link_cache[node_id]
            return cached['status'], cached.get('detail')

        # 步骤2：检查维基百科
        status, detail = self._check_wiki_status_api(node_id) # 使用一个辅助方法来执行API调用

        # 步骤3：如果维基页面不存在或出错，检查备用来源
        if status in ["NO_PAGE", "ERROR"]:
            if self.check_generic_url(BAIDU_BASE_URL, node_id):
                status = "BAIDU"
            elif self.check_generic_url(CDSPACE_BASE_URL, node_id):
                status = "CDT"
        
        # 步骤4：缓存有效结果 (不缓存 NO_PAGE 和 ERROR)
        if status not in ["NO_PAGE", "ERROR"]:
            self.link_cache[node_id] = {
                'status': status, 
                'detail': detail,
                'timestamp': datetime.now().isoformat() # 增添时间戳
            }
            self.link_cache_updated = True
            
        return status, detail

    def _check_wiki_status_api(self, node_id: str) -> tuple[str, str | None]:
        """执行维基百科API检查。"""
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
                    simplified_target = self.t2s_converter.convert(redirect_target)
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
                # logger.debug(f"(延迟 {delay:.1f}s)") # 可以取消这行注释来显示延时
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
