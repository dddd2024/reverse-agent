# DECISION_PACKET

## 1. Goal

本轮目标是审计 **second-hop candidate source / projection hypothesis**。

上一轮已经完成 pair/pool quality 审计，结论是：

```text
frontier_guided_2_5a3f7f46ddd474d0 已真实执行；
second-hop pool 没有产生任何优于 exact1 5a3e7f46ddd474d0 或 exact2 best 78d540b49c590770 的 compare-agree 候选；
没有发现 compare-agree 且 runtime 更优的候选被 pair gate、refine selection 或 final best selection 错误丢弃。
```

因此本轮不要继续修 pair gate，也不要继续查 loop plumbing。当前要回答的问题是：

```text
为什么 projected preserve second-hop anchor 5a3f7f46ddd474d0 只能产生回落到已知 exact1 或更差 exact0 的候选？
```

本轮应判断：

1. 当前 second-hop value source / projection source 是否质量不足。
2. projected preserve 假设是否已经失效或进入局部噪声区。
3. 现有 artifacts 是否足以提出一个更窄的候选源修正方向。
4. 是否需要转向 transform/profile 假设审计，而不是继续在同一候选源上迭代。

默认只审计，不改代码。只有证据证明存在小范围、非预算型、非 blind-search 的候选源 bug，才允许最小修复。

---

## 2. Current Evidence

事实来源是当前 `project_state`，不要用记忆替代文件。

### 已完成事项

1. second-hop continuation loop 已修复并通过真实 harness 验证。
2. 最新 indexed run：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502
```

3. 最新 artifact index 中已经存在：

```text
frontier_guided_2_5a3f7f46ddd474d0
frontier_refine_2
```

4. Codex 已完成 second-hop pair/pool quality 审计，没有修改 strategy，也没有重复运行 harness。

### 测试结果

上一轮审计阶段运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"  # 5 passed, 50 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                                                # 55 passed
```

loop 修复轮运行过：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py  # 55 passed
python -m pytest -q                                             # 137 passed
```

### 最新候选状态

| candidate | source stage | role | compare agree | runtime exact | distance5 | result |
|---|---|---|---:|---:|---:|---|
| `78d540b49c590770` | pairscan / final refine | `best_overall` | true | 2 | 246 | retained as exact2 best and selected candidate |
| `5a3e7f46ddd474d0` | frontier guided 1, second-hop validation, `frontier_refine_2` | `exact1_frontier` | true | 1 | 258 | remains best exact1; reappears as second-hop best |
| `5a3f7f46ddd474d0` | projected preserve handoff anchor | `validated_projected_preserve_second_hop` | true in prior validation | 0 | 740 | used to launch second-hop, but not improved output |
| `5a3f7fc2ddd474d0` | second-hop triad/top entry | `validated_projected_preserve_second_hop` | true | 0 | 419 | best new exact0, still worse than exact1 |
| `343f7f46ddd474d0` | second-hop triad/top entry | `validated_projected_preserve_second_hop` | true | 0 | 428 | worse than exact1 |

### Second-hop pool audit conclusion

| source/role | count | compare agree count | validated count | best runtime exact | best distance5 | conclusion |
|---|---:|---:|---:|---:|---:|---|
| `top_entries` / `validated_projected_preserve_second_hop` | 16 | 8 validated agree | 8 | 1 | 258 | best is existing `5a3e7f46ddd474d0`, no improvement |
| `validation_candidates` / `validated_projected_preserve_second_hop` | 8 | 8 | 8 | 1 | 258 | all compare-agree, none beats exact1 or exact2 |
| `pair_frontier_pool` / preserve neighbors | 8 | 1 validated agree | 1 | 1 | 258 | only validated entry is existing exact1 |
| `triad_frontier_pool` / generated triads | 8 | 6 validated agree | 6 | 0 | 419 | all are worse than exact1 |

Validation distribution:

```text
compare-agree = 8
compare-disagree = 0
runtime exact1 = 1 candidate, distance5 258
runtime exact0 = 7 candidates, distance5 419, 428, 432, 452, 480, 486, 529
```

Pair gate diagnosis from Codex:

```text
No gate/drop bug was found.
No compare-agree runtime-better candidate was incorrectly filtered.
No second-hop candidate beats exact1.
No second-hop candidate beats exact2.
The bottleneck is candidate_quality_insufficient_after_projected_winner.
```

### Current bottleneck

```text
stage = frontier_exact1
reason = candidate_quality_insufficient_after_projected_winner
confidence = high
```

Known transform chain remains:

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout、frontier iteration limit 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。
6. 不要继续修 frontier loop stop condition；`frontier_guided_2` 已生成。
7. 不要继续修 pair gate / refine selection；上一轮已审计没有 gate/drop bug。
8. 不要重新跑同一个 `samplereverse_second_hop_loop_fix_verify_20260502` 来碰运气。
9. 不要把 `5a3f7f46ddd474d0` 当作最终答案或无条件提升为 best。
10. 不要修改 GUI、模型调用路径、pipeline 总控、harness 总控。
11. 不要为了“试试看”直接引入第三跳；如认为需要第三跳，必须先用 artifacts 证明二跳候选源为什么不足。
12. 不要默认读取完整 `PROJECT_PROGRESS_LOG.txt`；只有本轮 artifacts 无法解释候选源假设时才允许请求战略复盘。

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

优先审计的代码范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

在 `compare_aware_search.py` 中只聚焦候选源相关逻辑，重点查找：

```text
projected preserve source generation
projected winner source selection
pair escape source
triad frontier pool generation
pair_frontier_pool generation
value ranking / source ranking
candidate metadata construction
source reject reasons
projected_generated_but_distance_explosive
profile_source_empty
```

只读最新 run 的必要 artifacts，不读取完整 `solve_reports`：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_refine_2\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_strata_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\bridge_search_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\pairscan_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_probe.json
```

