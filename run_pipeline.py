# run_pipeline.py

import time
import sys
import os

# 确保脚本可以找到 'scripts' 模块
sys.path.append(os.path.join(os.path.dirname(__file__), 'scripts'))

try:
    from scripts.process_list import main as process_main
    from scripts.merge_graphs import GraphMerger
    from scripts.clean_data import GraphCleaner
    from scripts.config import CONSOLIDATED_GRAPH_PATH, PROCESSED_LOG_PATH, DATA_TO_BE_CLEANED_DIR, CACHE_DIR
except ImportError as e:
    print(f"错误: 无法导入必要的模块。请确保项目结构正确。\n详细错误: {e}", file=sys.stderr)
    sys.exit(1)

def run_pipeline():
    """执行完整的端到端数据处理流水线。"""
    start_time = time.time()
    
    try:
        print("==================================================")
        print("==========  启动 Chinese-Elite 数据流水线  ==========")
        print("==================================================")

        # --- 第1步：处理实体列表 ---
        print("\n--- 步骤 1/3: 开始处理实体列表 (running process_list.py) ---")
        process_main()
        print("\n--- 步骤 1/3: 实体列表处理完成 ---\n")

        # --- 第2步：合并图谱 ---
        print("--- 步骤 2/3: 开始合并图谱文件 (running merge_graphs.py) ---")
        merger = GraphMerger(
            master_graph_path=CONSOLIDATED_GRAPH_PATH,
            log_path=PROCESSED_LOG_PATH
        )
        merger.run()
        print("\n--- 步骤 2/3: 图谱文件合并完成 ---\n")

        # --- 第3步：清理数据 ---
        print("--- 步骤 3/3: 开始清理主图谱数据 (running clean_data.py) ---")
        cleaner = GraphCleaner(
            graph_path=CONSOLIDATED_GRAPH_PATH,
            output_dir=DATA_TO_BE_CLEANED_DIR,
            cache_dir=CACHE_DIR
        )
        cleaner.run()
        print("\n--- 步骤 3/3: 主图谱数据清理完成 ---\n")

        end_time = time.time()
        elapsed_time = end_time - start_time

        print("==================================================")
        print("=============      数据流水线执行完毕      =============")
        print(f"==========   总耗时: {elapsed_time:.2f} 秒   ==========")
        print("==================================================")

    except Exception as e:
        print(f"\n[!!!] 流水线执行过程中发生严重错误: {e}", file=sys.stderr)
        print("[!!!] 请检查上述错误信息并修正。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_pipeline()
