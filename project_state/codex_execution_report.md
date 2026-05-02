# CODEX_EXECUTION_REPORT

## Summary

本轮按计划完成 second-hop candidate source / projection hypothesis 审计。没有修改 strategy 或测试代码，没有重复运行 harness。

结论：`frontier_guided_2_5a3f7f46ddd474d0` 的二跳候选源没有发现非预算型 source bug。`profile_source_empty` 不是 pair gate 漏收，而是当前 bad anchor 的 preserve 邻域已经回落到已知 exact1，escape lane 没有可用 profile escape entries。projected values 确实生成并进入 pair/triad pool，但 validated 结果仍只产生已知 exact1 或更差 exact0。因此 `5a3f7f46ddd474d0` 应降级为“已验证但无收益”的局部方向，下一轮单一推荐方向是：回到 exact2 seed `78d540b49c590770` 做候选源审计，不回 blind search。

## Pre-Audit Checks

| check | result |
|---|---|
| initial git status | clean |
| latest indexed run | `samplereverse_second_hop_loop_fix_verify_20260502` |
| latest guided artifact | `frontier_guided_2_5a3f7f46ddd474d0` present |
| current bottleneck | `frontier_exact1 / candidate_quality_insufficient_after_projected_winner` |
| previous gate/drop audit | no pair gate, refine selection, or final selection bug found |

## Source Quality Summary

| source | generated count | validated count | compare-agree count | best exact | best distance5 | dominant failure reason | conclusion |
|---|---:|---:|---:|---:|---:|---|---|
| `top_entries` | 16 | 8 | 8 | 1 | 258 | best is existing exact1 | no improvement over known exact1/exact2 |
| `validation_candidates` | 8 | 8 | 8 | 1 | 258 | validated set has no better candidate | sufficient negative evidence |
| `pair_frontier_pool` | 8 | 1 | 1 | 1 | 258 | only validated agree candidate is `5a3e7f46ddd474d0` | pair source collapses to known exact1 |
| `triad_frontier_pool` | 8 | 6 | 6 | 0 | 419 | triads are compare-agree but runtime exact0 | quality insufficient, not gate loss |
| `pair_escape_source` | 0 effective entries | 0 | 0 | n/a | n/a | `profile_source_empty`; no local/hard escape entries reached gate | no source to repair without changing hypothesis |
| projected value source | 24 projected values generated; 6 reached pair pool | validated through pair/triad/top entries | compare-agree validations only | 0 for new values | 419 | 14 distance-explosive, 5 local-compatible ranked out, 6 reached pool but did not improve | ranking behaved as designed; no better source was ranked out |

Important projection details:

- Projected winners were selected for pairs `0,1`, `0,2`, and `0,3`; examples include value `92` at position `0`, value `62` at position `1`, and value `128` at position `2`.
- `projected_generated_but_distance_explosive` is supported by single-byte quality scoring; it is not just a diagnostic label.
- Values that reached pair pool still failed runtime quality: best new compare-agree triad was `5a3f7fc2ddd474d0`, exact0 / distance5 419.
- `pair_escape_source_values` contains far lineage/source-anchor values, but `_exact1_neighbor_value_maps()` only projects local-compatible values into bounded escape maps; the remaining far values are intentionally recorded as too far, not silently discarded.

## Source Transition Comparison

| candidate | role | source path | exact | distance5 | source implication | keep / downgrade / investigate |
|---|---|---|---:|---:|---|---|
| `78d540b49c590770` | exact2 best | pairscan / bridge / final refine | 2 | 246 | strongest runtime-consistent candidate; bridge and pairscan both point here | keep and investigate as next source anchor |
| `5a3e7f46ddd474d0` | exact1 frontier | exact2 seed -> refine -> guided frontier; reappears in second-hop | 1 | 258 | best second-hop output is just known exact1 recovery | keep as reference, not a new direction |
| `5a3f7f46ddd474d0` | projected preserve handoff / second-hop anchor | exact1 projected preserve lane | 0 | 740 | anchor executed but starts from a runtime-regressed point | downgrade |
| `5a3f7fc2ddd474d0` | best new second-hop triad | second-hop triad from projected/preserve pool | 0 | 419 | new source improves over bad anchor but not over exact1/exact2 | do not promote |
| `343f7f46ddd474d0` | guided_pool exact0 | second-hop guided/top entry | 0 | 428 | broad guided mutation remains below exact1 | do not promote |

## Source And Code Audit

- `_exact1_neighbor_value_maps()` creates preserve neighbors, local escape neighbors, and bounded projected local values. The artifact shows these were generated and recorded; there is no evidence of missing metadata or a dropped compare-agree winner.
- `_diverse_pair_frontier_pool()` reports `profile_source_empty` when `pair_profile_escape_entries` is empty. In this run, preserve entries exist and the best preserve entry is the known exact1 `5a3e7f46ddd474d0`; no effective escape entries survive as profile candidates.
- `_triad_frontier_pool()` composes triads from the pair pool and position profiles. It produced 8 candidates, 6 validated compare-agree, all exact0. The best new candidate was distance5 419.
- `frontier_refine_2` retained exact2 `78d540b49c590770` and exact1 `5a3e7f46ddd474d0`; no higher-quality second-hop candidate was available.
- No code fix is recommended. There is no evidence that a legal compare-agree source was misclassified, prematurely ranked out, or excluded from second-hop pool.

## Hypothesis Ranking

| hypothesis | evidence for | evidence against | expected next evidence | risk | recommendation |
|---|---|---|---|---|---|
| continue projected preserve source with source-quality fix | projected values are generated and some reach pool | validated outputs are exact1 fallback or exact0; no source bug found | would need a specific misclassified source, currently absent | high chance of repeating local noise | do not continue now |
| return to exact2 seed source audit, without blind search | exact2 `78d540b49c590770` remains best runtime candidate and bridge/pairscan agree | previous exact1 frontier work has not improved it | source-quality table around exact2 seed, especially positions/pair sources that led to exact2 | low; bounded and evidence-driven | recommended next |
| profile escape source definition insufficient | second-hop reports `profile_source_empty` for `0,1`, `0,2`, `0,3` | source values exist but are far/projected; no local/hard escape candidate shows runtime promise | compare exact2 source profile against exact1/projected preserve source profile | medium; could become profile-specific | investigate only through exact2 seed audit |
| transform/profile boundary assumption needs audit | projected preserve collapses despite compare-agree semantics | current known transform still agrees at compare/runtime level | boundary audit only if exact2 source audit cannot explain candidate quality | medium; broader blast radius | defer |
| candidate quality insufficient and no code change should be made | all second-hop validations agree but fail to beat exact1/exact2 | none | updated state and next action | low | accepted classification |

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "second_hop_composition or validated_projected_preserve_handoff or second_frontier_guided_round"` | `5 passed, 50 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `55 passed` |

No full harness was run, per plan.

## Classification And Next Step

Classification: `candidate_quality_insufficient_after_projected_winner`.

Projected preserve status: downgraded to a validated but locally unproductive direction.

Next default direction:

- Audit exact2 seed source quality for `78d540b49c590770`, using existing bridge/pairscan/frontier artifacts first.
- Compare exact2 seed source lanes against the projected preserve lane to identify why exact2 preserves runtime exact2 while projected preserve collapses.
- Do not expand beam, budget, topN, timeout, or frontier iteration limit.
- Do not return to blind search.
- Do not run a new harness until an exact2-source hypothesis or small source fix is identified.
