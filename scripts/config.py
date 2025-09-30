# scripts/config.py

import os
import pytz

# --- 基础路径配置 ---
# 项目根目录 (Chinese-Elite/)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- 数据目录配置 ---
DATA_DIR = os.path.join(ROOT_DIR, 'data')
LIST_FILE_PATH = os.path.join(DATA_DIR, 'LIST.txt')
PROCESSED_LOG_PATH = os.path.join(DATA_DIR, 'processed_files.log')

# --- 待清理目录 ---
# 有待手动清理，不放data目录，以免 `merge_graphs.py` 错误整合
DATA_TO_BE_CLEANED_DIR = os.path.join(ROOT_DIR, 'data_to_be_cleaned')

# --- 缓存目录 ---
CACHE_DIR = os.path.join(ROOT_DIR, '.cache')

# --- 文档/输出 目录配置 ---
DOCS_DIR = os.path.join(ROOT_DIR, 'docs')
CONSOLIDATED_GRAPH_PATH = os.path.join(DOCS_DIR, 'consolidated_graph.json')

# --- Prompt 路径配置 ---
PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
PARSER_SYSTEM_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'parser_system.txt')
MERGE_CHECK_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'merge_check.txt')
MERGE_EXECUTE_PROMPT_PATH = os.path.join(PROMPTS_DIR, 'merge_execute.txt')


# --- LLM 模型配置 ---
# 用于从Wikitext解析实体和关系的主模型
PARSER_MODEL = "gemini-2.5-pro" 
# 用于在合并前快速检查是否有新信息
MERGE_CHECK_MODEL = "gemma-3-27b-it"
# 用于执行两个JSON对象的智能合并
MERGE_EXECUTE_MODEL = "gemini-2.5-flash"

# --- LLM 参数配置 ---
FEW_SHOT_NODE_SAMPLES = 24
FEW_SHOT_REL_SAMPLES = 12

# --- API 与外部服务配置 ---
WIKI_BASE_URL = "https://zh.wikipedia.org/zh-cn/"
WIKI_API_URL = "https://zh.wikipedia.org/w/api.php"
BAIDU_BASE_URL = "https://baike.baidu.com/item/"
CDSPACE_BASE_URL = "https://chinadigitaltimes.net/space/"
USER_AGENT = 'ChineseEliteExplorer/1.0 (https://github.com/anonym-g/Chinese-Elite)'

# --- 无向边配置 ---
NON_DIRECTED_LINK_TYPES = {
    'SIBLING_OF', 'LOVER_OF', 'RELATIVE_OF', 
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

# --- 全局配置 ---
TIMEZONE = pytz.timezone('Asia/Shanghai')
