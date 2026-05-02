# DECISION_PACKET

Generated: 2026-05-02

## 1. Goal

本轮目标是审计 **exact2 seed source quality**，核心对象是当前最强 runtime-consistent candidate：

```text
78d540b49c590770
```

上一轮 Codex 已完成 second-hop projected preserve / pair-pool source 审计，结论是：

```text
frontier_guided_2_5a3f7f46ddd474d0 已真实执行；
所有 validated second-hop candidates 都是 compare-agree；
但结果只回落到已知 exact1 5a3e7f46ddd474d0，或变成更差 exact0；
没有发现 pair gate、refine selection、final selection、metadata 或 source inclusion 的非预算型 bug；
projected preserve anchor 5a3f7f46ddd474d0 已降级为 validated-no-runtime-gain。
```

因此本轮不要继续围绕 `5a3f7f46ddd474d0` 做 second-hop 扩展，也不要扩大 beam/budget。本轮只回答一个更窄的问题：

```text
为什么 exact2 seed 78d540b49c590770 能保持 runtime exact2 / distance5 246，
而从它派生出的 exact1/projected-preserve 路线没有继续提升？
```

Codex 本轮应基于现有 artifacts，重建 `78d540b49c590770` 的候选来源链路，判断 exact2 source 是否存在可利用但未被后续 frontier/guided/source pool 正确继承的候选源。如果没有 source bug，则应明确转向下一类假设，而不是继续搜索同一局部邻域。

本轮默认是 **审计任务**，不是实现任务。只有证据证明存在小范围、非预算型、非 blind-search 的 exact2-source bug，才允许最小代码修复。

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
current_bottleneck.reason = candidate_quality_insufficient_after_projected_winner
current_bottleneck.confidence = high
next_local_action = audit_exact2_seed_source_quality_without_blind_search
```

### Current best candidates

| candidate | role | source | compare agree | runtime exact | distance5 | status |
|---|---|---|---:|---:|---:|---|
| `78d540b49c590770` | exact2 best | `pairscan` / bridge / final refine | true | 2 | 246 | keep and investigate as source anchor |
| `5a3e7f46ddd474d0` | exact1 frontier | `exact2_seed -> refine(seed) -> guided(frontier)` | true | 1 | 258 | keep as reference, not current breakthrough |
| `5a3f7f46ddd474d0` | projected preserve second-hop anchor | projected preserve lane | true in prior validation | 0 | 740 | downgraded; do not promote |
| `5a3f7fc2ddd474d0` | best new second-hop triad | projected/preserve pool | true | 0 | 419 | do not promote |
| `343f7f46ddd474d0` | second-hop guided/top entry | second-hop guided pool | true | 0 | 428 | do not promote |

### Latest Codex audit result

上一轮 `codex_execution_report.md` 的结论：

```text
code_fix_recommended = false
source_bug_found = false
projected_preserve_status = downgraded_validated_no_runtime_gain
classification = candidate_quality_insufficient_after_projected_winner
recommended_next = audit exact2 seed source quality for 78d540b49c590770 without blind search
```

Key findings:

1. `frontier_guided_2` 生成了 16 个 top entries、8 个 validation candidates、8 个 pair entries、8 个 triad entries。
2. 8 个 validated candidates 全部是 compare-agree。
3. 只有 `5a3e7f46ddd474d0` 保留 exact1；所有新的 second-hop candidates 都是 exact0。
4. best new second-hop candidate 是 `5a3f7fc2ddd474d0`，runtime exact0 / distance5 419。
5. `pair_escape_source_statuses` 为 `profile_source_empty`，是因为没有有效 profile escape entries 通过 gate，不是合法 better source 被漏收。
6. 没有发现 legal compare-agree better source 被 misclassified、ranked out 或 omitted。

### Test evidence from latest report

上一轮审计阶段运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"  # 5 passed, 50 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                                                # 55 passed
```

没有重复运行 full harness，符合上一轮计划。

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
10. 不要继续修 pair gate、refine selection、final selection 或 loop stop condition；这些方向已有 negative evidence。
11. 不要直接引入第三跳。若认为需要第三跳，必须先证明 exact2 source lane 无法解释当前瓶颈。
12. 不要修改 GUI、模型调用路径、云端 API 路径、pipeline 总控或 harness 总控。
13. 不要让 Codex 重复实现项目中已有的 compare-aware/frontier/guided/refine 功能。
14. 不要把本轮做成“再跑一次搜索”。本轮是 source-quality audit。

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
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

只聚焦 exact2 source chain 相关逻辑，重点查找：

```text
pairscan source construction
bridge source construction
frontier/refine retention of exact2 candidate
exact2 seed -> exact1 frontier derivation
candidate metadata construction
source role / anchor_mode / frontier_role assignment
source ranking / reject reasons
pair_frontier_pool source inclusion
triad_frontier_pool source inclusion
profile source and escape source construction
projected preserve source construction only as comparison target, not primary route
```

