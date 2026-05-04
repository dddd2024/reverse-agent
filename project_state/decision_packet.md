# DECISION_PACKET

Generated: 2026-05-04

## 1. Goal

本轮目标是：

```text
run_selected_bounded_profile_transform_validation_for_H1_H3
```

也就是：围绕 `prefix8 + Base64 chunk boundary` 做一个 **最多 8 个 hand-picked contrast candidates** 的小规模运行时验证，专门验证 H1/H3：

```text
H1: candidate byte layout / prefix length 假设错误或过窄
H3: Base64 boundary / padding / chunk alignment 可能存在 off-by-one
```

本轮不是继续审计报告，不是扩大搜索，而是把上一轮已经生成的 hypothesis matrix 落地为一个 bounded validation。当前状态已经明确建议下一步验证 H1/H3，理由是 helper/runtime prefix 一致，但 prefix8 raw bytes 落在 Base64 chunk 边界内部。

成功信号：

```text
runtime_ci_exact_wchars > 2
或
runtime_ci_distance5 < 246
且 compare_semantics_agree != false
```

如果没有达到上述信号，应记录 negative result，并停止该 H1/H3 contrast set，不要继续扩大。

---

## 2. Current Evidence

当前 active strategy：

```text
CompareAwareSearchStrategy
```

当前 sample/profile：

```text
samplereverse
```

当前已知 transform：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前 mainline：

```text
L15(prefix8)
```

当前 runtime best 仍是 exact2：

```text
candidate_hex = 78d540b49c59077041414141414141
candidate_prefix = 78d540b49c590770
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
compare_semantics_agree = true
source = pairscan
```

上一轮 Codex 已完成 profile/transform hypothesis audit，生成了 `profile_transform_hypothesis_matrix.json`，并确认 exact2 value-pool 分支没有改进：18 个生成、18 个 unique、18 个 runtime validated，best 仍为 `78d540b49c59077041414141414141`。

上一轮结论：

```text
classification = profile_transform_hypothesis_audit_complete
exact2 value-pool branch = stopped
H4/H5 temporarily demoted
next bounded target = H1/H3
```

---

## 3. Do Not Do

严格禁止：

```text
1. 不要回到 old sample_solver blind search。
2. 不要只增加 beam / budget / topN / timeout / frontier iteration limit。
3. 不要把 compare_semantics_agree=false candidates 作为主突破点。
4. 不要重复 exact2 basin value-pool evaluation。
5. 不要提交完整 solve_reports 目录。
6. 不要扫描完整 solve_reports。
7. 不要提升 5a3f7f46ddd474d0、5a3f7fc2ddd474d0、343f7f46ddd474d0。
8. 不要把 model-selected bare flag{ 当成 runtime improvement。
9. 不要重复上一轮 profile_transform_hypothesis_matrix 的纯审计工作。
10. 不要把本轮变成新的大规模 candidate generator。
```

特别注意：`negative_results.json` 已记录 exact2 basin value-pool evaluation 的失败分支，除非 diagnostics value pools 改变，否则不要重复。

---

## 4. Files To Inspect

必须先读：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
```

主要代码文件：

```text
reverse_agent/strategies/compare_aware_search.py
reverse_agent/transforms/samplereverse.py
reverse_agent/profiles/samplereverse.py
tests/test_compare_aware_search_strategy.py
```

只在必要时读取这些 artifact，不要读取完整 `solve_reports`：

```text
profile_transform_hypothesis_matrix
compare_probe
bridge_validation
pairscan_summary
exact2_basin_value_pool_validation
summary
run_manifest
```

artifact_index 已经列出最新 harness run 和关键 artifact 路径，应通过该索引定位文件，不要全目录扫描。

---

## 5. Required Audit

本轮不是重新做 hypothesis matrix，而是执行 **H1/H3 bounded validation**。Codex 必须完成：

### A. 复核上一轮前提

确认：

```text
1. profile_transform_hypothesis_audit 已完成。
2. exact2 value-pool branch 已停止。
3. H1/H3 是当前 selected bounded validation target。
4. current best baseline 是 exact2:
   78d540b49c59077041414141414141
   exact_wchars = 2
   distance5 = 246
