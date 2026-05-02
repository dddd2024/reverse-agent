# DECISION_PACKET

## 1. Goal

本轮目标不是继续实现 second-hop composition，而是验证上一轮 Codex 已提交的 metadata-gated second-hop patch 是否真正进入运行链路，并把新的 runtime/artifact 证据重建进 `project_state`。

上一轮 Codex 已经完成以下工作：

```text
validated projected preserve handoff -> validated_projected_preserve_second_hop -> bounded frontier-guided composition
```

本轮要回答一个更窄的问题：

```text
5a3f7f46ddd474d0 这类 validated projected-preserve handoff，是否已经在新 harness 中触发 frontier_guided_2 或等价 second-hop guided run？
```

验收目标：

1. 生成新的 harness run，不覆盖旧 run。
2. 在 artifacts 中确认是否出现 `frontier_guided_2_5a3f7f46ddd474d0` 或等价的 second-hop guided artifact。
3. 确认该 second-hop anchor 的 `frontier_role` 为 `validated_projected_preserve_second_hop`，或 artifacts 中存在等价 metadata。
4. 若仍卡住，基于 second-hop artifacts 重新分类瓶颈，而不是回到 validation-slot 或 blind search。
5. 更新 `project_state/codex_execution_report.md`，必要时重建 `task_packet/current_state/artifact_index/negative_results`。

---

## 2. Current Evidence

事实来源必须以仓库当前 `project_state` 为准，不要用记忆替代文件。

当前 `task_packet.json` / `current_state.json` 显示：

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

当前已知候选：

| role | candidate_prefix | compare agree | runtime exact | distance5 | source |
|---|---|---:|---:|---:|---|
| exact2 | `78d540b49c590770` | true | 2 | 246 | pairscan |
| exact1/frontier | `5a3e7f46ddd474d0` | true | 1 | 258 | exact2_seed -> refine -> guided(frontier) |
| projected preserve handoff | `5a3f7f46ddd474d0` | true | 0 | 740 | projected preserve validation |

上一轮 `codex_execution_report.md` 的关键结论：

1. 已确认真实 composition gap：`5a3f7f46ddd474d0` 已经被 runtime validation，且 `compare_semantics_agree=true`，但因为没有 runtime distance gain，原逻辑不会把它作为下一轮 anchor。
2. 已做最小 metadata-gated patch：只有已验证、compare-agree、且带有 projected preserve / projected winner 相关语义的 handoff，才允许作为 `validated_projected_preserve_second_hop` 进入下一轮 bounded frontier-guided composition。
3. 没有调整 beam、budget、topN、timeout、blind search、pipeline、harness、GUI 或模型路径。
4. 测试结果：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff"  # 3 passed, 51 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                   # 54 passed
python -m pytest -q                                                                              # 136 passed
```

5. 上一轮没有运行 harness。下一轮的核心证据应来自新的 harness run 或 quota 失败前生成的 compare-aware artifacts。

---

## 3. Do Not Do

严格禁止以下方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、topN、timeout 或扩大搜索空间。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。
6. 不要重复实现上一轮已经完成的 second-hop composition patch。
7. 不要继续争论 validation ordering；证据已经显示 `5a3f7f46ddd474d0` 进入过 validation。
8. 不要修改 GUI、harness 总控、pipeline 总控或模型调用路径，除非新 artifacts 明确证明问题不在 strategy，而在 reporting/project_state。
9. 不要因为 Copilot CLI quota `402` 就判定 strategy 失败；只要 artifacts 生成，就先基于 artifacts 分类。
10. 不要把 `5a3f7f46ddd474d0` 当作最终答案或无条件提升为 best candidate。

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

代码确认范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

需要确认上一轮 patch 是否仍在当前工作区中，重点查找：

```text
PROJECTED_PRESERVE_SECOND_HOP_ROLE
_validated_projected_preserve_second_hop_candidates
_frontier_continuation_candidates
second_hop_frontier_candidates
frontier_continuation_candidates
used_second_hop_frontier_candidates
validated_projected_preserve_second_hop
```

运行后只读新 run 目录下的必要 artifacts。优先读：

```text
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\summary.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\run_manifest.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\case_results\samplereverse-exact1-projected-vs-neighbor.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\reports\tool_artifacts\samplereverse\**\samplereverse_compare_aware_guided_pool_result.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\reports\tool_artifacts\samplereverse\**\guided_pool_validation\*.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\reports\tool_artifacts\samplereverse\**\samplereverse_compare_aware_result.json
solve_reports\harness_runs\samplereverse_second_hop_composition_verify_YYYYMMDD\reports\tool_artifacts\samplereverse\**\samplereverse_compare_aware_frontier_summary.json
```

不要读取完整 `solve_reports`。如果 artifact index 不足，先重建 project_state，再按 index 精读。

---

## 5. Required Audit

Codex 本轮必须先完成以下审计，再决定是否需要额外修补。

### A. Pre-run audit

1. 确认工作区是否干净。
2. 确认上一轮 patch 存在于当前代码中。
3. 确认 tests 中存在以下语义覆盖：

```text
test_validated_projected_preserve_handoff_can_seed_second_hop_composition
test_second_hop_composition_does_not_admit_compare_disagree_candidate
test_second_hop_composition_does_not_expand_budget
```

4. 运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果这些测试失败，先停止并报告。不要直接跑 harness。

### B. Harness / artifact validation

如果测试通过，运行新的 harness，使用新 run name：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_composition_verify_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

如果 Copilot quota 仍报 `402 You have no quota`，不要扩大 timeout 或换 blind search。改为：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_second_hop_composition_verify_20260502
python -m reverse_agent.project_state status
```

