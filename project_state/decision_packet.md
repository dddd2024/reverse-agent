# DECISION_PACKET

## 1. Goal

本轮目标是从已经确认的 `D. validated_but_no_runtime_gain` 状态出发，做一次 **second-hop composition audit（二跳组合审计）**。

不要继续验证上一轮 validation-slot patch。上一轮 Codex 报告已经确认：`projected preserve handoff` 候选 `5a3f7f46ddd474d0` 已经进入 `guided_pool_validation`，但 runtime 没有收益。因此当前问题不再是 validation ordering，而是：

> 在 preserve-stabilized projected winner 已经被验证但无收益之后，compare-aware pipeline 是否缺少下一跳组合逻辑？

本轮要回答两个问题：

1. `5a3f7f46ddd474d0` 这类 handoff candidate 后续是否被正确用于组合下一批候选。
2. 当前是否错误地把 `63@pos1` 直接混入 current neighbor，而不是先围绕 preserve-stabilized projected winner 做二跳组合。

默认先审计，不直接改代码。只有审计能定位到明确的 composition gap，才允许做最小实现。

---

## 2. Current Evidence

当前事实来源是 `project_state`，不要用记忆替代仓库状态。

已知状态：

```text
sample = samplereverse
active_strategy = CompareAwareSearchStrategy
current_mainline = L15(prefix8)
current_bottleneck.stage = frontier_refine
current_bottleneck.reason = projected_winner_reached_pair_gate
current_bottleneck.confidence = medium
missing_evidence = []
```

已知变换链：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前 best candidates：

| role | candidate_prefix | compare agree | runtime exact | distance5 | source |
|---|---|---:|---:|---:|---|
| exact2 | `78d540b49c590770` | true | 2 | 246 | pairscan |
| exact1/frontier | `5a3e7f46ddd474d0` | true | 1 | 258 | exact2_seed -> refine -> guided(frontier) |
| projected preserve handoff | `5a3f7f46ddd474d0` | true | 0 | 740 | projected preserve validation |

上一轮 Codex 结论：

```text
validation-slot patch 已经生效。
5a3f7f46ddd474d0 已经从 pair_frontier_pool 进入 validation_candidates。
该候选 validation 后没有 runtime gain。
当前失败分类推进为 D. validated_but_no_runtime_gain。
不要继续修 validation ordering。
```

测试历史：

```text
python -m pytest -q tests/test_compare_aware_search_strategy.py -> 51 passed
python -m pytest -q -> 133 passed
```

最新 harness 仍出现 `error_cases=1`，但原因是最终 model call 触发 Copilot CLI quota `402 You have no quota`。该错误不否定 compare-aware artifacts，因为 guided-pool validation、frontier validation、refine validation、SMT artifacts 已在 quota error 前生成。

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。
6. 不要继续围绕 validation ordering 做重复修复；上一轮证据显示 validation-slot patch 已生效。
7. 不要为了写报告消耗 Copilot/API quota。先做本地 artifact/code audit。
8. 不要修改 GUI、harness、pipeline 总控或模型调用路径，除非审计证据证明问题不在 compare-aware strategy。

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

然后只读 artifact index 指向的必要 artifacts。优先级如下：

```text
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\case_results\samplereverse-exact1-projected-vs-neighbor.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\summary.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\reports\tool_artifacts\samplereverse\frontier_guided_1_5a3e7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\reports\tool_artifacts\samplereverse\frontier_guided_1_5a3e7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\reports\tool_artifacts\samplereverse\frontier_refine_1\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
solve_reports\harness_runs\samplereverse_validation_slot_verify_20260430\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_strata_summary.json
```

代码审计范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

只有在这些文件无法解释 composition 断点时，才允许进一步搜索同名 helper、candidate metadata、frontier/refine composition 相关调用点。不要全文扫描 `solve_reports`。

---

## 5. Required Audit

Codex 本轮必须先输出审计发现，再决定是否改代码。

审计问题清单：

1. 确认工作区初始是否干净。
2. 确认当前 `project_state` 是否仍指向 `samplereverse_validation_slot_verify_20260430`。
3. 从 guided-pool validation artifact 中确认以下三个候选的 runtime 结果：

```text
5a3e7f46ddd474d0
5a3f7f46ddd474d0
78d540b49c590770
```

4. 从 guided-pool result / compare-aware result 中追踪 `5a3f7f46ddd474d0` 的 metadata：
   - `frontier_role`
   - `pair_candidate_origin`
   - `pair_projected_boundary_role`
   - `pair_projected_winner_gate_status`
   - 是否被标记为 `projected_preserve_handoff`

