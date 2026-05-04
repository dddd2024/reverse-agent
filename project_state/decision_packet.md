# DECISION_PACKET

Generated: 2026-05-04

## 1. Goal

本轮目标：

```text
scripted_breakpoint_probe_for_samplereverse_base64_rc4_construction
```

当前 `pre_rc4_material_probe` 已完成，但分类为：

```text
pre_rc4_probe_unavailable
```

这说明上一轮基于当前自动 harness 的 memory-scan lower-level probe 已经尝试过，但没有捕获到 `pre-RC4 / Base64 / RC4 key` 材料。当前 exact2 最优仍未改善：

```text
candidate_hex = 78d540b49c59077041414141414141
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
compare_semantics_agree = true
```

因此，本轮不要继续候选搜索，不要重复 memory scan，不要重复 compare-site hook。下一步应把“手工断点”转化成可复现的脚本化断点：先静态定位 Base64/RC4 构造点，再用 Frida / IDA batch / x64dbg 脚本在这些构造点 dump 参数、缓冲区、key、长度，并生成结构化 artifact。

本轮成功不以生成新候选为目标，而以获得以下任一证据为目标：

```text
1. RC4 key pointer / key bytes / key length
2. RC4 input pointer / input bytes / input length
3. Base64 output buffer / length / alphabet / padding behavior
4. UTF-16LE expanded payload buffer / length
5. RC4 PRGA output buffer before compare
6. exact2 wchar index 2 failure byte dependency chain
```

---

## 2. Current Evidence

当前已知主线：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前瓶颈：

```text
stage = pre_rc4_material_probe
reason = pre_rc4_probe_unavailable
confidence = medium
```

最新 `pre_rc4_material_probe` 状态：

```text
classification = pre_rc4_probe_unavailable
candidate_count = 3
runtime_backed_count = 3
rc4_key_status = unknown
rc4_input_status = unknown
next_bounded_action = switch to IDA/x64dbg manual breakpoints for Base64/RC4 construction points
```

上一轮 memory scan 没有捕获到：

```text
raw_input = unavailable
expanded_bytes = unavailable
utf16le_payload = unavailable
base64_material = unavailable
rc4_ksa_key = unavailable
rc4_encrypted_const = unavailable
rc4_output = unavailable
compare_buffer = unavailable
```

但 exact2 失败点已有更具体记录：

```text
wchar index = 2
runtime word = 4464
target word = 6100
encrypted const bytes = 8f3b
keystream bytes = cb5f
```

这说明下一轮应该围绕 RC4 keystream / encrypted const / input bytes 的生成链进行构造点断点，而不是再扫描 compare 之后的内存。

---

## 3. Do Not Do

Codex 不要做：

```text
1. Do not return to old sample_solver blind search.
2. Do not only increase guided_pool beam or budget.
3. Do not increase topN, timeout, or frontier iteration limits.
4. Do not use compare_semantics_agree=false candidates as primary frontier.
5. Do not repeat exact2 basin value-pool evaluation.
6. Do not repeat H1/H3 fixed boundary candidate set.
7. Do not repeat current transform_trace_consistency audit without new runtime evidence.
8. Do not repeat memory-scan lower-level pre-RC4/key material probe with current automatic harness.
9. Do not repeat compare-site-only dynamic_compare_path_probe.
10. Do not scan entire solve_reports.
11. Do not commit full solve_reports.
12. Do not create a broad candidate generator before obtaining Base64/RC4 construction evidence.
```

Important: The next step is not “manual GUI operation” as an instruction. Codex should convert the manual-breakpoint idea into a scripted breakpoint probe.

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

Primary implementation files:

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
tests/test_tool_runners.py
tests/test_project_state.py
```

Runtime / tool integration files:

```text
reverse_agent/tool_runners.py
reverse_agent/olly_scripts/compare_probe.py
reverse_agent/olly_scripts/pre_rc4_material_probe.py
reverse_agent/ida_scripts/collect_evidence.py
reverse_agent/transforms/samplereverse.py
reverse_agent/profiles/samplereverse.py
```

If present, inspect runtime / harness wrappers:

```text
reverse_agent/harness*
reverse_agent/runtime*
reverse_agent/validators*
reverse_agent/tools*
reverse_agent/pipeline.py
```

Artifact reading policy:

```text
Read only the latest artifacts referenced by artifact_index.json.
Do not scan full solve_reports.
Do not read full PROJECT_PROGRESS_LOG.txt unless project_state is insufficient or a strategic historical review is explicitly needed.
```

---

## 5. Required Audit

This round requires a scripted-breakpoint feasibility audit plus bounded implementation.

### A. Locate candidate Base64 / RC4 construction points statically

Codex must first inspect static evidence and identify candidate construction points before writing runtime hooks.

Required outputs:

```text
1. Candidate Base64 encode function or call site address / offset.
2. Candidate UTF-16LE expansion or wide-string conversion site address / offset.
3. Candidate RC4 KSA/init function or call site address / offset.
4. Candidate RC4 PRGA/encrypt/decrypt loop or call site address / offset.
5. Candidate encrypted const location / access site.
6. Confidence for each candidate point: high / medium / low.
```

If symbol names are unavailable, use instruction signatures and call contexts:

```text
Base64 clues:
- alphabet table references
- division/modulo by 3 or 4
- padding '=' handling
- output alphabet index table

