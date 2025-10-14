# run_pipeline.py

import time
import sys
import os
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from dotenv import load_dotenv

# --- 日志配置 ---
def setup_logging():
    log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if root_logger.hasHandlers(): root_logger.handlers.clear()
    
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    root_logger.addHandler(console_handler)

    log_dir = os.path.join(os.path.dirname(__file__), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'pipeline.log'), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
    logging.info("Logger configuration complete.")

setup_logging()

load_dotenv()

# 确保脚本可以找到 'scripts' 目录下的所有模块
sys.path.append(os.path.dirname(__file__))

# --- 导入业务逻辑类与服务 ---
try:
    from scripts.services.llm_service import LLMService
    from scripts.clients.wikipedia_client import WikipediaClient
    
    from scripts.process_list import ListProcessor
    from scripts.merge_graphs import GraphMerger
    from scripts.clean_data import GraphCleaner
    from scripts.check_pageviews import main as pageviews_main
    from scripts.generate_frontend_data import FrontendDataGenerator
    
    from scripts.config import MASTER_GRAPH_PATH, PROCESSED_LOG_PATH
except ImportError as e:
    logging.critical(f"错误: 无法导入必要的模块。请确保项目结构正确。\n详细错误: {e}", exc_info=True)
    sys.exit(1)

async def run_pipeline():
    """
    执行完整的端到端数据处理流水线。
    该函数现在负责实例化服务和处理器，并按顺序调用它们。
    """
    start_time = time.time()
    
    try:
        logging.info("==================================================")
        logging.info("===========  启动 Chinese-Elite 数据流水线  ===========")
        logging.info("==================================================")

        # --- 步骤 0: 依赖注入 - 初始化所有需要的服务 ---
        logging.info("[*] 正在初始化核心服务...")
        llm_service = LLMService()
        wiki_client = WikipediaClient()
        logging.info("[*] 核心服务初始化完毕。")

        # --- 第1步：处理实体列表 (从Wikitext提取数据) ---
        logging.info("\n--- 步骤 1/5: 开始处理实体列表 ---")
        list_processor = ListProcessor(wiki_client=wiki_client, llm_service=llm_service)
        list_processor.run()
        logging.info("--- 步骤 1/5: 实体列表处理完成 ---\n")

        # --- 第2步：合并图谱 ---
        logging.info("--- 步骤 2/5: 开始合并图谱文件 ---")
        merger = GraphMerger(
            master_graph_path=MASTER_GRAPH_PATH,
            log_path=PROCESSED_LOG_PATH,
            llm_service=llm_service,
            wiki_client=wiki_client
        )
        merger.run()
        logging.info("--- 步骤 2/5: 图谱文件合并完成 ---\n")

        # --- 第3步：深度维护数据 ---
        logging.info("--- 步骤 3/5: 开始深度维护主图谱 ---")
        cleaner = GraphCleaner(
            master_graph_path=MASTER_GRAPH_PATH,
            wiki_client=wiki_client,
            llm_service=llm_service
        )
        cleaner.run()
        logging.info("--- 步骤 3/5: 主图谱深度维护完成 ---\n")
        
        # --- 第4步：检查页面访问频次并重排列表 ---
        logging.info("--- 步骤 4/5: 开始检查页面热度并重排列表 ---")
        await pageviews_main()
        logging.info("--- 步骤 4/5: 页面热度检查与列表排序完成 ---\n")

        # --- 第5步：生成前端数据 ---
        logging.info("--- 步骤 5/5: 开始生成前端数据文件 ---")
        frontend_generator = FrontendDataGenerator()
        frontend_generator.run()
        logging.info("--- 步骤 5/5: 前端数据与数据库生成完成 ---\n")

        elapsed_time = time.time() - start_time
        logging.info("==================================================")
        logging.info(f"====== 数据流水线执行完毕 (总耗时: {elapsed_time:.2f} 秒) ======")
        logging.info("==================================================")

    except Exception as e:
        logging.critical(f"\n[!!!] 流水线执行过程中发生严重错误。", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
