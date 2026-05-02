# CODEX_EXECUTION_REPORT

## Summary

本轮按计划实现 exact2 basin transform/profile boundary 的 diagnostic-only 改动。没有扩大 beam、budget、topN、timeout 或 frontier iteration limit；没有运行新 harness；没有修改 final candidate selection 规则。

结论：当前 evidence 仍支持 `78d540b49c590770...` 作为 best exact2。新增诊断会在后续 compare-aware artifacts / metadata 中记录 per-wchar boundary breakdown，并在 primary SMT 选择 exact1 frontier 时记录一个 `exact2_basin_smt` diagnostic payload，说明 exact2 basin 若作为诊断 base 会使用哪些 variable positions/value pools。该 payload 不运行额外 runtime validation，不替换 best。

## Implemented Changes

| area | change | behavior impact |
|---|---|---|
| prefix boundary diagnostics | 新增 per-wchar breakdown helper，记录 raw/target UTF-16 pair、distance、continuous exact、wide-structure metrics | metadata only |
| runtime validation | 每条 `validate_compare_aware_results()` validation 附加 `prefix_boundary` | metadata only |
| frontier/refine metadata | `frontier_summary`、`strata_summary`、strategy metadata 和 search artifact payload 增加 `prefix_boundary_diagnostics` | metadata only |
| SMT diagnostics | primary SMT payload 增加 `prefix_boundary`；当 primary base 不是 exact2 且存在 compare-agree exact2 时，附加 `exact2_basin_smt` diagnostic | metadata only |
| tests | 增加 boundary breakdown 和 exact2-basin SMT diagnostic 单测 | no runtime behavior change |

## Boundary Evidence

| candidate | runtime prefix pairs | runtime exact | distance5 | interpretation |
|---|---|---:|---:|---|
| `78d540b49c590770` | `4600 6c00 4464 830d 311c` | 2 | 246 | stable exact2 basin: `f`, `l` match |
| `5a3e7f46ddd474d0` | `4600 6135 7f0b 8c68 8502` | 1 | 258 | exact1 frontier: only `f` matches |
| `5a3f7f46ddd474d0` | `7493 4b15 6ba6 9ef3 370f` | 0 | 740 | projected preserve anchor collapses before first wchar |
| `5a3f7fc2ddd474d0` | `854a e01a bc18 692c 7505` | 0 | 419 | best new second-hop candidate remains exact0 |

## Classification

Classification: `transform_profile_boundary_diagnostics_added`.

Current best did not improve:

- exact2 remains `78d540b49c59077041414141414141`, runtime exact2 / distance5 246.
- exact1 remains `5a3e7f46ddd474d041414141414141`, runtime exact1 / distance5 258.
- `5a3f7f46ddd474d0` remains downgraded and must not be promoted.

The current inability to reach exact3+ is not proven to be a scorer bug yet. The next artifact-producing run should use the new diagnostics to decide between:

- candidate quality insufficient,
- transform/profile boundary missing a useful signal,
- SMT base choice needing a bounded exact2-basin diagnostic execution.

## Commands

| command | result |
|---|---|
| `python -m pytest -q tests/test_compare_aware_search_strategy.py -k "smt or exact2 or boundary or frontier"` | `26 passed, 31 deselected` |
| `python -m pytest -q tests/test_compare_aware_search_strategy.py` | `57 passed` |
| `python -m pytest -q` | `139 passed` |

No full harness was run, per plan.

## Next Step

Next default direction:

- Run or audit the next compare-aware execution that produces the new `prefix_boundary_diagnostics` and `exact2_basin_smt` payload.
- If diagnostics show exact2-basin SMT has promising bounded variable positions, implement a real exact2 diagnostic pass without changing timeouts or selection.
- If diagnostics show no exact2-basin signal, classify as `candidate_quality_insufficient_after_transform_boundary` and defer code changes until a new profile hypothesis exists.
