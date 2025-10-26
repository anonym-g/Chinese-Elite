# .github/scripts/update_data.sh

# 配置 Git 用户信息
git config --global user.name 'github-actions[bot]'
git config --global user.email 'github-actions[bot]@users.noreply.github.com'

# 检查工作区是否有任何变更 (包括新增、修改、删除的文件)
# The --quiet flag makes git diff exit with 1 if there are changes, 0 if not.
if ! git diff --quiet; then
    echo "检测到文件变更，正在提交..."
    
    # 添加所有变更的文件 (使用 -A 参数来包含新增、修改和删除)
    git add -A

    # 创建一个更通用的 commit message
    git commit -m "自动化更新 (Automated Update): 同步数据文件" -m "同步本次运行生成或修改的所有数据文件，包括：master_graph_qcode.json, new data, processed_files.log, cache, etc."
    
    git push
else
    echo "工作区无变更，无需提交。"
fi
