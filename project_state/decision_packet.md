# DECISION_PACKET

Generated: 2026-05-02

## 1. Goal

本轮目标是审计或产出 **new compare-aware diagnostics artifacts**，核心任务是验证上一轮 diagnostic-only 改动是否能为下一步提供可执行证据。

当前任务名：

```text
run_or_audit_new_compare_aware_diagnostics_without_blind_search
```

上一轮 Codex 已实现 transform/profile boundary 的 diagnostic-only 改动，新增 metadata 包括：

```text
prefix_boundary
prefix_boundary_diagnostics
exact2_basin_smt
```

该改动没有扩大 beam、budget、topN、timeout 或 frontier iteration limit；没有运行新 harness；没有修改 final candidate selection。当前 best 仍是：

```text
78d540b49c59077041414141414141
runtime exact2 / distance5 246
```

本轮只回答一个更窄的问题：

```text
新加入的 prefix_boundary_diagnostics 和 exact2_basin_smt 是否能证明：
1. exact2 basin 有可执行的 bounded SMT 方向；
2. transform/profile boundary 缺少可利用信号；
3. 还是当前仍然只是 candidate_quality_insufficient。
```

本轮不是继续写新搜索逻辑，也不是扩大预算。Codex 必须先产出或审计包含新 diagnostics 的 compare-aware artifact，然后再决定是否需要下一轮 real bounded exact2 SMT pass。

---

## 2. Current Evidence

事实来源是当前 `project_state` 文件，不要用记忆替代仓库状态。

### Current state

```text
active_strategy = CompareAwareSearchStrategy
sample = samplereverse
current_mainline = L15(prefix8)
known_transform = input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
current_bottleneck.stage = transform_profile_boundary
current_bottleneck.reason = diagnostics_added_for_transform_profile_boundary_and_exact2_basin_smt
current_bottleneck.confidence = high
next_local_action = run_or_audit_new_compare_aware_diagnostics_without_blind_search
```

### Current best candidates

| candidate | role | source | compare agree | runtime exact | distance5 | status |
|---|---|---|---:|---:|---:|---|
| `78d540b49c590770` | exact2 best / stable reference | `profile_seed -> bridge_pairscan -> seed_guided -> final_refine` | true | 2 | 246 | keep and use as exact2 basin reference |
| `5a3e7f46ddd474d0` | exact1 frontier / contrast case | `exact2_seed(78d...) -> refine(seed) -> guided(frontier)` | true | 1 | 258 | contrast against exact2 |
| `5a3f7f46ddd474d0` | projected preserve second-hop anchor | projected preserve lane | true | 0 | 740 | downgraded; do not promote |
| `5a3f7fc2ddd474d0` | best new second-hop triad | projected/preserve pool | true | 0 | 419 | do not promote |
| `343f7f46ddd474d0` | second-hop guided/top entry | second-hop guided pool | true | 0 | 428 | do not promote |

### Latest Codex audit result

上一轮 `codex_execution_report.md` 的结论：

```text
classification = transform_profile_boundary_diagnostics_added
behavior_change = diagnostic_metadata_only
code_fix_recommended = false
source_bug_found = false
projected_preserve_status = downgraded_validated_no_runtime_gain
```

Implemented diagnostic changes:

1. `prefix_boundary` diagnostics break each candidate prefix into UTF-16 wchar deltas against `flag{`.
2. Runtime validations now carry `prefix_boundary` metadata.
3. Frontier/refine artifacts and strategy metadata now include `prefix_boundary_diagnostics`.
4. Primary SMT payload now records `prefix_boundary`.
5. When primary SMT chooses an exact1 frontier while a compare-agree exact2 exists, `exact2_basin_smt` records a diagnostic-only exact2 basin plan.
6. No candidate selection behavior was changed.

### Boundary evidence from latest report

| candidate | runtime prefix pairs | runtime exact | distance5 | interpretation |
|---|---|---:|---:|---|
| `78d540b49c590770` | `4600 6c00 4464 830d 311c` | 2 | 246 | stable exact2 basin: `f`, `l` match |
| `5a3e7f46ddd474d0` | `4600 6135 7f0b 8c68 8502` | 1 | 258 | exact1 frontier: only `f` matches |
| `5a3f7f46ddd474d0` | `7493 4b15 6ba6 9ef3 370f` | 0 | 740 | projected preserve anchor collapses before first wchar |
| `5a3f7fc2ddd474d0` | `854a e01a bc18 692c 7505` | 0 | 419 | best new second-hop candidate remains exact0 |

### Test evidence from latest report

