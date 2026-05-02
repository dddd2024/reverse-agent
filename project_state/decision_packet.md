# DECISION_PACKET

## 1. Goal

本轮目标是审计并修复 **frontier loop stop condition / artifact emission 断点**：

```text
second-hop continuation candidate 已经被记录为可用，但没有实际生成 frontier_guided_2_5a3f7f46ddd474d0 或等价二跳 guided artifact。
```

上一轮已经完成了 second-hop runtime 验证，因此不要再重复验证“候选有没有进入 validation”或“候选有没有被放入 continuation candidates”。当前要回答的问题已经收窄为：

```text
为什么 5a3f7f46ddd474d0 已出现在 second_hop_frontier_candidates / frontier_continuation_candidates，并且 used_second_hop_frontier_candidates=true，但 harness 目录中没有 frontier_guided_2_5a3f7f46ddd474d0？
```

本轮优先审计，不默认改代码。只有定位到明确的小断点时，才允许做最小修复。

---

## 2. Current Evidence

事实来源是当前仓库 `project_state`，不要用记忆替代文件。

### 已经做了什么

1. 上一轮 Codex 已经按 `project_state/decision_packet.md` 执行 second-hop runtime 验证。
2. 单元测试通过：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff"  # 3 passed, 51 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                   # 54 passed
```

3. 新 harness 已运行：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_composition_verify_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

4. harness 结果：

```text
run = solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502
executed_cases = 1
completed_without_expected = 1
error_cases = 0
candidate_quality = 1.0
evidence_coverage = 1.0
Copilot quota 402 = not observed
selected candidate = 78d540b49c59077041414141414141
```

5. `project_state` 已重建，`artifact_index.json` 指向新 run，且 `missing=[]`。

### 关键 runtime/artifact 结论

| question | answer |
|---|---|
| 是否出现 `frontier_guided_2_5a3f7f46ddd474d0` 或等价 artifact | 否；新 run 只有 `frontier_guided_1_5a3e7f46ddd474d0` |
| `second_hop_frontier_candidates` 是否包含 `5a3f7f46ddd474d0` | 是 |
| `frontier_continuation_candidates` 是否包含 `5a3f7f46ddd474d0` | 是 |
| `used_second_hop_frontier_candidates` 是否记录实际使用 | 是，值为 `true` |
| role 是否为 `validated_projected_preserve_second_hop` | 是 |
| `5a3f7f46ddd474d0` 是否 compare agree | 是，`compare_semantics_agree=true` |
| second-hop 后是否有 runtime 改进 | 否；没有二跳 guided run，所以没有二跳后新收益 |
| exact2 best 是否保留 | 是，`78d540b49c590770` 仍为 selected / best overall |

### 当前候选表

| candidate | source stage | frontier role | compare agree | runtime exact | distance5 | second-hop eligible | actually used in second-hop | result |
|---|---|---|---:|---:|---:|---:|---:|---|
| `78d540b49c590770` | pairscan / guided pool / final validation | `exact2_seed` / `best_overall` | true | 2 | 246 | no | no | retained as best overall / selected candidate |
| `5a3e7f46ddd474d0` | frontier guided iteration 1 | `exact1_frontier` | true | 1 | 258 | no | no | remains best exact1 frontier |
| `5a3f7f46ddd474d0` | projected preserve handoff -> second-hop continuation candidate | `validated_projected_preserve_second_hop` | true | 0 | 740 | yes | yes, as continuation candidate only | continuation recorded, but no `frontier_guided_2` artifact emitted |

### Current bottleneck

```text
stage = frontier_loop_stop_condition
reason = second_hop_continuation_candidate_recorded_but_no_frontier_guided_2_artifact
confidence = high
```

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。
6. 不要重复实现上一轮已经完成且测试通过的 second-hop candidate 生成逻辑。
7. 不要继续围绕 validation ordering 做重复修复。
8. 不要重新跑同一个 harness 来碰运气；先审计 loop stop condition。
9. 不要把 `used_second_hop_frontier_candidates=true` 直接解释为已经生成二跳 guided run；它目前只证明 continuation candidate 被记录或纳入逻辑路径。
10. 不要把 `5a3f7f46ddd474d0` 当作最终答案或无条件提升为 best。
11. 不要修改 GUI、模型调用路径、pipeline 总控或 harness 总控，除非审计证明问题不在 strategy loop，而在 artifact emission / reporting。

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

在 `compare_aware_search.py` 中重点查找：

```text
FRONTIER_MAX_ITERATIONS
_frontier_continuation_candidates
_validated_projected_preserve_second_hop_candidates
frontier loop
frontier iteration counter
frontier_guided_* artifact path construction
used_second_hop_frontier_candidates
second_hop_frontier_candidates
frontier_continuation_candidates
distance_not_improved
break / continue / return branches around frontier_refine
```

只读以下新 run 中的必要 artifacts，不读取完整 `solve_reports`：

```text
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\summary.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\run_manifest.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\case_results\samplereverse-exact1-projected-vs-neighbor.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_1_5a3e7f46ddd474d0\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\reports\tool_artifacts\samplereverse\frontier_guided_1_5a3e7f46ddd474d0\guided_pool_validation\samplereverse_compare_aware_guided_pool_validation.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\reports\tool_artifacts\samplereverse\frontier_refine_1\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_20260502\reports\tool_artifacts\samplereverse\samplereverse_compare_aware_frontier_summary.json
```

只有当这些文件不能解释断点时，才允许搜索同名 helper 或 artifact emission 相关调用点。

---

## 5. Required Audit

Codex 本轮必须先输出审计结果，再决定是否修改代码。

### A. Pre-audit checks

1. 确认工作区初始是否干净。
2. 确认 `project_state` 最新 run 是：

```text
samplereverse_second_hop_composition_verify_20260502
```

3. 确认当前 `current_bottleneck` 是：

```text
stage = frontier_loop_stop_condition
reason = second_hop_continuation_candidate_recorded_but_no_frontier_guided_2_artifact
```

4. 确认上一轮没有出现 Copilot quota `402`。

### B. Loop stop condition audit

必须回答：

1. `FRONTIER_MAX_ITERATIONS` 当前允许几轮 frontier guided？
2. 进入 `frontier_guided_1` 后，循环是否在生成 continuation candidate 前或后停止？
3. `frontier_continuation_candidates` 被计算出来后，是否真的被赋值为下一轮 frontier seed？
4. 是否存在如下伪逻辑问题：

```text
if distance_not_improved:
    record continuation candidate
    mark used_second_hop_frontier_candidates = true
    break / return before launching next guided iteration