只读最新 run 的必要 artifacts，不读取完整 `solve_reports`：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\run_manifest.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\bridge_search_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\pairscan_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\bridge\bridge_validation\bridge_validation.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_refine_2\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_strata_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_probe.json
```

仅作为对照读取 projected preserve second-hop artifacts：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
```

只有当上述 indexed artifacts 无法解释 exact2 source chain 时，才允许搜索同名 helper 或 profile/transform 相关调用点。

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
frontier_exact1 / candidate_quality_insufficient_after_projected_winner
```

4. 确认上一轮结论：

```text
projected preserve source downgraded
no source bug found
no code fix recommended
no full harness rerun required
```

### B. Exact2 seed source reconstruction

必须重建 `78d540b49c590770` 的来源链路，至少回答：

1. `78d540b49c590770` 最早在哪个 artifact / stage 出现：`pairscan_summary`、`bridge_search_result`、`bridge_validation`、`frontier_refine_2`，还是其他 indexed artifact。
2. 它的 source role、anchor mode、frontier role、rank reason、selection reason、validation record 分别是什么。
3. 它是如何从 pairscan/bridge 进入 final refine 或 best candidate retention 的。
4. 它是否被用于后续 frontier/guided source generation；如果没有，原因是设计如此、metadata 不足，还是 ranking/source inclusion bug。
5. `exact2_seed(78d540b49c590770) -> refine(seed) -> guided(frontier)` 如何产生 `5a3e7f46ddd474d0`，其中哪些 byte/position/pair 改动导致 exact2 降为 exact1。
6. `78d540b49c590770` 与 `5a3e7f46ddd474d0` 的 source-lane 差异是否被记录在 frontier/profile/strata artifacts 中。
7. `78d540b49c590770` 中能够解释 runtime exact2 的 source values 是否被后续 source pool 继承、ranked out、过滤、遗漏或改写。
8. 是否存在“exact2 只作为 final candidate 保留，但未作为 source anchor 进入下一轮 bounded generation”的结构性问题。
9. 如果 exact2 seed 没有被转化为可复用 source profile，判断这是合理策略约束还是当前 bottleneck 的根因。
10. 是否能从 exact2 source lane 提出一个更窄的 non-budget hypothesis，例如 source-role promotion、source metadata preservation、exact2-lane anchored audit，而不是扩大搜索。

### C. Exact2 vs exact1/projected-preserve comparison

必须比较以下候选：

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
5a3f7fc2ddd474d0
343f7f46ddd474d0
```

必须回答：

1. exact2 `78d540b49c590770` 比 exact1 `5a3e7f46ddd474d0` 多匹配的 runtime exact 位，是否对应特定 source lane、pair、position 或 transform boundary。
2. exact1 `5a3e7f46ddd474d0` 为什么能从 exact2 seed 派生，但反而 runtime exact 降低。
3. projected preserve `5a3f7f46ddd474d0` 为什么距离爆炸到 exact0 / distance5 740。
4. best new second-hop `5a3f7fc2ddd474d0` 为什么只能达到 exact0 / distance5 419，是否说明 second-hop source 没有继承 exact2 的关键 lane。
5. `343f7f46ddd474d0` 这类 guided/top entry 是否代表 broader mutation noise，而不是有效 source lane。
6. 如果 exact2 advantage 与 transform/profile boundary 有关，必须具体指出可能边界：compare prefix、UTF-16LE/Base64/RC4 boundary、pair positions、prefix length L15(prefix8)，不能笼统说“transform 可能错”。

### D. Source bug / no-source-bug classification

必须明确分类为以下之一：

```text
A. exact2 source metadata/ranking/inclusion bug found
B. exact2 source lane is retained correctly, but cannot generate stronger candidates under current profile
C. exact2 advantage suggests transform/profile boundary audit is required
D. evidence insufficient; project_state/artifact_index must be rebuilt with more source diagnostics
```

如果选择 A，必须给出最小修复点。

如果选择 B 或 C，默认不改代码，只更新 `codex_execution_report.md` 和必要的 `project_state`。

如果选择 D，不要读取完整 `solve_reports`；应要求 Codex 增强 project_state/artifact indexing 或 source diagnostics。

### E. Required evidence tables

#### Table 1: exact2 source chain

| stage | artifact | candidate | source role | rank / selection reason | validation result | implication |
|---|---|---|---|---|---|---|

#### Table 2: exact2 vs descendants

| candidate | role | source path | exact | distance5 | byte/pair/source delta vs exact2 | source implication | keep / downgrade / investigate |
|---|---|---|---:|---:|---|---|---|

