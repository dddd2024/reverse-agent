# DECISION_PACKET

Generated: 2026-05-05

## 1. Goal

本轮目标：

```text
static_construction_point_locator_for_samplereverse_base64_rc4
```

当前 `base64_rc4_breakpoint_probe` 已经完成一轮，但分类为：

```text
classification = breakpoint_probe_partial
```

关键结论：

```text
compare_buffer = available
utf16le_payload = unavailable
base64_input = unavailable
base64_output = unavailable
rc4_key = unavailable
rc4_input = unavailable
rc4_output = unavailable
```

这说明上一轮 Frida/scripted breakpoint probe 只验证了 compare-site，未找到可 hook 的 UTF-16LE/Base64/RC4 构造点。当前 static_points 中 Base64、encrypted_const、RC4 KSA、RC4 PRGA、UTF-16LE 都是 low confidence，并且没有有效 module_offset。

因此，本轮目标不是继续跑候选搜索，也不是重复当前 breakpoint probe，而是：

```text
定位真实 UTF-16LE / Base64 / RC4 construction point 的地址、module offset、调用上下文和断点方案。
```

本轮成功标准不是 exact2 -> exact3，而是产出一个可执行的人机交接 artifact：

```text
1. 明确的 IDA/x64dbg breakpoint 地址或 module_offset；
2. 每个地址的函数/基本块上下文；
3. 预期寄存器/栈参数/内存指针；
4. 对应要 dump 的 buffer、长度、key；
5. 如果仍找不到，给出精确的失败证据，而不是泛泛说 unavailable。
```

---

## 2. Current Evidence

当前主线仍为：

```text
input -> UTF-16LE -> Base64 -> RC4 -> compare flag{ prefix
```

当前最优候选：

```text
exact2:
candidate_hex = 78d540b49c59077041414141414141
candidate_prefix = 78d540b49c590770
compare_semantics_agree = true
runtime_ci_exact_wchars = 2
runtime_ci_distance5 = 246
source = pairscan

frontier / exact1:
candidate_hex = 5a3e7f46ddd474d041414141414141
candidate_prefix = 5a3e7f46ddd474d0
compare_semantics_agree = true
runtime_ci_exact_wchars = 1
runtime_ci_distance5 = 258
source = exact2_seed -> refine -> guided(frontier)
```

上一轮 Codex 已经实现并运行：

```text
reverse_agent/olly_scripts/base64_rc4_breakpoint_probe.py
run_base64_rc4_breakpoint_probe()
base64_rc4_breakpoint_probe.json
```

测试结果：

```text
python -m pytest -q tests/test_compare_aware_search_strategy.py  -> 76 passed
python -m pytest -q tests/test_tool_runners.py                  -> 10 passed
python -m pytest -q tests/test_project_state.py                  -> 11 passed
python -m pytest -q                                             -> 164 passed
```

Harness run：

```text
samplereverse_base64_rc4_breakpoint_probe_20260504
completed, 1 case, 0 errors
```

但 probe finding 是：

```text
construction hook hits = 0
rc4 key status = unknown
rc4 input/base64 status = unknown
```

并且当前 artifact_index 中：

```text
dynamic_compare_path_probe = null
pre_rc4_material_probe = null
h1_h3_boundary_validation = null
smt_validation = null
```

说明 project_state 目前没有新的动态路径证据可以支撑继续候选扩展。

---

## 3. Do Not Do

Codex 不要做：

```text
1. Do not return to old sample_solver blind search.
2. Do not only increase guided_pool beam or budget.
3. Do not increase topN, timeout, frontier iterations, or Copilot timeout.
4. Do not use compare_semantics_agree=false candidates as primary frontier.
5. Do not repeat exact2 basin value-pool evaluation.
6. Do not repeat H1/H3 fixed boundary candidate set.
7. Do not repeat transform_trace_consistency without new runtime evidence.
8. Do not repeat scripted Base64/RC4 breakpoint probe with current static access points.
9. Do not repeat compare-site-only probe.
10. Do not run another broad memory scan as the main method.
11. Do not scan full solve_reports.
12. Do not commit full solve_reports directory.
13. Do not create a new broad candidate generator.
```

特别注意：

```text
current static access points are not enough.
```

所以本轮不能只是再次调用：

```text
base64_rc4_breakpoint_probe.py
```

除非 Codex 先找到了新的、具体的、高置信度 module_offset / address。

---

## 4. Files To Inspect

Codex 必须先读：

```text
project_state/task_packet.json
project_state/current_state.json
project_state/artifact_index.json
project_state/negative_results.json
project_state/codex_execution_report.md
```

重点代码文件：

```text
reverse_agent/strategies/compare_aware_search.py
reverse_agent/olly_scripts/base64_rc4_breakpoint_probe.py
reverse_agent/olly_scripts/compare_probe.py
reverse_agent/olly_scripts/pre_rc4_material_probe.py
reverse_agent/ida_scripts/collect_evidence.py
reverse_agent/transforms/samplereverse.py
reverse_agent/profiles/samplereverse.py
reverse_agent/tool_runners.py
```

