# scripts/config.py

import os
import pytz

# --- 基础路径配置 ---
# 项目根目录 (Chinese-Elite/)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- 数据目录配置 ---
DATA_DIR = os.path.join(ROOT_DIR, 'data')
LIST_FILE_PATH = os.path.join(DATA_DIR, 'LIST.md')
PROCESSED_LOG_PATH = os.path.join(DATA_DIR, 'processed_files.log')

# --- 待清理目录 ---
# 有待手动清理，不放data目录，以免 `merge_graphs.py` 错误整合
DATA_TO_BE_CLEANED_DIR = os.path.join(ROOT_DIR, 'data_to_be_cleaned')

# --- 缓存目录 ---
CACHE_DIR = os.path.join(ROOT_DIR, '.cache')
FALSE_RELATIONS_CACHE_PATH = os.path.join(CACHE_DIR, 'false_relations_cache.json')

# --- 文档/输出 目录配置 ---
DOCS_DIR = os.path.join(ROOT_DIR, 'docs')
MASTER_GRAPH_PATH = os.path.join(DOCS_DIR, 'master_graph_qcode.json')
FRONTEND_DATA_DIR = os.path.join(DOCS_DIR, 'data')

# --- 用于过滤：时间、概率常量 ---
PROB_START_DAY = 7
PROB_END_DAY = 30
PROB_START_VALUE = 1 / 12
PROB_END_VALUE = 9 / 10

# --- 关系清洗概率配置 ---
REL_CLEAN_NUM = 1000 # 每次运行检查的关系数量
REL_CLEAN_SKIP_DAYS = 30
REL_CLEAN_PROB_START_DAYS = 30
REL_CLEAN_PROB_END_DAYS = 90
REL_CLEAN_PROB_START_VALUE = 1 / 12
REL_CLEAN_PROB_END_VALUE = 9 / 10

# --- 规模常数配置 ---
MAX_LIST_ITEMS_TO_CHECK = 2000
MAX_WORKERS_LIST_SCREENING = 32
MAX_LIST_ITEMS_PER_RUN = 400
MAX_WORKERS_LIST_PROCESSING = 8

MAX_UPDATE_WORKERS = 200
LIST_UPDATE_LIMIT = 20000
MASTER_GRAPH_UPDATE_LIMIT = 20000

MAX_PAGEVIEW_CHECKS_LIMIT = 7000

CORE_NETWORK_SIZE = 2000

# --- Prompt 路径配置 ---
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
PARSER_SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'parser_system.txt')
MERGE_CHECK_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'merge_check.txt')
MERGE_EXECUTE_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'merge_execute.txt')
CLEAN_SINGLE_RELATION_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'clean_single_relation.txt')
VALIDATE_PR_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'pr_validator.txt')
BOT_QA_PROMPT = os.path.join(PROMPTS_DIR, 'bot_rag.txt')

# --- LLM 模型配置 ---
# 用于从Wikitext解析实体和关系的主模型
PARSER_MODEL = "gemini-2.5-pro" 
# 用于在合并前快速检查是否有新信息
MERGE_CHECK_MODEL = "gemma-3-27b-it"
# 用于执行两个JSON对象的智能合并
MERGE_EXECUTE_MODEL = "gemini-2.5-flash"
# 用于单条关系清洗的模型
RELATION_CLEANER_MODEL = "gemini-2.5-flash-lite"
# 用于验证PR有效性的模型
VALIDATE_PR_MODEL = "gemini-2.5-flash-preview-09-2025"
# Telegram Bot调用的模型
BOT_QA_MODEL = "gemini-2.5-flash-lite-preview-09-2025"

# --- LLM 参数配置 ---
FEW_SHOT_NODE_SAMPLES = 12
FEW_SHOT_REL_SAMPLES = 24

# --- API 与外部服务配置 ---
WIKI_BASE_URL_TPL = "https://{lang}.wikipedia.org/wiki/"
WIKI_API_URL_TPL = "https://{lang}.wikipedia.org/w/api.php"
PAGEVIEWS_API_BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
BAIDU_BASE_URL = "https://baike.baidu.com/item/"
CDSPACE_BASE_URL = "https://chinadigitaltimes.net/space/"
USER_AGENT = 'ChineseEliteExplorer/1.0 (https://github.com/anonym-g/Chinese-Elite)'

# --- 全局配置 ---
TIMEZONE = pytz.timezone('Asia/Shanghai')

# --- 无向边配置 ---
NON_DIRECTED_LINK_TYPES = {
    'SPOUSE_OF', 'SIBLING_OF', 'LOVER_OF', 'RELATIVE_OF', 
    'FRIEND_OF', 'ENEMY_OF', 'MET_WITH'
}

# --- 关系清洗规则 ---
# 定义关系类型与其端点节点类型之间的有效组合
# 键: 关系类型
# 值: 一个字典，包含 'source' 和 'target' 两个键，其值为允许的节点类型列表
# 如果某个键不存在，则不进行该方向的类型检查
RELATIONSHIP_TYPE_RULES = {
    # --- 个人与个人 ---
    "SPOUSE_OF":        {"source": ["Person"], "target": ["Person"]},
    "CHILD_OF":         {"source": ["Person"], "target": ["Person"]},
    "SIBLING_OF":       {"source": ["Person"], "target": ["Person"]},
    "LOVER_OF":         {"source": ["Person"], "target": ["Person"]},
    "RELATIVE_OF":      {"source": ["Person"], "target": ["Person"]},
    "MET_WITH":         {"source": ["Person"], "target": ["Person"]},

    # --- 出生地 ---
    "BORN_IN":          {"source": ["Person"], "target": ["Location"]},
    
    # --- 涉及组织 ---
    "ALUMNUS_OF":       {
        "source": ["Person"], 
        "target": ["Organization"]
    },
    "MEMBER_OF":        {
        "source": ["Person", "Organization"], 
        "target": ["Organization"]
    },
    "SUBORDINATE_OF":   {
        "source": ["Person", "Organization"], 
        "target": ["Person", "Organization"]
    },
    "FRIEND_OF":        {
        "source": ["Person", "Organization"], 
        "target": ["Person", "Organization"]
    },
    "ENEMY_OF":         {
        "source": ["Person", "Organization"], 
        "target": ["Person", "Organization"]
    },
    "FOUNDED":          {
        "source": ["Person", "Organization"], 
        "target": ["Organization", "Movement"]
    },
    
    # --- 通用关系 ---
    "PUSHED":           {
        "source": ["Person", "Organization", "Event", "Movement", "Document"], 
        "target": ["Person", "Organization", "Event", "Movement", "Document"]
    },
    "BLOCKED":          {
        "source": ["Person", "Organization", "Event", "Movement", "Document"],
        "target": ["Person", "Organization", "Event", "Movement", "Document"]
    },
    "INFLUENCED":    {
        "source": ["Person", "Organization", "Event", "Movement", "Document"],
        "target": ["Person", "Organization", "Event", "Movement", "Document", "Location"]
    }
}
