---
name: samplereverse-frontier
description: Use together with reverse-agent-iteration when the user is solving or optimizing samplereverse.exe, samplereverse, compare-aware search, exact1/exact2 frontier work, L15(prefix8), or samplereverse frontier/refine/SMT artifacts. This is the sample-specific layer that records the current samplereverse facts, baselines, constraints, and next default direction without replacing the generic reverse-agent iteration workflow.
metadata:
  short-description: Samplereverse frontier handoff facts
---

# Samplereverse Frontier

Use this skill only after the generic reverse-agent iteration workflow has established the latest artifacts. This skill supplies the sample-specific facts and guardrails for `samplereverse.exe`.

## Fixed Mainline

- Keep the main search line locked to `L15(prefix8)`.
- Fixed suffix remains `AAAAAAA`.
- Runtime compare truth should use 64-byte CompareProbe capture when available.
- Do not reopen older length windows, blind brute force, or the old `sample_solver` path unless fresh compare/runtime evidence explicitly invalidates the `L15(prefix8)` line.

## Current Baselines

- Best exact2 runtime-consistent basin:
  - anchor: `78d540b49c590770`
  - candidate form: `78d540b49c59077041414141414141`
  - runtime: `runtime_ci_exact_wchars=2`, `runtime_ci_distance5=246`
- Best exact1 runtime-consistent frontier:
  - anchor: `5a3e7f46ddd474d0`
  - candidate form: `5a3e7f46ddd474d041414141414141`
  - runtime: `runtime_ci_exact_wchars=1`, `runtime_ci_distance5=258`
- Recent exact1 frontier status:
  - `local_escape` generation works.
  - borderline handoff works.
  - latest stall is `frontier_refine`, not `pair_pool`.
  - current borderline candidates are too low quality: they enter handoff but have distance around `558+`, so runtime does not improve.

## Latest Artifact To Prefer

Prefer the newest matching run under `solve_reports/harness_runs/`. As of the current handoff, the most relevant run is:

- `samplereverse_exact1_borderline_escape_20260423`

Important files inside that run:

- `reports/tool_artifacts/samplereverse/samplereverse_compare_aware_frontier_summary.json`
- `reports/tool_artifacts/samplereverse/samplereverse_compare_aware_strata_summary.json`
- `reports/tool_artifacts/samplereverse/frontier_guided_1_5a3e7f46ddd474d0/samplereverse_compare_aware_guided_pool_result.json`

## Default Next Direction

Do not keep widening the exact1 gate. The next default direction is exact1 borderline quality:

- Split borderline local escapes into `near_local_escape` and `wide_local_escape`.
- Let only near-local candidates enter the main pair frontier/refine handoff.
- Keep wide-local candidates as diagnostics only.
- Add a single-byte guard before pair escape combinations so pair generation does not create distance-exploding candidates.
- Prioritize lower `ci_distance5/raw_distance10`, small mutation radius, and preserved local structure over simply increasing borderline count.

## Acceptance Criteria

Minimum useful progress for the next real run:

- `exact1_near_local_escape_count > 0`, or
- best exact1 borderline offline distance improves below the current weak borderline region, or
- `best exact1 runtime_ci_distance5 < 258`, or
- any runtime candidate reaches `runtime_ci_exact_wchars >= 2`.

If none of these happen, the next diagnosis should answer whether the failure is from single-byte guard strictness, pair neighborhood quality, refine handoff, or SMT value selection.

## Regression Expectations

- Run focused compare-aware strategy tests after code edits.
- Run full `python -m pytest -q` when feasible.
- For real regression, use the known target path when present:
  - `E:\xwechat_files\wxid_9ky6h8wz58b912_a1e5\msg\file\2026-04\samplereverse.exe`
- If the harness fails later in model fallback with the known GBK encoding issue, inspect compare-aware artifacts before discarding the run.