重点测试文件：

```text
tests/test_compare_aware_search_strategy.py
tests/test_tool_runners.py
tests/test_project_state.py
```

只读必要 artifact：

```text
solve_reports/harness_runs/samplereverse_base64_rc4_breakpoint_probe_20260504/summary.json
solve_reports/harness_runs/samplereverse_base64_rc4_breakpoint_probe_20260504/run_manifest.json
solve_reports/harness_runs/samplereverse_base64_rc4_breakpoint_probe_20260504/reports/tool_artifacts/samplereverse/base64_rc4_breakpoint_probe/base64_rc4_breakpoint_probe.json
solve_reports/harness_runs/samplereverse_base64_rc4_breakpoint_probe_20260504/reports/tool_artifacts/samplereverse/transform_trace_consistency/transform_trace_consistency.json
```

不要默认读取：

```text
full PROJECT_PROGRESS_LOG.txt
full solve_reports directory
full solve_reports historical runs
```

---

## 5. Required Audit

本轮 Required Audit 分成 4 个阶段。

### A. 审计上一轮 probe 为什么没有 construction hits

Codex 必须检查：

```text
1. base64_rc4_breakpoint_probe.py 中 static_points 的来源；
2. static_points 为什么全部 low confidence；
3. 为什么 standard Base64 alphabet not found；
4. 为什么 modeled encrypted const prefix not found；
5. RC4 KSA / PRGA signature 当前为什么 unresolved；
6. compare offset 0x258C 是否仍然有效；
7. 当前 Frida hook 方式是否只适合已有 offset，不适合发现 offset。
```

输出要求：

```json
{
  "previous_probe_failure_audit": {
    "compare_offset_valid": true,
    "construction_offsets_available": false,
    "reason_static_points_unhookable": [],
    "reason_no_base64_alphabet": "",
    "reason_no_rc4_signature": "",
    "next_required_evidence": "static disassembly locator"
  }
}
```

### B. 实现静态 construction point locator

本轮建议新增一个静态定位器，不直接做候选搜索。

建议文件：

```text
reverse_agent/ida_scripts/locate_base64_rc4_points.py
```

或者如果项目已有 IDA evidence collector，可以扩展：

```text
reverse_agent/ida_scripts/collect_evidence.py
```

该 locator 要输出：

```text
UTF-16LE construction candidates
Base64 encode candidates
RC4 KSA candidates
RC4 PRGA candidates
encrypted const candidates
compare-site backwards slice candidates
```

每个 candidate point 至少包含：

```json
{
  "kind": "utf16le | base64 | rc4_ksa | rc4_prga | encrypted_const | compare_backslice",
  "ea": "0x...",
  "module_offset": "0x...",
  "function": "",
  "basic_block": "",
  "confidence": "high | medium | low",
  "evidence": [],
  "breakpoint_type": "execute | memory_read | memory_write",
  "expected_registers": [],
  "expected_stack_args": [],
  "dump_plan": []
}
```

### C. 静态识别规则

Codex 必须至少审计以下规则，而不是只搜索字符串：

#### UTF-16LE construction

查找：

```text
1. 每个输入字节后写入 0x00 的循环；
2. MultiByteToWideChar / wide char 相关 API；
3. mov [dst + i*2], al / mov [dst + i*2 + 1], 0；
4. 字节输入长度 -> wide buffer 长度翻倍的逻辑。
```

#### Base64 construction

查找：

```text
1. 3-byte to 4-byte expansion；
2. shr / shl / and 0x3f；
3. output alphabet index；
4. '=' padding；
5. 输出长度约为 4 * ceil(n / 3)；
6. 即使没有标准 alphabet，也要查 custom alphabet 或动态构造 alphabet。
```

#### RC4 KSA

查找：

```text
1. 256-byte S-box 初始化；
2. for i in 0..255；
3. S[i] = i；
4. j = j + S[i] + key[i % keylen]；
5. swap S[i], S[j]。
```

#### RC4 PRGA

查找：

```text
1. i = i + 1；
2. j = j + S[i]；
3. swap S[i], S[j]；
4. keystream = S[S[i] + S[j]；
5. data_byte xor keystream。
```

#### compare-site backwards slice

从已有 compare buffer 出发，向前追：

```text
1. compare lhs buffer 的来源；
2. lhs buffer 写入点；
3. 写入点前的 xor；
4. xor 两侧分别是什么；
5. 哪一侧是 encrypted const，哪一侧是 keystream。
```

### D. 生成人工断点交接 artifact

建议 artifact：

```text
solve_reports/.../tool_artifacts/samplereverse/base64_rc4_static_locator/base64_rc4_static_locator.json
```

建议同时生成一个 Markdown 交接文件：

```text
solve_reports/.../tool_artifacts/samplereverse/base64_rc4_static_locator/manual_breakpoint_handoff.md
```

`manual_breakpoint_handoff.md` 要给人类操作者直接用：