然后基于已生成 artifacts 做分类。

### C. Artifact questions

必须回答：

1. 是否出现 `frontier_guided_2_5a3f7f46ddd474d0` 或等价 second-hop guided artifact？
2. `second_hop_frontier_candidates` 是否包含 `5a3f7f46ddd474d0`？
3. `frontier_continuation_candidates` 是否包含它？
4. `used_second_hop_frontier_candidates` 是否记录它被实际使用？
5. 它是否保持 `compare_semantics_agree=true`？
6. 它是否被 metadata gate 限定为 `validated_projected_preserve_second_hop`，而不是普通 blind candidate？
7. second-hop 之后的最佳候选是否改进：
   - runtime exact wchars 是否提升；
   - distance5 是否下降；
   - compare semantics 是否仍 agree。
8. exact2 best `78d540b49c590770` 是否仍被保留，没有被错误降级或丢失。

### D. Required table

报告中必须给出表格：

| candidate | source stage | frontier role | compare agree | runtime exact | distance5 | second-hop eligible | actually used in second-hop | result |
|---|---|---|---:|---:|---:|---:|---:|---|

至少包含：

```text
78d540b49c590770
5a3e7f46ddd474d0
5a3f7f46ddd474d0
```

如果新 run 产生更优候选，也必须加入表格。

---

## 6. Implementation Scope

默认本轮不实现新策略，只做验证与 project_state 更新。

允许修改：

```text
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/model_gate.json
PROJECT_PROGRESS_LOG.txt
```

只有在以下情况才允许改 strategy：

1. 单元测试显示上一轮 second-hop patch 已丢失或没有被合并。
2. 新 artifacts 证明 metadata-gated candidate 已生成，但由于一个明确的小逻辑分支没有被实际传入 guided pool。
3. 断点能限定在 `reverse_agent/strategies/compare_aware_search.py` 内。
4. 修复不扩大预算、不改 blind search、不改 model path、不使用 compare-disagree candidate。

允许的代码修改范围仅限：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

若问题只是 artifact/reporting 没有记录 second-hop 使用情况，不要改 strategy；优先修 project_state/reporting 的证据采集，或在 `codex_execution_report.md` 中明确指出证据缺口。

---

## 7. Tests

最低测试序列：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果本轮没有改代码，但跑了 harness，则需要：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_second_hop_composition_verify_20260502
python -m reverse_agent.project_state status
```

如果本轮改了代码，必须跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

如运行 harness：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_composition_verify_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

报告中必须列出每条命令的结果。失败时给出错误摘要，不要扩大任务范围。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. second-hop unit tests 失败。
   - 不跑 harness。
   - 报告失败测试、失败原因和是否与上一轮 patch 有关。

2. 新 harness 生成了 `frontier_guided_2_5a3f7f46ddd474d0` 或等价 artifact。
   - 停止改 strategy。
   - 基于 second-hop 结果更新 project_state。
   - 如果 second-hop 后仍无收益，下一轮应分析 second-hop candidate quality，而不是 composition plumbing。

3. 新 harness 没生成 second-hop artifact，但 artifacts 显示 `second_hop_frontier_candidates` 和 `frontier_continuation_candidates` 已正确存在。
   - 停止改 strategy。
   - 审计 reporting / artifact emission / loop stop condition。

4. 新 harness 没生成 second-hop artifact，且 artifacts 显示 `5a3f7f46ddd474d0` 没进入 continuation candidates。
   - 只允许做最小 branch-level 修复。
   - 不扩大 beam/budget。

5. Copilot CLI 报 `402 You have no quota`。
   - 不改变模型路径。
   - 不把它当 strategy 失败。
   - 基于 quota error 前已生成 artifacts 重建 project_state 并报告证据是否足够。

6. 任何候选需要依赖 `compare_semantics_agree=false` 才能继续。
   - 不作为主线。
   - 记录到 negative_results。

7. 为继续判断必须读取完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt`。
   - 停止。
   - 先补 artifact_index 或让 project_state build 产出更精确索引。

---

## GPT Decision Summary

当前应接受上一轮 Codex 的 second-hop composition patch 为“测试通过但尚未 runtime 验证”的状态。下一步不是继续写策略，而是运行 `samplereverse_second_hop_composition_verify_20260502`，确认 `validated_projected_preserve_second_hop` 是否真的触发二跳 guided composition。

如果新 artifacts 证明二跳已经触发但仍无收益，下一轮转向候选质量和 second-hop 生成逻辑分析；如果二跳没有触发，则只修具体 continuation/reporting 断点，仍禁止回退到 blind search 或扩大搜索预算。
