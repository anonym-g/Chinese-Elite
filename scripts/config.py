# scripts/config.py

import os

# 使用绝对路径确保在任何位置运行脚本时路径都正确
# __file__ 是当前文件 (config.py) 的路径
# os.path.abspath 获取其绝对路径
# os.path.dirname 获取该文件所在的目录 (scripts 目录)
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# scripts 目录的上一级就是项目根目录
PROJECT_ROOT = os.path.dirname(SCRIPTS_DIR)

# 定义所有其他脚本可能需要用到的共享路径
DATA_PATH = os.path.join(PROJECT_ROOT, 'data')
DOCS_PATH = os.path.join(PROJECT_ROOT, 'docs')
CACHE_DIR = os.path.join(PROJECT_ROOT, '.cache')
DATA_TO_BE_CLEANED_DIR = os.path.join(PROJECT_ROOT, 'data_to_be_cleaned')

# 定义主图谱文件来源 (Single Source of Truth)
CONSOLIDATED_GRAPH_PATH = os.path.join(DOCS_PATH, 'consolidated_graph.json')
