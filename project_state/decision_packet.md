# DECISION_PACKET

Generated: 2026-05-04

## 1. Goal

本轮目标：

```text
lower_level_probe_for_samplereverse_pre_rc4_and_key_material
```

上一轮 `dynamic_compare_path_probe` 已完成，但结论是：当前 compare-site hook 只能看到 compare 调用附近的证据，不能直接暴露 `pre-RC4 / Base64 / RC4 key` 材料。当前 exact2 最优没有提升，仍停在：

```text
candidate_hex = 78d540b49c59077041414141414141
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
compare_semantics_agree = true
```

当前状态文件也明确给出下一步：

```text
move to lower-level dynamic instrumentation/manual reversing for pre-RC4 or key material
```

因此，本轮不要继续局部候选搜索，而是把任务切到低层动态插桩 / 手工逆向审计，目标是抓到真实运行时的 RC4 key、RC4 input、Base64 中间态或 UTF-16LE 编码后缓冲区。

---

## 2. Current Evidence

当前主线：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前最好候选：

```text
exact2 candidate:
78d540b49c59077041414141414141

runtime:
exact_wchars = 2
distance5 = 246
compare_semantics_agree = true
```

上一轮动态探针结果：

```text
classification = dynamic_probe_complete
candidate_count = 3
runtime_backed_count = 3
```

已确认可见：

```text
raw input
post-RC4 compare buffer
compare target
compare length
compare unit
```

仍未直接可见：

```text
pre-RC4 runtime material
Base64 material
RC4 input
RC4 key
UTF-16LE payload
```

这些关键点目前只是 `inferred` 或 `unavailable`，不是运行时实证。

上一轮还确认：

```text
compare target = wide flag{
compare count = 5 wide chars
no compare-site evidence of offset / prefix-skip / stride / null-stop
first runtime failure after exact2:
  wchar index = 2
  raw = 4464
  target = 6100
  distance = 103
```

这说明 compare 侧已经基本审完，下一步必须往 compare 之前追。

---

## 3. Do Not Do

Codex 不要做：

```text
1. 不要回到 old sample_solver blind search。
2. 不要只增加 beam / budget / topN / timeout。
3. 不要把 compare_semantics_agree=false 的候选作为主突破点。
4. 不要重复 exact2 basin value-pool evaluation。
5. 不要重复 H1/H3 fixed 8-candidate boundary contrast set。
6. 不要重复当前 transform_trace_consistency audit。
7. 不要重复当前 compare-site hook 的 dynamic_compare_path_probe。
8. 不要扫描完整 solve_reports。
9. 不要提交完整 solve_reports。
10. 不要新建广义 candidate generator。
```

这些方向已经被 `negative_results.json` 明确标记为不应重复。

---

## 4. Files To Inspect