RC4 clues:
- 256-byte S-box initialization
- loops over 0..255
- KSA swap pattern
- PRGA i/j update and swap
- xor with keystream byte
```

### B. Convert manual breakpoints into scripted breakpoints

Codex should implement a diagnostic script that attaches to the target and hooks the located construction points. Prefer Frida if practical because existing probes already use Frida + pywinauto. IDA batch or x64dbg script is acceptable if Frida cannot target the needed point.

Suggested script name:

```text
reverse_agent/olly_scripts/base64_rc4_breakpoint_probe.py
```

Suggested strategy function:

```text
run_base64_rc4_breakpoint_probe()
```

Suggested artifact:

```text
solve_reports/.../tool_artifacts/samplereverse/base64_rc4_breakpoint_probe/base64_rc4_breakpoint_probe.json
```

The probe must not promote candidates and must not modify ranking.

### C. Hook points and dump requirements

At minimum attempt to hook these points, where locatable:

```text
1. UTF-16LE expansion / wide payload construction
2. Base64 encode input
3. Base64 encode output
4. RC4 KSA / key setup
5. RC4 PRGA input/output
6. Compare pre-call buffer as a final cross-check only
```

For each hooked point, dump:

```text
address / module_offset
hit_count
candidate_hex
register snapshot if available
stack argument preview
pointer arguments
length arguments
buffer preview hex
buffer preview ascii / utf16 when applicable
confidence that this point is UTF16/Base64/RC4-related
```

RC4 KSA dump requirements:

```text
key_ptr
key_len
key_preview_hex
key_preview_ascii
whether key is static / runtime-constructed / input-derived / unknown
```

RC4 input/output dump requirements:

```text
input_ptr
input_len
input_preview_hex
input_preview_ascii
output_ptr
output_len
output_preview_hex
first bytes relevant to exact2 failure
```

Base64 dump requirements:

```text
input_ptr
input_len
input_preview_hex
output_ptr
output_len
output_preview_hex
output_preview_ascii
alphabet evidence
padding evidence
```

### D. Exact2 failure dependency trace

Use the known exact2 failure:

```text
wchar index = 2
runtime word = 4464
target word = 6100
encrypted const bytes = 8f3b
keystream bytes = cb5f
```

Codex must try to map this failure backward:

```text
1. Which RC4 output bytes form runtime word 4464?
2. Which encrypted const bytes were XORed with keystream bytes cb5f?
3. Which PRGA positions generated cb5f?
4. Which RC4 input/base64 bytes influence these PRGA positions, if the implementation is input-dependent?
5. Which original candidate bytes influence those Base64 bytes?
6. Does this produce a bounded constraint for candidate byte(s), or does it prove the key/input model is still unknown?
```

Do not turn this into broad search. This is a dependency-trace diagnostic.

---

## 6. Implementation Scope

Allowed:

```text
1. Add one diagnostic script for scripted breakpoints.
2. Add one strategy method that invokes the diagnostic.
3. Add project_state indexing for the new artifact.
4. Add focused tests for schema, boundedness, and non-promotion.
5. Add small static helper routines for identifying Base64/RC4 candidate offsets if needed.
```

Not allowed:

```text
1. Broad searcher.
2. More guided_pool expansion.
3. More exact2 basin pool mutation.
4. Full solve_reports scan.
5. Reusing memory scan as the primary evidence source.
6. Using compare_semantics_agree=false candidates as a breakthrough path.
```

Suggested artifact schema:

```json
{
  "classification": "breakpoint_probe_complete | breakpoint_probe_partial | breakpoint_probe_unavailable",
  "candidate_count": 3,
  "runtime_backed_count": 0,
  "static_points": {
    "utf16le_candidate_points": [],
    "base64_candidate_points": [],
    "rc4_ksa_candidate_points": [],
    "rc4_prga_candidate_points": [],
    "encrypted_const_points": []
  },
  "hook_results": {
    "utf16le_payload": "available | unavailable | inferred",
    "base64_input": "available | unavailable | inferred",
    "base64_output": "available | unavailable | inferred",
    "rc4_key": "available | unavailable | inferred",
    "rc4_input": "available | unavailable | inferred",
    "rc4_output": "available | unavailable | inferred",
    "compare_buffer": "available | unavailable | inferred"
  },
  "rc4_key": {
    "status": "confirmed | unknown | contradicted",
    "key_ptr": "",
    "key_len": null,
    "key_preview_hex": "",
    "source_hypothesis": "static | runtime_constructed | input_derived | unknown"
  },
  "rc4_input": {
    "status": "confirmed | unknown | contradicted",
    "input_ptr": "",
    "input_len": null,
    "input_preview_hex": "",
    "input_preview_ascii": "",
    "matches_offline_base64": null
  },
  "base64_material": {
    "status": "confirmed | unknown | contradicted",
    "input_preview_hex": "",
    "output_preview_ascii": "",
    "alphabet": "standard | custom | unknown",
    "padding_behavior": "kept | stripped | unknown"
  },
  "exact2_failure_trace": {
    "wchar_index": 2,
    "runtime_word": "4464",
    "target_word": "6100",
    "encrypted_const_bytes": "8f3b",
    "keystream_bytes": "cb5f",
    "prga_positions": [],
    "candidate_byte_dependencies": [],
    "bounded_constraint": ""
  },
  "findings": [],
  "next_bounded_action": ""
}
```

Candidate set must remain fixed:

```text
78d540b49c59077041414141414141
78d540b49c59077040414141414141
5a3e7f46ddd474d041414141414141
```

At most two additional control candidates are allowed only if they are needed to distinguish pointer/length behavior. Total candidate count must not exceed 5.

---

## 7. Tests

Minimum tests:

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q tests/test_tool_runners.py
python -m pytest -q tests/test_project_state.py
python -m pytest -q
```

