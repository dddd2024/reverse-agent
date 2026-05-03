# DECISION_PACKET

Generated: 2026-05-03

## 1. Goal

本轮目标是审计 `bounded exact2 basin SMT pass` 为什么在极小 value pools 下仍然返回 `unknown`，并判断是否应改成 deterministic bounded value-pool execution/evaluation path。

当前任务：

```text
audit_smt_unknown_with_tiny_value_pools_without_timeout_expansion
```

核心问题：

```text
real bounded exact2 SMT pass 已经执行；
exact2 basin base = 78d540b49c590770；
value pools 很小；
但 Z3 返回 unknown，且没有 validation candidates。
```

本轮不是继续扩大 SMT timeout，也不是增加 beam/budget/topN。目标是查明：

```text
1. unknown 是由 solver formulation 太重导致；
2. 还是 value-pool constraints / byte-nibble constraint handoff 有 bug；
3. 还是 exact2 basin 的候选质量确实不足；
4. 是否可以用 deterministic enumeration/evaluation 替代 heavy symbolic RC4 SMT。
```

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
current_bottleneck.stage = smt_exact2_basin_unknown
current_bottleneck.reason = bounded_exact2_basin_smt_attempted_but_z3_unknown
next_local_action = audit_smt_unknown_with_tiny_value_pools_without_timeout_expansion
```

最新 harness run：

```text
samplereverse_exact2_basin_smt_20260503
```

最新实现结果：

```text
real bounded exact2 basin SMT pass 已实现；
solve_targeted_prefix8() 已支持 value_pools；
compare-aware strategy 已增加 smt_exact2_basin 第二 SMT pass；
exact2-basin validations 会在产生 candidate 时进入 final candidate aggregation；
但本次没有产生 validation candidates。
```

当前 best candidate 仍是：

```text
78d540b49c59077041414141414141
runtime exact2 / distance5 246
compare_semantics_agree = true
```

SMT 结果：

```text
primary SMT:
  base = 5a3e7f46ddd474d0
  result = targeted z3 finished with unknown

exact2 basin SMT:
  base = 78d540b49c590770
  result = targeted z3 finished with unknown
  validation_candidates = []
  validations = []
