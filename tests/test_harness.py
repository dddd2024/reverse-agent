from pathlib import Path

from reverse_agent.evidence import StructuredEvidence
from reverse_agent.harness import (
    HarnessCase,
    HarnessConfig,
    _safe_console_log,
    filter_harness_cases,
    run_harness,
)
from reverse_agent.pipeline import SolveResult
from reverse_agent.tool_runners import ToolAutomationConfig


def test_safe_console_log_replaces_unencodable_output(monkeypatch) -> None:
    class FakeStdout:
        encoding = "gbk"

        def __init__(self) -> None:
            self.output = ""

        def write(self, value: str) -> None:
            value.encode(self.encoding)
            self.output += value

        def flush(self) -> None:
            return

    fake_stdout = FakeStdout()
    monkeypatch.setattr("sys.stdout", fake_stdout)

    _safe_console_log("候选评分Top3: ´")

    assert "候选评分Top3: ?" in fake_stdout.output


def test_filter_harness_cases_supports_case_ids_tags_and_limit() -> None:
    cases = [
        HarnessCase(case_id="a", input_value="a.exe", tags=["smoke"]),
        HarnessCase(case_id="b", input_value="b.exe", tags=["regression"]),
        HarnessCase(case_id="c", input_value="c.exe", tags=["smoke", "gui"]),
    ]

    selected = filter_harness_cases(cases, case_ids=["a", "c"], tags=["smoke"], limit=1)
    assert [item.case_id for item in selected] == ["a"]


def test_run_harness_writes_manifest_summary_and_case_results(
    tmp_path: Path, monkeypatch
) -> None:
    reports_dir = tmp_path / "reports"

    def _fake_run_pipeline(**kwargs):  # noqa: ANN001
        input_value = kwargs["input_value"]
        name = Path(input_value).stem
        return SolveResult(
            input_value=input_value,
            resolved_path=input_value,
            analysis_mode=kwargs["analysis_mode"],
            model_name="Copilot CLI",
            candidates=["flag{demo}", "NOT_FOUND"],
            selected_flag="flag{demo}" if name == "ok_case" else "NOT_FOUND",
            prompt="prompt",
            model_output="flag{demo}\nreasoning" if name == "ok_case" else "NOT_FOUND",
            extracted_strings_count=12,
            tool_artifacts=[],
            structured_evidence=[StructuredEvidence(kind="CandidateEvidence", source_tool="fake")],
            active_profile="samplereverse" if name == "ok_case" else "",
            matched_profiles=["samplereverse"] if name == "ok_case" else [],
            applied_strategies=["CompareAwareSearchStrategy"] if name == "ok_case" else [],
            candidate_validations=[],
            report_path=str(reports_dir / f"{name}.md"),
        )

    monkeypatch.setattr("reverse_agent.harness.run_pipeline", _fake_run_pipeline)

    config = HarnessConfig(
        cases=[
            HarnessCase(case_id="ok-case", input_value="ok_case.exe", expected_flag="flag{demo}", tags=["smoke"]),
            HarnessCase(case_id="miss-case", input_value="miss_case.exe", expected_flag="flag{miss}", category="gui_compare", tags=["regression"]),
        ],
        reports_dir=reports_dir,
        run_name="smoke_suite",
        dataset_path=str(tmp_path / "dataset.json"),
        analysis_mode="Static Analysis",
        model_type="Copilot CLI",
        tool_config=ToolAutomationConfig(enabled=False),
        runtime_validation_enabled=False,
    )

    summary = run_harness(config, log=lambda _: None)

    run_dir = reports_dir / "harness_runs" / "smoke_suite"
    assert summary.total_cases == 2
    assert summary.executed_cases == 2
    assert summary.resumed_cases == 0
    assert summary.passed_cases == 1
    assert summary.failed_cases == 1
    assert summary.not_found_cases == 1
    assert summary.evidence_coverage == 1.0
    assert summary.candidate_quality == 0.5
    assert summary.solve_rate_by_category["gui_compare"] == 0.0
    assert (run_dir / "run_manifest.json").exists()
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "case_results" / "ok-case.json").exists()
    assert (run_dir / "case_results" / "miss-case.json").exists()


def test_run_harness_resume_skips_completed_cases(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def _fake_run_pipeline(**kwargs):  # noqa: ANN001
        calls.append(kwargs["input_value"])
        return SolveResult(
            input_value=kwargs["input_value"],
            resolved_path=kwargs["input_value"],
            analysis_mode=kwargs["analysis_mode"],
            model_name="Copilot CLI",
            candidates=["flag{demo}"],
            selected_flag="flag{demo}",
            prompt="prompt",
            model_output="flag{demo}",
            extracted_strings_count=5,
            tool_artifacts=[],
            structured_evidence=[],
            candidate_validations=[],
            report_path=str(tmp_path / "reports" / "demo.md"),
        )

    monkeypatch.setattr("reverse_agent.harness.run_pipeline", _fake_run_pipeline)

    config = HarnessConfig(
        cases=[HarnessCase(case_id="demo", input_value="demo.exe", expected_flag="flag{demo}")],
        reports_dir=tmp_path / "reports",
        run_name="resume_suite",
        analysis_mode="Static Analysis",
    )

    first = run_harness(config, log=lambda _: None)
    second = run_harness(config, log=lambda _: None)

    assert first.executed_cases == 1
    assert second.executed_cases == 0
    assert second.resumed_cases == 1
    assert calls == ["demo.exe"]


def test_run_harness_rejects_same_run_name_with_different_config(
    tmp_path: Path, monkeypatch
) -> None:
    def _fake_run_pipeline(**kwargs):  # noqa: ANN001
        return SolveResult(
            input_value=kwargs["input_value"],
            resolved_path=kwargs["input_value"],
            analysis_mode=kwargs["analysis_mode"],
            model_name="Copilot CLI",
            candidates=["flag{demo}"],
            selected_flag="flag{demo}",
            prompt="prompt",
            model_output="flag{demo}",
            extracted_strings_count=5,
            tool_artifacts=[],
            structured_evidence=[],
            candidate_validations=[],
            report_path=str(tmp_path / "reports" / "demo.md"),
        )

    monkeypatch.setattr("reverse_agent.harness.run_pipeline", _fake_run_pipeline)

    base = HarnessConfig(
        cases=[HarnessCase(case_id="demo", input_value="demo.exe", expected_flag="flag{demo}")],
        reports_dir=tmp_path / "reports",
        run_name="stable_suite",
        analysis_mode="Static Analysis",
    )
    changed = HarnessConfig(
        cases=[HarnessCase(case_id="demo", input_value="demo.exe", expected_flag="flag{other}")],
        reports_dir=tmp_path / "reports",
        run_name="stable_suite",
        analysis_mode="Static Analysis",
    )

    run_harness(base, log=lambda _: None)
    try:
        run_harness(changed, log=lambda _: None)
    except ValueError as exc:
        assert "different harness config" in str(exc)
    else:
        raise AssertionError("expected run_harness to reject mismatched reused run_name")
