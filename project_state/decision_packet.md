# DECISION_PACKET

Generated: 2026-05-04

## 1. Goal

本轮目标：

```text
run_focused_dynamic_probe_for_samplereverse_compare_path
```

Current `transform_trace_consistency` has confirmed that five runtime-backed candidates agree with the offline `UTF-16LE/Base64/RC4/compare` trace, with `mismatches = 0`. Therefore, the exact2 plateau is not explained by an audited transform-model mismatch.

The next step is to stop the local mutation route and use a different bounded evidence source:

```text
focused dynamic probe around compare path
```

The goal is to capture runtime-backed material around:

```text
pre-RC4 material
Base64 material
RC4 key / key schedule evidence
post-RC4 compare buffer
compare target
compare length / compare unit
```

Success is not measured by generating more candidates. Success is obtaining runtime evidence that explains why the path stalls after exact2.

---

## 2. Current Evidence

Current active strategy:

```text
CompareAwareSearchStrategy
```

Current known transform:

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

Current runtime best remains:

```text
candidate_hex = 78d540b49c59077041414141414141
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
compare_semantics_agree = true
```

Latest bottleneck:

```text
stage = transform_consistency
reason = transform_model_confirmed
confidence = medium
```

Latest Codex execution result:

```text
run = samplereverse_transform_trace_consistency_20260504
classification = transform_model_confirmed
candidates = 5
runtime-backed candidates = 5
mismatches = 0
missing runtime evidence = 0
current runtime best = exact2 / distance5 246
```

Already failed or blocked directions:

```text
old sample_solver blind search
only increase beam or budget
compare_semantics_agree=false candidates as primary frontier
exact2 basin value-pool evaluation
H1/H3 fixed 8-candidate boundary contrast set
repeat current 5-candidate transform trace consistency audit without new runtime evidence
```

---

## 3. Do Not Do

Codex must not:

```text
1. Do not return to old sample_solver blind search.
2. Do not increase beam, budget, topN, timeout, or frontier iteration limit.
3. Do not use compare_semantics_agree=false candidates as primary frontier.
4. Do not repeat exact2 basin value-pool evaluation.
5. Do not repeat the H1/H3 fixed 8-candidate boundary contrast set.
6. Do not repeat the current 5-candidate transform trace consistency audit.
7. Do not scan full solve_reports.
8. Do not commit full solve_reports.
9. Do not treat model-selected bare flag{ as runtime improvement.
10. Do not create a new broad candidate generator before obtaining new runtime evidence.
```

---

## 4. Files To Inspect

