# DECISION_PACKET

## Goal

本轮目标：验证上一轮 `exact1_projected_preserve_lane` handoff 修正是否能在真实 `samplereverse` harness 中推进解题；如果不能推进，定位下一个最小局部瓶颈，优先审计 `projected preserve handoff -> second-hop composition -> runtime validation` 这条链路。

专用性边界：本轮允许使用 `samplereverse` 的当前候选、artifact、harness run 作为审计锚点。

通用性边界：任何代码修改都必须基于候选 metadata、frontier role、lane、boundary role、runtime/offline validation 等通用字段；不得把具体 candidate hex 写成策略规则。

最终产物：

- 运行或明确解释为何不能运行最小 `samplereverse` harness 验证。
- 生成新的 `project_state/codex_execution_report.md`。
- 重新生成 `project_state/current_state.json`、`project_state/artifact_index.json`、`project_state/task_packet.json`、`project_state/model_gate.json`。
- 如有必要，只做最小局部代码修改和对应测试。
- 不提交完整 `solve_reports`。

## Current Evidence

当前事实来源显示：

- active strategy 是 `CompareAwareSearchStrategy`。
- sample/profile 是 `samplereverse`。
- 当前主线是 `L15(prefix8)`。
- 已知变换链是 `input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix`。
- 当前瓶颈仍是 `frontier_refine`，原因仍是 `projected_winner_reached_pair_gate`。
- `model_gate.should_call_model = true`，`context_level = 2`，`missing_evidence = []`。

当前 best candidates：

- exact1/frontier：
  - `candidate_prefix = 5a3e7f46ddd474d0`
  - `candidate_hex = 5a3e7f46ddd474d041414141414141`
  - `compare_semantics_agree = true`
  - `runtime_ci_exact_wchars = 1`
  - `runtime_ci_distance5 = 258`
  - source = `exact2_seed(78d540b49c590770) -> refine(seed) -> guided(frontier)`

- exact2 seed：
  - `candidate_prefix = 78d540b49c590770`
  - `candidate_hex = 78d540b49c59077041414141414141`
  - `compare_semantics_agree = true`
  - `runtime_ci_exact_wchars = 2`
  - `runtime_ci_distance5 = 246`
  - source = `pairscan`

上一轮 Codex 已执行：

- 本地关键 artifacts 存在。
- 已补 `exact1_projected_preserve_lane` handoff slot 的最小回归测试。
- 已在 `_diverse_pair_frontier_pool()` 中做局部 handoff 保留修正。
- `tests/test_compare_aware_search_strategy.py` 通过，完整 pytest 通过。
- 没有运行真实 `samplereverse` harness。
- 重新 build project_state 后，model gate 仍然停在 `projected_winner_reached_pair_gate`。

因此本轮不能重复做同一个 pair_pool handoff 修正；必须先用最小 harness 或等价 runtime validation 验证该修正是否改变真实候选推进。

