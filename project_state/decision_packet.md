# DECISION_PACKET

Generated: 2026-05-02

## 1. Goal

本轮目标是审计 **transform/profile boundary for the exact2 basin**，核心稳定参考点是当前 best exact2 candidate：

```text
78d540b49c590770
```

上一轮 Codex 已完成 exact2 seed source quality 审计，结论是：

```text
78d540b49c590770 是 profile 传入的首位 seed anchor；
它被 bridge/pairscan、seed-guided validation、frontier refine 一致保留为当前 best exact2；
没有发现 source metadata 丢失、lane 误分类、validation ordering、candidate exclusion、gate/drop 或 artifact emission 的非预算型 bug；
后续 guided/frontier 变体一旦偏离该 anchor，质量会快速退化到 exact1 或 exact0。
```

因此本轮不要继续扩大候选源、不要继续 projected preserve second-hop、不要回退 blind search。本轮只回答一个更窄的问题：

```text
为什么 78d540b49c590770 能稳定保持 runtime exact2 / distance5 246，
但当前 transform/profile/scoring 边界无法从这个 exact2 basin 继续推进？
```

Codex 本轮应围绕 `samplereverse` 的 profile、transform、compare-window、UTF-16LE/Base64/RC4 boundary 和 runtime exact-position mapping 做审计，判断当前 scoring/profile 是否只保护了 `flag{` prefix shape，而没有正确表达后续字符或边界信息。

本轮默认是 **审计任务**，不是搜索任务。只有证据证明存在小范围、非预算型、非 blind-search 的 transform/profile boundary bug，才允许最小代码修复。

---

## 2. Current Evidence

事实来源是当前 `project_state` 文件，不要用记忆替代仓库状态。

### Current state

```text
active_strategy = CompareAwareSearchStrategy
sample = samplereverse
current_mainline = L15(prefix8)
known_transform = input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
current_bottleneck.stage = frontier_exact1
current_bottleneck.reason = candidate_quality_or_transform_profile_boundary_after_exact2_source_audit
current_bottleneck.confidence = high
next_local_action = audit_transform_profile_boundary_for_exact2_basin_without_blind_search
```

### Current best candidates

| candidate | role | source | compare agree | runtime exact | distance5 | status |
|---|---|---|---:|---:|---:|---|
| `78d540b49c590770` | exact2 best / stable reference | `profile_seed -> bridge_pairscan -> seed_guided -> final_refine` | true | 2 | 246 | keep and use as boundary reference |
| `5a3e7f46ddd474d0` | exact1 frontier / contrast case | `exact2_seed(78d...) -> refine(seed) -> guided(frontier)` | true | 1 | 258 | contrast against exact2 |
| `5a3f7f46ddd474d0` | projected preserve second-hop anchor | projected preserve lane | true in prior validation | 0 | 740 | downgraded; do not promote |
| `5a3f7fc2ddd474d0` | best new second-hop triad | projected/preserve pool | true | 0 | 419 | do not promote |
| `343f7f46ddd474d0` | second-hop guided/top entry | second-hop guided pool | true | 0 | 428 | do not promote |

### Latest Codex audit result

上一轮 `codex_execution_report.md` 的结论：

```text
classification = exact2_seed_source_quality_audited_no_source_bug
code_fix_recommended = false
source_bug_found = false
projected_preserve_status = downgraded_validated_no_runtime_gain
recommended_next = audit transform/profile boundary for the exact2 basin using 78d540b49c590770 as stable reference
```

Key findings:

1. `78d540b49c590770` 是 profile first seed anchor，并在 bridge、seed-guided validation、final refine 中保持 best exact2。
2. bridge pairscan 记录 hot_positions `0,1`，并且只验证出 `78d540b49c590770` 为 exact2 / distance5 246。
3. seed-guided mutations around `78d540b49c590770` 会掉到 exact0，而 unchanged base anchor 保持 exact2。
4. frontier1 恢复 exact1 `5a3e7f46ddd474d0` / distance5 258，但没有改善 exact2。
5. frontier2 / projected preserve 主要恢复同一个 exact1 或 exact0 variants；没有合法 better compare-agree source 被遗漏。
6. 没有发现 source metadata loss、lane misclassification 或 validation candidate exclusion。

### Test evidence from latest report

