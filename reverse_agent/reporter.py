from __future__ import annotations

import platform
import re
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from .dynamic_templates import get_analysis_template
from .pipeline import SolveResult


@dataclass
class ReportRules:
    puzzle_type: str
    has_affine: bool
    affine_a: int = 3
    affine_b: int = 7
    affine_mod: int = 26
    length_check: int | None = None
    dash_positions: list[int] | None = None


def write_report(result: SolveResult, reports_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = Path(result.resolved_path).stem.replace(" ", "_")
    path = reports_dir / f"{ts}_{safe_name}_solve_report.md"

    template = get_analysis_template(result.analysis_mode)
    explanation = _extract_model_explanation(result.model_output, result.selected_flag)
    rules = _detect_report_rules(result, explanation)
    candidate_table = _build_candidate_table(result.candidates, result.selected_flag)
    tool_artifacts_text = _build_tool_artifacts_block(result)
    address_ctx = _build_address_context(result, explanation)
    env_block = _build_environment_block(result)
    concise_explanation = _first_lines(explanation, 40)
    selected_flag = _sanitize_text_weak(result.selected_flag)
    input_value = _sanitize_path_field(result.input_value)
    resolved_path = _sanitize_path_field(result.resolved_path)
    model_name = _sanitize_text_weak(result.model_name)
    yaml_meta = _build_yaml_meta(result, selected_flag)

    content = f"""---
{yaml_meta}
---

# Reverse CTF Writeup（新手友好版）

## 0x00 题目信息
- **输入**: `{input_value}`
- **解析后文件**: `{resolved_path}`
- **分析模式**: `{result.analysis_mode}`
- **模型**: `{model_name}`
- **提取字符串数量**: `{result.extracted_strings_count}`

## 0x01 最终答案（先看这个）
- ✅ **最终 Flag**: **`{selected_flag}`**

## 0x02 观察与定位（给新手）
1. 先从输入点与比较点入手，确认题型为：`{rules.puzzle_type}`。  
2. 再锁定长度检查、字符过滤（固定符号位）和核心算式，并给出证据编号（见 0x06）。  
3. 最后做“逆推 + 正向回代”双验证，避免只靠格式猜测答案。  

> **本章结论**：优先证据链（E1/E2/E3）而不是“看起来像 flag”。

## 0x03 逻辑流程图（文字流）
```text
{_build_flow_diagram(rules)}
```

## 0x04 关键伪代码还原（Decompiled Code）
```cpp
bool verify_flag(const std::string& input_flag) {{
    if (input_flag.size() != {rules.length_check or 24}) return false;  // 长度校验

    const int dash_pos[] = {{{", ".join(str(x) for x in (rules.dash_positions or [9, 12, 16, 19, 21]))}}};  // 字符过滤
    for (int p : dash_pos) {{
        if (input_flag[p] != '-') return false;
    }}

    std::string transformed = input_flag;
    for (char& encrypted_char : transformed) {{
{_build_transform_code(rules)}
    }}

    return transformed == target_cipher_text;
}}
```

## 0x05 核心算法推导（含示例）
{_build_math_section(rules)}

> **本章结论**：逆运算必须可逐字符复算，且能回代到目标密文。

## 0x06 地址与函数上下文标注（工具对照）
{address_ctx}

> **本章结论**：每个关键地址都要能落到具体函数与作用点。

## 0x07 工具证据与环境
### 分析模板
```text
{template}
```

### 工具摘要（已去敏）
{tool_artifacts_text if tool_artifacts_text else "- 未启用工具链自动分析"}

### 环境
{env_block}

> **本章结论**：工具输出只保留可复现实证据，不保留本机敏感细节。

## 0x08 候选答案对比与排除
{candidate_table}

> **本章结论**：候选应按证据强度排序，不应只按“格式像不像”。

## 0x09 推导摘要与复现提示
### 推导摘要
```text
{concise_explanation}
```

### 复现建议
```python
# 1) 先按长度与固定符号位过滤输入
# 2) 仅对字母做仿射逆变换 x = 9*(y-7) mod 26
# 3) 正向回代 y = (3*x+7) mod 26 验证与目标密文一致
```
"""
    path.write_text(content, encoding="utf-8")
    return path


def _extract_model_explanation(model_output: str, selected_flag: str) -> str:
    lines = [line for line in model_output.splitlines() if line.strip()]
    if not lines:
        return "模型未返回有效解释。"
    filtered: list[str] = []
    selected = selected_flag.strip().lower()
    for line in lines:
        val = line.strip().strip("`")
        if selected and val.lower() == selected:
            continue
        if re.fullmatch(r"(?:flag|ctf|key)\{[^\r\n}]{1,300}\}", val, re.IGNORECASE):
            continue
        filtered.append(line)
    rest = "\n".join(filtered).strip()
    return _sanitize_text_weak(rest) or "模型未给出额外解释，建议结合“关键证据链”手动复核。"


def _build_candidate_table(candidates: list[str], selected: str) -> str:
    rows = ["| rank | candidate | selected | confidence |", "|---:|---|---|---:|"]
    ranked = candidates[:] if candidates else []
    if selected and selected not in ranked:
        ranked.insert(0, selected)
    if not ranked:
        rows.append("| - | - | - | - |")
        return "\n".join(rows)
    for idx, item in enumerate(ranked, start=1):
        is_selected = "yes" if item == selected else "no"
        confidence = "0.95" if item == selected else f"{max(0.30, 0.80 - (idx * 0.1)):.2f}"
        rows.append(
            f"| {idx} | `{_escape_table(item)}` | {is_selected} | {confidence} |"
        )
    return "\n".join(rows)


def _build_tool_artifacts_block(result: SolveResult) -> str:
    return "\n".join(
        [
            (
                f"- **工具**: {item.tool_name}\n"
                f"  - 启用: {item.enabled}\n"
                f"  - 尝试执行: {item.attempted}\n"
                f"  - 成功: {item.success}\n"
                f"  - 摘要: {_sanitize_text_weak(item.summary or '-')}\n"
                f"  - 命令: `{_sanitize_path_field(item.command or '-')}`\n"
                f"  - 输出文件: `{_sanitize_path_field(item.output_path or '-')}`\n"
                f"  - 错误: `{_sanitize_text_weak(item.error or '-')}`\n"
                f"  - 证据样本: {_sanitize_text_weak(', '.join(item.evidence[:8]) if item.evidence else '-')}"
            )
            for item in result.tool_artifacts
        ]
    )


def _first_lines(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines] + ["...（以下省略）"])


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|")