上一轮运行：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier"  # 26 passed, 31 deselected
python -m pytest -q tests/test_compare_aware_search_strategy.py                                      # 57 passed
python -m pytest -q                                                                                 # 139 passed
```

No full harness was run, per plan.

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
8. 不要把 `5a3f7f46ddd474d0`、`5a3f7fc2ddd474d0` 或 `343f7f46ddd474d0` 提升为 best/final。
9. 不要只因为 diagnostic metadata 存在就改变 final candidate selection。
10. 不要直接实现 real exact2 SMT pass，除非 diagnostics 证明它有 bounded variable positions / value pools。
11. 不要修改 GUI、模型调用路径、云端 API 路径、pipeline 总控或 harness 总控。
12. 不要让 Codex 重复实现项目中已有的 compare-aware/frontier/guided/refine 功能。
13. 不要把本轮做成 blind 搜索或预算扩张。本轮是 diagnostics artifact audit / production。

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

必要时再读：

```text
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
```

只聚焦 diagnostic metadata / exact2 basin SMT 相关逻辑，重点查找：

```text
prefix_boundary helper
prefix_boundary_diagnostics emission
runtime validation metadata
frontier_summary / strata_summary metadata
primary SMT payload prefix_boundary
exact2_basin_smt diagnostic payload
exact2 candidate selection as diagnostic base
variable positions / value pools construction
compare-agree filtering for exact2_basin_smt
metadata-only behavior guard
final selection isolation from diagnostics
```

当前 `artifact_index.json` 仍指向旧 harness run：

```text
solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502
```

注意：该 artifact index 还不是新 diagnostics run 的索引。上一轮只实现了 diagnostics metadata 代码，没有运行新 harness。Codex 本轮需要判断是否运行一个新的 diagnostics-only artifact-producing run，或是否已有足够 artifacts 可审计。

---

## 5. Required Audit

Codex 本轮必须先输出审计发现，再决定是否运行新 diagnostics harness 或修改 metadata emission。

### A. Pre-audit checks

1. 确认工作区初始是否 clean。
2. 确认当前 `project_state` 的 task 是：

```text
run_or_audit_new_compare_aware_diagnostics_without_blind_search
```

3. 确认 latest classification 是：

```text
transform_profile_boundary_diagnostics_added
```

4. 确认上一轮是 diagnostic metadata only：

```text
no budget expansion
no final selection behavior change
no new harness run
```

### B. Confirm diagnostics are wired into code path

必须回答：

1. `validate_compare_aware_results()` 是否为每条 runtime validation 附加 `prefix_boundary`。
2. `frontier_summary`、`strata_summary`、strategy metadata、search artifact payload 是否包含 `prefix_boundary_diagnostics`。
3. primary SMT payload 是否包含 `prefix_boundary`。
4. 当 primary SMT chooses exact1 frontier 且存在 compare-agree exact2 时，是否附加 `exact2_basin_smt` diagnostic payload。
5. `exact2_basin_smt` 是否只作为 diagnostic metadata，不运行额外 runtime validation，不替换 final best。
6. 是否有测试覆盖 metadata-only guard，防止 diagnostics 改变 final selection。

### C. Decide whether to run a new diagnostics-only artifact-producing execution

Codex 必须选择以下路径之一，并说明理由：

```text
Path 1: artifacts already sufficient
- 不运行 harness。
- 只更新 codex_execution_report / project_state。
- 说明为什么现有单元测试与 metadata inspection 足够。

Path 2: need real compare-aware artifacts with new diagnostics
- 允许运行一个新的 diagnostics-only harness。
- 不扩大预算，不改 selection，不提交完整 solve_reports。
- run name 必须是新的，不能覆盖旧 run。
```

建议 run name：

```text
samplereverse_prefix_boundary_diagnostics_20260502
```

如果选择 Path 2，运行后必须执行：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_prefix_boundary_diagnostics_20260502
python -m reverse_agent.project_state status
```

### D. Audit exact2_basin_smt payload

如果存在 `exact2_basin_smt` payload，必须回答：

1. payload 是否存在。
2. base candidate 是否是 `78d540b49c590770`。
3. base 的 `prefix_boundary` 是否显示 exact2：`f`, `l` match。
4. variable positions 是哪些。
5. value pools 是哪些，是否 bounded。
6. 是否保留 compare-agree 约束。
7. 是否能解释从 exact2 到 exact3+ 的可能方向。
8. 是否只是 metadata，没有 runtime validation。
9. 是否支持下一轮 real bounded exact2 SMT pass。

### E. Required evidence tables

#### Table 1: diagnostics wiring audit

| diagnostic field | expected location | present? | behavior impact | evidence |
|---|---|---:|---|---|

Required rows:

```text
prefix_boundary
prefix_boundary_diagnostics
primary SMT prefix_boundary
exact2_basin_smt
metadata-only guard
final selection isolation
```

#### Table 2: exact2 basin diagnostic payload

| field | value | bounded? | implication |
|---|---|---:|---|

Required rows if payload exists:

```text
base candidate
runtime exact / distance5
prefix_boundary matched wchars
variable positions
value pools
compare-agree guard
runtime validation status
selection impact
```

#### Table 3: next classification