```markdown
# Manual Breakpoint Handoff

## Target

samplereverse.exe

## Breakpoints

### 1. RC4 KSA candidate

Address / offset:
- VA: 0x...
- module_offset: 0x...

Why:
- evidence...

x64dbg:
```text
bp ...
```

IDA:
```python
ida_dbg.add_bpt(...)
```

When hit, dump:
- key pointer:
- key length:
- S-box pointer:
- input buffer:
```

如果找不到任何 construction point，也必须输出：

```text
No hookable construction point found.
```

但要说明：

```text
1. 搜索了哪些函数；
2. 搜索了哪些指令模式；
3. 找到了哪些近似但被排除的点；
4. 下一步需要人类在 IDA 中观察哪个函数/地址范围。
```

---

## 6. Implementation Scope

允许做：

```text
1. 新增一个静态定位脚本；
2. 新增一个 project_state artifact kind，例如 base64_rc4_static_locator；
3. 新增 manual_breakpoint_handoff.md；
4. 修改 project_state builder，让它索引新 artifact；
5. 增加测试，确保 locator 输出 schema 稳定；
6. 如果定位到新 module_offset，可选择性运行一次 base64_rc4_breakpoint_probe.py，但必须使用新 offset，不能重复旧 static_points。
```

不允许做：

```text
1. 新候选生成器；
2. 新 guided_pool；
3. 新 exact2 basin pool；
4. 扩大 beam / budget；
5. 大范围 solve_reports 扫描；
6. 把 compare_semantics_agree=false 作为突破口；
7. 只生成自然语言建议而没有 artifact。
```

---

## 7. Tests

最低测试：

```bash
python -m pytest -q tests/test_compare_aware_search_strategy.py
python -m pytest -q tests/test_tool_runners.py
python -m pytest -q tests/test_project_state.py
python -m pytest -q
```

新增或更新测试：

```text
test_base64_rc4_static_locator_schema
test_base64_rc4_static_locator_records_candidate_points
test_base64_rc4_static_locator_records_previous_probe_failure_audit
test_base64_rc4_static_locator_does_not_promote_candidates
test_base64_rc4_static_locator_does_not_expand_search_budget
test_project_state_indexes_base64_rc4_static_locator
test_manual_breakpoint_handoff_generated_when_offsets_found_or_missing
```

如果定位到新 offset，可以追加一次 bounded harness：

```powershell
python -m reverse_agent.harness --dataset .\samplereverse_exact1_projected_vs_neighbor_20260424.json --run-name samplereverse_base64_rc4_static_locator_20260505 --reports-dir solve_reports --analysis-mode "Auto" --model-type "Copilot CLI" --copilot-timeout-seconds 300 --ctf-skill-profile compact --case-id samplereverse-exact1-projected-vs-neighbor --no-resume
```

然后更新 project_state：

```powershell
python -m reverse_agent.project_state build --reports-dir solve_reports --sample samplereverse --run-name samplereverse_base64_rc4_static_locator_20260505
python -m reverse_agent.project_state status
```

---

## 8. Stop Conditions

立即停止并报告：

```text
1. 需要回到 blind search；
2. 需要扩大 beam / budget / topN / timeout；
3. 需要重复 exact2 value-pool；
4. 需要重复 H1/H3 boundary set；
5. 需要重复 current transform_trace_consistency；
6. 需要重复 current static_points 的 base64_rc4_breakpoint_probe；
7. 需要全量扫描 solve_reports；
8. 只能依赖 compare_semantics_agree=false candidate；
9. 静态 locator 无法访问目标二进制或 IDA evidence。
```

成功停止条件：

```text
1. 生成 base64_rc4_static_locator.json；
2. 生成 manual_breakpoint_handoff.md；
3. 至少给出一个高/中置信度 construction point；
4. 或者明确证明当前静态证据不足，并列出已排除的函数/模式；
5. project_state 能索引新 artifact；
6. 全部测试通过；
7. CODEX_EXECUTION_REPORT.md 说明下一步是：
   - 使用新 offset 运行 breakpoint probe；
   - 或由人类在 IDA/x64dbg 按 handoff 文件手动断点 dump。
```

---

## GPT Decision Summary

当前瓶颈不是“候选不够”，而是“缺少真实 Base64/RC4 构造点证据”。

上一轮 Codex 已经把手工断点方向做成了 scripted Frida probe，但由于没有有效 static point，construction hook hits 为 0。因此下一轮 Codex 不应重复运行同一个 probe，而应先完成：

```text
static construction point locator + manual breakpoint handoff
```

如果本轮能定位 RC4 KSA / PRGA / Base64 encode 的真实地址，下一轮再用这些新 offset 运行 breakpoint probe，才有可能抓到：

```text
RC4 key
RC4 input
Base64 output
encrypted const
PRGA keystream position
```

这些证据一旦到位，才可以把 exact2 的失败点：

```text
runtime word 4464 vs target word 6100
encrypted const bytes 8f3b
keystream bytes cb5f
```

转化成有约束的 byte-level 逆推。当前阶段不应继续搜索。
