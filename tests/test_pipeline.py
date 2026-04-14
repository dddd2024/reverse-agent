from pathlib import Path

from reverse_agent.pipeline import _extract_best_answer_line, _extract_first_flag, run_pipeline
from reverse_agent.tool_runners import ToolAutomationConfig


def test_extract_best_answer_line_prefers_directive() -> None:
    output = "分析如下\n最终答案为: SEPTA\n其余说明"
    assert _extract_best_answer_line(output) == "SEPTA"


def test_extract_first_flag() -> None:
    output = "foo\nflag{demo_value}\nbar"
    assert _extract_first_flag(output) == "flag{demo_value}"


def test_runtime_validation_disabled_does_not_execute_sample(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "ABCD\nreasoning",
    )

    def _should_not_be_called(file_path, candidate, timeout_seconds=5):  # noqa: ARG001
        raise AssertionError("runtime validation should be disabled")

    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        _should_not_be_called,
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Static Analysis",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=False),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "ABCD"


def test_runtime_validation_enabled_can_promote_validated_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "ABCD\nreasoning",
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        lambda file_path, candidate, timeout_seconds=5: candidate == "ABCDA",  # noqa: ARG005
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Static Analysis",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=False),
        runtime_validation_enabled=True,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "ABCDA"
