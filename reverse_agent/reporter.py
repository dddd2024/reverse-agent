from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .dynamic_templates import get_analysis_template
from .pipeline import SolveResult


def write_report(result: SolveResult, reports_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = Path(result.resolved_path).stem.replace(" ", "_")
    path = reports_dir / f"{ts}_{safe_name}_solve_report.md"

    template = get_analysis_template(result.analysis_mode)
    explanation = _extract_model_explanation(result.model_output)
    candidate_table = _build_candidate_table(result.candidates, result.selected_flag)
    tool_artifacts_text = _build_tool_artifacts_block(result)
    prompt_short = _first_lines(result.prompt, 120)

    content = f"""# Reverse CTF Writeup（新手友好版）

## 0x00 题目信息
- **输入**: `{result.input_value}`
- **解析后文件**: `{result.resolved_path}`
- **分析模式**: `{result.analysis_mode}`
- **模型**: `{result.model_name}`
- **提取字符串数量**: `{result.extracted_strings_count}`

## 0x01 最终答案（先看这个）
- **最终 flag/答案**: **`{result.selected_flag}`**

## 0x02 题目目标（给新手）
这类题的核心目标是：找到程序内部真正参与校验的“期望值”或“变换规则”，再反推出可通过校验的输入。  
不要被大量无关字符串干扰，优先关注：比较函数、校验分支、失败提示、关键常量。

## 0x03 解题路线（参考优秀 writeup 结构）
1. **信息收集**：提取可打印字符串、函数/符号线索。  
2. **定位校验点**：围绕字符串比较、哈希比较、分支判断做证据聚合。  
3. **逆向还原**：根据常量和控制流推回正确输入。  
4. **结果验证**：结合工具证据与模型分析，选出唯一最可信答案。

## 0x04 使用的分析模板
```text
{template}
```

## 0x05 关键证据链
{tool_artifacts_text if tool_artifacts_text else "- 未启用工具链自动分析"}

## 0x06 候选答案对比与排除
{candidate_table}

## 0x07 逐步推导（模型解释，按 writeup 叙事）
```text
{explanation}
```

## 0x08 给新手的排错建议
1. 如果答案不稳定，先看是否只取了“看起来像 flag 的字符串”而没走到实际比较逻辑。  
2. 如果动态调试没结果，先确认断点是否下在 compare/check 前后，而不是入口附近。  
3. 如果候选很多，优先相信“有明确校验路径支持”的候选，而不是“格式像 flag”的候选。  
4. 如果是非 `flag{{}}` 题型，注意题目可能要求纯 token（如 `SEPTA`）。

## 0x09 本次发送给模型的 Prompt（节选）
```text
{prompt_short}
```

## 0x0A 模型原始输出（完整留档）
```text
{result.model_output}
```
"""
    path.write_text(content, encoding="utf-8")
    return path


def _extract_model_explanation(model_output: str) -> str:
    lines = [line for line in model_output.splitlines() if line.strip()]
    if not lines:
        return "模型未返回有效解释。"
    # 第一行通常是最终答案，后续保留为推导说明。
    rest = "\n".join(lines[1:]).strip()
    return rest or "模型未给出额外解释，建议结合“关键证据链”手动复核。"


def _build_candidate_table(candidates: list[str], selected: str) -> str:
    rows = ["| rank | candidate | selected |", "|---:|---|---|"]
    ranked = candidates[:] if candidates else []
    if selected and selected not in ranked:
        ranked.insert(0, selected)
    if not ranked:
        rows.append("| - | - | - |")
        return "\n".join(rows)
    for idx, item in enumerate(ranked, start=1):
        is_selected = "yes" if item == selected else "no"
        rows.append(f"| {idx} | `{_escape_table(item)}` | {is_selected} |")
    return "\n".join(rows)


def _build_tool_artifacts_block(result: SolveResult) -> str:
    return "\n".join(
        [
            (
                f"- **工具**: {item.tool_name}\n"
                f"  - 启用: {item.enabled}\n"
                f"  - 尝试执行: {item.attempted}\n"
                f"  - 成功: {item.success}\n"
                f"  - 摘要: {item.summary or '-'}\n"
                f"  - 命令: `{item.command or '-'}`\n"
                f"  - 输出文件: `{item.output_path or '-'}`\n"
                f"  - 错误: `{item.error or '-'}`\n"
                f"  - 证据样本: {', '.join(item.evidence[:12]) if item.evidence else '-'}"
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