```

5. `used_second_hop_frontier_candidates=true` 的语义到底是：
   - candidate 被实际送入下一轮 guided pool；还是
   - candidate 只被记录为 continuation path 可用。
6. artifact path 是否只支持 `frontier_guided_1_*`，导致二跳运行了但路径或索引没有记录为 `frontier_guided_2_*`？
7. project_state builder 是否只索引第一轮 guided artifact，漏掉第二轮 guided artifact？
8. 是否存在 “loop stop condition 正确，artifact emission 错误” 和 “artifact emission 正确，loop 没执行” 两种可能；必须用代码和 artifacts 区分。

### C. Required evidence table

报告中必须给出：

| check | evidence source | observed value | conclusion |
|---|---|---|---|
| max frontier iterations | code |  |  |
| continuation candidate generated | artifact/code |  |  |
| continuation candidate selected as next seed | code/artifact |  |  |
| second guided run launched | artifact/code |  |  |
| second guided artifact emitted | artifact index/run dir |  |  |
| project_state indexed second guided artifact | artifact_index/current_state |  |  |

### D. Candidate safety table

继续保留候选安全表：

| candidate | role | compare agree | runtime exact | distance5 | may drive next step? | reason |
|---|---|---:|---:|---:|---:|---|
| `78d540b49c590770` | exact2 / selected | true | 2 | 246 | yes, baseline only | must not be lost |
| `5a3e7f46ddd474d0` | exact1 frontier | true | 1 | 258 | yes, first-hop frontier | current frontier anchor |
| `5a3f7f46ddd474d0` | validated projected preserve second-hop | true | 0 | 740 | yes, continuation audit only | allowed only as metadata-gated second-hop anchor |

---

## 6. Implementation Scope

默认本轮只做审计，不改代码。

只有满足以下条件，才允许最小修改：

1. 代码证明 continuation candidate 已生成，但 loop 在启动第二轮 guided 前错误停止。
2. 修复能限制在 `reverse_agent/strategies/compare_aware_search.py` 或 artifact emission / project_state indexing 的小范围内。
3. 不增加 beam、budget、topN、timeout。
4. 不改变旧 blind search。
5. 不使用 `compare_semantics_agree=false` 候选。
6. 不把 `5a3f7f46ddd474d0` 提升为最终答案。
7. 不破坏 `78d540b49c590770` 作为 exact2 / selected baseline 的保留。

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

若问题是 project_state builder 或 artifact indexing 漏索引，允许修改对应 project_state/reporting 代码，但必须先在审计中说明为什么不是 strategy loop 问题。

不允许修改：

```text
reverse_agent/harness.py
reverse_agent/pipeline.py
GUI 相关文件
模型调用/云端 API 路径
全局预算配置
完整 solve_reports 目录
```

可能的最小修复方向，仅在审计证明后使用：

```text
当 distance_not_improved 但存在 metadata-gated frontier_continuation_candidates，且 frontier iteration budget 尚未耗尽时，不应立即 break/return；应启动下一轮 bounded frontier guided run，并把 artifact 显式落到 frontier_guided_2_<candidate_prefix> 或等价可索引路径。
```

---

## 7. Tests

审计前先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果改了 strategy loop 或 artifact emission，必须新增或更新测试，语义至少覆盖：

```text
test_frontier_continuation_candidate_triggers_second_guided_iteration
test_frontier_continuation_does_not_expand_budget
test_frontier_continuation_preserves_exact2_best_candidate
test_frontier_continuation_does_not_use_compare_disagree_candidate
```

代码修改后必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

是否运行 harness 由审计结果决定：

- 如果只发现 reporting/indexing 问题，先运行 project_state build/status，不必重跑 harness。
- 如果修了 loop stop condition，建议运行新的 run name，不覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_frontier_loop_continuation_verify_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_frontier_loop_continuation_verify_20260502
python -m reverse_agent.project_state status
```

