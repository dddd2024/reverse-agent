# DECISION_PACKET

## 1. Goal

验证上一轮新增的 **validation-slot patch** 是否真正生效。

核心目标不是继续扩大搜索，而是回答一个明确问题：

> `projected preserve handoff` 候选 `5a3f7f46ddd474d0` 是否已经从 `pair_frontier_pool` 进入 `guided_pool_validation`？

如果进入 validation 但仍无收益，则把当前失败分类从 **B. selected_but_not_composed** 推进为 **D. validated_but_no_runtime_gain**。如果仍未进入 validation，则说明上一轮 validation ordering 修正没有真正打通断点，需要做更窄的修正。

---

## 2. Current Evidence

当前任务来自 `samplereverse`，主策略是 `CompareAwareSearchStrategy`。状态文件显示当前瓶颈仍在 `frontier_refine`，原因是 `projected_winner_reached_pair_gate`。当前主线为 `L15(prefix8)`，已知变换链为：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前 best candidate 仍然没有实质提升：

- `exact2 seed`: `78d540b49c590770`，`runtime_ci_exact_wchars=2`，`distance5=246`
- `exact1 frontier`: `5a3e7f46ddd474d0`，`runtime_ci_exact_wchars=1`，`distance5=258`
- `projected preserve handoff`: `5a3f7f46ddd474d0`，上一轮报告中仍是 selected but not validated，`distance5=740`

上一轮 Codex 已经做了最小代码修正：新增 `_frontier_guided_validation_candidates()`，目标是在固定 `GUIDED_POOL_VALIDATE_TOP` 数量内，为 `projected_winner_promoted_to_near_local` handoff 保留一个 validation slot，不增加预算。测试已通过，但还没有跑第二次 harness 来验证运行时是否真的生效。

---

## 3. Do Not Do

不要做以下事情：

1. 不要回退到旧的 `sample_solver` blind search。
2. 不要只通过增大 beam、budget、topN 来碰运气。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破方向。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`。只读取 `artifact_index.json` 指向的必要 artifacts。

---

## 4. Files To Inspect

优先检查这些文件：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md

reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

必要 artifacts 只从 `artifact_index.json` 指向的路径读取，尤其是：

```text
guided_pool_validation
guided_pool_result
compare_aware_result
frontier_summary
summary.json
```

不要读取完整 `PROJECT_PROGRESS_LOG.txt`，除非上述状态文件自相矛盾或缺失。当前 `artifact_index.json` 没有标记 missing artifact，说明下一轮不需要战略复盘。

---

## 5. Required Audit

Codex 下一轮先做审计，不要立刻改代码。

必须确认：

1. 当前工作区是否干净。
2. `_frontier_guided_validation_candidates()` 是否存在。
3. `run_compare_aware_guided_pool()` 是否已经使用该 helper 产生 `validation_candidates`。
4. 对应测试 `test_frontier_guided_validation_candidates_preserve_projected_handoff_slot()` 是否存在。
5. 当前 `project_state` 是否仍指向 `samplereverse_handoff_verify_20260429`。
6. 新一轮 harness 之后，检查：

```text
frontier_guided_1_5a3e7f46ddd474d0/guided_pool_validation/
```

确认候选：

```text
5a3f7f46ddd474d0
```

是否出现在 validation 结果中。

---

## 6. Implementation Scope

本轮默认只允许做 **验证**，不主动实现新搜索策略。

只有在 harness 结果明确指出 validation-slot patch 没有生效时，才允许做最小修正。允许的修正范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

允许修正的问题类型：

1. helper 逻辑没有覆盖真实 metadata。
2. validation ordering 中 handoff slot 被后续排序覆盖。
3. `projected_winner_promoted_to_near_local` 的标记在真实 artifact 中名称不同。
4. selected candidate 进入了 `pair_frontier_pool`，但没有被正确送入 validation candidate list。

不允许的实现范围：

```text
pipeline.py
harness.py
GUI
model path
全局预算参数
旧 blind search
大规模 solve_reports 扫描
```

如果新 harness 显示 `5a3f7f46ddd474d0` 已经进入 validation，但 best 仍无提升，则不要继续乱改 validation ordering。应将失败分类为：

```text
D. validated_but_no_runtime_gain
```

然后基于 artifacts 判断下一跳是否是：

```text
second-hop composition
```

但这一步只做判断，不要直接扩大搜索。

---

## 7. Tests

先跑单元测试：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

再跑全量测试：

```powershell
python -m pytest -q
```

然后跑一轮最小 harness：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_validation_slot_verify_20260430 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

harness 后重建 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_validation_slot_verify_20260430
python -m reverse_agent.project_state status
```

然后输出新的：

```text
project_state/codex_execution_report.md
```

报告必须包含：

1. 新 run name。
2. `selected_flag`。
3. `frontier_stall_stage`。
4. `frontier_stall_reason`。
5. `5a3f7f46ddd474d0` 是否进入 validation。
6. 如果进入 validation，validation 后 exact / distance 是否变化。
7. 如果没进入 validation，指出断点发生在 helper、ordering、metadata 还是 artifact path。
8. 是否需要进入 second-hop composition。

---

## 8. Stop Conditions

立即停止并报告的情况：

1. `5a3f7f46ddd474d0` 已进入 validation，但没有任何 runtime gain。
   - 结论：分类为 `D. validated_but_no_runtime_gain`。
   - 不要继续扩大预算。

2. `5a3f7f46ddd474d0` 仍没有进入 validation。
   - 结论：上一轮 validation-slot patch 没有真正生效。
   - 只允许做最小修正，并补测试。

3. 新候选出现 `compare_semantics_agree=false`。
   - 不作为主线。
   - 只能记录为旁路证据。

4. harness 结果仍是：

```text
frontier_refine / projected_winner_reached_pair_gate
```

但 artifacts 无法解释 handoff 是否 validation。
   - 不要继续猜。
   - 重新 build `project_state`，并补充 artifact index。

---

## GPT Decision Summary

下一步最关键不是继续找 flag，而是先确认 **上一轮补的 validation slot 是否真的让 handoff 候选进入验证链路**。

当前最可能的两种分支是：

```text
A. 没进入 validation
=> 说明 patch 没打到真实运行路径，需要修 validation candidate selection。

B. 进入 validation 但没有收益
=> 说明当前 projected preserve handoff 不是直接突破点，应转向 second-hop composition 或重新审计 compare-aware 的后续组合逻辑。
```

因此，下一轮 Codex 的任务应该是 **一次最小 harness 验证 + 断点分类**，而不是继续加搜索预算。
