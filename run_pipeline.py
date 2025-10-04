# run_pipeline.py

import time
import sys
import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio

# --- 步骤1：在这里添加日志配置 ---
def setup_logging():
    """配置全局日志记录器"""
    # 定义日志格式
    log_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 获取根记录器
    root_logger = logging.getLogger()
    # 设置基础日志级别，INFO级别意味着INFO, WARNING, ERROR, CRITICAL都会被记录
    root_logger.setLevel(logging.INFO)

    # 1. 配置控制台输出 (StreamHandler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    # 2. 配置文件输出 (RotatingFileHandler)
    # 确保日志目录存在
    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, 'pipeline.log')
    
    # 使用RotatingFileHandler，当日志文件达到5MB时会自动轮转，最多保留5个备份文件
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)

    logging.info("Logger configuration complete.")

# 在主逻辑开始前调用配置函数
setup_logging()
# -----------------------------

# 确保脚本可以找到 'scripts' 目录下的所有模块
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))

try:
    # 导入五个步骤所需的模块/类
    from scripts.process_list import main as process_main
    from scripts.merge_graphs import GraphMerger
    from scripts.clean_data import GraphCleaner
    from scripts.check_pageviews import main as pageviews_main
    from scripts.generate_frontend_data import main as generate_main
    
    # 从config模块导入路径配置
    from scripts.config import MASTER_GRAPH_PATH, PROCESSED_LOG_PATH, CACHE_DIR
except ImportError as e:
    logging.critical(f"错误: 无法导入必要的模块。请确保项目结构正确且所有脚本都存在。\n详细错误: {e}")
    sys.exit(1)

async def run_pipeline():
    """执行完整的端到端数据处理流水线，现在包含五个步骤。"""
    start_time = time.time()
    
    try:
        logging.info("==================================================")
        logging.info("==========  启动 Chinese-Elite 数据流水线  ==========")
        logging.info("==================================================")

        # --- 第1步：处理实体列表 (从Wikitext提取数据) ---
        logging.info("\n--- 步骤 1/5: 开始处理实体列表 (process_list.py) ---")
        process_main()
        logging.info("\n--- 步骤 1/5: 实体列表处理完成 ---\n")

        # --- 第2步：合并图谱 (将碎片化的JSON合并到主图谱) ---
        logging.info("--- 步骤 2/5: 开始合并图谱文件 (merge_graphs.py) ---")
        merger = GraphMerger(
            master_graph_path=MASTER_GRAPH_PATH,
            log_path=PROCESSED_LOG_PATH
        )
        merger.run()
        logging.info("\n--- 步骤 2/5: 图谱文件合并完成 ---\n")

        # --- 第3步：深度维护数据 (升级临时ID、清理冗余关系等) ---
        logging.info("--- 步骤 3/5: 开始深度维护主图谱 (clean_data.py) ---")
        cleaner = GraphCleaner(
            master_graph_path=MASTER_GRAPH_PATH,
            cache_dir=CACHE_DIR
        )
        cleaner.run()
        logging.info("\n--- 步骤 3/5: 主图谱深度维护完成 ---\n")
        
        # --- 第4步：检查页面访问频次并重排列表 ---
        logging.info("--- 步骤 4/5: 开始检查页面访问频次并重排LIST.txt (check_pageviews.py) ---")
        await pageviews_main()
        logging.info("\n--- 步骤 4/5: 页面访问频次检查与列表排序完成 ---\n")

        # --- 第5步：生成前端数据 (生成 initial.json 和 /nodes 数据库) ---
        logging.info("--- 步骤 5/5: 开始生成前端数据文件 (generate_frontend_data.py) ---")
        generate_main()
        logging.info("\n--- 步骤 5/5: 前端数据与数据库生成完成 ---\n")

        end_time = time.time()
        elapsed_time = end_time - start_time

        logging.info("==================================================")
        logging.info("=============    数据流水线执行完毕    =============")
        logging.info(f"==========   总耗时: {elapsed_time:.2f} 秒   ==========")
        logging.info("==================================================")

    except Exception as e:
        logging.error(f"\n[!!!] 流水线执行过程中发生严重错误: {e}")
        # logging.exception 会自动记录异常信息和堆栈跟踪
        logging.exception("详细的错误堆栈信息如下:")
        logging.error("[!!!] 请检查上述错误信息并修正。")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