最终必须更新：

```text
project_state/codex_execution_report.md
```

报告必须包含：

1. 断点是 loop stop condition、artifact emission，还是 project_state indexing。
2. 是否改代码；如果改了，具体改了哪些函数/分支。
3. 是否新增测试，测试结果是什么。
4. 是否运行新 harness，run name 和关键结果是什么。
5. 是否生成 `frontier_guided_2_5a3f7f46ddd474d0` 或等价二跳 artifact。
6. 如果仍没有二跳 artifact，下一轮应该查哪一个更窄的断点。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 审计发现 `used_second_hop_frontier_candidates=true` 只是 reporting 误命名，并不代表候选实际进入下一轮 guided run。
   - 不要扩大策略。
   - 修正 reporting 或更新报告语义。

2. 审计发现 continuation candidate 已正确作为下一轮 seed，但 artifact 没有被写出或没被 artifact_index 收录。
   - 不改 strategy。
   - 只修 artifact emission / project_state indexing。

3. 审计发现 loop 在有 continuation candidate 且 iteration budget 未耗尽时提前 break/return。
   - 只做最小 loop branch 修复。
   - 补单元测试。

4. 修复导致 exact2 best `78d540b49c590770` 被丢失或降级。
   - 立即回退该修复。

5. 唯一可继续方向依赖 `compare_semantics_agree=false`。
   - 不作为主线。
   - 记录到 negative_results。

6. 需要读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt` 才能继续。
   - 停止。
   - 先补 artifact_index 或 project_state builder 的索引能力。

7. Copilot CLI 重新出现 quota `402`。
   - 不切换模型路径。
   - 不把它当 strategy 失败。
   - 基于 quota 前 artifacts 重建 project_state 并报告证据是否足够。

---

## GPT Decision Summary

现在已经不是“second-hop patch 是否存在”的阶段。最新证据显示：

```text
5a3f7f46ddd474d0 已被记录为 second-hop continuation candidate，且 used_second_hop_frontier_candidates=true；但实际 run 目录没有 frontier_guided_2 artifact。
```

所以下一步计划必须查 frontier loop stop condition / artifact emission，而不是继续加搜索预算、重复实现 second-hop candidate 生成、或回退 blind search。

本计划的理由是：如果候选已经生成但没有进入实际第二轮 guided run，问题更可能在“循环控制、break/return 条件、artifact 路径写出、project_state 索引”之间；继续扩大搜索空间不会解决这个控制流断点，只会增加噪声和 token 成本。
