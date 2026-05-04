# DECISION_PACKET

Generated: 2026-05-04

## 1. Goal

Resolve the current `samplereverse` stall after `h1_h3_boundary_contrast_exhausted_no_gain`.

The next goal is **not** to expand search. The goal is to test one bounded hypothesis:

```text
The current exact2 plateau may be caused by an incorrect offline transform model around the UTF-16LE/Base64/RC4/compare boundary, rather than by candidate-value selection.
```

Codex should audit whether the compare-aware pipeline is faithfully modeling the runtime path before generating new candidates.

Current best remains:

```text
exact2 / distance5 246
candidate_hex = 78d540b49c59077041414141414141
compare_semantics_agree = true
```

Latest H1/H3 boundary validation did not improve over exact2.

---

## 2. Current Evidence

Known mainline:

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

Current best exact2 candidate:

```text
78d540b49c59077041414141414141
```

with:

```text
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
compare_semantics_agree = true
```

The latest H1/H3 validation tested 8 fixed boundary candidates and concluded:

```text
h1_h3_boundary_contrast_exhausted_no_gain
```

Best runtime candidate remained exact2 / distance5 246, so the boundary-contrast set is exhausted.

Negative results already include:

```text
exact2 basin value-pool evaluation exhausted
H1/H3 fixed 8-candidate boundary contrast exhausted
old blind search blocked
beam/budget expansion blocked
compare_semantics_agree=false primary frontier blocked
```

---

## 3. Do Not Do

Codex must not:

```text
1. Return to old sample_solver blind search.
2. Increase beam, budget, topN, timeout, or frontier iteration limit.
3. Promote compare_semantics_agree=false candidates.
4. Repeat the exact2 basin value-pool evaluation.
5. Repeat the fixed 8-candidate H1/H3 boundary contrast set.
6. Commit full solve_reports.
7. Treat model-selected bare flag{ as a runtime improvement.
8. Generate a large new candidate pool before completing the transform audit.
```

---

## 4. Files To Inspect

Primary files:

```text
reverse_agent/transforms/samplereverse.py
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
```

Project-state files:

```text
project_state/current_state.json
project_state/task_packet.json
project_state/negative_results.json
project_state/artifact_index.json
project_state/codex_execution_report.md
```

Target artifacts only, not full `solve_reports`:

```text
h1_h3_boundary_validation.json
h1_h3_boundary_validation/runtime validation json
profile_transform_hypothesis_matrix.json
compare_probe.json
bridge_validation.json
pairscan_summary.json
frontier_summary.json
strata_summary.json
smt_result.json
smt_exact2_basin_result.json
```

Use `artifact_index.json` paths for these. Do not scan the entire report tree.

---

## 5. Required Audit

Codex should perform a focused audit with these questions.

### A. UTF-16LE audit

Check whether offline transform uses exactly the same bytes as runtime:

```text
input character -> UTF-16LE bytes -> Base64 bytes
```

Specifically inspect:

```text
ASCII candidate char -> [char_byte, 00]
input length in wchar vs byte length
null terminator handling
whether runtime includes or excludes final 00 00
```

Codex should produce a table for candidate prefixes length 1-10:

```text
wchar_len
utf16le_hex
base64_text
base64_len
base64_remainder_mod4
rc4_input_len
```

### B. Base64 boundary audit

The previous H1/H3 contrast tested a fixed boundary set, but it did not prove the full input space. Codex should determine whether the current model assumes the wrong Base64 chunk alignment.

Check:

```text
whether runtime Base64 encodes UTF-16LE bytes directly
whether Base64 output includes padding =
whether padding is stripped before RC4
whether newline or null byte is included
whether the compare target starts at RC4 byte 0
```

### C. RC4 audit

Check whether the RC4 implementation matches runtime exactly:

```text
key bytes
KSA initialization
PRGA first-byte discard or no discard
signed/unsigned byte behavior
state reset per candidate or reused state
input type: Base64 ASCII bytes vs decoded Base64 bytes
```

The main failure pattern may be: exact2 is real, but the offline model becomes wrong after two compared wide chars.

### D. Compare audit

Check whether `exact_wchars` is computed against the same unit as runtime:

```text
byte compare vs wchar compare
case-sensitive compare
comparison stops at null byte or explicit length
whether target is "flag{" as ASCII, UTF-16LE, or post-RC4 bytes
```

Codex should verify whether `distance5 = 246` is computed against the same five logical characters that runtime uses.

---

## 6. Implementation Scope

Codex should add **one diagnostic mode**, not a new search mode.

Suggested scope:

```text
add a transform_trace_consistency diagnostic for samplereverse
```

This diagnostic should:

```text
1. Take 3-5 known candidates:
   - 78d540b49c59077041414141414141
   - 78d540b49c59077040414141414141
   - 5a3e7f46ddd474d041414141414141
2. Emit stage-by-stage bytes:
   - raw input bytes
   - UTF-16LE bytes
   - Base64 bytes/string
   - RC4 output bytes
   - compare window bytes
   - expected target bytes
   - exact_wchars calculation
   - distance5 calculation
3. Compare offline trace with runtime validation artifacts.
4. Report the first stage where the model can no longer be justified by evidence.
```

No candidate promotion unless the audit identifies a concrete mismatch.

---

## 7. Tests

Required tests:

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q
```

Add targeted tests if diagnostic code is added:

```text
test_samplereverse_transform_trace_contains_utf16le_base64_rc4_compare_stages
test_samplereverse_transform_trace_is_deterministic
test_samplereverse_transform_trace_does_not_expand_search_budget
```

If a transform mismatch is found, add a regression test that proves the corrected transform changes the trace for the current exact2 candidate.

---

## 8. Stop Conditions

### Stop A: Transform mismatch found

Report:

```text
mismatch stage
old assumption
runtime-supported correction
minimal code change required
whether current exact2 candidate should be revalidated
```

Do not launch a broad search yet.

### Stop B: Transform model confirmed correct

Report:

```text
all audited stages match current assumptions
exact2 plateau is likely not caused by transform modeling
next bounded hypothesis recommendation
```

### Stop C: Evidence insufficient

Report exactly which artifact is missing or ambiguous.

Do not infer from absent data.

---

## Practical Expectation

This round is not expected to solve the flag directly. It should answer this question:

```text
Is exact2 stalled because candidates are weak, or because the transform/compare model is wrong?
```

If the model is wrong, the next round may quickly move to exact3+.

If the model is correct, the current route is in a deeper bottleneck and the next step should be a different bounded hypothesis, not expanded search.