| classification | evidence for | evidence against | next action | recommendation |
|---|---|---|---|---|

Candidate classifications must include:

```text
diagnostics show promising exact2_basin_smt
diagnostics show no exact2-basin signal
candidate_quality_insufficient_after_transform_boundary
transform/profile boundary bug found
diagnostics insufficient; enhance artifact_index or metadata
```

---

## 6. Implementation Scope

默认不改搜索行为。

允许做的事情：

```text
1. 审计 diagnostic metadata 是否已进入代码路径。
2. 如需要，运行一个新的 diagnostics-only compare-aware execution。
3. 更新 project_state/codex_execution_report.md。
4. 更新 project_state/task_packet.json、current_state.json、artifact_index.json、model_gate.json。
5. 如果 diagnostics 字段缺失，修复 metadata emission。
6. 如果测试不足，补充 diagnostic metadata tests。
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

只有满足以下条件，才允许 metadata 小修：

1. diagnostics 字段在代码路径中缺失或未被 artifact/index 捕获。
2. 修复只是 metadata/reporting emission。
3. 不改变 candidate generation、ranking、validation、final selection。
4. 不扩大任何预算。
5. 必须补测试证明 metadata-only。

如果 diagnostics 显示 `exact2_basin_smt` 有 promising bounded positions，本轮仍不要直接实现 real exact2 SMT execution。只在报告中推荐下一轮执行 real bounded exact2 SMT pass。

---

## 7. Tests

审计或 metadata 小修后先跑：

```powershell
python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier"
python -m pytest -q tests/test_compare_aware_search_strategy.py
```

如果修改了 metadata emission 或 diagnostics：

```powershell
python -m pytest -q
```

如果运行新的 diagnostics harness，建议命令：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_prefix_boundary_diagnostics_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_prefix_boundary_diagnostics_20260502
python -m reverse_agent.project_state status
```

最终报告必须包含：

1. 是否运行了新 diagnostics harness。
2. run name。
3. 是否生成 `prefix_boundary_diagnostics`。
4. 是否生成 `exact2_basin_smt`。
5. `exact2_basin_smt` 的 base candidate、variable positions、value pools。
6. 是否有 candidate 超过 exact2。
7. 是否保持不扩大预算、不改变 final selection。
8. 下一轮是否推荐 real bounded exact2 SMT pass。

---

## 8. Stop Conditions

出现以下情况立即停止并报告：

1. 新 diagnostics artifact 缺少 `prefix_boundary_diagnostics` 或 `exact2_basin_smt`。
   - 不继续行为修改。
   - 先修 metadata/reporting 或 project_state indexing。

2. `exact2_basin_smt` 存在，但 payload 为空、没有 bounded positions、没有 value pools，或 base 不是 `78d540b49c590770`。
   - 分类为 diagnostics insufficient 或 candidate_quality_insufficient_after_transform_boundary。

3. `exact2_basin_smt` 显示有明确 bounded variable positions / value pools。
   - 不直接扩大搜索。
   - 不直接改 selection。
   - 下一轮再决策是否运行 real bounded exact2 SMT pass。

4. diagnostics 指向 transform/profile boundary bug。
   - 只做最小 metadata or boundary 修复。
   - 补测试。
   - 不改预算。

5. 需要完整 `solve_reports` 或完整 `PROJECT_PROGRESS_LOG.txt`。
   - 停止。
   - 先增强 artifact_index 或 diagnostics output。

6. 唯一可行方向依赖 `compare_semantics_agree=false` candidate。
   - 不作为主线。
   - 记录到 negative evidence。

7. 需要 blind search、第三跳、更大 beam/topN/timeout 才能继续。
   - 停止。
   - 不执行。

---

## GPT Decision Summary

当前已经排除的方向：

```text
1. second-hop 没有执行：已修复。
2. pair gate / refine / final selection 错误过滤更优候选：已审计，未发现。
3. projected preserve second-hop 继续扩展：已验证无收益，5a3f7f46ddd474d0 降级。
4. exact2 seed source metadata/ranking/inclusion bug：已审计，未发现。
5. transform/profile boundary 直接行为修复：尚无证据，不应直接改 selection。
```

下一轮单一方向：

```text
run or audit a new compare-aware diagnostic artifact containing prefix_boundary_diagnostics and exact2_basin_smt, without blind search or budget expansion
```

Codex 不应继续扩大搜索，而应先回答：

```text
新 diagnostics 是否真实进入 artifact；
exact2_basin_smt 是否以 78d540b49c590770 为 base；
是否存在 bounded variable positions / value pools；
这些 evidence 是否足以支持下一轮 real bounded exact2 SMT pass。
```

如果 diagnostics 没有提供 exact2-basin signal，本轮应分类为：

```text
candidate_quality_insufficient_after_transform_boundary
```

如果 diagnostics 明确给出 bounded exact2 SMT 方向，本轮只报告并推荐下一轮执行，不要直接改变 final selection。