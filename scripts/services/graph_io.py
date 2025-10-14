# scripts/services/graph_io.py

import json
import os
import sys
import logging
from typing import Dict, Any

# --- 日志配置 ---
logger = logging.getLogger(__name__)

# --- 类型提示 ---
GraphData = Dict[str, Any]

def load_master_graph(path: str) -> GraphData:
    """
    从指定的JSON文件路径加载主图谱数据。

    如果文件不存在或解析失败，则会记录一个警告，
    并返回一个空的、结构正确的基础图谱字典。

    Args:
        path (str): master_graph_qcode.json 文件的完整路径。

    Returns:
        GraphData: 一个包含 'nodes' 和 'relationships' 键的字典。
    """
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                logger.info(f"成功加载主图谱文件: {os.path.basename(path)}")
                graph = json.load(f)
                # 确保基础结构存在
                if 'nodes' not in graph: graph['nodes'] = []
                if 'relationships' not in graph: graph['relationships'] = []
                return graph
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"无法读取或解析主图谱文件 '{path}'，将返回一个空图。错误: {e}")
    else:
        logger.warning(f"主图谱文件 '{path}' 不存在，将返回一个空图。")
    
    # 返回一个保证结构完整性的空图
    return {"nodes": [], "relationships": []}

def save_master_graph(path: str, graph_data: GraphData):
    """
    将图谱数据以格式化的JSON形式保存到指定路径。

    Args:
        path (str): master_graph_qcode.json 文件的完整路径。
        graph_data (GraphData): 要保存的图谱数据字典。
    """
    try:
        # 确保保存的目标目录存在
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            # indent=2 使JSON文件具有良好的可读性
            # ensure_ascii=False 确保中文字符能被正确写入
            json.dump(graph_data, f, indent=2, ensure_ascii=False)
        logger.info(f"主图谱已成功保存至: {path}")
    except IOError as e:
        # 如果保存失败，这是一个严重问题，应记录为 critical
        logger.critical(f"严重错误: 保存主图谱文件失败 - {e}")
        sys.exit(1) # 在自动化流程中，保存失败可能意味着数据丢失，终止程序