Must read first:

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
```

Primary code files:

```text
reverse_agent/strategies/compare_aware_search.py
reverse_agent/transforms/samplereverse.py
tests/test_compare_aware_search_strategy.py
```

If runtime / harness / debugger related implementation exists, inspect:

```text
reverse_agent/harness*
reverse_agent/runtime*
reverse_agent/validators*
reverse_agent/profiles/samplereverse.py
```

Read only key artifacts pointed to by `artifact_index.json`; do not scan the full `solve_reports` tree:

```text
transform_trace_consistency
compare_probe
bridge_validation
pairscan_summary
profile_transform_hypothesis_matrix
frontier_summary
strata_summary
summary
run_manifest
```

---

## 5. Required Audit

This round requires a focused dynamic probe feasibility audit plus minimal implementation.

### A. Locate runtime compare path

Codex should identify where runtime validation obtains:

```text
exact_wchars
distance5
compare_semantics_agree
selected flag / compare prefix
```

Required answers:

```text
1. Is exact_wchars derived from real process output or from the offline evaluator?
2. Which buffer / character unit is distance5 calculated from?
3. What runtime evidence supports compare_semantics_agree?
4. Can the current system locate runtime bytes before and after compare?
```

### B. Design dynamic probe points

Codex should add bounded probe points around these stages:

```text
raw input candidate
UTF-16LE encoded bytes
Base64 output bytes
RC4 input bytes
RC4 key material / KSA input
RC4 output bytes
compare buffer
compare target
compare length
```

The probe does not need to obtain every point in one round, but every point must be marked as available, unavailable, or inferred, with evidence source.

### C. Probe exact2 baseline and near controls

Probe at least these candidates:

```text
78d540b49c59077041414141414141
78d540b49c59077040414141414141
5a3e7f46ddd474d041414141414141
```

At most two additional control candidates are allowed. Total candidate count must not exceed 5.

For each candidate, output:

```text
candidate_hex
runtime exact_wchars
runtime distance5
runtime compare buffer preview
runtime compare target preview
runtime compare length / unit
pre-RC4 material if available
RC4 key material if available
whether evidence explains exact2 plateau
```

### D. Determine whether hidden runtime material differs from modeled material

Codex must answer:

```text
1. Is the RC4 key fully confirmed?
2. Is RC4 input confirmed as Base64 ASCII bytes?
3. Is the compare target confirmed as flag{ in the same encoding unit?
4. Does compare start at byte 0?
5. Is there an offset / prefix skip / wchar stride / null-stop behavior?
6. What is the first real runtime byte/word that fails after exact2?
```

---

## 6. Implementation Scope

Allowed: add one diagnostic.

Do not add a searcher.

Suggested function name:

```text
run_dynamic_compare_path_probe()
```

or:

```text
run_samplereverse_runtime_compare_probe()
```

Allowed artifact:

```text
solve_reports/.../tool_artifacts/samplereverse/dynamic_compare_path_probe/dynamic_compare_path_probe.json
```

Suggested artifact schema:

```json
{
  "classification": "dynamic_probe_complete | dynamic_probe_partial | dynamic_probe_unavailable",
  "candidate_count": 3,
  "runtime_backed_count": 3,
  "probe_points": {
    "pre_rc4": "available | unavailable | inferred",
    "rc4_key": "available | unavailable | inferred",
    "post_rc4_compare_buffer": "available | unavailable | inferred",
    "compare_target": "available | unavailable | inferred",
    "compare_length": "available | unavailable | inferred"
  },
  "findings": [],
  "next_bounded_action": ""
}
```

If the current harness cannot directly obtain runtime bytes, Codex should implement the minimal feasible probe or report why the evidence is unavailable. Do not convert this into broad search.

---

## 7. Tests

At minimum run:

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

If a diagnostic is added, add or update tests:

```text
test_dynamic_compare_path_probe_has_bounded_candidate_count
test_dynamic_compare_path_probe_does_not_expand_search_budget
test_dynamic_compare_path_probe_records_probe_point_availability
test_dynamic_compare_path_probe_preserves_existing_selection_behavior
```

Run harness with new run name:

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_dynamic_compare_path_probe_20260504 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

Then update project state:

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_dynamic_compare_path_probe_20260504
python -m reverse_agent.project_state status
```

---

## 8. Stop Conditions

Stop immediately and report if:

```text
1. Need to expand beam / budget / topN / timeout.
2. Need to return to blind search.
3. Need to repeat exact2 basin value-pool.
4. Need to repeat H1/H3 fixed boundary set.
5. Need to repeat transform trace consistency audit.
6. Cannot obtain any new runtime evidence.
7. Can only advance through compare_semantics_agree=false candidates.
8. Need to scan full solve_reports.
```

Successful stop conditions:

```text
1. Dynamic probe completes with at least 3 candidates having runtime-backed probe result.
2. Each probe point is marked available, unavailable, or inferred.
3. Report whether the exact2 plateau is explained by runtime compare path evidence.
4. If new offset / key / compare-length / compare-unit evidence is found, propose one next bounded hypothesis.
5. If no new evidence is obtainable, record a negative result and recommend stopping the current compare-aware local route.
```

---

## GPT Decision Summary

Do not continue local exact2 mutation. Do not re-check transform consistency. Both directions are exhausted or confirmed.

Next Codex action:

```text
focused dynamic compare-path probe
```

The core target is runtime evidence for:

```text
RC4 key / pre-RC4 material / post-RC4 compare buffer / compare target / compare length
```

If this round obtains real compare-path bytes, the next step may become a structural breakthrough instead of another local candidate search.

If this round cannot obtain new runtime evidence, the compare-aware pipeline has insufficient observability and the next strategy should move toward lower-level dynamic instrumentation or manual reversing, not more candidate search.
