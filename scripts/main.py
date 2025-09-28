#!/usr/bin/env python3
# scripts/main.py

"""
项目主执行文件。
按顺序执行数据处理的完整流水线：
1. process_list: 从维基百科抓取、解析实体信息。
2. merge_graphs: 将独立的JSON文件合并成一个主图谱。
3. clean_data: 清理主图谱中的无效链接和问题节点。
"""

import sys
import time

try:
    from process_list import main as process_main
    from merge_graphs import main as merge_main
    from clean_data import main as clean_main
except ImportError as e:
    print(
        f"错误: 无法导入必要的子模块。\n"
        f"请确保您是从项目根目录使用 'python -m scripts.main' 命令来运行此脚本。\n"
        f"详细错误: {e}",
        file=sys.stderr
    )
    sys.exit(1)

def run_pipeline():
    """执行完整的端到端数据处理流水线。"""
    start_time = time.time()
    
    try:
        print("==================================================")
        print("==========   启动 Chinese-Elite 数据流水线   ==========")
        print("==================================================")

        # --- 第1步：处理实体列表 ---
        print("\n--- 步骤 1/3: 开始处理实体列表 (running process_list.py) ---")
        process_main()
        print("\n--- 步骤 1/3: 实体列表处理完成 ---\n")

        # --- 第2步：合并图谱 ---
        print("--- 步骤 2/3: 开始合并图谱文件 (running merge_graphs.py) ---")
        merge_main()
        print("\n--- 步骤 2/3: 图谱文件合并完成 ---\n")

        # --- 第3步：清理数据 ---
        print("--- 步骤 3/3: 开始清理主图谱数据 (running clean_data.py) ---")
        clean_main()
        print("\n--- 步骤 3/3: 主图谱数据清理完成 ---\n")

        end_time = time.time()
        elapsed_time = end_time - start_time

        print("==================================================")
        print(f"==========      数据流水线执行完毕      ==========")
        print(f"==========   总耗时: {elapsed_time:.2f} 秒   ==========")
        print("==================================================")

    except Exception as e:
        print(f"\n[!!!] 流水线执行过程中发生严重错误: {e}", file=sys.stderr)
        print("[!!!] 请检查上述错误信息并修正。", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # 只有当这个文件被直接执行时，才会运行流水线
    run_pipeline()
