# DECISION_PACKET

Generated: 2026-05-03

## 1. Goal

本轮目标是实现并验证一个 **real bounded exact2 basin SMT pass**，只围绕上一轮 diagnostics 已经给出的 exact2 basin 信号做最小实现。

当前任务：

```text
implement_real_bounded_exact2_smt_pass_without_budget_expansion
```

核心目标：

```text
从 exact2 base candidate 78d540b49c590770 出发，
使用 exact2_basin_smt 中已经给出的 bounded variable byte/nibble positions 和 value pools，
尝试生成并验证是否存在 exact3+ runtime candidate。
```

本轮不是 blind search，不是扩大 beam/budget/topN/timeout，也不是替换 compare-aware 主流程。它是一个受 diagnostics 约束的、小范围、可审计的 exact2 basin SMT 执行分支。

---

## 2. Current Evidence

事实来源：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
```

当前主线：

```text
active_strategy = CompareAwareSearchStrategy
sample = samplereverse
current_mainline = L15(prefix8)
known_transform = input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
current_bottleneck.stage = smt_exact2_basin_diagnostic
current_bottleneck.reason = exact2_basin_smt_diagnostic_has_bounded_positions
next_local_action = implement_real_bounded_exact2_smt_pass_without_budget_expansion
```

当前 best candidates：

| role | candidate_prefix | compare agree | runtime exact | distance5 | source |
|---|---|---:|---:|---:|---|
| exact2 best | `78d540b49c590770` | true | 2 | 246 | pairscan |
| exact1/frontier | `5a3e7f46ddd474d0` | true | 1 | 258 | exact2_seed -> refine -> guided |
| frontier | `5a3e7f46ddd474d0` | true | 1 | 258 | exact2_seed -> refine -> guided |

上一轮 Codex 结论：

```text
classification = diagnostics_show_promising_exact2_basin_smt
code_fix_recommended = false
metadata_fix_applied = exact2_basin_smt_payload_written_to_smt_result
runtime_best_improved = false
```

exact2 basin diagnostic payload：

```text
base_anchor = 78d540b49c590770
primary_base_anchor = 5a3e7f46ddd474d0
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
variable_byte_positions = [1, 2, 3, 0, 4]
variable_nibble_positions = [2, 3, 0, 1, 4]
value_pools = {1:[213,62,60], 2:[64,127,128], 3:[180,143], 0:[120], 4:[156]}
attempted = false
recommended = true
```

解释：

```text
78d540b49c590770 是稳定 compare-agree exact2 reference。
现有 primary SMT 仍偏向 exact1 frontier 5a3e7f46ddd474d0。
diagnostics 已经给出 exact2 basin 的 bounded positions / value pools。
因此下一步不是继续诊断，而是执行一个真实 bounded exact2 SMT pass。
```

---

## 3. Do Not Do

严格禁止：

```text
1. do not return to old sample_solver blind search
2. do not only increase beam or budget
3. do not expand beam, budget, topN, timeout, or frontier iteration limit
4. do not use compare_semantics_agree=false candidates as primary frontier
5. do not commit full solve_reports directory
6. do not scan entire solve_reports unless explicitly needed
7. do not promote 5a3f7f46ddd474d0, 5a3f7fc2ddd474d0, or 343f7f46ddd474d0
8. do not treat model-selected bare flag{ as runtime improvement
9. do not rewrite harness/pipeline/model API/GUI paths
10. do not replace final selection unless the new exact2 SMT branch produces runtime-validated exact3+
```

特别注意：

```text
selected_flag = flag{ 不是 runtime improvement。
compare-aware runtime best 仍然是 78d540b49c59077041414141414141。
```

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

主要代码范围：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

必要时再读：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
```

重点查找：

```text
run_compare_aware_smt
exact2_basin_smt
prefix_boundary
prefix_boundary_diagnostics
candidate generation
runtime validation
final selection
compare_semantics_agree guard
strategy metadata payload
smt_result payload emission
```

不要默认读取：

```text
full solve_reports
full PROJECT_PROGRESS_LOG.txt
```

---

## 5. Required Audit

Codex 在实现前必须先回答：

### A. exact2 basin SMT 输入是否可执行

确认：

```text
1. exact2_basin_smt payload 存在。
2. base candidate 是 78d540b49c590770。
3. base candidate compare_semantics_agree = true。
4. variable_byte_positions = [1, 2, 3, 0, 4]。
5. variable_nibble_positions = [2, 3, 0, 1, 4]。
6. value_pools 是小集合，不需要扩大搜索预算。
7. diagnostics attempted=false，说明上一轮没有真正执行该 pass。
```

### B. SMT pass 应该是 bounded branch，不是新搜索器

必须保证：

```text
1. 只在 exact2_basin_smt.recommended = true 时启用。
2. 只使用 diagnostics 已给出的 variable positions / value pools。
3. 不扩大全局 beam/budget/topN/timeout。
4. 不把 compare_semantics_agree=false candidate 纳入主线。
5. 生成候选后必须走现有 runtime validation。
6. 只有 runtime exact_wchars > 2 或 distance5 明确优于 246，才允许报告 improvement。
```

### C. final selection guard

必须审计：

```text
1. bounded exact2 SMT 生成的 candidate 是否独立标记 source。
2. 未验证候选不能替换 final best。
3. exact0/exact1 candidate 不能替换 exact2 best。
4. model-selected bare flag{ 不得覆盖 compare-aware runtime best。
5. 如果没有 exact3+，final best 仍应保持 78d540b49c59077041414141414141。
```

### D. Required evidence tables

Codex 报告必须包含以下表格。

#### Table 1: implementation wiring audit

| component | expected behavior | present? | evidence |
|---|---|---:|---|
| exact2_basin_smt reader | reads base/positions/pools from SMT diagnostic payload | yes/no | file/function |
| bounded candidate generator | only mutates diagnostic positions | yes/no | file/function |
| compare-agree guard | rejects compare_semantics_agree=false as primary | yes/no | file/function |
| runtime validator | validates generated candidates before promotion | yes/no | file/function |
| final selection guard | no unvalidated or worse candidate can replace best | yes/no | file/function |
| artifact emission | result is persisted into SMT/search metadata | yes/no | file/function |

#### Table 2: bounded exact2 SMT result

| field | value |
|---|---|
| base candidate | |
| variable byte positions | |
| variable nibble positions | |
| value pools | |
| generated candidate count | |
| validated candidate count | |
| best runtime exact | |
| best distance5 | |
| improved over exact2? | |
| final best changed? | |

#### Table 3: classification

| classification | evidence for | evidence against | next action | recommendation |
|---|---|---|---|---|
| exact2_basin_smt_produced_exact3_plus | | | promote only if runtime-validated | yes/no |
| exact2_basin_smt_no_runtime_gain | | | preserve current exact2 best | yes/no |
| exact2_basin_payload_insufficient | | | improve diagnostics metadata only | yes/no |
| implementation_bug_in_smt_branch | | | minimal code fix + tests | yes/no |
| candidate_quality_insufficient_after_bounded_smt | | | stop, report negative result | yes/no |

---

## 6. Implementation Scope

允许修改：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/model_gate.json
```

允许实现：

```text
1. Add a bounded exact2 basin SMT execution branch.
2. Consume exact2_basin_smt diagnostic payload.
3. Generate candidates only from bounded byte/nibble positions and value pools.
4. Runtime-validate generated candidates.
5. Persist exact2_basin_smt execution results into SMT/search artifacts.
6. Add tests proving no budget expansion and no selection regression.
```

不允许实现：

```text
1. new blind search
2. new third-hop search
3. budget expansion
4. global ranking rewrite
5. harness rewrite
6. model/API path rewrite
7. GUI changes
8. full solve_reports commit
```

如果发现现有代码已经有 bounded SMT branch：

```text
不要重复实现。
只审计为什么 diagnostics attempted=false，
然后修复触发条件、payload handoff 或 artifact emission。
```

---

## 7. Tests

先跑 targeted tests：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果修改了 candidate generation、selection guard 或 artifact emission：

```powershell
python -m pytest -q
```

如果实现完成并需要 harness 验证，运行一个新 run name，不覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_exact2_basin_smt_20260503 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

运行后更新 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_exact2_basin_smt_20260503
python -m reverse_agent.project_state status
```

最终 `CODEX_EXECUTION_REPORT.md` 必须写明：

```text
1. 是否实现 real bounded exact2 SMT pass。
2. 是否使用 78d540b49c590770 作为 base。
3. 是否只使用 bounded positions / value pools。
4. 是否扩大任何预算。
5. 生成了多少候选。
6. runtime validation 结果。
7. 是否出现 exact3+。
8. final best 是否改变。
9. 若没有提升，是否把该方向写入 negative_results。
```

---

## 8. Stop Conditions

立即停止并报告：

```text
1. exact2_basin_smt payload 缺失。
2. base candidate 不是 78d540b49c590770。
3. payload 没有 bounded variable positions 或 value pools。
4. 实现需要扩大 beam/budget/topN/timeout 才能继续。
5. 唯一可行方向依赖 compare_semantics_agree=false candidate。
6. 生成候选无法 runtime validate。
7. final selection 会被未验证候选、exact0/exact1 候选或 bare flag{ 覆盖。
8. 需要读取完整 solve_reports 或完整 PROJECT_PROGRESS_LOG.txt 才能继续。
```

成功停止条件：

```text
1. real bounded exact2 SMT pass 产生 runtime-validated exact3+ candidate。
2. 或证明 bounded exact2 SMT pass 无 runtime gain，并保留 current exact2 best。
3. 或发现 bounded SMT branch/payload handoff 的具体实现 bug，并用最小补丁修复。
```

---

## GPT Decision Summary

当前 evidence 已足够支持下一步：

```text
implement_real_bounded_exact2_smt_pass_without_budget_expansion
```

原因：

```text
1. exact2_basin_smt 已经存在。
2. base 是稳定 compare-agree exact2 candidate：78d540b49c590770。
3. variable byte/nibble positions 已给出。
4. value pools 很小，属于 bounded execution，不需要扩大预算。
5. 上一轮 attempted=false，所以真实 SMT pass 尚未执行。
```

Codex 本轮只做这一件事：

```text
把 exact2_basin_smt 从 diagnostic metadata 变成一个真实、受限、runtime-validated 的 execution branch。
```

不要回到 blind search，不要扩大预算，不要替换 selection guard。
