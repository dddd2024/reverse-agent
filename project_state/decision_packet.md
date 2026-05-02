# DECISION_PACKET

## 1. Goal

本轮目标是审计 **second-hop candidate quality / pair gate after projected winner**。

上一轮已经完成并验证：

```text
frontier loop stop condition 已修复
frontier_guided_2_5a3f7f46ddd474d0 已真实生成
role = validated_projected_preserve_second_hop
```

因此本轮不要继续查 loop plumbing，也不要继续查 artifact emission。当前要回答的新问题是：

```text
为什么 5a3f7f46ddd474d0 作为 second-hop anchor 进入 frontier_guided_2 后，仍然没有产生优于 5a3e7f46ddd474d0 或 exact2 best 78d540b49c590770 的候选？
```

本轮默认是审计任务，不直接改代码。只有在 artifacts 和代码共同证明存在明确的小断点时，才允许最小修复。

---

## 2. Current Evidence

事实来源是当前 `project_state`，不要用记忆替代文件。

### 已经做了什么

上一轮 Codex 已按计划修复 frontier second-hop loop stop condition。根因已经确认：

```text
_frontier_continuation_candidates() 已经把 5a3f7f46ddd474d0 作为 validated_projected_preserve_second_hop 返回 "continue"，
但主循环在“无 improved frontier candidate”时把 "continue" 覆盖回 "distance_not_improved"，
导致记录了 continuation，却没有实际进入第二轮 guided run。
```

已做最小修复：

```text
当 used_second_hop=True 时保留 "continue"，不再归一化为 distance_not_improved。
```

没有扩大：

```text
FRONTIER_MAX_ITERATIONS
beam
budget
topN
timeout
blind search
model path
GUI
pipeline
harness 总控
```

### 测试结果

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"  # 5 passed, 50 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                                                # 55 passed
python -m pytest -q                                                                                                           # 137 passed
```

### 最新 harness

```text
run = solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502
executed_cases = 1
completed_without_expected = 1
error_cases = 0
candidate_quality = 1.0
evidence_coverage = 1.0
selected candidate = 78d540b49c59077041414141414141
Copilot quota 402 = not observed
project_state missing = []
```

### 最新 artifact 结论

| question | answer |
|---|---|
| 是否出现 `frontier_guided_2_5a3f7f46ddd474d0` | 是 |
| `frontier_guided_runs` 数量 | 2 |
| 第二轮 anchor | `5a3f7f46ddd474d0` |
| 第二轮 role | `validated_projected_preserve_second_hop` |
| 第一轮 converge reason | `continue` |
| 总体 converge reason | `iteration_limit` |
| exact2 best 是否保留 | 是，最终 selected candidate 仍为 `78d540b49c59077041414141414141` |
| 二跳后 best 是否改善 | 否；exact1 仍为 `5a3e7f46ddd474d0`，exact2 仍为 `78d540b49c590770` |

### 当前候选表

| candidate | source stage | frontier role | compare agree | runtime exact | distance5 | second-hop eligible | actually used in second-hop | result |
|---|---|---|---:|---:|---:|---:|---:|---|
| `78d540b49c590770` | pairscan / guided pool / final validation | `exact2_seed` / `best_overall` | true | 2 | 246 | no | no | retained as best overall / selected candidate |
| `5a3e7f46ddd474d0` | frontier guided iteration 1 and second-hop validation result | `exact1_frontier` / second-hop validation entry | true | 1 | 258 | no | no | remains best exact1 frontier |
| `5a3f7f46ddd474d0` | projected preserve handoff -> second-hop guided anchor | `validated_projected_preserve_second_hop` | true | 0 | 740 | yes | yes | second-hop artifact emitted, but no runtime gain |

### Current bottleneck

```text
stage = frontier_exact1
reason = pair_gate_after_projected_winner
confidence = medium
```

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。
6. 不要继续修 frontier loop stop condition；上一轮已修复且 harness 已验证 `frontier_guided_2` 生成。
7. 不要继续争论 validation ordering；候选已经进入 validation 和第二轮 guided。
8. 不要重新跑同一个 `samplereverse_second_hop_loop_fix_verify_20260502` 来碰运气。
9. 不要把 `5a3f7f46ddd474d0` 当作最终答案或无条件提升为 best。
10. 不要修改 GUI、模型调用路径、pipeline 总控、harness 总控。
11. 不要以“二跳没有收益”为理由直接扩大搜索预算；先分析二跳 pair/pool 输出质量。

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

代码审计优先范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

只读以下最新 run 中的必要 artifacts，不读取完整 `solve_reports`：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\run_manifest.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\case_results\samplereverse-exact1-projected-vs-neighbor.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_2_5a3f7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\frontier_refine_2\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_strata_summary.json
```

