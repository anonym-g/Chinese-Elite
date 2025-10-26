# .github/scripts/pr_auto_merger.sh

# --- 开启详细日志模式 ---
set -x

echo "开始智能验证流程..."
merged_count=0
# 此上限限制成功合并的PR数量
max_merges=1

pr_numbers=$(gh pr list --state open --json number --jq '.[].number')
if [ -z "$pr_numbers" ]; then
    echo "没有待处理的 PR。"
    exit 0
fi

for pr_number in $pr_numbers; do
    if [ "$merged_count" -ge "$max_merges" ]; then
        echo "已达到本次运行的合并上限 (${max_merges})，流程结束。"
        break
    fi

    echo "--- 正在检查 PR #${pr_number} ---"
    
    files_changed=$(gh pr diff $pr_number --name-only)
    
    echo "PR #${pr_number} 包含以下文件变更:"
    echo "${files_changed}"
    
    # 1. 文件数量是否为1
    file_count=$(echo -n "${files_changed}" | grep -c .)
    echo "文件数量 (file_count): ${file_count}"
    if [ "${file_count}" -ne 1 ]; then
        echo "PR #${pr_number} 修改了多个文件 (${file_count} 个)，已跳过。"
        continue
    fi

    # 2. 文件名是否在白名单内
    if ! echo "${files_changed}" | grep -qE '^(docs/master_graph_qcode.json|data/LIST.md)$'; then
        echo "PR #${pr_number} 修改了不允许的文件: ${files_changed}，已跳过。"
        continue
    fi
    
    # 3: 检查 PR 是否存在合并冲突
    echo "正在检查 PR #${pr_number} 的可合并性..."
    mergeable_state=$(gh pr view $pr_number --json mergeable --jq '.mergeable')
    echo "可合并性: ${mergeable_state}"

    if [ "${mergeable_state}" = "CONFLICTING" ]; then
        echo "❌ PR #${pr_number} 存在合并冲突。正在关闭并留言..."
        comment_body="This Pull Request has been automatically closed due to merge conflicts with the base branch. Please update your branch, resolve the conflicts, then open a new Pull Request."
        gh pr comment $pr_number --body "$comment_body"
        gh pr close $pr_number
        # 跳过此PR，继续检查下一个。
        continue
    fi
    
    # 如果状态不是 'MERGEABLE' (例如 'UNKNOWN'，GitHub正在后台检查)，则本次跳过
    if [ "${mergeable_state}" != "MERGEABLE" ]; then
        echo "⚠️ PR #${pr_number} 的可合并性状态为 '${mergeable_state}' (非 MERGEABLE)，本次运行将跳过此 PR。"
        continue
    fi
    
    echo "✅ PR #${pr_number} 可以合并。开始内容评估..."

    # 4. 调用 Python 脚本进行 AI 评估
    # 将 python 命令放在 if 结构中，以便在返回非0退出码时能正常捕获
    if python scripts/validate_pr.py $pr_number; then
        exit_code=0
    else
        exit_code=$?
    fi

    if [ $exit_code -eq 0 ]; then
        echo "✅ AI评估结果：有意义。准备合并..."
        # 包装合并命令，以处理小概率发生的临时合并失败
        if gh pr merge $pr_number --auto --squash --delete-branch; then
            echo "PR #${pr_number} 已成功合并。"
            merged_count=$((merged_count + 1))
        else
            echo "❌ 合并 PR #${pr_number} 失败。可能是因为在检查后产生了新的冲突或权限问题。"
        fi
    elif [ $exit_code -eq 1 ]; then
        echo "❌ AI评估结果：无意义。正在关闭 PR 并留言..."
        comment_body="This Pull Request has been automatically closed because our AI reviewer determined the changes to be trivial or not meaningful. If you believe this is an error, please open a new PR with a more detailed explanation."
        gh pr comment $pr_number --body "$comment_body"
        gh pr close $pr_number
    else
        echo "⚠️ 评估脚本执行出错 (退出码: $exit_code)，已跳过此 PR。"
    fi
done