只有当这些文件无法解释候选源质量时，才允许搜索同名 helper 或 profile/transform 相关调用点。

---

## 5. Required Audit

Codex 本轮必须先输出审计发现，再决定是否修改代码。

### A. Pre-audit checks

1. 确认工作区初始是否干净。
2. 确认最新 indexed run 是：

```text
samplereverse_second_hop_loop_fix_verify_20260502
```

3. 确认当前瓶颈是：

```text
frontier_exact1 / candidate_quality_insufficient_after_projected_winner
```

4. 确认上一轮 pair/pool 审计结论没有发现 gate/drop bug。

### B. Candidate source / projection hypothesis audit

必须回答：

1. `5a3f7f46ddd474d0` 作为 second-hop anchor 时，候选值来源分别是什么：preserve neighbor、triad、projected source、pair escape source、其他 source。
2. 哪些 source 为空，哪些 source 产生候选但质量不足。必须解释：

```text
profile_source_empty
projected_generated_but_distance_explosive
ranked out
```

3. `5a3f7f46ddd474d0` 与 `5a3e7f46ddd474d0` 的差异是否被候选源有效利用，还是二跳生成只回到已知 exact1 邻域。
4. 为什么 `triad_frontier_pool` 的最佳新候选只有 exact0 / distance5 419，不能靠近 exact1 / exact2。
5. 为什么 `pair_frontier_pool` 中唯一 validated agree 的有效结果是已知 exact1 `5a3e7f46ddd474d0`。
6. `pair_escape_source_statuses` 为 `profile_source_empty` 是否说明 profile 的 escape source 定义不足，还是说明当前 transform 假设下没有可用 escape。
7. `bridge_search_result` / `pairscan_summary` 中的 exact2 `78d540b49c590770` 是否提示应回到 exact2 seed 的候选源，而不是继续 projected preserve anchor。
8. 当前 projected preserve hypothesis 是否仍然值得继续，还是应降级为已验证但收益不足的局部方向。
9. 是否存在非预算型修复点，例如：source metadata 误分类、projected value source 过早 rank out、合法 source 未进入二跳 pool。若没有，明确写出“无代码修复建议”。
10. 是否需要转向 transform/profile 假设审计：例如 compare prefix、RC4/base64/UTF-16LE 解释、boundary role、pair positions 是否存在错误假设。注意：不要默认推翻已知 transform，只能基于 artifacts 提出需要验证的具体假设。

### C. Required evidence tables

#### Table 1: source quality summary

| source | generated count | validated count | compare-agree count | best exact | best distance5 | dominant failure reason | conclusion |
|---|---:|---:|---:|---:|---:|---|---|

#### Table 2: source transition comparison

比较 first-hop anchor、second-hop anchor、best known exact1/exact2：

| candidate | role | source path | exact | distance5 | source implication | keep / downgrade / investigate |
|---|---|---|---:|---:|---|---|

