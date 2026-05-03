# CODEX_EXECUTION_REPORT

## Summary

Implemented the SMT unknown audit and deterministic exact2 basin value-pool evaluator.

This iteration keeps the existing SMT timeout and search budgets unchanged. It records Z3 `reason_unknown()` and constraint-size diagnostics, then falls back to a bounded deterministic evaluator only when the exact2-basin SMT pass has no candidates and the diagnostic value-pool space is small enough.

Final harness run:

```text
samplereverse_exact2_basin_valuepool_eval_20260503
```

## Implemented Changes

| area | change | behavior impact |
|---|---|---|
| Z3 targeted solver | Records solver type, timeout, symbolic compare bytes, selected byte/nibble counts, value-pool sizes, estimated combinations, and `z3_reason_unknown` | SMT unknown artifacts now explain the tiny bounded search shape |
| Z3 decrypt helper | `_decrypt_prefix()` now supports a 10-byte default prefix length | SMT evidence aligns with the 5-wchar compare target |
| exact2 value-pool evaluator | Added deterministic enumeration for diagnostic exact2 pools, capped at 128 combinations | the 18-combo pool can be exhausted without increasing SMT timeout |
| runtime validation | Every generated value-pool candidate is sent through existing CompareProbe validation | only runtime-validated, compare-agree improvements are promotable |
| project state | Recorded `exact2_basin_value_pools_exhausted_no_gain` in compact state and negative results | next sessions should not repeat this exact pool branch |

## Harness Result

| item | value |
|---|---|
| run | `samplereverse_exact2_basin_valuepool_eval_20260503` |
| status | completed, 1 case, 0 errors |
| primary SMT | base `5a3e7f46ddd474d0`, `targeted z3 finished with unknown` |
| exact2 basin SMT | base `78d540b49c590770`, `targeted z3 finished with unknown`, `z3_reason_unknown=unknown` |
| exact2 value-pool combinations | 18 estimated, 18 generated, 18 unique |
| exact2 value-pool validation | 18 runtime validated |
| best runtime candidate in pool | `78d540b49c59077041414141414141`, exact2 / distance5 246 |
| improved over exact2? | no |
| classification | `exact2_basin_value_pools_exhausted_no_gain` |
| selected flag note | model/candidate scoring still selected bare `flag{`; not a runtime improvement |

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier or value"` | `37 passed, 23 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `60 passed` |
| `python -m pytest -q` | `142 passed` |
| `python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_exact2_basin_valuepool_eval_20260503 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume` | completed, 0 errors |
| `python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_exact2_basin_valuepool_eval_20260503` | passed; compact state manually corrected because auto classifier reverted to old pair-gate label |

## Classification

Classification: `exact2_basin_value_pools_exhausted_no_gain`.

Evidence:
- The exact2-basin SMT pass used the intended pools: `0:78`, `1:d5/3e/3c`, `2:40/7f/80`, `3:b4/8f`, `4:9c`.
- The SMT payload recorded `estimated_value_pool_combinations=18` and `z3_reason_unknown=unknown`.
- The deterministic evaluator generated and runtime-validated all 18 combinations.
- No candidate improved beyond exact2 or below distance5 246; the best remained `78d540b49c59077041414141414141`.

## Next Step

Stop this exact2 basin value-pool branch unless the diagnostic pools change. The next useful direction should be a new bounded profile/transform hypothesis or a different evidence source, not a repeat of this pool, a timeout increase, or blind search.