5. 审计 `compare_aware_search.py` 中候选组合逻辑：
   - pair frontier 如何生成；
   - guided pool 如何从 frontier/near-local/projected candidates 生成；
   - refine 阶段如何选择下一跳 seed；
   - validation 后无收益的 candidate 是否仍可作为下一跳组合 anchor；
   - 是否存在“validated 但无收益后直接丢弃”的路径。

6. 明确回答：

```text
当前 pipeline 是否支持围绕 5a3f7f46ddd474d0 做 second-hop composition？
```

7. 如果不支持，指出最小断点位置，必须具体到函数/分支/metadata 条件。

8. 产出一个表格，至少包含：

| candidate | source stage | metadata role | validation result | next-hop eligible? | drop/composition point |
|---|---|---|---|---|---|

---

## 6. Implementation Scope

默认本轮是审计任务，不改代码。

只有满足下面全部条件，才允许实现最小 patch：

1. artifact 证明 `5a3f7f46ddd474d0` 已经 validated but no gain。
2. 代码审计证明它后续没有进入 second-hop composition。
3. 断点能在 `compare_aware_search.py` 内通过小范围逻辑修复。
4. 修复不增加全局预算、不扩大 blind search、不引入 compare_semantics_agree=false 主线。

允许修改：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/current_state.json
project_state/task_packet.json
project_state/artifact_index.json
project_state/model_gate.json
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

允许的最小实现方向：

```text
把 compare_semantics_agree=true 且 metadata 显示为 projected_preserve_handoff / projected_winner_promoted_to_near_local 的 validated candidate，作为 second-hop composition anchor 进入下一轮局部组合。
```

实现时必须保持：

1. 不提高 beam/budget。
2. 不改变旧 blind search。
3. 不把失败候选无条件提升为 best。
4. 不把 `5a3f7f46ddd474d0` 当作最终答案。
5. 只新增一个受 metadata gate 控制的二跳入口。

如果审计结果显示已有 second-hop composition 逻辑，只是 artifact 没有被正确记录，则不要改 strategy，优先修 project_state/reporting 的证据采集。

---

## 7. Tests

审计阶段先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
python -m reverse_agent.project_state status
```

如果没有改代码，不要为了重复证明 validation-slot patch 而重跑同一轮 harness。直接输出审计报告即可。

如果实现了最小 second-hop composition patch，必须补单元测试，测试名建议覆盖以下语义：

```text
test_validated_projected_preserve_handoff_can_seed_second_hop_composition
test_second_hop_composition_does_not_admit_compare_disagree_candidate
test_second_hop_composition_does_not_expand_budget
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

如需 harness 验证，使用新的 run name，不覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_composition_audit_20260430 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

如果 Copilot quota 仍报 `402 You have no quota`，不要把它误判为 strategy 失败。只要 compare-aware artifacts 已生成，就基于已生成 artifacts 重建 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_second_hop_composition_audit_20260430
python -m reverse_agent.project_state status
```

最终必须更新：

```text
project_state/codex_execution_report.md
```

报告必须包含：

1. 是否发现 second-hop composition gap。
2. 如果发现，断点函数和触发条件是什么。
3. 是否改代码；如果改了，改动范围是什么。
4. 是否补测试，测试结果是什么。
5. 是否运行 harness；如果运行，new run name 和关键 runtime 结果是什么。
6. 是否仍停在 `frontier_refine / projected_winner_reached_pair_gate`。
7. 下一轮是否应继续 second-hop composition，还是转向 artifact/reporting 修正。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 审计确认 `5a3f7f46ddd474d0` 已支持 second-hop composition，但 artifact 没显示下一跳结果。
   - 停止改 strategy。
   - 转向 artifact/reporting 审计。

2. 审计确认 `5a3f7f46ddd474d0` 不支持 second-hop composition，且断点明确。
   - 只做最小 metadata-gated patch。
   - 补单元测试。
   - 不扩大预算。

3. 审计发现唯一可行候选是 `compare_semantics_agree=false`。
   - 不作为主线。
   - 记录为负结果。

4. 新 patch 导致 `exact2` best candidate `78d540b49c590770` 被丢失或降级。
   - 立即回退该 patch。

5. 测试失败且原因不在本轮改动范围内。
   - 不继续扩大修改范围。
   - 把失败命令、错误摘要、相关文件写入 `codex_execution_report.md`。

6. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先补充 `artifact_index.json` 或要求重建 project_state。

---

## GPT Decision Summary

下一轮 Codex 不应继续问“handoff 有没有进入 validation”，因为已经进入；也不应继续调大搜索预算。当前应该把问题收窄为：

```text
validated-but-no-gain 的 projected preserve handoff 是否还能作为二跳组合 anchor？
```

如果不能，做一个受 metadata gate 控制的最小 second-hop composition patch；如果已经能，则不要改 strategy，改查 artifact/reporting 为什么没有反映下一跳。