5. 本轮不得重复 exact2 basin value-pool pool。
```

### B. 构造 tiny contrast set

围绕以下边界构造最多 8 个候选：

```text
prefix8 raw bytes
UTF-16LE wchar boundary
Base64 3-byte chunk boundary
Base64 padding / remainder
RC4 decrypted prefix comparison
```

候选必须是 hand-picked contrast candidates，不允许自动扩展 beam/budget。

建议候选设计方向：

```text
1. 保留 exact2 prefix，微调 prefix8 附近 1-2 byte。
2. 测试 Base64 chunk boundary remainder 从 2 调整到邻近状态的候选。
3. 测试 UTF-16LE wchar 对齐变化是否影响 runtime exact_wchars。
4. 测试 candidate layout/prefix length 是否导致 offline trace 与 runtime trace 解释错位。
```

### C. Runtime validation required

每个候选都必须 runtime validate。报告中必须列出：

```text
candidate_hex
candidate_prefix
layout hypothesis
base64 boundary state
offline prefix/decrypt prefix
runtime exact_wchars
runtime distance5
compare_semantics_agree
whether improved over exact2
```

### D. 判断逻辑

如果出现：

```text
runtime_ci_exact_wchars > 2
或 runtime_ci_distance5 < 246
且 compare_semantics_agree = true
```

则把该候选作为新的 bounded frontier，并更新 project_state。

如果没有出现改进：

```text
1. 记录 H1/H3 contrast set negative result。
2. 不扩大候选数。
3. 不继续本方向。
4. 给出下一轮可验证的单一新 hypothesis。
```

---

## 6. Implementation Scope

允许修改：

```text
reverse_agent/strategies/compare_aware_search.py
reverse_agent/transforms/samplereverse.py
reverse_agent/profiles/samplereverse.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/current_state.json
project_state/task_packet.json
project_state/artifact_index.json
project_state/negative_results.json
```

允许新增或更新的 artifact：

```text
solve_reports/.../reports/tool_artifacts/samplereverse/h1_h3_boundary_validation.json
```

允许的实现：

```text
1. 增加一个 H1/H3 bounded contrast candidate evaluator。
2. 增加 trace metadata：candidate layout、UTF-16LE bytes、Base64 boundary、RC4 decrypt prefix。
3. 增加最多 8 个 hand-picked candidates。
4. 对每个 candidate 做 runtime validation。
5. 更新 compact project_state。
6. 记录 negative result，如果本轮无改进。
```

不允许的实现：

```text
1. 新 blind search。
2. 新 guided pool expansion。
3. 新 beam search。
4. 提高 timeout。
5. 修改 final selection 逻辑来制造“改进”。
6. 重构 harness/pipeline。
7. 提交完整 solve_reports。
```

---

## 7. Tests

至少运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "profile or transform or boundary or trace or compare"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果改动 transform/profile/helper：

```powershell
python -m pytest -q
```

运行 bounded harness，使用新 run name：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_h1_h3_boundary_validation_20260504 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

然后更新 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_h1_h3_boundary_validation_20260504
python -m reverse_agent.project_state status
```

上一轮测试基线是 `tests/test_compare_aware_search_strategy.py` 62 passed，全量 pytest 144 passed；本轮不得引入回归。

---

## 8. Stop Conditions

立即停止并报告：

```text
1. 需要超过 8 个 hand-picked contrast candidates。
2. 需要扩大 beam / budget / topN / timeout。
3. 需要 full solve_reports scan。
4. 发现 helper/runtime prefix mismatch。
5. 只能依赖 compare_semantics_agree=false candidate。
6. 无法 runtime validate。
7. 无法证明本轮候选确实围绕 H1/H3 boundary。
8. 需要修改 final selection 才能显示收益。
```

成功停止条件：

```text
1. 完成 H1/H3 最多 8 个候选的 runtime validation。
2. 每个候选都有 transform trace。
3. 明确是否有 exact_wchars > 2 或 distance5 < 246。
4. 如果有改进，更新 current_best 和 next_local_action。
5. 如果无改进，记录 negative result，并给出下一轮单一 bounded hypothesis。
```

## GPT Decision Summary

下一步不要再做“审计矩阵”，因为 Codex 已经完成。下一步应让 Codex 实施一个很小的 H1/H3 bounded validation：

```text
最多 8 个 hand-picked contrast candidates
围绕 prefix8 + Base64 chunk boundary
必须 runtime validate
不扩大搜索预算
不重复 exact2 value-pool
```

当前基线是：

```text
78d540b49c59077041414141414141
exact_wchars = 2
distance5 = 246
compare_semantics_agree = true
```

只有超过这个 runtime baseline，才算真正推进。