必须先读：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
```

然后重点检查：

```text
reverse_agent/strategies/compare_aware_search.py
tests/test_compare_aware_search_strategy.py
tests/test_tool_runners.py
```

接着定位 runtime / compare / harness 相关实现：

```text
reverse_agent/harness*
reverse_agent/runtime*
reverse_agent/validators*
reverse_agent/tools*
reverse_agent/profiles/samplereverse.py
reverse_agent/transforms/samplereverse.py
```

只读 `artifact_index.json` 指向的关键 artifact，不要扫描整个 `solve_reports`。

---

## 5. Required Audit

本轮 Codex 要回答 4 个核心问题。

### A. RC4 key 是否真的已知？

必须确认：

```text
1. 当前 RC4 key 来源是什么？
2. 它是静态代码常量、运行时构造、输入派生，还是外部状态派生？
3. 当前离线模型使用的 key 是否和运行时 key 完全一致？
4. 是否存在宽字符、Base64、null byte、长度截断导致的 key 差异？
```

输出要求：

```json
{
  "rc4_key_status": "confirmed | inferred | unknown | contradicted",
  "evidence_source": "...",
  "runtime_key_preview": "...",
  "offline_key_preview": "...",
  "key_length": null
}
```

### B. RC4 input 是否真的是 Base64 ASCII bytes？

必须确认：

```text
1. RC4 输入缓冲区起点在哪里？
2. RC4 输入长度是多少？
3. 该输入是否等于 UTF-16LE(input) 后的 Base64 ASCII？
4. 是否存在额外前缀、后缀、padding、null terminator、长度 off-by-one？
```

输出要求：

```json
{
  "rc4_input_status": "confirmed | inferred | unknown | contradicted",
  "runtime_input_preview_hex": "...",
  "runtime_input_preview_ascii": "...",
  "offline_base64_preview": "...",
  "length_match": true
}
```

### C. UTF-16LE / Base64 中间态是否和模型一致？

必须确认：

```text
1. 原始输入进入程序后是否直接转 UTF-16LE？
2. UTF-16LE 后的字节序是否为 little-endian？
3. Base64 编码是否标准 Base64？
4. Base64 padding 是否保留？
5. 是否存在自定义 alphabet？
```

重点是排除这种情况：

```text
offline model: UTF-16LE -> standard Base64 -> RC4
runtime model: slightly different UTF-16/Base64/key/length
```

### D. exact2 失败点为什么是 wchar index 2？

已知失败点：

```text
index = 2
raw = 4464
target = 6100
```

Codex 需要追溯这个 `4464` 是如何由：

```text
candidate -> UTF-16LE -> Base64 -> RC4
```

生成的。

要求输出：

```text
1. index 2 的 post-RC4 word 由哪些 RC4 keystream bytes 产生？
2. 对应 RC4 input bytes 是什么？
3. 这些 bytes 来自 Base64 的哪个位置？
4. 这些 Base64 bytes 又来自原始 candidate 的哪些字节？
5. 是否能反推该位置需要的 candidate byte constraint？
```

这一步是本轮最重要的突破点。

---

## 6. Implementation Scope

允许做：

```text
1. 新增一个 lower-level diagnostic。
2. 新增或扩展 artifact schema。
3. 新增最小测试。
4. 新增手工逆向辅助脚本。
5. 对已有 compare probe 做只读式增强，但不能再重复当前 compare-site hook 逻辑。
```

建议新增函数名：

```text
run_pre_rc4_material_probe()
```

或：

```text
run_samplereverse_pre_rc4_key_probe()
```

建议新增 artifact：

```text
solve_reports/.../tool_artifacts/samplereverse/pre_rc4_material_probe/pre_rc4_material_probe.json
```

建议 artifact schema：

```json
{
  "classification": "pre_rc4_probe_complete | pre_rc4_probe_partial | pre_rc4_probe_unavailable",
  "candidate_count": 3,
  "runtime_backed_count": 0,
  "probe_points": {
    "raw_input": "available | inferred | unavailable",
    "utf16le_payload": "available | inferred | unavailable",
    "base64_material": "available | inferred | unavailable",
    "rc4_key": "available | inferred | unavailable",
    "rc4_input": "available | inferred | unavailable",
    "rc4_output": "available | inferred | unavailable",
    "compare_buffer": "available | inferred | unavailable"
  },
  "exact2_failure_trace": {
    "wchar_index": 2,
    "runtime_word": "4464",
    "target_word": "6100",
    "rc4_input_offsets": [],
    "keystream_offsets": [],
    "candidate_byte_dependencies": []
  },
  "findings": [],
  "next_bounded_action": ""
}
```

候选数量限制：

```text
默认只跑 3 个候选，最多 5 个。
```

固定候选：

```text
78d540b49c59077041414141414141
78d540b49c59077040414141414141
5a3e7f46ddd474d041414141414141
```

不要生成新的大候选池。

---

## 7. Tests

最低测试：

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q tests/test_tool_runners.py
python -m pytest -q tests/test_project_state.py
python -m pytest -q
```

如果新增 diagnostic，需要增加测试：

```text
test_pre_rc4_material_probe_has_bounded_candidate_count
test_pre_rc4_material_probe_records_probe_point_availability
test_pre_rc4_material_probe_does_not_promote_candidates
test_pre_rc4_material_probe_does_not_expand_search_budget
test_pre_rc4_material_probe_writes_artifact_schema
test_project_state_indexes_pre_rc4_material_probe
```

运行 harness：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_pre_rc4_material_probe_20260504 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

更新 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_pre_rc4_material_probe_20260504
python -m reverse_agent.project_state status
```

---

## 8. Stop Conditions

立即停止并报告，如果出现：

```text
1. 只能通过扩大搜索预算推进。
2. 需要回到 blind search。
3. 需要重复 exact2 value-pool。
4. 需要重复 H1/H3 boundary set。
5. 需要重复当前 compare-site hook。
6. 只能使用 compare_semantics_agree=false 候选。
7. 需要扫描完整 solve_reports。
8. 找不到任何 pre-RC4 / key / Base64 证据来源。
```

成功停止条件：

```text
1. 找到 RC4 key 的真实来源。
2. 或确认 RC4 input 的真实运行时缓冲区。
3. 或确认 Base64 / UTF-16LE 中间态和离线模型是否一致。
4. 或追溯 exact2 第 3 个 wchar 失败点的依赖链。
5. 或明确证明当前自动 harness 无法观测，需要转 IDA/x64dbg 手工断点。
```

---

## GPT Decision Summary

下一轮不要再做候选搜索。

本轮 Codex 的唯一主线是：

```text
从 compare 往前追，拿 pre-RC4 / Base64 / RC4 key 的真实证据。
```

优先级：

```text
1. RC4 key 来源
2. RC4 input 缓冲区
3. Base64 中间态
4. UTF-16LE 中间态
5. exact2 失败点反向依赖链
```

如果本轮能拿到 RC4 key 或 RC4 input 的真实运行时材料，就有机会从 exact2 平台进入 exact3+。如果拿不到，下一步应切到 IDA/x64dbg 手工逆向，而不是继续让 Codex 在现有 harness 内搜索。