```

exact2 basin value pools：

```text
0: [0x78]
1: [0xd5, 0x3e, 0x3c]
2: [0x40, 0x7f, 0x80]
3: [0xb4, 0x8f]
4: [0x9c]
```

候选空间上界：

```text
1 * 3 * 3 * 2 * 1 = 18 combinations
```

关键判断：

```text
如果只有 18 个组合，继续让 Z3 走 heavy symbolic RC4 objective 并返回 unknown，说明下一步不应加 timeout，而应审计 solver formulation 或直接做 deterministic enumeration/evaluation。
```

---

## 3. Do Not Do

严格禁止：

```text
1. 不要回到 old sample_solver blind search。
2. 不要只增加 guided_pool beam 或 budget。
3. 不要扩大 beam、budget、topN、timeout、frontier iteration limit。
4. 不要把 compare_semantics_agree=false candidates 作为主突破点。
5. 不要提交完整 solve_reports 目录。
6. 不要扫描完整 solve_reports，除非 artifact_index 不足。
7. 不要提升 5a3f7f46ddd474d0、5a3f7fc2ddd474d0、343f7f46ddd474d0。
8. 不要把 model-selected bare flag{ 当成 runtime improvement。
9. 不要重写 harness、pipeline、GUI、云端 API 路径。
10. 不要因为 Z3 unknown 就默认提高 timeout。
```

特别约束：

```text
unknown 不是 unsat proof。
但 tiny value pools 下的 unknown 更像 formulation/evaluation path 问题。
本轮只允许做局部审计或小范围 deterministic bounded evaluator。
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

重点代码文件：

```text
reverse_agent/samplereverse_z3.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

必要时再读：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
```

重点函数 / 逻辑：

```text
solve_targeted_prefix8()
run_compare_aware_smt()
smt_exact2_basin branch
value_pools constraints
byte/nibble override positions
candidate generation from SMT model
runtime validation handoff
final candidate aggregation
prefix_boundary scoring
```

优先审计：

```text
1. value_pools 是否真的约束到目标 bytes。
2. base byte 是否被强制保留。
3. byte positions 和 nibble positions 是否混用错位。
4. unknown 是否来自完整 RC4 symbolic encoding。
5. 是否可以直接 enumerate 18 combinations。
6. enumerate 后是否能调用现有 runtime validator。
```

---

## 5. Required Audit

### A. SMT unknown path audit

Codex 必须回答：

```text
1. solve_targeted_prefix8() 在 value_pools 下构造了多少 symbolic variables。
2. value_pools 是否实际进入 Z3 constraints。
3. base byte 是否被加入 value pool 或被固定保留。
4. byte positions [1,2,3,0,4] 与 pools key 是否一致。
5. nibble positions [2,3,0,1,4] 是否只影响目标 objective，而不是错误约束 byte index。
6. Z3 unknown 的 reason 是什么，是否能从 solver.reason_unknown() 取出。
7. unknown 是 primary SMT 和 exact2-basin SMT 都出现，还是 exact2-only。
8. 当前 formulation 是否仍在 symbolic RC4 全链路上求解。
```

### B. Tiny value-pool deterministic evaluation audit

因为 value pools 只有 18 个组合，Codex 必须判断是否可做：

```text
for each combination in value_pools:
    patch base prefix bytes
    build candidate
    run existing compare/runtime validation
    collect exact_wchars / distance5
```

必须保证：

```text
1. 不扩大搜索预算。
2. 不引入 blind search。
3. 只枚举 diagnostics 给出的 value pools。
4. 枚举数量必须记录在 artifact 中。
5. 每个 candidate 必须走现有 runtime validation。
6. 只有 runtime exact_wchars > 2 或 distance5 < 246 才算 improvement。
```

### C. Candidate promotion guard

必须确认：

```text
1. exact0/exact1 candidate 不能替换 current exact2 best。
2. unvalidated candidate 不能替换 final best。
3. bare flag{ 不能覆盖 compare-aware runtime best。
4. compare_semantics_agree=false candidate 不能作为主线。
5. 如果 18-combo enumeration 没有提升，则 final best 保持 78d540b49c59077041414141414141。
```

### D. Required evidence tables

#### Table 1: SMT unknown audit

| item | expected | observed | implication |
|---|---|---|---|
| primary SMT result | sat/unsat/unknown | | |
| exact2-basin SMT result | sat/unsat/unknown | | |
| solver reason_unknown | recorded | | |
| symbolic RC4 full chain used? | yes/no | | |
| value_pools constraints applied? | yes/no | | |
| base bytes preserved? | yes/no | | |
| validation candidates generated? | yes/no | | |

#### Table 2: deterministic value-pool feasibility

| item | value |
|---|---|
| base candidate | `78d540b49c590770` |
| value pool sizes | `1 * 3 * 3 * 2 * 1` |
| total combinations | `18` |
| requires budget expansion? | no |
| requires blind search? | no |
| can use existing runtime validator? | yes/no |
| recommended path | deterministic enumeration / keep SMT / stop |

#### Table 3: deterministic evaluation result

| field | value |
|---|---|
| generated combinations | |
| validated candidates | |
| best candidate | |
| best runtime exact_wchars | |
| best distance5 | |
| improved over exact2? | |
| final best changed? | |
| negative result recorded? | |

#### Table 4: classification

| classification | evidence for | evidence against | next action | recommendation |
|---|---|---|---|---|
| smt_unknown_due_to_heavy_symbolic_formulation | | | replace with deterministic bounded evaluator | yes/no |
| value_pool_constraint_bug | | | fix handoff/constraints + tests | yes/no |
| exact2_basin_value_pools_exhausted_no_gain | | | record negative result, stop this branch | yes/no |
| exact2_basin_deterministic_eval_produced_exact3_plus | | | promote only runtime-validated candidate | yes/no |
| candidate_quality_insufficient_after_exact2_basin_smt | | | move to next bounded evidence source | yes/no |

---

## 6. Implementation Scope

允许修改：

```text
reverse_agent/samplereverse_z3.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/model_gate.json
```

允许做的实现：

```text
1. 记录 solver.reason_unknown()。
2. 记录 SMT variable/constraint summary。
3. 修复 value_pools handoff bug，如果确实存在。
4. 增加 deterministic bounded value-pool evaluator。
5. 枚举 exact2_basin_smt 中的 tiny value pools。
6. 对枚举 candidate 走现有 runtime validation。
7. 将结果写入 artifact：generated_count、validated_count、best_candidate、best_exact、best_distance5。
8. 增加测试证明 enumeration 不扩大预算、不跑 blind search、不破坏 final selection。
```

不允许做的实现：

```text
1. 不要提高 SMT timeout。
2. 不要扩大 value pools。
3. 不要引入新 beam search。
4. 不要第三跳扩展。
5. 不要全局改 ranking。
6. 不要改 harness/pipeline/model API。
7. 不要提交完整 solve_reports。
```

推荐实现方向：

```text
优先加一个 deterministic bounded evaluator，而不是继续让 Z3 解 18-combo 的 heavy symbolic RC4 目标。
```

建议命名：

```text
evaluate_exact2_basin_value_pools()
run_exact2_basin_value_pool_evaluation()
```

---

## 7. Tests

先跑 targeted tests：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier or value"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果修改 `samplereverse_z3.py` 或 candidate aggregation：

```powershell
python -m pytest -q
```

如果实现 deterministic evaluator，运行新 harness，不覆盖旧 run：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_exact2_basin_valuepool_eval_20260503 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

运行后更新 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_exact2_basin_valuepool_eval_20260503
python -m reverse_agent.project_state status
```

最终报告必须包含：

```text
1. 是否记录 solver.reason_unknown()。
2. 是否确认 value_pools 真实进入 constraints。
3. 是否实现 deterministic bounded value-pool evaluator。
4. 枚举组合数量是否为 18。
5. runtime validation 结果。
6. 是否出现 exact3+。
7. 是否低于 distance5 246。
8. final best 是否改变。
9. 如果无提升，是否将 exact2_basin_value_pools_exhausted_no_gain 写入 negative_results。
```

---

## 8. Stop Conditions

立即停止并报告：

```text
1. value_pools 没有实际进入 solver constraints。
2. byte positions / nibble positions handoff 错位。
3. base candidate 不是 78d540b49c590770。
4. deterministic enumeration 需要超过 diagnostics value pools。
5. 实现需要扩大 timeout、beam、budget、topN。
6. 生成 candidate 不能走 runtime validation。
7. final selection 会被 unvalidated candidate 或 bare flag{ 覆盖。
8. 唯一提升依赖 compare_semantics_agree=false candidate。
```

成功停止条件：

```text
1. 找到 value_pools / constraint handoff bug，并最小修复。
2. deterministic 18-combo evaluation 产生 runtime-validated exact3+。
3. deterministic 18-combo evaluation 无提升，并把该方向记录为 negative result。
4. 证明 unknown 只来自 heavy symbolic formulation，并完成 deterministic evaluator 替代。
```

---

## GPT Decision Summary

当前不应继续加 SMT timeout。

原因：

```text
1. real bounded exact2 SMT pass 已经执行。
2. exact2 value pools 极小，理论组合只有 18 个。
3. Z3 返回 unknown，但这不是 unsat proof。
4. 没有 validation candidates，也没有 runtime best improvement。
5. 下一步更合理的是 deterministic bounded value-pool evaluation。
```

Codex 本轮只做一件事：

```text
审计 SMT unknown，并优先把 tiny value pools 转换为 deterministic enumeration + runtime validation。
```

不要回到 blind search，不要扩大预算，不要提升 compare_semantics_agree=false candidate。