## Do Not Do

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、iteration、pool size。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports`。
5. 不要默认扫描完整 `solve_reports`，只读取 `artifact_index.json`、`run_manifest.json`、`summary.json`、相关 case result 和关键 tool artifacts。
6. 不要重复实现上一轮已经完成的 handoff slot 修正。
7. 不要把 `5a3e7f46ddd474d0`、`78d540b49c590770` 等具体 candidate 写入策略判断；它们只能作为审计样本和回归验证输入。
8. 不要修改 `pipeline.py`、GUI、模型调用逻辑或 harness 主体，除非只是读取 run manifest 或报告执行结果。

## Files To Inspect

优先读取：

1. `project_state/task_packet.json`
2. `project_state/current_state.json`
3. `project_state/artifact_index.json`
4. `project_state/model_gate.json`
5. `project_state/negative_results.json`
6. `project_state/codex_execution_report.md`
7. `project_state/decision_packet.md`

代码文件：

1. `reverse_agent/strategies/compare_aware_search.py`
2. `tests/test_compare_aware_search_strategy.py`
3. `reverse_agent/transforms/samplereverse.py`
4. `reverse_agent/profiles/samplereverse.py`

运行复现相关文件，只读：

1. `solve_reports/harness_runs/samplereverse_exact1_projected_preserve_lane_20260424/run_manifest.json`
2. `solve_reports/harness_runs/samplereverse_exact1_projected_preserve_lane_20260424/summary.json`
3. `solve_reports/harness_runs/samplereverse_exact1_projected_preserve_lane_20260424/case_results/samplereverse-exact1-projected-vs-neighbor.json`
4. artifact_index 指定的 `guided_pool_validation`
5. artifact_index 指定的 `guided_pool_result`
6. artifact_index 指定的 `compare_aware_result`
7. artifact_index 指定的 `frontier_summary`

不要默认读取完整 `PROJECT_PROGRESS_LOG.txt`。

## Required Audit

### 1. 验证上一轮 patch 状态

确认 `compare_aware_search.py` 中确实存在上一轮 handoff 保留逻辑，且测试中存在对应回归用例。

必须输出：

| check | result |
|---|---|
| handoff preservation code exists | yes/no |
| regression test exists | yes/no |
| target test passes | yes/no |
| full pytest required | yes/no |

如果 patch 或测试不存在，先修复该断裂，再继续。

### 2. 复原最小 harness 命令

从 `run_manifest.json` 或 README/harness 参数中复原上一次 `samplereverse` 单 case harness 的最小可运行命令。

要求：

- 使用同一个 sample/case，或同一个 dataset 中的 `samplereverse` case。
- 使用新的 run name，避免覆盖旧 artifacts。
- 不提交新 run 的完整 `solve_reports`。
- 如果缺少本地样本路径、dataset 或运行环境，停止并在报告中写清楚缺少什么。

### 3. 运行最小 runtime 验证

优先执行单 case harness。若无法运行完整 harness，但可以运行现有 runtime compare validation 工具，则运行最小等价 runtime validation。

验证后必须比较：

| metric | previous | new | improved |
|---|---:|---:|---|
| best runtime_ci_exact_wchars | 2 baseline / 1 frontier | new value | yes/no |
| best runtime_ci_distance5 | 246 seed / 258 frontier | new value | yes/no |
| compare_semantics_agree | true | new value | yes/no |
| frontier_stall_reason | projected_winner_reached_pair_gate | new reason | changed? |
| projected preserve handoff selected | expected yes after patch | new value | yes/no |

### 4. 定位 no-improvement 类型

如果 harness 没有推进，Codex 必须把失败归入以下一个类别，不允许泛化描述：

A. `handoff_not_selected`：上一轮 patch 没有真正进入 runtime 候选。

B. `selected_but_not_composed`：projected preserve handoff 进入 selected pool，但没有形成有效 second-hop 候选。

C. `composed_but_filtered_before_validation`：second-hop 候选生成了，但在排序/过滤中进入不了 validation。

D. `validated_but_no_runtime_gain`：候选进入 validation，但 runtime 指标无提升。

E. `runtime_offline_disagree`：offline 指标看似提升，但 runtime compare 不同意。

F. `environment_missing`：本地缺少样本、dataset、工具或执行环境。

### 5. second-hop composition 审计

只有当失败类型是 B 或 C 时，才允许审计 second-hop composition。

审计重点：

- selected handoff 候选是否被作为下一轮 pair/triad/local source。
- `frontier_exact1` submode 下排序是否过度惩罚短期 distance 略差但结构更好的 follow-up。
- follow-up 是否保留了 `compare_semantics_agree=true` 的验证路径。
- lineage 是否能从 `exact2_seed -> refine -> guided(frontier) -> projected_preserve` 继续传到 second-hop。
- 是否存在只记录 diagnostics、不进入 candidate payload 的断层。

## Decision Gates

Codex 必须按以下顺序执行。

### Gate 1: Artifact and Command Gate

If key artifacts or run command cannot be reconstructed:

- Do not modify strategy code.
- Write exact missing files/fields/env to `codex_execution_report.md`.
- Stop.

Else continue.

### Gate 2: Baseline Reproduction Gate

If the current best candidates cannot be reproduced from existing artifacts or a minimal runtime check:

- Do not patch.
- Report reproduction mismatch.
- Stop.

Else continue.

### Gate 3: Harness Result Gate

If the new harness/runtime validation solves the sample:

- Record the flag/result path.
- Rebuild project_state.
- Stop.

If runtime exact improves beyond current exact2, or distance improves while `compare_semantics_agree=true`:

- Accept current patch as useful.
- Rebuild project_state.
- Report the new bottleneck.
- Stop.

If no improvement:

- Continue to Gate 4.

### Gate 4: Failure Classification Gate

Classify failure as A/B/C/D/E/F from Required Audit section.

- For A: revisit only handoff retention; do not touch second-hop composition.
- For B/C: inspect second-hop composition and payload propagation.
- For D: inspect runtime validation ranking and metric mismatch; do not expand search.
- For E: stop and report validation inconsistency.
- For F: stop and report environment gap.

### Gate 5: Patch Permission Gate

A new code patch is allowed only if all are true:

- failure type is A, B, or C;
- artifacts and runtime/offline semantics are consistent;
- the bug is local to candidate retention, second-hop payload propagation, lineage propagation, or validation ordering;
- the patch does not increase global budget;
- the patch is covered by a focused unit test.

Otherwise, write audit results only.

### Gate 6: Specificity/Generality Gate

Before committing any patch, Codex must verify:

- no candidate hex is hardcoded into strategy behavior;
- no sample-specific constant is introduced unless it already belongs to the `samplereverse` profile path;
- the logic is expressed in terms of lane, frontier role, candidate origin, boundary role, compare agreement, and metric deltas;
- the test may use concrete candidates as fixtures, but the implementation may not.

## Implementation Scope

Allowed modifications:

1. `reverse_agent/strategies/compare_aware_search.py`
2. `tests/test_compare_aware_search_strategy.py`
3. `project_state/codex_execution_report.md`
4. generated `project_state/*.json`
5. `project_state/decision_packet.md` only if Codex needs to append execution status

Allowed only if necessary for reporting, not solving logic:

- small helper code that extracts compact diagnostics from existing artifacts.

Not allowed:

- changing `reverse_agent/harness.py` to make a failing result look successful;
- changing `reverse_agent/pipeline.py`;
- modifying GUI code;
- modifying model API paths;
- adding broad blind search fallback;
- committing `solve_reports` bulk output.

## Tests

Always run before any patch:

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

If a strategy patch is made, run:

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

Runtime validation:

- Reconstruct and run the minimal single-case `samplereverse` harness from `run_manifest.json` when possible.
- Use a new run name, for example `samplereverse_handoff_verify_<date_or_short_sha>`.
- After runtime validation, run:

```bash
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse
python -m reverse_agent.project_state status
```

The test/report section must include exact commands and pass/fail status.

## Stop Conditions

Stop and report without further code changes if any condition occurs:

1. Key artifacts referenced by `artifact_index.json` are missing locally.
2. The minimal harness command cannot be reconstructed.
3. The local sample/dataset/tooling required for runtime validation is missing.
4. Current best candidates cannot be reproduced.
5. runtime/offline compare semantics disagree.
6. No-improvement cannot be classified into A/B/C/D/E/F.
7. A proposed fix requires global budget expansion.
8. A proposed fix hardcodes candidate hex into strategy behavior.
9. A proposed fix touches pipeline, GUI, model API, or broad harness behavior.
10. Codex needs full `PROJECT_PROGRESS_LOG.txt` to proceed.
11. Codex needs to scan full `solve_reports` rather than indexed artifacts and the latest harness run.