上一轮审计阶段运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "exact2 or frontier or pairscan"  # 23 passed, 32 deselected
```

没有运行新 harness，符合上一轮计划。

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout、frontier iteration limit 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认读取完整 `solve_reports`。
6. 不要默认读取完整 `PROJECT_PROGRESS_LOG.txt`。
7. 不要继续围绕 `5a3f7f46ddd474d0` 做 projected preserve second-hop 扩展。
8. 不要重新跑同一个 `samplereverse_second_hop_loop_fix_verify_20260502` 来碰运气。
9. 不要把 `5a3f7f46ddd474d0`、`5a3f7fc2ddd474d0` 或 `343f7f46ddd474d0` 提升为 best/final。
10. 不要继续修 pairscan、bridge validation、frontier source、selection、loop stop condition 或 exact2 source metadata；这些方向已有 negative evidence。
11. 不要直接引入第三跳。若认为需要第三跳，必须先证明 transform/profile boundary 审计无法解释当前瓶颈。
12. 不要修改 GUI、模型调用路径、云端 API 路径、pipeline 总控或 harness 总控。
13. 不要让 Codex 重复实现项目中已有的 compare-aware/frontier/guided/refine 功能。
14. 不要把本轮做成“再跑一次搜索”。本轮是 transform/profile boundary audit。

---

## 4. Files To Inspect

必须先读：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
project_state/decision_packet.md
```

优先审计代码范围：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

只聚焦 transform/profile/scoring boundary 相关逻辑，重点查找：

```text
SamplereverseProfile seed / anchor / known prefix / target prefix assumptions
known_transform construction
UTF-16LE input expansion
Base64 encoding block boundaries
RC4 key / stream / compare alignment
compare prefix / compare window / runtime exact-position mapping
L15(prefix8) interpretation
runtime_ci_exact_wchars and distance5 scoring assumptions
compare_probe output mapping
profile scoring around flag{ prefix shape
candidate byte index -> transformed compare index mapping
```

只读最新 run 的必要 artifacts，不读取完整 `solve_reports`：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\run_manifest.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_probe.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_probe.log
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\bridge_search_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\pairscan_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\bridge_validation\bridge_validation.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_refine_2\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_strata_summary.json
```

仅作为 contrast 读取 projected preserve second-hop artifacts：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
```

只有当上述 indexed artifacts 无法解释 transform/profile boundary 时，才允许搜索同名 helper 或要求增强 project_state/artifact diagnostics。

---

## 5. Required Audit

Codex 本轮必须先输出审计发现，再决定是否修改代码。

### A. Pre-audit checks

1. 确认工作区初始是否 clean。
2. 确认最新 indexed run 是：

```text
samplereverse_second_hop_loop_fix_verify_20260502
```

3. 确认当前 bottleneck 是：

```text
frontier_exact1 / candidate_quality_or_transform_profile_boundary_after_exact2_source_audit
```

4. 确认上一轮结论：

```text
exact2 seed source quality audited
no source bug found
no code fix recommended
projected preserve downgraded
no full harness rerun required before concrete transform/profile hypothesis
```

### B. Transform/profile boundary audit

必须回答：

1. `SamplereverseProfile` 中的 seed、anchor、known prefix、target prefix、profile scoring 假设分别是什么。
2. `samplereverse` transform 是否确认为：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

3. `L15(prefix8)` 的实际含义是什么：15 是输入长度、transformed compare length、candidate prefix length，还是 profile-specific label。
4. `prefix8` 与 `flag{` prefix shape 的关系是什么。
5. `runtime_ci_exact_wchars=2` 对应 compare output 的哪两个 wchar/position。
6. `distance5=246` 如何计算，是否只反映前五个 wchar/compare chars 的距离。
7. `78d540b49c590770` 的两个 exact wchar 是否对应特定 byte、Base64 block、RC4 keystream offset 或 compare window。
8. `5a3e7f46ddd474d0` 为什么 local shape 更接近但 runtime exact 从 2 降到 1。
9. `5a3f7f46ddd474d0` 为什么 compare-agree 但 runtime distance 爆炸到 740。
10. `5a3f7fc2ddd474d0` 和 `343f7f46ddd474d0` 为什么只能达到 exact0 / distance5 419 或 428，是否说明当前 profile score 不能表达 exact2 basin 的关键 boundary。
11. 是否存在以下边界问题：

