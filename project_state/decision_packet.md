# DECISION_PACKET

## Goal

本轮目标：只审计并修复 `samplereverse` 的 `exact1 pair_pool bottleneck`。

当前主线是 `CompareAwareSearchStrategy`，不要切回旧 blind search。重点不是扩大预算，而是解释为什么当前 exact1 frontier 候选已经到达 pair gate，但没有稳定推进到更高 exact 匹配。

最终产物：

- 更新 `project_state/decision_packet.md`
- 生成新的 `project_state/codex_execution_report.md`
- 如有必要，只做最小代码修改
- 如本地 artifacts 缺失，先重建 project_state，而不是凭空改搜索逻辑

## Current Evidence

当前事实来源显示：

- active strategy 是 `CompareAwareSearchStrategy`。
- sample/profile 是 `samplereverse`。
- 当前已知变换链是：`input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix`。
- 当前主线是 `L15(prefix8)`。
- 当前瓶颈阶段是 `frontier_refine`，原因是 `projected_winner_reached_pair_gate`，置信度 medium。

当前最好候选：

- exact1/frontier 候选：
  - `candidate_prefix = 5a3e7f46ddd474d0`
  - `candidate_hex = 5a3e7f46ddd474d041414141414141`
  - `compare_semantics_agree = true`
  - `runtime_ci_exact_wchars = 1`
  - `runtime_ci_distance5 = 258`
  - 来源：`exact2_seed(78d540b49c590770) -> refine(seed) -> guided(frontier)`。

- exact2 候选：
  - `candidate_prefix = 78d540b49c590770`
  - `candidate_hex = 78d540b49c59077041414141414141`
  - `compare_semantics_agree = true`
  - `runtime_ci_exact_wchars = 2`
  - `runtime_ci_distance5 = 246`
  - 来源：`pairscan`。

artifact index 指向最新 harness run：

- `solve_reports/harness_runs/samplereverse_exact1_projected_preserve_lane_20260424`
- 关键 artifacts 包括 `guided_pool_result`、`guided_pool_validation`、`compare_aware_result`、`frontier_summary`、`bridge_validation`、`pairscan_summary`、`smt_result`。

注意：这些 `solve_reports` 运行产物在 GitHub 当前分支上不可直接读取，Codex 需要在本地仓库读取。

## Do Not Do

禁止方向：

1. 不要回退到旧 `sample_solver` blind search。
2. 不要只增加 beam、budget、迭代次数。
3. 不要把 `compare_semantics_agree=false` 的候选作为主突破点。
4. 不要提交完整 `solve_reports` 目录。
5. 不要默认扫描完整 `solve_reports`，只读取 artifact_index 指定的关键文件。

## Files To Inspect

优先读取：

1. `project_state/task_packet.json`
2. `project_state/current_state.json`
3. `project_state/artifact_index.json`
4. `project_state/negative_results.json`
5. `project_state/codex_execution_report.md`

代码文件：

1. `reverse_agent/strategies/compare_aware_search.py`
2. `tests/test_compare_aware_search_strategy.py`

本地 artifacts，只读索引指定路径：

1. `guided_pool_validation`
2. `guided_pool_result`
3. `compare_aware_result`
4. `frontier_summary`
5. `bridge_validation`
6. `pairscan_summary`
7. `smt_result`
8. `summary.json`
9. `run_manifest.json`

不要默认读取完整 `PROJECT_PROGRESS_LOG.txt`。当前 `task_packet`、`current_state`、`artifact_index` 信息已经足够进入下一轮。

## Required Audit

Codex 必须先做审计，不要直接改代码。

### 1. 验证 artifacts 是否存在

检查 `artifact_index.json` 中所有关键路径是否在本地存在。

如果缺失：

- 停止代码修改。
- 生成报告说明缺失哪些 artifact。
- 建议重新运行 project_state 构建脚本或最近一次 harness。
- 不要根据 GitHub 上缺失的 solve_reports 猜测结果。

### 2. 复核当前 best candidate 排序

