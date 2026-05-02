# CODEX_EXECUTION_REPORT

## Summary

本轮按计划完成 exact2 seed `78d540b49c590770` 的 source quality 审计。没有修改 strategy、profile、transform 或测试代码；没有运行新 harness。

结论：`78d540b49c590770` 没有发现可修复的 source metadata 丢失、lane 误分类或 compare-agree 候选被错误排除。它是 profile 传入的首位 seed anchor，并被 bridge/pairscan、seed-guided validation、frontier refine 一致保留为当前 best exact2。后续 guided/frontier 变体一旦偏离该 anchor，质量会快速退化到 exact1/exact0。因此当前卡点仍是候选质量/假设边界，不是 gate、loop、validation ordering 或 artifact emission。

## Pre-Audit Checks

| check | result |
|---|---|
| initial git status | clean |
| latest indexed run | `samplereverse_second_hop_loop_fix_verify_20260502` |
| latest harness summary | `error_cases=0`, `candidate_quality=1.0`, `evidence_coverage=1.0` |
| current best exact2 | `78d540b49c59077041414141414141`, runtime exact2 / distance5 246 |
| projected preserve status | `5a3f7f46ddd474d0` downgraded, runtime exact0 / distance5 740 |

## Exact2 Source Quality Table

| stage | candidate | role / lane | runtime exact | distance5 | source implication |
|---|---|---|---:|---:|---|
| profile seed | `78d540b49c590770` | first configured anchor | n/a | n/a | intentional bounded seed, not discovered by blind expansion this run |
| bridge pairscan | `78d540b49c590770` | `pairscan`, positions `0,1` | exact2 | 246 | pairscan agrees with base anchor and finds no better pair/triad/quartet/quint candidate |
| bridge validation | `78d540b49c590770` | top1 | exact2 | 246 | offline and runtime metrics agree; compare semantics agree |
| seed guided validation | `78d540b49c590770` | `exact2_seed`, source `78d...` | exact2 | 246 | base anchor survives guided pool unchanged; nearby guided mutations drop to exact0 |
| frontier1 guided | `5a3e7f46ddd474d0` | `exact1_frontier`, source `78d...` | exact1 | 258 | best frontier output improves local shape but loses one exact wchar versus exact2 seed |
| frontier2 guided | `5a3e7f46ddd474d0` | second-hop output from projected preserve | exact1 | 258 | second-hop mostly recovers known exact1; new variants remain exact0 |
| final refine2 | `78d540b49c590770` | retained global best | exact2 | 246 | refine keeps exact2 seed above all exact1/exact0 alternatives |

## Exact2 vs Projected Preserve

| candidate | source path | best validated result | interpretation |
|---|---|---|---|
| `78d540b49c590770` | profile seed -> bridge pairscan -> seed guided -> refine | exact2 / distance5 246 | stable local basin; any useful next work must explain this basin rather than mutate around exact1 blindly |
| `5a3e7f46ddd474d0` | exact2 seed -> guided frontier | exact1 / distance5 258 | useful reference frontier, but not a better source than exact2 |
| `5a3f7f46ddd474d0` | projected preserve handoff -> second-hop guided | exact0 / distance5 740 as anchor; best second-hop recovery exact1 / 258 | projected preserve is locally unproductive and should stay downgraded |
| `5a3f7fc2ddd474d0` | second-hop triad | exact0 / distance5 419 | best new second-hop value, still below exact1/exact2 |

## Source And Code Audit

- `SamplereverseProfile` passes `78d540b49c590770` as the first configured anchor. This is expected sample-specific profile state, not a generic strategy bug.
- `run_compare_aware_bridge()` evaluates all byte pairs around the base anchor, records `hot_positions=[0,1]`, and validates the single pairscan winner. It does not discard a better bridge candidate; triad/quartet/quint stages had no higher-quality candidate in the artifact.
- `_bridge_progress()` correctly does not treat exact2 / distance5 246 as solved, because it only stops early on runtime exact3+ or distance below the baseline. This preserves the exact2 seed while still allowing downstream frontier/refine work.
- `_diverse_validation_candidates()` and `_frontier_anchor_candidates()` keep exact2/exact1/exact0 representatives. Existing tests cover this representative selection; no lane classification bug was found.
- `frontier_guided_1` had real local escape candidates and kept exact1. `frontier_guided_2` reports `profile_source_empty` for escape lanes and only recovers exact1 through preserve behavior, so the second-hop failure is source quality, not gate loss.

## Classification

Classification: `exact2_seed_source_quality_audited_no_source_bug`.

No code fix is recommended. The evidence supports “candidate quality / transform-profile hypothesis boundary” rather than an implementation defect in pairscan, bridge validation, frontier selection, or second-hop emission.

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "exact2 or frontier or pairscan"` | `23 passed, 32 deselected` |

No full harness was run, per plan.

## Next Step

Next default direction:

- Audit the transform/profile boundary for the exact2 basin, using `78d540b49c590770` as the stable reference and `5a3e7f46ddd474d0` / `5a3f7f46ddd474d0` as contrast cases.
- Focus on why the current compare-aware score preserves only the `flag{` prefix shape but cannot progress beyond exact2, rather than expanding search budgets.
- Do not promote `5a3f7f46ddd474d0`; do not return to blind search; do not run a new harness until a concrete transform/profile hypothesis or small source fix is identified.