```text
compare prefix/window boundary mismatch
UTF-16LE wchar alignment issue
Base64 block boundary issue
RC4 keystream alignment issue
candidate byte index -> transformed compare index mismatch
runtime exact-position mapping mismatch
distance5 overfitting flag{ prefix shape
known prefix or target prefix too short / too rigid
```

12. 如果 transform/profile 与 runtime 完全一致，必须说明为什么当前 scoring 无法继续利用 exact2 basin，以及下一轮应如何形成新的 bounded hypothesis。

### C. Exact2 basin contrast

必须比较以下候选：

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
5a3f7fc2ddd474d0
343f7f46ddd474d0
```

必须回答：

1. exact2 `78d540b49c590770` 比 exact1 `5a3e7f46ddd474d0` 多匹配的 runtime exact 位，是否对应具体 transformed position。
2. exact1 `5a3e7f46ddd474d0` 的 byte/pair/source delta 为什么导致 exact 数下降但 distance5 接近。
3. projected preserve `5a3f7f46ddd474d0` 为什么距离爆炸。
4. best new second-hop `5a3f7fc2ddd474d0` 是否只是 broader mutation noise，而不是 valid boundary evidence。
5. `343f7f46ddd474d0` 是否说明 guided/top entries 没有抓住 exact2 的关键 transform boundary。

### D. Boundary bug / no-boundary-bug classification

必须明确分类为以下之一：

```text
A. transform/profile boundary bug found
B. transform/profile is consistent, but scoring is too weak/short to guide beyond exact2 basin
C. exact2 basin requires a new bounded hypothesis, not more source/search work
D. evidence insufficient; project_state/artifact_index must be rebuilt with better transform/profile diagnostics
```

如果选择 A，必须给出最小修复点。

如果选择 B 或 C，默认不改代码，只更新 `codex_execution_report.md` 和必要的 `project_state`。

如果选择 D，不要读取完整 `solve_reports`；应要求 Codex 增强 project_state/artifact indexing 或 transform/profile diagnostics。

### E. Required evidence tables

#### Table 1: transform/profile boundary map

| boundary | current assumption | evidence artifact/code | matches runtime? | implication |
|---|---|---|---:|---|

Required rows:

```text
UTF-16LE expansion
Base64 block boundary
RC4 stream alignment
compare prefix/window
runtime exact wchar mapping
distance5 scoring
L15(prefix8)
profile known prefix / target prefix
```

#### Table 2: exact2 basin contrast

| candidate | role | exact | distance5 | byte/pair delta vs exact2 | mapped compare positions | implication | keep / downgrade / investigate |
|---|---|---:|---:|---|---|---|---|

Required rows:

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
5a3f7fc2ddd474d0
343f7f46ddd474d0
```

#### Table 3: boundary hypotheses

| hypothesis | evidence for | evidence against | expected next evidence | risk | recommendation |
|---|---|---|---|---|---|

Candidate hypotheses must include at least:

```text
compare prefix/window boundary mismatch
UTF-16LE wchar alignment issue
Base64 block boundary issue
RC4 keystream alignment issue
runtime exact-position mapping mismatch
profile scoring overfits flag{ prefix shape
transform/profile is consistent but insufficiently informative
continue projected preserve second-hop source
return to blind search / increase budget
```

The last two should normally be rejected unless new evidence contradicts current state.

---

## 6. Implementation Scope

默认本轮不改代码。

允许做的事情：

```text
1. 审计 existing artifacts、samplereverse profile、samplereverse transform 和 compare_aware_search.py 中 scoring/boundary 相关逻辑。
2. 写一个小型本地 diagnostic/read-only script 临时映射 candidate byte -> transform output -> runtime compare position，但不要提交大型 runtime output。
3. 更新 project_state/codex_execution_report.md。
4. 如已有 project_state builder 能表达 transform/profile boundary，可运行它重建 project_state。
5. 只有发现 transform/profile boundary bug 时，才做最小代码修复和测试。
```

