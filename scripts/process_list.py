# scripts/process_list.py

import os
import sys
import json
import re
from datetime import datetime
import random
import logging
import concurrent.futures
from typing import List, Dict, Any

# 使用相对路径导入
from .config import (
    DATA_DIR, LIST_FILE_PATH, CACHE_DIR,
    PROB_START_DAY, PROB_END_DAY, PROB_START_VALUE, PROB_END_VALUE,
    SAMPLING_MIN_WEIGHT, SAMPLING_MAX_WEIGHT, SAMPLING_EXPONENT,
    MAX_LIST_ITEMS_TO_CHECK, MAX_WORKERS_LIST_SCREENING,
    SORTING_MIN_WEIGHT, SORTING_MAX_WEIGHT, SORTING_EXPONENT,
    MAX_LIST_ITEMS_PER_RUN, MAX_WORKERS_LIST_PROCESSING,
    TIMEZONE
)
from .clients.wikipedia_client import WikipediaClient
from .services.llm_service import LLMService
from .utils import sanitize_filename

logger = logging.getLogger(__name__)

class ListProcessor:
    """
    负责处理 `LIST.md` 中的实体列表，执行从维基百科提取、解析、
    并保存为结构化 JSON 文件的核心流程。
    """
    def __init__(self, wiki_client: WikipediaClient, llm_service: LLMService):
        """
        初始化 ListProcessor。

        Args:
            wiki_client: 用于与维基百科交互的客户端实例。
            llm_service: 用于调用大语言模型的服务实例。
        """
        self.wiki_client = wiki_client
        self.llm_service = llm_service
        self.items_to_process = {}
        self.pageviews_cache = self._load_pageviews_cache()

    def _load_pageviews_cache(self):
        """加载页面热度缓存文件。"""
        cache_path = os.path.join(CACHE_DIR, 'pageviews_cache.json')
        if not os.path.exists(cache_path):
            logger.warning("页面热度缓存文件 pageviews_cache.json 未找到，将回退至随机筛选模式。")
            return None
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            logger.warning("读取或解析页面热度缓存失败，将回退至随机筛选模式。")
            return None

    def _parse_list_file(self) -> bool:
        """解析 LIST.md 文件，将待处理条目加载到 self.items_to_process。"""
        if not os.path.exists(LIST_FILE_PATH):
            logger.error(f"错误：列表文件不存在于 '{LIST_FILE_PATH}'")
            return False
        
        logger.info(f"正在读取列表文件: {LIST_FILE_PATH}")
        categorized_items = {}
        current_category = None
        lang_pattern = re.compile(r'\((?P<lang>[a-z]{2})\)\s*')

        with open(LIST_FILE_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('## '):
                    category_name = line[3:].strip().lower()
                    if category_name == 'new': break
                    current_category = category_name
                    if current_category not in categorized_items:
                        categorized_items[current_category] = []
                    continue
                if not line or line.startswith('//'): continue
                if current_category:
                    lang = 'zh'
                    item_name = line
                    match = lang_pattern.match(line)
                    if match:
                        lang = match.group('lang')
                        item_name = line[match.end():].strip()
                    categorized_items[current_category].append((item_name, lang))
        
        self.items_to_process = categorized_items
        return True

    def _get_last_local_process_time(self, item_name: str, category: str) -> datetime | None:
        """检查本地文件，获取该条目最后一次处理的时间。"""
        safe_item_name = sanitize_filename(item_name)
        item_dir = os.path.join(DATA_DIR, category, safe_item_name)
        if not os.path.isdir(item_dir): return None

        latest_time = None
        timestamp_regex = re.compile(r'_(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})\.json$')
        for filename in os.listdir(item_dir):
            match = timestamp_regex.search(filename)
            if match:
                try:
                    dt_object = datetime.strptime(match.group(1), '%Y-%m-%d-%H-%M-%S')
                    localized_dt = TIMEZONE.localize(dt_object)
                    if latest_time is None or localized_dt > latest_time:
                        latest_time = localized_dt
                except ValueError: continue
        return latest_time

    def _should_process_item(self, item_tuple: tuple, category: str) -> bool:
        """根据更新日期、维基历史和概率，判断是否应处理该条目。"""
        item_name, lang = item_tuple
        
        last_local_time = self._get_last_local_process_time(item_name, category)

        if not last_local_time:
            logger.info(f"'{item_name}': 首次处理。")
            return True
        
        now = datetime.now(TIMEZONE)
        age_in_days = (now - last_local_time).days
        if age_in_days <= PROB_START_DAY:
            return False # 最近处理过，跳过
        
        latest_wiki_time = self.wiki_client.get_latest_revision_time(item_name, lang=lang)
        if latest_wiki_time and latest_wiki_time <= last_local_time:
            return False # 本地数据已是最新，跳过

        if PROB_START_DAY < age_in_days <= PROB_END_DAY:
            ratio = (age_in_days - PROB_START_DAY) / (PROB_END_DAY - PROB_START_DAY)
            probability = PROB_START_VALUE + (PROB_END_VALUE - PROB_START_VALUE) * ratio
            if random.random() >= probability:
                return False # 概率期内，按概率跳过
            logger.info(f"'{item_name}': 在概率期内，按概率 ({probability:.2%}) 重新提取。")
        else: # age_in_days > PROB_END_DAY
            logger.info(f"'{item_name}': 已超过 {PROB_END_DAY} 天未更新，将重新提取。")
        
        return True

    def _process_item(self, item_tuple: tuple, category: str):
        """对单个条目执行Wikitext获取、LLM解析和文件保存。"""
        item_name, lang = item_tuple
        logger.info(f"--- 开始处理 '{item_name}' (类别: {category}, 语言: {lang}) ---")

        # 接收 get_wikitext 返回的 final_title
        wikitext, final_title = self.wiki_client.get_wikitext(item_name, lang=lang)
        
        if not (wikitext and final_title):
            logger.warning(f"失败：未能获取 '{item_name}' 的Wikitext，跳过。")
            return
        
        structured_data = self.llm_service.parse_wikitext(wikitext)
        if not structured_data:
            logger.warning(f"失败：LLM未能解析 '{item_name}' 的Wikitext，跳过。")
            return
            
        try:
            # 使用 final_title 作为文件名
            safe_item_name = sanitize_filename(final_title)
            output_dir = os.path.join(DATA_DIR, category, safe_item_name)
            os.makedirs(output_dir, exist_ok=True)
            timestamp = datetime.now(TIMEZONE).strftime('%Y-%m-%d-%H-%M-%S')
            
            # 文件名使用权威标题
            file_name = f"{safe_item_name}_{timestamp}.json"
            output_path = os.path.join(output_dir, file_name)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(structured_data, f, indent=2, ensure_ascii=False)
            
            # 日志中报告原始名称和最终保存路径
            logger.info(f"成功：'{item_name}' (解析为 '{final_title}') 的处理结果已保存至: {output_path}")

            for old_filename in os.listdir(output_dir):
                if old_filename.endswith('.json') and old_filename != file_name:
                    os.remove(os.path.join(output_dir, old_filename))
                    logger.info(f"已删除旧版本: {old_filename}")
        except Exception as e:
            logger.error(f"严重错误：在保存文件时发生异常 - {e}")

    def _perform_weighted_sampling(
        self,
        items: List[Dict[str, Any]], 
        k: int, 
        min_weight: float, 
        max_weight: float, 
        exponent: float
    ) -> List[Dict[str, Any]]:
        """
        对按热度降序排列的条目列表执行加权无放回抽样。

        该函数采用 A-ExpJ 算法，通过随机排序键进行加权抽样。权重曲线由指数函数生成，以实现平滑非线性分布。

        Args:
            items (List[Dict[str, Any]]): 待抽样的条目列表。必须是已排序的。
            k (int): 需要抽样的条目数量。
            min_weight (float): 列表中最末位条目的最小权重。
            max_weight (float): 列表中首位条目的最大权重。
            exponent (float): 控制权重曲线形状的指数 (< 1.0 产生凸形曲线)。

        Returns:
            List[Dict[str, Any]]: 返回一个包含 k 个被选中条目的新列表。
        """
        total_items = len(items)
        if total_items == 0:
            return []

        # 1. 根据排名计算每个条目的权重
        weights = []
        denominator = (total_items - 1) if total_items > 1 else 1
        for i in range(total_items):
            rank_ratio = i / denominator
            weight = min_weight + (max_weight - min_weight) * ((1 - rank_ratio) ** exponent)
            weights.append(weight)

        # 2. 使用 A-ExpJ 算法生成随机排序键
        weighted_for_sampling = [
            {'item': item, 'key': random.random() ** (1.0 / weight)}
            for item, weight in zip(items, weights)
        ]
        
        # 3. 按随机键降序排序
        weighted_for_sampling.sort(key=lambda x: x['key'], reverse=True)

        # 4. 截取前 k 个条目作为抽样结果
        num_to_take = min(k, total_items)
        return [d['item'] for d in weighted_for_sampling[:num_to_take]]
    
    def _run_weighted_selection(self):
        """基于热度的加权随机筛选与处理。"""
        logger.info("--- 步骤 1/5: 预筛选 ---")
        
        # 按热度降序排序，用于计算权重
        all_potential_items = []
        for category, items in self.items_to_process.items():
            for item_tuple in items:
                item_name, _ = item_tuple
                score = self.pageviews_cache.get(item_name, {}).get('avg_daily_views', 0)
                all_potential_items.append({'data': (item_tuple, category), 'score': score})
        
        all_potential_items.sort(key=lambda x: x['score'], reverse=True)
        
        # 抽样
        items_to_check = self._perform_weighted_sampling(
            items=all_potential_items,
            k=MAX_LIST_ITEMS_TO_CHECK,
            min_weight=SAMPLING_MIN_WEIGHT,
            max_weight=SAMPLING_MAX_WEIGHT,
            exponent=SAMPLING_EXPONENT
        )
        if items_to_check:
            logger.info(f"通过加权抽样，从 {len(all_potential_items)} 项中筛选出 {len(items_to_check)} 个条目。")
        else:
            logger.info("条目列表为空或抽样结果为空，无需筛选。")

        # --- 步骤 2: 并行时间检查 ---
        logger.info("--- 步骤 2/5: 并行时间检查 ---")
        eligible_items = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_LIST_SCREENING) as executor:
            future_to_item = {
                executor.submit(self._should_process_item, item['data'][0], item['data'][1]): item
                for item in items_to_check
            }
            for future in concurrent.futures.as_completed(future_to_item):
                item_data = future_to_item[future]
                try:
                    if future.result():
                        eligible_items.append(item_data)
                except Exception as exc:
                    logger.error(f"检查条目 '{item_data['data'][0][0]}' 时发生错误: {exc}")

        if not eligible_items:
            logger.info("本轮没有需要处理的条目。")
            return
            
        logger.info(f"初筛完成，共有 {len(eligible_items)} 个条目符合处理条件。")

        # --- 步骤 3: 按原始热度排序 ---
        logger.info("--- 步骤 3/5: 重新排序以确定权重 ---")
        eligible_items.sort(key=lambda x: x['score'], reverse=True)

        # --- 步骤 4: 加权随机排序
        logger.info("--- 步骤 4/5: 加权随机排序 ---")
        sorted_eligible_items = self._perform_weighted_sampling(
            items=eligible_items,
            k=MAX_LIST_ITEMS_PER_RUN,
            min_weight=SORTING_MIN_WEIGHT,
            max_weight=SORTING_MAX_WEIGHT,
            exponent=SORTING_EXPONENT
        )
        
        # 通过参数 k，已完成内部截取
        final_list_to_process = [d['data'] for d in sorted_eligible_items]
        
        logger.info(f"--- 步骤 5/5: 已确定 {len(final_list_to_process)} 个待处理条目，开始并行处理 ---")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_LIST_PROCESSING) as executor:
            futures = [executor.submit(self._process_item, item_tuple, category) for item_tuple, category in final_list_to_process]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"一个处理任务在执行期间发生意外错误: {exc}", exc_info=True)
        
        logger.info("所有条目处理完毕。")

    def _run_random_selection(self):
        """纯随机筛选和处理，作为热度缓存不存在时的后备方案。"""
        logger.info("--- 步骤 1/3: 并行筛选本轮需要处理的条目 ---")
        items_for_this_run = []
        # 将所有待检查的条目平铺到一个列表中
        all_potential_items = [
            (item_tuple, category)
            for category, items in self.items_to_process.items()
            for item_tuple in items
        ]

        # --- 随机抽样，以控制单次运行检查量 ---
        if len(all_potential_items) > MAX_LIST_ITEMS_TO_CHECK:
            logger.info(f"列表过大 ({len(all_potential_items)}项)，将随机抽样 {MAX_LIST_ITEMS_TO_CHECK} 项进行检查。")
            items_to_check_this_run = random.sample(all_potential_items, MAX_LIST_ITEMS_TO_CHECK)
        else:
            items_to_check_this_run = all_potential_items
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_LIST_SCREENING) as executor:
            # 为抽样后的每个条目提交一个检查任务
            future_to_item = {
                executor.submit(self._should_process_item, item_tuple, category): (item_tuple, category)
                for item_tuple, category in items_to_check_this_run
            }
            
            # 实时收集已完成任务的结果
            for future in concurrent.futures.as_completed(future_to_item):
                item_data = future_to_item[future]
                try:
                    # future.result() 会返回 _should_process_item 函数的布尔值结果
                    should_process = future.result()
                    if should_process:
                        items_for_this_run.append(item_data)
                except Exception as exc:
                    logger.error(f"检查条目 '{item_data[0][0]}' 时发生错误: {exc}")
        
        if not items_for_this_run:
            logger.info("本轮没有需要处理的条目。")
            return

        logger.info(f"--- 步骤 2/3: 已确定 {len(items_for_this_run)} 个条目，正在打乱顺序 ---")
        random.shuffle(items_for_this_run)

        # 为避免单次运行时间过长，设定一个处理上限
        if len(items_for_this_run) > MAX_LIST_ITEMS_PER_RUN:
            logger.info(f"待处理条目过多 ({len(items_for_this_run)}个)，将截取前 {MAX_LIST_ITEMS_PER_RUN} 个进行处理。")
            items_for_this_run = items_for_this_run[:MAX_LIST_ITEMS_PER_RUN]

        logger.info("--- 步骤 3/3: 开始并行处理 ---")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_LIST_PROCESSING) as executor:
            # 使用 executor.submit 来分派任务
            futures = [executor.submit(self._process_item, item_tuple, category) for item_tuple, category in items_for_this_run]
            
            # 等待所有任务完成
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f"一个处理任务在执行期间发生意外错误: {exc}", exc_info=True)
        
        logger.info("所有条目处理完毕。")

    def run(self):
        """脚本主入口函数。根据热度缓存是否存在，选择不同的筛选策略。"""
        if not self._parse_list_file():
            logger.info("列表文件为空或不存在，任务结束。")
            return

        if self.pageviews_cache:
            self._run_weighted_selection()
        else:
            logger.warning("热度缓存缺失，回退至纯随机筛选模式。")
            self._run_random_selection()
