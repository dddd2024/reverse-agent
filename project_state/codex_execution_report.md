# CODEX_EXECUTION_REPORT

## Summary

本轮按计划完成 second-hop pair/pool quality 审计，没有修改 strategy 代码，也没有重复运行 harness。

结论：`frontier_guided_2_5a3f7f46ddd474d0` 已真实执行，但二跳 pool 没有产生任何优于现有 exact1 `5a3e7f46ddd474d0` 或 exact2 best `78d540b49c590770` 的候选。未发现 compare-agree 且 runtime 更优的候选被 pair gate、refine selection 或 final best selection 错误丢弃。因此本轮不应修 `compare_aware_search.py`，瓶颈应从疑似 `pair_gate_after_projected_winner` 收敛为 `candidate_quality_insufficient_after_projected_winner`。

## Pre-Audit Checks

| check | result |
|---|---|
| initial git status | clean |
| latest indexed run | `samplereverse_second_hop_loop_fix_verify_20260502` |
| `frontier_guided_2_5a3f7f46ddd474d0` indexed | yes |
| `frontier_refine_2` indexed | yes |
| prior bottleneck | `frontier_exact1 / pair_gate_after_projected_winner` |

## Second-Hop Pool Summary

| source/role | count | compare agree count | validated count | best runtime exact | best distance5 | conclusion |
|---|---:|---:|---:|---:|---:|---|
| `top_entries` / `validated_projected_preserve_second_hop` | 16 | 8 validated agree | 8 | 1 | 258 | best is existing `5a3e7f46ddd474d0`, no improvement |
| `validation_candidates` / `validated_projected_preserve_second_hop` | 8 | 8 | 8 | 1 | 258 | all compare-agree, none beats exact1 or exact2 |
| `pair_frontier_pool` / preserve neighbors | 8 | 1 validated agree | 1 | 1 | 258 | only validated entry is existing exact1 |
| `triad_frontier_pool` / generated triads | 8 | 6 validated agree | 6 | 0 | 419 | all are worse than exact1 |

Validation distribution for second-hop: 8 compare-agree, 0 compare-disagree. Runtime exact counts: one exact1 candidate at distance5 258, seven exact0 candidates at distance5 419, 428, 432, 452, 480, 486, and 529.

## Pair Gate Diagnosis

| gate/checkpoint | evidence source | pass count | reject/drop count | main reject reason | candidate examples |
|---|---|---:|---:|---|---|
| second-hop pair escape gate | guided pool `pair_gate_kept_escape`, `pair_gate_failed_escape` | 0 | 0 | no local/hard escape candidates reached the gate | none |
| pair escape source status | guided pool `pair_escape_source_statuses` | 0 | 3 pair lanes | `profile_source_empty` for `0,1`, `0,2`, `0,3` | no escape candidates emitted |
| projected source filtering | guided pool `pair_escape_source_reject_reasons` | 4 projected values reached pair pool, others ranked out or distance explosive | multiple projected values | ranked out or `projected_generated_but_distance_explosive` | values like `88`, `91`, `125`, `126`, `68`, `72` |
| selected pair set | guided pool `pair_set_comparison_summary` | primary selected | alternate not selected | both sets had 0 gate-kept, 0 near, 0 wide, 0 borderline escape candidates | primary pairs `0,1`, `0,2`, `0,3` |
| refine selection | `frontier_refine_2` result | exact2 and exact1 retained | no better second-hop candidate found | no compare-agree better candidate available to select | `78d540...` remains exact2 best |

This is not a gate/drop bug. The second-hop pair stage produced preserve-neighbor and triad candidates, but no escape lane candidate reached the pair gate. The best generated/validated candidate was the already-known exact1 `5a3e7f46ddd474d0`.

## Candidate Comparison

| candidate | source stage | role | compare agree | runtime exact | distance5 | selected for refine? | reason |
|---|---|---|---:|---:|---:|---:|---|
| `78d540b49c590770` | pairscan / final refine | `best_overall` | true | 2 | 246 | yes | retained as exact2 best and selected candidate |
| `5a3e7f46ddd474d0` | frontier guided 1, second-hop validation, `frontier_refine_2` | `exact1_frontier` | true | 1 | 258 | yes | remains best exact1, reappears as second-hop best |
| `5a3f7f46ddd474d0` | projected preserve handoff anchor | `validated_projected_preserve_second_hop` | true in prior validation | 0 | 740 | anchor only | used to launch second-hop, not present as improved output |
| `5a3f7fc2ddd474d0` | second-hop triad/top entry | `validated_projected_preserve_second_hop` | true | 0 | 419 | validation only | best new exact0, still worse than exact1 |
| `343f7f46ddd474d0` | second-hop triad/top entry | `validated_projected_preserve_second_hop` | true | 0 | 428 | validation only | worse than exact1 |

No unvalidated `top_entries`, `pair_frontier_pool`, or `triad_frontier_pool` candidate beats exact1. No second-hop candidate beats exact2.

## Code Audit

Reviewed the narrow strategy path only:

- `_validated_projected_preserve_second_hop_candidates()` keeps the metadata-gated, compare-agree handoff rule and does not admit compare-disagree candidates.
- `_frontier_continuation_candidates()` correctly allows second-hop continuation only when the first frontier converges with `distance_not_improved` and iteration budget remains.
- The pair pool path records `pair_stage_stats`, `pair_drop_reasons`, escape lane diagnostics, and pair-set comparison data. In this run, `pair_drop_reasons` is empty because no gate candidate was produced, not because a candidate was dropped.
- `frontier_refine_2` retained `78d540b49c590770` and `5a3e7f46ddd474d0`; no higher-quality second-hop candidate was available for selection.

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"` | `5 passed, 50 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `55 passed` |

No full harness was run, per plan.

## Classification And Next Step

Classification: `candidate_quality_insufficient_after_projected_winner`.

Next default direction:

- Do not modify pair gate or refine selection based on this audit.
- Do not expand beam, budget, topN, timeout, or frontier iteration limit.
- Do not return to blind search.
- Next useful work is a separate decision on candidate-source quality: either improve the second-hop value source/projection hypothesis, or audit the transform/profile assumptions that make projected preserve candidates collapse back to exact1 noise.
