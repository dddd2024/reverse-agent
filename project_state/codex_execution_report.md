# CODEX_EXECUTION_REPORT

## Summary

本轮按计划修复 frontier second-hop loop stop condition。根因确认：`_frontier_continuation_candidates()` 已经把 `5a3f7f46ddd474d0` 作为 `validated_projected_preserve_second_hop` 返回 `"continue"`，但主循环随后在“无 improved frontier candidate”时把 `"continue"` 覆盖回 `"distance_not_improved"`，导致记录了 continuation，却没有实际进入第二轮 guided run。

本轮做了最小通用修复：当 `used_second_hop=True` 时保留 `"continue"`，不再把它归一化为 `distance_not_improved`。没有扩大 `FRONTIER_MAX_ITERATIONS`、beam、budget、topN、timeout；没有改变 candidate generation、metadata gate、model path、GUI、pipeline 或 harness 总控。

真实 harness 已验证修复生效：新 run 生成了 `frontier_guided_2_5a3f7f46ddd474d0`，第二轮 role 为 `validated_projected_preserve_second_hop`。二跳执行后仍未改善 runtime best，下一轮瓶颈应转为 second-hop candidate quality / pair gate after projected winner。

## Files Changed

- `reverse_agent/strategies/compare_aware_search.py`
- `tests/test_compare_aware_search_strategy.py`
- `project_state/codex_execution_report.md`
- `project_state/artifact_index.json`
- `project_state/current_state.json`
- `project_state/model_gate.json`
- `project_state/task_packet.json`
- `PROJECT_PROGRESS_LOG.txt`

## Implementation

- 在 frontier loop 中保留 second-hop continuation 的 `"continue"` 状态：
  - 修复前：无 improved candidate 时会把 `"continue"` 改回 `"distance_not_improved"`。
  - 修复后：只有非 second-hop continuation 才执行该归一化。
- 新增 strategy-level 回归测试：
  - 第一轮 frontier 无 runtime improvement；
  - 生成 metadata-gated `validated_projected_preserve_second_hop` continuation；
  - 断言第二轮 guided pool 以 `5a3f7f46ddd474d0` 为 anchor 运行；
  - 断言第一轮 `used_second_hop_frontier_candidates=true` 且 `frontier_converged_reason=continue`。

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"` | `5 passed, 50 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `55 passed` |
| `python -m pytest -q` | `137 passed` |
| `python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_second_hop_loop_fix_verify_20260502 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume` | completed, `error_cases=0`, no Copilot quota `402` |
| `python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_second_hop_loop_fix_verify_20260502` | completed |
| `python -m reverse_agent.project_state status` | latest run indexed, `missing=[]` |

## Harness Result

- Run: `solve_reports\harness_runs\samplereverse_second_hop_loop_fix_verify_20260502`
- Summary: `executed_cases=1`, `completed_without_expected=1`, `error_cases=0`, `candidate_quality=1.0`, `evidence_coverage=1.0`
- Case result: `status=completed_no_expected`
- Selected flag/candidate: `78d540b49c59077041414141414141`
- Copilot quota: 本轮没有出现 `402 You have no quota`
- Artifacts: complete; `project_state/artifact_index.json` rebuilt with `missing=[]`

## Artifact Answers

| question | answer |
|---|---|
| 是否出现 `frontier_guided_2_5a3f7f46ddd474d0` | 是 |
| `frontier_guided_runs` 数量 | 2 |
| 第二轮 anchor | `5a3f7f46ddd474d0` |
| 第二轮 role | `validated_projected_preserve_second_hop` |
| 第一轮是否记录 used second-hop | 是，`used_second_hop_frontier_candidates=true` |
| 第一轮 converge reason | `continue` |
| 总体 converge reason | `iteration_limit` |
| exact2 best 是否保留 | 是，最终 selected candidate 仍为 `78d540b49c59077041414141414141` |
| 二跳后 best 是否改善 | 否；exact1 仍为 `5a3e7f46ddd474d0`，exact2 仍为 `78d540b49c590770` |

## Candidate Table

| candidate | source stage | frontier role | compare agree | runtime exact | distance5 | second-hop eligible | actually used in second-hop | result |
|---|---|---|---:|---:|---:|---:|---:|---|
| `78d540b49c590770` | pairscan / guided pool / final validation | `exact2_seed` / `best_overall` | true | 2 | 246 | no | no | retained as best overall / selected candidate |
| `5a3e7f46ddd474d0` | frontier guided iteration 1 and second-hop validation result | `exact1_frontier` / second-hop validation entry | true | 1 | 258 | no | no | remains best exact1 frontier |
| `5a3f7f46ddd474d0` | projected preserve handoff -> second-hop guided anchor | `validated_projected_preserve_second_hop` | true | 0 | 740 | yes | yes | second-hop artifact emitted, but no runtime gain |

## Classification

本轮修复成功解决了 frontier loop stop condition / artifact emission 问题：second-hop continuation 现在能实际进入第二轮 guided run。

新的瓶颈不是 plumbing，而是候选质量：`5a3f7f46ddd474d0` 触发二跳后，第二轮 guided/refine 没有产生优于 `5a3e7f46ddd474d0` 或 exact2 best 的候选。下一轮默认方向应分析 `frontier_guided_2_5a3f7f46ddd474d0` 的 pair/pool 输出，重点看 projected winner 后的 pair gate / candidate quality，不要扩大 beam/budget，也不要回到 blind search。