必须至少包含：

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
5a3f7fc2ddd474d0
343f7f46ddd474d0
```

#### Table 3: next hypothesis ranking

| hypothesis | evidence for | evidence against | expected next evidence | risk | recommendation |
|---|---|---|---|---|---|

候选 hypothesis 至少包括：

```text
continue projected preserve source with source-quality fix
return to exact2 seed source audit, without blind search
profile escape source definition insufficient
transform/profile boundary assumption needs audit
candidate quality is insufficient and no code change should be made
```

---

## 6. Implementation Scope

默认本轮不改代码。

只有满足以下条件，才允许最小修改：

1. artifacts 证明存在非预算型、非 blind-search 的候选源 bug。
2. 断点能具体定位到 `compare_aware_search.py` 中某个 source generation、metadata、ranking 或 source inclusion 分支。
3. 修复不扩大 beam、budget、topN、timeout、iteration limit。
4. 修复不使用 compare-disagree 候选作为主线。
5. 修复不改变 harness、pipeline、GUI、模型调用路径。
6. 修复不破坏 exact2 best `78d540b49c590770` 的保留。

允许修改：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/model_gate.json
PROJECT_PROGRESS_LOG.txt
```

如果问题只是 evidence 不足，允许补充 project_state/reporting 证据采集；但不得修改完整 solve_reports 或提交 runtime output。

不允许修改：

```text
reverse_agent/harness.py
reverse_agent/pipeline.py
GUI 相关文件
模型调用/云端 API 路径
全局预算配置
完整 solve_reports 目录
```

允许的最小修复方向，仅在审计证明后使用：

```text
如果合法 compare-agree source 被误标记、过早 rank out、或未进入 second-hop pool，
则修正 source metadata / ranking / inclusion 条件，
但不得通过扩大预算绕过候选源质量问题。
```

---

## 7. Tests

审计阶段先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果不改代码，不要重复运行 harness。直接更新：

```text
project_state/codex_execution_report.md
```

如果改候选源 / ranking / metadata inclusion，必须补充单元测试，语义至少覆盖：

```text
test_second_hop_source_quality_includes_legitimate_compare_agree_source
test_second_hop_source_quality_does_not_admit_compare_disagree_source
test_second_hop_source_quality_does_not_expand_budget
test_second_hop_source_quality_preserves_exact2_best_candidate
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

只有在代码修改后需要 runtime 验证，才运行新 harness，且必须使用新 run name：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_candidate_source_hypothesis_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_candidate_source_hypothesis_20260502
python -m reverse_agent.project_state status
```

最终报告必须包含：

1. 候选源质量结论。
2. projected preserve hypothesis 是否保留、降级或暂停。
3. 是否应回到 exact2 seed source audit。
4. 是否需要 transform/profile 假设审计。
5. 是否改代码；如果改，改动函数和测试结果。
6. 下一轮的单一推荐方向。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 所有 second-hop source 都没有 compare-agree 更优候选，且没有 source bug。
   - 不改代码。
   - 推荐转向 exact2 seed source audit 或 transform/profile hypothesis audit。

2. projected preserve source 只产生 exact1 回落或 exact0 噪声。
   - 将 projected preserve 方向降级为局部无收益方向。
   - 不继续在同一 source 上扩大预算。

3. 发现合法 compare-agree source 被误分类、rank out 或未进入 pool。
   - 只做最小 source/ranking/inclusion 修复。
   - 补单元测试。

4. 需要第三跳或更大预算才能继续。
   - 不直接改预算。
   - 先提出单独决策，附 artifact 证据。

5. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先补 artifact_index 或要求 project_state/reporting 产出更精确索引。

6. 唯一可行方向依赖 `compare_semantics_agree=false`。
   - 不作为主线。
   - 记录到 negative_results。

---

## GPT Decision Summary

当前已经排除两个旧方向：

```text
1. second-hop 没有执行：已修复，frontier_guided_2 已生成。
2. pair gate/refine 错误过滤更优候选：已审计，未发现。
```

新的瓶颈是候选源质量：二跳源只生成了已知 exact1 或更差 exact0 候选。下一轮应审计 projected preserve source、pair/triad/projected value source、profile escape source 和 transform/profile boundary 假设，判断是继续修候选源，还是转向 exact2 seed source 或 transform/profile 假设审计。