Required rows:

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
5a3f7fc2ddd474d0
343f7f46ddd474d0
```

#### Table 3: source-lane inheritance audit

| exact2 source lane | present in exact2? | inherited by exact1 frontier? | inherited by projected preserve? | ranked out / filtered / missing? | evidence | conclusion |
|---|---:|---:|---:|---|---|---|

#### Table 4: next hypothesis ranking

| hypothesis | evidence for | evidence against | expected next evidence | risk | recommendation |
|---|---|---|---|---|---|

Candidate hypotheses must include at least:

```text
promote exact2 seed source as bounded source anchor
preserve exact2 source metadata into frontier/guided generation
exact2 source lane cannot be exploited under current profile
transform/profile boundary assumption needs audit
project_state artifacts lack sufficient source diagnostics
continue projected preserve second-hop source
return to blind search / increase budget
```

The last two should normally be rejected unless new evidence contradicts current state.

---

## 6. Implementation Scope

默认本轮不改代码。

允许做的事情：

```text
1. 审计 existing artifacts 和 compare_aware_search.py 中 exact2 source chain。
2. 写一个小型本地 diagnostic/read-only script 临时读取 indexed artifacts，但不要提交大型 runtime output。
3. 更新 project_state/codex_execution_report.md。
4. 如已有 project_state builder 能表达 exact2 source chain，可运行它重建 project_state。
5. 只有发现 exact2-source bug 时，才做最小代码修复和测试。
```

允许修改的文件范围：

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

1. artifacts 证明 exact2 的合法 compare-agree source lane 被误分类、过早 rank out、未继承到 frontier/guided source pool，或 source metadata 丢失。
2. 修复点能定位到 `compare_aware_search.py` 中具体 source generation、source metadata、ranking、inclusion 或 retention 分支。
3. 修复不扩大 beam、budget、topN、timeout、iteration limit。
4. 修复不使用 compare-disagree candidates 作为主线。
5. 修复不破坏 current exact2 best `78d540b49c590770` 的保留。
6. 修复必须补充单元测试证明不是预算扩大，也不是 blind search。

如果审计结果只是“exact2 source chain 可解释但不可利用”，不要改代码。应写出下一轮转向：exact2-lane anchored hypothesis、transform/profile boundary audit，或 project_state diagnostics enhancement。

---

## 7. Tests

审计阶段先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果只更新报告 / project_state，且没有代码修改，不要重复运行 full harness。

如果修改 exact2 source metadata / ranking / inclusion / retention，必须补充或更新单元测试，语义至少覆盖：

```text
test_exact2_seed_source_metadata_is_preserved_for_frontier_generation
test_exact2_seed_source_lane_can_be_a_bounded_anchor_without_budget_expansion
test_exact2_seed_source_does_not_admit_compare_disagree_candidates
test_exact2_seed_source_preserves_current_best_candidate
test_exact2_seed_source_does_not_expand_beam_or_timeout
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

只有在代码修复后需要 runtime 验证，才运行新 harness。必须使用新 run name，不能覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_exact2_source_quality_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_exact2_source_quality_20260502
python -m reverse_agent.project_state status
```

最终报告必须包含：

1. exact2 seed source chain。
2. exact2 vs exact1/projected-preserve source-lane comparison。
3. 是否发现 exact2-source bug。
4. 是否修改代码；如果修改，列出函数、测试、是否运行 harness。
5. 如果不修改代码，明确下一轮单一推荐方向。
6. 是否需要 transform/profile boundary audit。
7. 是否需要增强 project_state/artifact diagnostics。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 发现 exact2 source lane 已被正确保留，但当前 profile 无法从它生成更强候选。
   - 不改代码。
   - 写清楚 exact2 advantage 的来源和无法继续利用的原因。
   - 推荐下一轮 exact2-lane anchored hypothesis 或 transform/profile boundary audit。

2. 发现 exact2 合法 compare-agree source lane 被误分类、ranked out、未继承或 metadata 丢失。
   - 只做最小修复。
   - 补单元测试。
   - 不扩大预算。

3. exact2 advantage 指向 transform/profile boundary，而不是 source/ranking bug。
   - 不推翻完整 transform。
   - 提出具体 boundary hypothesis。
   - 下一轮再单独验证。

4. 需要第三跳、更大预算、更多 beam/topN/timeout 才能继续。
   - 停止。
   - 不修改预算。
   - 只报告为什么当前 evidence 不足以支持该扩展。

5. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先要求增强 `artifact_index` 或 source diagnostics。

6. 唯一可行方向依赖 `compare_semantics_agree=false` candidate。
   - 不作为主线。
   - 记录到 negative results 或报告中。

7. 审计结果仍然只建议继续 projected preserve second-hop。
   - 拒绝该方向，除非提供新的 artifact 证明上一轮 no-source-bug 结论错误。

---

## GPT Decision Summary

当前已经排除的方向：

```text
1. second-hop 没有执行：已修复，frontier_guided_2 已生成。
2. pair gate / refine / final selection 错误过滤更优候选：已审计，未发现。
3. projected preserve second-hop 继续扩展：已验证无收益，5a3f7f46ddd474d0 降级。
```

下一轮单一方向：

```text
audit exact2 seed source quality for 78d540b49c590770 without blind search
```

Codex 不应继续扩大搜索，而应回答：

```text
78d540b49c590770 的 exact2 source lane 是什么；
它为什么没有被后续 frontier/guided generation 继续利用；
这是 source metadata/ranking/inclusion bug，还是 transform/profile boundary 问题。
```

如果没有可定位的 source bug，本轮不要改代码。下一轮应转向更窄的 exact2-lane hypothesis 或 transform/profile boundary audit。