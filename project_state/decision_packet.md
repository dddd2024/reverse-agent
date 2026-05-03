# DECISION_PACKET

Generated: 2026-05-03

## 1. Goal

本轮目标是 **停止 exact2 basin value-pool 分支**，转向新的 bounded profile / transform hypothesis audit。

当前任务：

```text
stop_exact2_basin_value_pool_branch_and_seek_new_bounded_profile_or_transform_hypothesis
```

核心判断：

```text
exact2_basin_smt 已执行；
Z3 unknown 已审计；
18 个 exact2 value-pool combinations 已全部 deterministic runtime-validated；
无 exact3+，无 distance5 < 246；
该分支应停止，不应重复。
```

本轮不是继续扩大搜索，也不是重复 exact2 value-pool evaluation。Codex 应建立一个新的、有证据约束的 profile/transform 假设矩阵，定位下一轮可以验证的最小 bounded hypothesis。

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

当前状态：

```text
active_strategy = CompareAwareSearchStrategy
sample = samplereverse
current_mainline = L15(prefix8)
known_transform = input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
current_bottleneck.stage = candidate_quality_insufficient_after_exact2_value_pool_eval
current_bottleneck.reason = exact2_basin_value_pools_exhausted_no_gain
next_local_action = stop_exact2_basin_value_pool_branch_and_seek_new_bounded_profile_or_transform_hypothesis
```

最新 harness run：

```text
samplereverse_exact2_basin_valuepool_eval_20260503
```

最新分类：

```text
classification = exact2_basin_value_pools_exhausted_no_gain
```

当前 best 仍是：

```text
78d540b49c59077041414141414141
candidate_prefix = 78d540b49c590770
runtime exact_wchars = 2
runtime distance5 = 246
compare_semantics_agree = true
source = pairscan
```

已穷尽的 exact2 basin value pools：

```text
0: [0x78]
1: [0xd5, 0x3e, 0x3c]
2: [0x40, 0x7f, 0x80]
3: [0xb4, 0x8f]
4: [0x9c]
```

穷尽结果：

```text
estimated combinations = 18
generated_count = 18
unique_count = 18
validated_count = 18
best_candidate = 78d540b49c59077041414141414141
best_runtime_exact_wchars = 2
best_runtime_distance5 = 246
improved_over_exact2 = false
runtime_best_improved = false
```

负面结果已记录：

```text
do not repeat exact2 basin value-pool evaluation with pools:
0:78
1:d5/3e/3c
2:40/7f/80
3:b4/8f
4:9c
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
6. 不要扫描完整 solve_reports。
7. 不要重复 exact2 basin value-pool evaluation，除非 diagnostics value pools 改变。
8. 不要提升 5a3f7f46ddd474d0、5a3f7fc2ddd474d0、343f7f46ddd474d0。
9. 不要把 model-selected bare flag{ 当成 runtime improvement。
10. 不要因为 exact2 branch 失败就默认增加 timeout 或扩大搜索空间。
```

额外约束：

```text
本轮应转向 profile/transform hypothesis audit。
不是直接写新搜索器。
不是直接重构 pipeline。
不是默认修改 RC4/Base64/UTF-16LE 逻辑。
必须先形成 evidence-backed hypothesis matrix。
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

主要代码文件：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
reverse_agent/strategies/compare_aware_search.py
reverse_agent/samplereverse_z3.py
tests/test_compare_aware_search_strategy.py
```

只在必要时读取这些 artifact，不要读取完整 `solve_reports`：

```text
artifact_index.latest_artifacts.summary
artifact_index.latest_artifacts.compare_probe
artifact_index.latest_artifacts.bridge_validation
artifact_index.latest_artifacts.pairscan_summary
artifact_index.latest_artifacts.smt_result
artifact_index.latest_artifacts.smt_exact2_basin_result
artifact_index.latest_artifacts.exact2_basin_value_pool_result
artifact_index.latest_artifacts.exact2_basin_value_pool_validation
```

重点审计：

```text
1. profile 中对 samplereverse 的输入长度、prefix 长度、candidate layout 的假设。
2. transform 链：input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix。
3. L15(prefix8) 与 runtime 5-wchar compare 的边界是否一致。
4. Base64 boundary 是否存在 off-by-one / padding / chunk alignment 假设。
5. UTF-16LE wchar 拆分与 prefix byte mutation 是否一致。
6. RC4 key / keystream / decrypt_prefix helper 是否与 runtime compare probe 一致。
7. compare_probe 与 offline semantics 的 agree/disagree 分布。
8. exact2 candidate 为什么能稳定匹配 `f`, `l`，但局部 value-pool 无法推进到 exact3。
```

---

## 5. Required Audit

### A. Stop exact2 branch

Codex 必须确认：

```text
1. exact2_basin_value_pool_eval attempted = true。
2. generated_count = 18。
3. validated_count = 18。
4. best remains 78d540b49c59077041414141414141。
5. no exact3+。
6. no distance5 < 246。
7. negative_results 已记录该 exact2 value-pool pool。
8. 本轮不得重复该 pool。
```

### B. Profile / transform hypothesis matrix

Codex 必须构建一个 hypothesis matrix，至少包含这些候选假设：

```text
H1: candidate byte layout / prefix length 假设错误或过窄。
H2: UTF-16LE wchar boundary 与 byte mutation positions 存在错位。
H3: Base64 boundary / padding / chunk alignment 假设存在 off-by-one。
H4: RC4 helper/runtime mismatch。
H5: compare-aware offline semantics 与 runtime semantics 在特定 candidate basin 上有系统偏差。
H6: current exact2 candidate 已处于局部最优，当前 evidence 不足以继续 profile 内局部变异。
```

