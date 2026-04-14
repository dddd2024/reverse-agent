from pathlib import Path

from reverse_agent.pipeline import SolveResult
from reverse_agent.reporter import write_report
from reverse_agent.tool_runners import ToolRunArtifact


def test_reporter_redacts_paths_and_includes_required_sections(tmp_path: Path) -> None:
    result = SolveResult(
        input_value=r"E:\Users\demo\Desktop\sample.exe",
        resolved_path=r"E:\Users\demo\Desktop\sample.exe",
        analysis_mode="Static Analysis",
        model_name='Copilot CLI (gh copilot -p "{prompt}" -s)',
        candidates=["flag{demo}"],
        selected_flag="flag{demo}",
        prompt=(
            "你是逆向工程解题助手，请根据证据推断最终 flag。\n"
            "分析模式: Static Analysis\n"
            "任务:\n"
            "1) 推断最可能的最终 flag。\n"
            "2) 第一行只输出一个 flag。"
        ),
        model_output=(
            "flag{demo}\n"
            "0x401190: len check\n"
            "0x4012E0: affine y=(3*x+7) mod 26\n"
        ),
        extracted_strings_count=10,
        tool_artifacts=[
            ToolRunArtifact(
                tool_name="IDA",
                enabled=True,
                attempted=True,
                success=True,
                command=r"E:\Program Files\ida\idat64.exe -Sscript.py",
                summary="ok",
                output_path=r"E:\Users\demo\out.json",
                evidence=["IDA函数: sub_401190", "IDA函数: sub_4012E0"],
            )
        ],
    )

    report_path = write_report(result, reports_dir=tmp_path)
    content = report_path.read_text(encoding="utf-8")

    assert "## 0x04 关键伪代码还原（Decompiled Code）" in content
    assert "input_flag.size() != 24" in content
    assert "3*9 = 27" in content
    assert "0x401190" in content
    assert "<LOCAL_PATH>" in content
    assert r"E:\Users\demo" not in content
    assert "report_type: reverse_ctf_writeup" in content
    assert "| confidence |" in content
    assert "关键指令节选" not in content


def test_reporter_non_affine_fallback(tmp_path: Path) -> None:
    result = SolveResult(
        input_value="sample.exe",
        resolved_path="sample.exe",
        analysis_mode="Static Analysis",
        model_name="Local Model",
        candidates=[],
        selected_flag="SEPTA",
        prompt="任务:\n1) only output final answer",
        model_output="SEPTA\ncompare with table\n0x401050",
        extracted_strings_count=4,
        tool_artifacts=[],
    )
    report_path = write_report(result, reports_dir=tmp_path)
    content = report_path.read_text(encoding="utf-8")

    assert "未检测到稳定的仿射特征" in content
    assert "规则处理/特征变换" in content