如果这些 artifacts 不能解释 pair gate 断点，才允许搜索同名 helper 或 pair gate 相关调用点。不要全文扫描 `solve_reports`。

---

## 5. Required Audit

Codex 本轮必须先输出审计结果，再决定是否修改代码。

### A. Pre-audit checks

1. 确认工作区初始是否干净。
2. 确认最新 indexed run 是：

```text
samplereverse_second_hop_loop_fix_verify_20260502
```

3. 确认 artifact index 中存在：

```text
frontier_guided_2_5a3f7f46ddd474d0
frontier_refine_2
```

4. 确认当前瓶颈是：

```text
stage = frontier_exact1
reason = pair_gate_after_projected_winner
```

### B. Second-hop pair/pool quality audit

必须回答：

1. `frontier_guided_2_5a3f7f46ddd474d0` 的 guided pool 中候选数量、来源分布、role 分布是什么？
2. 第二轮 generated candidates 中有多少：
   - `compare_semantics_agree=true`
   - `compare_semantics_agree=false`
   - 未验证 / 被过滤 / 被 pair gate 拦截
3. 第二轮 validation top candidates 的 runtime exact / distance5 分布是什么？
4. 是否存在比 `5a3f7f46ddd474d0` 更接近、但没有进入 refine 或 final validation 的候选？
5. `frontier_refine_2` 为什么没有产生更优 exact1 或 exact2？
6. `pair_gate_after_projected_winner` 的具体 gate 条件是什么？它拦截的是：
   - pair source 生成阶段；
   - projected winner boundary；
   - pair candidate ranking；
   - validation slot；
   - refine seed selection；
   - 还是 final best selection。
7. `5a3f7f46ddd474d0` 与 `5a3e7f46ddd474d0` 的差异是否被二跳组合有效利用，还是只生成了等价邻域噪声？
8. `78d540b49c590770` 为什么仍是 exact2 best；是否存在被二跳错误覆盖或被 pair gate 排除的 exact2-adjacent 候选？
9. second-hop iteration limit 是否正常命中；是否需要第三跳，还是当前二跳 pool 本身质量不足？注意：不要直接扩大 iteration limit，先给证据。

### C. Required evidence tables

报告中必须给出以下表格。

#### Table 1: second-hop pool summary

| source/role | count | compare agree count | validated count | best runtime exact | best distance5 | conclusion |
|---|---:|---:|---:|---:|---:|---|

#### Table 2: pair gate diagnosis

| gate/checkpoint | evidence source | pass count | reject/drop count | main reject reason | candidate examples |
|---|---|---:|---:|---|---|

#### Table 3: candidate comparison

| candidate | source stage | role | compare agree | runtime exact | distance5 | selected for refine? | reason |
|---|---|---|---:|---:|---:|---:|---|