每个 hypothesis 必须包含：

```text
1. evidence for
2. evidence against
3. files/artifacts needed
4. bounded validation method
5. expected success signal
6. stop condition
7. whether code change is allowed
```

### C. Bounded validation design

每个新 hypothesis 的验证必须是 bounded 的，例如：

```text
1. 只比较现有 best candidates 的 transform trace。
2. 只生成 trace metadata，不生成大规模候选。
3. 只验证少量 hand-picked contrast candidates。
4. 只审计 byte/wchar/base64/rc4 boundary，不扩展 beam。
5. 只使用 compare_semantics_agree=true candidates 作为主线。
```

不允许把 hypothesis audit 变成：

```text
1. blind search
2. third-hop search
3. larger guided pool
4. timeout expansion
5. full solve_reports scan
```

### D. Required evidence tables

#### Table 1: exhausted branch confirmation

| field | value |
|---|---|
| run | |
| branch | exact2 basin value-pool |
| generated_count | |
| unique_count | |
| validated_count | |
| best candidate | |
| best exact_wchars | |
| best distance5 | |
| improved over exact2? | |
| negative result recorded? | |

#### Table 2: profile / transform hypothesis matrix

| hypothesis | evidence for | evidence against | files/artifacts | bounded validation | recommendation |
|---|---|---|---|---|---|
| H1 candidate layout / prefix length | | | | | |
| H2 UTF-16LE wchar boundary | | | | | |
| H3 Base64 boundary / padding | | | | | |
| H4 RC4 helper/runtime mismatch | | | | | |
| H5 offline/runtime semantic skew | | | | | |
| H6 candidate quality insufficient | | | | | |

#### Table 3: next recommended bounded experiment

| field | value |
|---|---|
| selected hypothesis | |
| reason | |
| files to modify | |
| artifacts to read | |
| candidate count allowed | |
| runtime validation required? | |
| expected improvement signal | |
| stop condition | |

---

## 6. Implementation Scope

默认本轮只做 audit / metadata / report，不直接改搜索行为。

允许修改：

```text
project_state/codex_execution_report.md
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/model_gate.json
```

如果缺少必要 trace metadata，允许最小修改：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

允许做的最小实现：

```text
1. 增加 transform trace metadata。
2. 增加 candidate layout / UTF-16LE / Base64 / RC4 boundary trace。
3. 增加 compare_probe/offline semantics contrast table。
4. 增加 hypothesis_matrix artifact。
5. 增加测试，证明只是 metadata，不改变 candidate generation/ranking/selection。
```

不允许做：

```text
1. 新 blind search。
2. 新 beam/guided expansion。
3. 重复 exact2 value-pool branch。
4. 提高 SMT timeout。
5. 改 final selection。
6. 改 harness/pipeline/API/GUI。
7. 提交完整 solve_reports。
```

推荐产出 artifact：

```text
solve_reports/.../reports/tool_artifacts/samplereverse/profile_transform_hypothesis_matrix.json
```

或 compact project_state 字段：

```text
latest_audit.profile_transform_hypotheses
next_local_action = run_selected_bounded_profile_transform_validation
```

---

## 7. Tests

如果只更新 project_state / report：

```powershell
python -m reverse_agent.project_state status
```

如果增加 metadata / trace：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "profile or transform or boundary or trace or compare"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果修改 transform/profile helper：

```powershell
python -m pytest -q
```

如果需要运行 bounded diagnostics harness，使用新 run name：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_profile_transform_hypothesis_audit_20260503 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

运行后：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_profile_transform_hypothesis_audit_20260503
python -m reverse_agent.project_state status
```

最终报告必须包含：

```text
1. exact2 value-pool branch 是否确认停止。
2. negative_results 是否已记录，不重复该 pool。
3. profile/transform hypothesis matrix。
4. 每个 hypothesis 的 evidence for/against。
5. 推荐的唯一下一步 bounded experiment。
6. 是否修改代码。
7. 是否改变 candidate generation/ranking/final selection。
8. 测试结果。
```

---

## 8. Stop Conditions

立即停止并报告：

```text
1. 需要重复 exact2 value-pool evaluation。
2. 需要扩大 beam/budget/topN/timeout。
3. 需要 full solve_reports scan。
4. 唯一方向依赖 compare_semantics_agree=false candidate。
5. 需要改 final selection 才能看到收益。
6. 无法把 hypothesis 限定为 bounded validation。
7. 缺少 artifact_index 支持，无法定位必要 artifact。
```

成功停止条件：

```text
1. 生成 profile/transform hypothesis matrix。
2. 选出一个 evidence-backed bounded hypothesis。
3. 明确下一轮只验证该 hypothesis。
4. 不重复 exact2 value-pool branch。
5. 不扩大搜索预算。
```

---

## GPT Decision Summary

当前 exact2 basin value-pool 分支应停止。

原因：

```text
1. real bounded exact2 SMT 已执行。
2. Z3 unknown 已审计。
3. deterministic value-pool evaluator 已穷尽 18 个组合。
4. 18 个候选全部 runtime validated。
5. 无 exact3+。
6. 无 distance5 improvement。
7. negative_results 已记录该分支不要重复。
```

下一轮单一方向：

```text
build profile/transform hypothesis matrix and choose one bounded validation target
```

Codex 不应继续扩大搜索，也不应继续 exact2 pool，而应把注意力转到：

```text
candidate layout / UTF-16LE boundary / Base64 boundary / RC4 helper-runtime consistency / offline-runtime semantic skew
```