从 `guided_pool_validation`、`compare_aware_result`、`frontier_summary` 中抽取 top candidates，确认：

- `compare_semantics_agree=true`
- runtime exact/wchar 指标和 offline 指标是否一致
- exact1 frontier `5a3e7f46ddd474d0` 是不是确实卡在 pair gate
- exact2 seed `78d540b49c590770` 为什么虽然 exact 更高，但没有成为当前 frontier 主线

输出一个小表：

| role | candidate_prefix | exact | distance5 | compare_agree | source | accepted_as_frontier |
|---|---|---:|---:|---|---|---|

### 3. 审计 pair_pool 生成逻辑

聚焦 `compare_aware_search.py` 中 exact1 相关逻辑，尤其是这些常量和路径：

- `EXACT1_PAIR_LOCK_LIMIT`
- `EXACT1_PAIR_DISTANCE_ESCAPE`
- `EXACT1_PAIR_PRESERVE_VALUE_LIMIT`
- `EXACT1_PAIR_ESCAPE_VALUE_LIMIT`
- `EXACT1_PAIR_PROFILE_PRESERVE_TOP`
- `EXACT1_PAIR_PROFILE_ESCAPE_TOP`
- `EXACT1_PROJECTED_STEP_LIMIT`
- `EXACT1_PROJECTED_KEEP_PER_DIRECTION`
- `EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR`
- `EXACT1_PAIR_HARD_ESCAPE_DIAG_SAMPLES`

检查问题不是“预算不够”，而是：

- pair_pool 是否过早丢弃了 promising exact1 family；
- preserve lane 和 escape lane 是否互相覆盖；
- projected winner 到达 pair gate 后，是否缺少继续扩展的局部邻域；
- exact2 seed 到 exact1 frontier 的 lineage 是否导致排序偏置；
- pair gate 是否把结构指标较好但短期 distance 略差的候选过滤掉。

`compare_aware_search.py` 中已经存在大量 exact1 pair/projected 相关常量和逻辑，因此不要重写策略，只做局部诊断和最小修正。

### 4. 审计测试覆盖

检查 `tests/test_compare_aware_search_strategy.py` 是否覆盖：

- exact1 projected preserve lane
- exact1 escape lane
- compare_semantics_agree=false hard block
- pair_pool gate 后候选不应被完全丢弃
- exact2 seed 和 exact1 frontier 同时存在时的排序规则

如果缺测试，先补测试再改代码。

## Implementation Scope

允许修改：

1. `reverse_agent/strategies/compare_aware_search.py`
2. `tests/test_compare_aware_search_strategy.py`
3. `project_state/decision_packet.md`
4. `project_state/codex_execution_report.md`

谨慎允许新增小型本地审计脚本，但必须放在合适位置，并且不能依赖完整 solve_reports 全量扫描。

不允许：

- 大规模重构策略。
- 修改旧 blind search 作为主路线。
- 提交 `solve_reports`。
- 把搜索预算、beam、iteration 简单调大作为主要方案。
- 引入新的模型调用路径。

## Tests

至少运行：

```bash
pytest tests/test_compare_aware_search_strategy.py -q
```

如果修改影响策略主流程，再运行：

```bash
pytest tests -q
```

如果本地有 samplereverse harness，则运行最小相关 harness，验证是否重新生成：

- `guided_pool_validation`
- `compare_aware_result`
- `frontier_summary`
- `project_state/current_state.json`
- `project_state/artifact_index.json`

测试报告必须写入 `project_state/codex_execution_report.md`。

## Stop Conditions

遇到以下情况必须停止并报告，不要继续扩大修改范围：

1. `artifact_index.json` 指向的关键 artifact 在本地不存在。
2. 当前 best candidate 无法复现。
3. runtime/offline compare semantics 不一致。
4. 需要读取完整 `PROJECT_PROGRESS_LOG.txt` 才能判断方向。
5. 需要扩大搜索预算才能看到任何变化。
6. 需要使用 `compare_semantics_agree=false` 候选作为主线。
7. 修改超过 `compare_aware_search.py` 和对应测试文件范围。
