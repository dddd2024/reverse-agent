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
    tool_artifacts_text = "\n".join(
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
                f"  - 证据: {', '.join(item.evidence[:8]) if item.evidence else '-'}"
            )
            for item in result.tool_artifacts
        ]
    )
    content = f"""# Reverse 解题报告

## 总览
- **输入**: `{result.input_value}`
- **解析后文件**: `{result.resolved_path}`
- **分析模式**: `{result.analysis_mode}`
- **模型**: `{result.model_name}`
- **提取字符串数量**: `{result.extracted_strings_count}`
- **最终 flag**: `{result.selected_flag}`

## 使用的分析模板
```text
{template}
```

## 本地提取候选 flag
{chr(10).join(f"- `{c}`" for c in result.candidates) if result.candidates else "- None"}

## 工具链执行结果
{tool_artifacts_text if tool_artifacts_text else "- 未启用工具链自动分析"}

## 模型原始输出
```text
{result.model_output}
```

## 发送给模型的 Prompt
```text
{result.prompt}
```
"""
    path.write_text(content, encoding="utf-8")
    return path