def _sanitize_path_field(value: str) -> str:
    if not value:
        return value
    return re.sub(r"[A-Za-z]:[\\/][^\s`'\"]+", "<LOCAL_PATH>", value)


def _sanitize_text_weak(value: str) -> str:
    if not value:
        return value
    text = value
    text = re.sub(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\s`'\"]+", "<LOCAL_USER>", text)
    text = re.sub(r"/(?:Users|home)/[^/\s`'\"]+", "<LOCAL_USER>", text)
    return text


def _build_address_context(result: SolveResult, explanation: str) -> str:
    ida_funcs: dict[str, str] = {}
    for item in result.tool_artifacts:
        for ev in item.evidence:
            if not ev.startswith("IDA函数: "):
                continue
            name = ev.split(":", 1)[1].strip()
            m = re.match(r"sub_([0-9A-Fa-f]+)$", name)
            if not m:
                continue
            ida_funcs[m.group(1).lower()] = name

    seen: set[str] = set()
    rows: list[str] = ["| 证据ID | 地址 | 函数名(工具) | 说明 |", "|---|---|---|---|"]
    evidence_id = 1
    for m in re.finditer(r"0x([0-9A-Fa-f]{4,16})", explanation):
        full = f"0x{m.group(1)}"
        key = m.group(1).lower()
        if full in seen:
            continue
        seen.add(full)
        fn = ida_funcs.get(key, "未在自动证据中命名")
        rows.append(
            f"| E{evidence_id} | `{full}` | `{fn}` | IDA 反汇编上下文中出现 |"
        )
        evidence_id += 1
        if len(rows) >= 12:
            break

    if len(rows) == 2:
        rows.append("| - | - | - | 当前输出未提取到可标注地址 |")
    return "\n".join(rows)


def _build_environment_block(result: SolveResult) -> str:
    libs = {"requests"}
    model_output = result.model_output.lower()
    if "gmpy2" in model_output:
        libs.add("gmpy2")
    if "secret" in model_output:
        libs.add("Secret")
    ida_used = any(a.tool_name == "IDA" and a.success for a in result.tool_artifacts)
    ida_line = "IDA Pro 7.x（依据工具产物）" if ida_used else "未启用 IDA 自动化"
    return (
        f"- Python: `{platform.python_version()}`\n"
        f"- 逆向工具: `{ida_line}`\n"
        f"- 第三方库: `{', '.join(sorted(libs))}`"
    )


def _build_yaml_meta(result: SolveResult, selected_flag: str) -> str:
    return "\n".join(
        [
            "report_type: reverse_ctf_writeup",
            f"analysis_mode: {result.analysis_mode}",
            f"model: \"{_sanitize_text_weak(result.model_name)}\"",
            f"flag: \"{selected_flag}\"",
            f"strings_count: {result.extracted_strings_count}",
        ]
    )


def _detect_report_rules(result: SolveResult, explanation: str) -> ReportRules:
    text = f"{result.model_output}\n{explanation}".lower()
    has_affine = "mod 26" in text or "affine" in text or "(3*x+7)" in text or "(3 * x + 7)" in text
    if has_affine:
        puzzle_type = "仿射替换 + 密文比较"
    elif "md5" in text or "sha" in text or "hash" in text:
        puzzle_type = "哈希校验"
    elif "strcmp" in text or "memcmp" in text or "compare" in text:
        puzzle_type = "直接比较/表驱动比较"
    else:
        puzzle_type = "通用校验逻辑"

    length_check = None
    m_len = re.search(r"(?:长度|len|size)\D{0,20}(24|0x18)", text)
    if m_len:
        value = m_len.group(1)
        length_check = int(value, 16) if value.startswith("0x") else int(value)

    dash_positions = None
    m_pos = re.search(r"\[(\d+(?:\s*,\s*\d+)*)\]", result.model_output)
    if m_pos:
        values = [int(x.strip()) for x in m_pos.group(1).split(",")]
        if 2 <= len(values) <= 12:
            dash_positions = values

    return ReportRules(
        puzzle_type=puzzle_type,
        has_affine=has_affine,
        length_check=length_check,
        dash_positions=dash_positions,
    )


def _build_flow_diagram(rules: ReportRules) -> str:
    if rules.has_affine:
        return "输入获取 -> 格式与长度预检查 -> 字母位仿射处理 -> 密文对比"
    return "输入获取 -> 格式与长度预检查 -> 规则处理/特征变换 -> 目标校验"


def _build_transform_code(rules: ReportRules) -> str:
    if not rules.has_affine:
        return (
            "        // 非仿射题型：此处应替换为本题真实变换逻辑\n"
            "        // e.g. 哈希、异或、查表、状态机等\n"
        )
    return (
        f"        if ('a' <= encrypted_char && encrypted_char <= 'z') {{\n"
        "            int x = encrypted_char - 'a';\n"
        f"            encrypted_char = static_cast<char>('a' + ({rules.affine_a} * x + {rules.affine_b}) % {rules.affine_mod});\n"
        "        } else if ('A' <= encrypted_char && encrypted_char <= 'Z') {\n"
        "            int x = encrypted_char - 'A';\n"
        f"            encrypted_char = static_cast<char>('A' + ({rules.affine_a} * x + {rules.affine_b}) % {rules.affine_mod});\n"
        "        }\n"
    )


def _build_math_section(rules: ReportRules) -> str:
    if not rules.has_affine:
        return (
            "```text\n"
            "未检测到稳定的仿射特征，建议按“输入变换函数”逐步逆推：\n"
            "1) 明确输入域与输出域\n"
            "2) 写出正向关系 f(x)\n"
            "3) 求逆或枚举逆映射得到 x\n"
            "4) 正向回代验证 f(x) == target\n"
            "```\n"
        )
    return (
        "```python\n"
        f"# 已知加密: y = ({rules.affine_a}*x + {rules.affine_b}) mod {rules.affine_mod}\n"
        "# 逆运算:  x = 9*(y - 7) mod 26\n"
        "# 因为 3*9 = 27 ≡ 1 (mod 26)\n"
        "```\n\n"
        "以字符 `w -> f` 为例：  \n"
        "1. `w` 的索引 `y = 22`（a=0, b=1, ...）。  \n"
        "2. `x = 9 * (22 - 7) mod 26 = 9 * 15 mod 26 = 135 mod 26 = 5`。  \n"
        "3. 索引 5 对应 `f`，因此逆推结果正确。"
    )
