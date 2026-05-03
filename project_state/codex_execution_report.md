# CODEX_EXECUTION_REPORT

## Summary

Implemented the planned real bounded exact2 basin SMT pass.

The previous run only emitted `exact2_basin_smt` as diagnostic metadata. This iteration makes that payload executable: `solve_targeted_prefix8()` now accepts bounded byte value pools, the compare-aware strategy runs a second SMT pass in `smt_exact2_basin`, and exact2-basin validations are included in final candidate aggregation when a candidate is produced.

Final harness run:

```text
samplereverse_exact2_basin_smt_20260503
```

## Implemented Changes

| area | change | behavior impact |
|---|---|---|
| Z3 targeted solver | Added optional `value_pools` to `solve_targeted_prefix8()` and constrain selected bytes to those pools while preserving the base byte | bounded SMT search now honors diagnostic value pools |
| compare-aware SMT | `run_compare_aware_smt()` accepts override byte/nibble positions and value pools | exact2-basin diagnostic positions can be executed directly |
| exact2 basin pass | Added a second SMT run under `smt_exact2_basin` when `exact2_basin_smt.recommended=true` | primary frontier SMT is preserved; exact2 pass is additive |
| artifacts/metadata | Primary SMT payload and exact2-basin SMT payload both record attempted status, paths, evidence, top entries, validation candidates, and validations | reporting now reflects the real bounded pass |
| tests | Added coverage for value pool propagation and exact2-basin SMT execution | strategy regression coverage increased |

## Harness Result

| item | value |
|---|---|
| run | `samplereverse_exact2_basin_smt_20260503` |
| status | completed, 1 case, 0 errors |
| primary SMT | base `5a3e7f46ddd474d0`, `targeted z3 finished with unknown` |
| exact2 basin SMT | base `78d540b49c590770`, `targeted z3 finished with unknown` |
| exact2 value pools | `0:78`, `1:d5/3e/3c`, `2:40/7f/80`, `3:b4/8f`, `4:9c` |
| exact2 validation candidates | none |
| runtime best improved? | no |
| current runtime best | `78d540b49c59077041414141414141`, exact2 / distance5 246 |
| selected flag note | model/candidate scoring still selected bare `flag{`; not a runtime improvement |

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier"` | `26 passed, 32 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `58 passed` |
| `python -m pytest -q` | `140 passed` |
| `python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_exact2_basin_smt_20260503 --analysis-mode "Auto"` | completed, 0 errors |
| `python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_exact2_basin_smt_20260503` | passed; compact state manually corrected because auto classifier reverted to old pair-gate label |

## Classification

Classification: `candidate_quality_insufficient_after_exact2_basin_smt`.

Evidence:
- The exact2-basin pass executed with the intended bounded byte/nibble positions and value pools.
- Both primary SMT and exact2-basin SMT returned `unknown`, producing no validation candidates.
- No compare-agree exact3+ candidate and no distance5 improvement below 246 appeared.

Residual risk: `unknown` is not an unsat proof. The next useful direction is not increasing timeout by default, but auditing why a tiny value-pool problem still routes through a heavy symbolic RC4 objective.

## Next Step

Audit the SMT unknown path with the existing tiny value pools. Prefer a deterministic bounded value-pool execution/evaluation path, or a smaller solver formulation, over increasing timeout or returning to blind search.
