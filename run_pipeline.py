# run_pipeline.py

import time
import sys
import os

# 确保脚本可以找到 'scripts' 目录下的所有模块
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))

try:
    # 导入四个步骤所需的模块/类
    from scripts.process_list import main as process_main
    from scripts.merge_graphs import GraphMerger
    from scripts.clean_data import GraphCleaner
    from scripts.check_pageviews import main as pageviews_main
    # 从config模块导入路径配置，以便传递给实例化的类
    from scripts.config import CONSOLIDATED_GRAPH_PATH, PROCESSED_LOG_PATH, DATA_TO_BE_CLEANED_DIR, CACHE_DIR
except ImportError as e:
    print(f"错误: 无法导入必要的模块。请确保项目结构正确且所有脚本都存在。\n详细错误: {e}", file=sys.stderr)
    sys.exit(1)

def run_pipeline():
    """执行完整的端到端数据处理流水线，现在包含四个步骤。"""
    start_time = time.time()
    
    try:
        print("==================================================")
        print("==========  启动 Chinese-Elite 数据流水线  ==========")
        print("==================================================")

        # --- 第1步：处理实体列表 (从Wikitext提取数据) ---
        print("\n--- 步骤 1/4: 开始处理实体列表 (process_list.py) ---")
        process_main()
        print("\n--- 步骤 1/4: 实体列表处理完成 ---\n")

        # --- 第2步：合并图谱 (将碎片化的JSON合并到主图谱) ---
        print("--- 步骤 2/4: 开始合并图谱文件 (merge_graphs.py) ---")
        merger = GraphMerger(
            master_graph_path=CONSOLIDATED_GRAPH_PATH,
            log_path=PROCESSED_LOG_PATH
        )
        merger.run()
        print("\n--- 步骤 2/4: 图谱文件合并完成 ---\n")

        # --- 第3步：清理数据 (验证链接、移除无效关系等) ---
        print("--- 步骤 3/4: 开始清理主图谱数据 (clean_data.py) ---")
        cleaner = GraphCleaner(
            graph_path=CONSOLIDATED_GRAPH_PATH,
            output_dir=DATA_TO_BE_CLEANED_DIR,
            cache_dir=CACHE_DIR
        )
        cleaner.run()
        print("\n--- 步骤 3/4: 主图谱数据清理完成 ---\n")
        
        # --- 第4步：检查页面访问频次并重排列表 ---
        print("--- 步骤 4/4: 开始检查页面访问频次并重排LIST.txt (check_pageviews.py) ---")
        pageviews_main()
        print("\n--- 步骤 4/4: 页面访问频次检查与列表排序完成 ---\n")

        end_time = time.time()
        elapsed_time = end_time - start_time

        print("==================================================")
        print("=============    数据流水线执行完毕    =============")
        print(f"==========   总耗时: {elapsed_time:.2f} 秒   ==========")
        print("==================================================")

    except Exception as e:
        print(f"\n[!!!] 流水线执行过程中发生严重错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc() # 打印详细的错误堆栈信息
        print("[!!!] 请检查上述错误信息并修正。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