允许修改的文件范围：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/model_gate.json
PROJECT_PROGRESS_LOG.txt
```

不允许修改：

```text
reverse_agent/harness.py
reverse_agent/pipeline.py
GUI 相关文件
模型调用/云端 API 路径
全局预算配置
完整 solve_reports 目录
```

只有满足以下全部条件，才允许最小代码修复：

1. artifacts 或代码审计证明存在具体 transform/profile boundary bug。
2. 修复点能定位到 `samplereverse.py` profile/transform 或 `compare_aware_search.py` 中具体 scoring/boundary mapping 分支。
3. 修复不扩大 beam、budget、topN、timeout、iteration limit。
4. 修复不使用 compare-disagree candidates 作为主线。
5. 修复不破坏 current exact2 best `78d540b49c590770` 的保留。
6. 修复必须补充单元测试证明不是预算扩大，也不是 blind search。

如果审计结果只是“transform/profile consistent but insufficiently informative”，不要改代码。应写出下一轮转向：新的 bounded hypothesis、transform/profile diagnostics enhancement，或更窄的 runtime compare-position hypothesis。

---

## 7. Tests

审计阶段先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "exact2 or frontier or pairscan or profile or transform"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果只更新报告 / project_state，且没有代码修改，不要重复运行 full harness。

如果修改 profile / transform / scoring / boundary mapping，必须补充或更新单元测试，语义至少覆盖：

```text
test_samplereverse_transform_boundary_maps_runtime_exact_positions
test_samplereverse_profile_prefix_window_matches_compare_probe
test_exact2_reference_candidate_preserves_runtime_mapping
test_projected_preserve_candidate_does_not_get_promoted
test_transform_profile_boundary_does_not_expand_budget
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

只有在代码修复后需要 runtime 验证，才运行新 harness。必须使用新 run name，不能覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_transform_profile_boundary_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_transform_profile_boundary_20260502
python -m reverse_agent.project_state status
```

最终报告必须包含：

1. transform/profile boundary map。
2. exact2 basin contrast table。
3. 是否发现 transform/profile boundary bug。
4. 是否修改代码；如果修改，列出函数、测试、是否运行 harness。
5. 如果不修改代码，明确下一轮单一推荐方向。
6. 是否需要增强 project_state/artifact diagnostics。
7. 是否仍坚持不回 blind search、不扩大预算。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 发现具体 transform/profile boundary bug。
   - 只做最小修复。
   - 补单元测试。
   - 不扩大预算。

2. 发现 transform/profile 与 runtime 完全一致，但 scoring/profile 信息不足以继续推进 exact2 basin。
   - 不改代码。
   - 写清楚 exact2 basin 无法继续利用的原因。
   - 推荐下一轮新的 bounded hypothesis 或 diagnostics enhancement。

3. exact2 basin 指向 runtime compare-position / boundary 假设，但证据不足。
   - 不推翻完整 transform。
   - 提出具体 missing diagnostics。
   - 更新 project_state/report。

4. 需要第三跳、更大预算、更多 beam/topN/timeout 才能继续。
   - 停止。
   - 不修改预算。
   - 只报告为什么当前 evidence 不足以支持该扩展。

5. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先要求增强 `artifact_index` 或 transform/profile diagnostics。

6. 唯一可行方向依赖 `compare_semantics_agree=false` candidate。
   - 不作为主线。
   - 记录到 negative results 或报告中。

7. 审计结果仍然只建议继续 projected preserve second-hop。
   - 拒绝该方向，除非提供新的 artifact 证明前两轮 no-source-bug 结论错误。

---

## GPT Decision Summary

当前已经排除的方向：

```text
1. second-hop 没有执行：已修复，frontier_guided_2 已生成。
2. pair gate / refine / final selection 错误过滤更优候选：已审计，未发现。
3. projected preserve second-hop 继续扩展：已验证无收益，5a3f7f46ddd474d0 降级。
4. exact2 seed source metadata/ranking/inclusion bug：已审计，未发现。
```

下一轮单一方向：

```text
audit transform/profile boundary for the exact2 basin using 78d540b49c590770 as stable reference, without blind search or budget expansion
```

Codex 不应继续扩大搜索，而应回答：

```text
78d540b49c590770 的 exact2 basin 对应哪些 transform/profile boundary；
为什么 5a3e7f46ddd474d0、5a3f7f46ddd474d0、5a3f7fc2ddd474d0 等对照候选会退化；
当前 profile/scoring 是否只保护 flag{ prefix shape，无法继续推进后续字符；
这是 transform/profile boundary bug，还是 scoring 信息不足。
```

如果没有可定位的 boundary bug，本轮不要改代码。下一轮应转向更窄的 runtime compare-position hypothesis 或 diagnostics enhancement。