至少包含：

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
```

如果 artifacts 中出现更接近但被过滤的候选，也必须加入表格。

---

## 6. Implementation Scope

默认本轮只做审计，不改代码。

只有满足以下条件，才允许最小修改：

1. artifacts 证明 second-hop pool 中存在 compare-agree 且更优的候选，但被错误 gate/drop。
2. 断点能具体定位到 `compare_aware_search.py` 中的一个函数、分支或 metadata 条件。
3. 修复不扩大 beam、budget、topN、timeout、iteration limit。
4. 修复不引入 blind search。
5. 修复不使用 compare-disagree 候选作为主线。
6. 修复不破坏 exact2 best `78d540b49c590770` 的保留。
7. 修复不把 `5a3f7f46ddd474d0` 直接提升为最终答案。

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

如果问题只是 artifact/reporting 不足以解释 pair gate，应优先补充 project_state/reporting 的证据采集，而不是改 strategy。

不允许修改：

```text
reverse_agent/harness.py
reverse_agent/pipeline.py
GUI 相关文件
模型调用/云端 API 路径
全局预算配置
完整 solve_reports 目录
```

允许的最小实现方向，仅在审计证明后使用：

```text
如果 pair_gate_after_projected_winner 错误丢弃了 compare-agree、runtime 更优、且 metadata 合法的 second-hop candidates，
则修正该 gate 的 metadata 条件或 refine selection 条件；
不得通过扩大搜索预算绕过 gate。
```

---

## 7. Tests

审计阶段先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果不改代码，不要重复跑 harness；直接更新 `codex_execution_report.md` 和必要的 project_state。

如果改了 pair gate / refine selection，必须新增或更新测试，语义至少覆盖：

```text
test_second_hop_pair_gate_keeps_compare_agree_improving_candidate
test_second_hop_pair_gate_rejects_compare_disagree_candidate
test_second_hop_pair_gate_does_not_expand_budget
test_second_hop_pair_gate_preserves_exact2_best_candidate
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

如需 harness 验证，使用新 run name，不覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_pair_gate_audit_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_second_hop_pair_gate_audit_20260502
python -m reverse_agent.project_state status
```

最终必须更新：

```text
project_state/codex_execution_report.md
```

报告必须包含：

1. second-hop pool 的来源、role、compare-agree、validation 分布。
2. pair gate after projected winner 的具体断点。
3. 是否有更优候选被错误过滤。
4. 是否改代码；若改，改动函数和分支是什么。
5. 测试结果。
6. 是否运行新 harness；若运行，run name 和关键结果是什么。
7. 下一轮应继续 pair gate、candidate quality，还是转向 transform/model 假设。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. second-hop pool 中没有任何 compare-agree 且 runtime 更优的候选。
   - 不改 strategy。
   - 把瓶颈分类为 candidate quality insufficient。

2. 更优候选只存在于 compare-disagree 集合。
   - 不作为主线。
   - 记录到 negative_results。

3. 发现 compare-agree 更优候选被 pair gate / refine selection 错误丢弃。
   - 只做最小 gate/selection 修复。
   - 补单元测试。

4. 发现 exact2 best `78d540b49c590770` 会被修复影响而丢失或降级。
   - 立即回退该修复。

5. 判断需要第三跳或更大预算。
   - 不直接修改预算。
   - 先在报告中用 artifacts 证明二跳 pool 为什么不足，并提出单独决策请求。

6. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先补 artifact_index 或 reporting 索引能力。

7. Copilot CLI 出现 quota `402`。
   - 不切换模型路径。
   - 不把它当 strategy 失败。
   - 基于 quota 前 artifacts 重建 project_state 并报告证据是否足够。

---

## GPT Decision Summary

当前已经完成从“二跳候选生成”到“二跳实际执行”的验证闭环：`frontier_guided_2_5a3f7f46ddd474d0` 已经生成，说明上一轮控制流问题已解决。

下一步不应继续修 loop，也不应扩大搜索预算。新的瓶颈是：二跳执行后没有产生更优候选。因此 Codex 应审计 `frontier_guided_2` 的 pair/pool 输出、validation 分布和 `pair_gate_after_projected_winner` 的 gate 条件，判断是候选质量不足，还是存在 compare-agree 更优候选被错误过滤。