Add or update tests:

```text
test_base64_rc4_breakpoint_probe_has_bounded_candidate_count
test_base64_rc4_breakpoint_probe_does_not_promote_candidates
test_base64_rc4_breakpoint_probe_does_not_expand_search_budget
test_base64_rc4_breakpoint_probe_records_static_points
test_base64_rc4_breakpoint_probe_records_hook_results
test_base64_rc4_breakpoint_probe_records_exact2_failure_trace
test_project_state_indexes_base64_rc4_breakpoint_probe
```

Run harness with a new run name:

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_base64_rc4_breakpoint_probe_20260504 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

Then update project state:

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_base64_rc4_breakpoint_probe_20260504
python -m reverse_agent.project_state status
```

---

## 8. Stop Conditions

Stop immediately and report if:

```text
1. Need to return to blind search.
2. Need to expand beam / budget / topN / timeout / frontier iterations.
3. Need to repeat exact2 value-pool.
4. Need to repeat H1/H3 boundary set.
5. Need to repeat transform_trace_consistency without new runtime evidence.
6. Need to repeat memory-scan lower-level pre-RC4 probe as the primary method.
7. Need to scan full solve_reports.
8. Can only use compare_semantics_agree=false candidates.
9. No Base64/RC4 construction point can be located statically or dynamically.
```

Successful stop conditions:

```text
1. At least one Base64 or RC4 construction point is located with evidence.
2. Scripted breakpoint probe captures RC4 key bytes or proves key is still unavailable.
3. Scripted breakpoint probe captures RC4 input/Base64 material or proves it is unavailable.
4. Exact2 failure trace is connected to PRGA/encrypted-const bytes more tightly than previous report.
5. A JSON artifact is generated and indexed by project_state.
6. If scripted breakpoints cannot be implemented in the current environment, report the exact missing capability and provide concrete manual x64dbg/IDA breakpoint addresses/instructions for the human operator.
```

---

## GPT Decision Summary

Current automatic harness-local probing is exhausted for pre-RC4 material. The next step is not more search and not another memory scan.

Next Codex action:

```text
Implement a Base64/RC4 construction-point scripted breakpoint probe.
```

The intended workflow is:

```text
1. Static audit locates candidate UTF-16LE/Base64/RC4 construction points.
2. Codex writes Frida/IDA/x64dbg-style scripted breakpoints for those points.
3. The script dumps key/input/output/length buffers into a structured artifact.
4. Project state records whether RC4 key and RC4 input are confirmed, contradicted, or still unknown.
```

If RC4 key or RC4 input is captured, the next round can derive a bounded candidate constraint and may break exact2. If no construction point can be hooked, the next round should move to human-assisted IDA/x64dbg with explicit addresses and breakpoint instructions, not further automated candidate search.
