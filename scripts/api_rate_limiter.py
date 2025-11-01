# scripts/api_rate_limiter.py

import time
import threading
import json
import os
from datetime import date
from collections import deque
from functools import wraps
import logging
import random

# 使用相对路径导入
from .config import CACHE_DIR

logger = logging.getLogger(__name__)

# --- 一个自定义异常，用于表示每日配额耗尽 ---
class DailyQuotaExceededError(Exception):
    """当每日API调用配额耗尽时抛出此异常。"""
    pass

class APIRateLimiter:
    """
    一个API速率限制器。
    - RPM (Requests Per Minute): 基于时间滑动窗口控制请求频率。
    - RPD (Requests Per Day): 基于计数器文件控制每日总请求量。
    """
    def __init__(self, max_requests: int, per_seconds: int, rpd_limit: int | None = None, counter_name: str | None = None):
        # RPM 配置
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.requests = deque()
        self.lock = threading.Lock()

        # RPD 配置
        self.rpd_limit = rpd_limit
        self.counter_file: str | None = None # 明确 self.counter_file 的类型
        if rpd_limit is not None and counter_name:
            self.counter_file = os.path.join(CACHE_DIR, f"{counter_name}_rpd_counter.json")
            self._load_daily_counter()
        else:
            self.counter_file = None

    def _load_daily_counter(self):
        """加载或重置每日请求计数器。"""
        if not self.counter_file:
            return

        today_str = date.today().isoformat()
        if os.path.exists(self.counter_file):
            try:
                with open(self.counter_file, 'r') as f:
                    data = json.load(f)
                if data.get('date') == today_str:
                    self.daily_count = data.get('count', 0)
                    return
            except (json.JSONDecodeError, IOError):
                pass # 文件损坏或无法读取，将重置
        
        self.daily_count = 0
        self._save_daily_counter()

    def _save_daily_counter(self):
        """将当前计数和日期保存到文件。"""
        if not self.counter_file: 
            return

        today_str = date.today().isoformat()
        data = {'date': today_str, 'count': self.daily_count}
        try:
            os.makedirs(os.path.dirname(self.counter_file), exist_ok=True)
            with open(self.counter_file, 'w') as f:
                json.dump(data, f)
        except IOError:
            pass

    def _check_and_wait(self):
        """
        执行双重检查：先查RPD，再查RPM。
        """
        with self.lock:
            if self.rpd_limit is not None:
                if self.daily_count >= self.rpd_limit:
                    raise DailyQuotaExceededError(f"RPD limit of {self.rpd_limit} reached for {self.counter_file}")

            now = time.monotonic()
            while self.requests and self.requests[0] <= now - self.per_seconds:
                self.requests.popleft()
            
            if len(self.requests) >= self.max_requests:
                wait_time = self.requests[0] - (now - self.per_seconds)
                if wait_time > 0:
                    time.sleep(wait_time)
            
            self.requests.append(time.monotonic())
    
    def increment_and_save(self):
        """在API调用成功后，增加计数并保存。"""
        if self.rpd_limit is not None and self.counter_file:
            with self.lock:
                self.daily_count += 1
                self._save_daily_counter()

    def limit(self, func):
        """
        一个限制 API 访问速率的装饰器。
        """
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                self._check_and_wait()
                result = func(*args, **kwargs)
                if result is not None:
                    self.increment_and_save()
                else:
                    # API调用失败 (返回None) 时，以 25% 的几率增加计数。
                    if random.random() < 0.25:
                        logger.info(f"函数 '{func.__name__}' 调用失败，按25%的概率增加RPD计数。")
                        self.increment_and_save()
                return result
            except DailyQuotaExceededError:
                if self.counter_file:
                    model_name = os.path.basename(self.counter_file).replace('_rpd_counter.json', '')
                    logger.warning(f"模型 {model_name} 的每日配额已耗尽。将跳过本次调用。")
                
                # 根据函数上下文返回合理空值
                if "merge_llm" in func.__name__:
                    return True
                if "cleaner_llm" in func.__name__:
                    return []
                return None 
            except Exception:
                raise
        return wrapper

# --- 限制器实例定义 ---

# RPD 统一乘 112.5%，以容纳网络波动/Token超限等特殊异常导致的请求次数虚高。
# https://ai.google.dev/gemini-api/docs/rate-limits

# Gemini-2.5-pro, RPM: 5, RPD: 100.
gemini_pro_limiter = APIRateLimiter(
    max_requests=5, per_seconds=60, 
    rpd_limit=113, 
    counter_name='gemini_pro'
)

# Gemini-2.5-flash, RPM: 10, RPD: 250
gemini_flash_limiter = APIRateLimiter(
    max_requests=10, per_seconds=60, 
    rpd_limit=281, 
    counter_name='gemini_flash'
)

# Gemini-2.5-flash-preview-09-2025, RPM: 10, RPD: 250
gemini_flash_preview_limiter = APIRateLimiter(
    max_requests=10, per_seconds=60, 
    rpd_limit=281, 
    counter_name='gemini_flash'
)

# Gemini-2.5-flash-lite, RPM: 15, RPD: 1000
gemini_flash_lite_limiter = APIRateLimiter(
    max_requests=15, per_seconds=60, 
    rpd_limit=1125, 
    counter_name='gemini_flash_lite'
)

# Gemini-2.5-flash-lite-preview-09-2025, RPM: 15, RPD: 1000
gemini_flash_lite_preview_limiter = APIRateLimiter(
    max_requests=15, per_seconds=60, 
    rpd_limit=1125, 
    counter_name='gemini_flash_lite_preview'
)

# Gemma-3-27b-it, RPM: 30, RPD: 14400
gemma_limiter = APIRateLimiter(
    max_requests=30, per_seconds=60, 
    rpd_limit=16200, 
    counter_name='gemma'
)

# Wiki, 无官方 RPM, RPD
wiki_sync_limiter = APIRateLimiter(
    max_requests=9000, per_seconds=60
)
