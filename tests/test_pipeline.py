from pathlib import Path

from reverse_agent.evidence import StructuredEvidence
from reverse_agent.models import ModelError
from reverse_agent.pipeline import (
    _candidate_to_gui_text,
    _extract_best_answer_line,
    _extract_first_flag,
    _extract_flag_prefix_hint,
    _probe_gui_runtime_outputs,
    _is_windows_gui_exe,
    _is_placeholder_candidate,
    build_prompt,
    extract_strings,
    find_binary_prefix_candidates,
    run_pipeline,
)
from reverse_agent.strategies.base import StrategyResult
from reverse_agent.tool_runners import ToolAutomationConfig
from reverse_agent.tool_runners import ToolRunArtifact


def test_extract_best_answer_line_prefers_directive() -> None:
    output = "分析如下\n最终答案为: SEPTA\n其余说明"
    assert _extract_best_answer_line(output) == "SEPTA"


def test_extract_first_flag() -> None:
    output = "foo\nflag{demo_value}\nbar"
    assert _extract_first_flag(output) == "flag{demo_value}"


def test_extract_first_flag_rejects_explanatory_sentence() -> None:
    output = "flag{` 开头”；为满足题目常见提交格式，最稳妥的单一候选是最短闭合形式 `flag{}"
    assert _extract_first_flag(output) == ""


def test_extract_flag_prefix_hint() -> None:
    output = "结论如下\nflag{\n后续说明"
    assert _extract_flag_prefix_hint(output) == "flag{"


def test_placeholder_candidate_rejected() -> None:
    assert _is_placeholder_candidate("flag{...}") is True


def test_extract_strings_includes_utf16le_ascii_text(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(
        b"MZ"
        + "Flag : ".encode("utf-16le")
        + b"\x00\x00"
        + "密钥不正确".encode("utf-16le")
        + b"\x00\x00"
    )
    values = extract_strings(sample, min_length=4, max_items=100)
    assert any("Flag :" in item for item in values)
    assert "密钥不正确" in values


def test_build_prompt_includes_ctf_skill_by_default(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    prompt = build_prompt(
        file_path=sample,
        strings=["alpha", "beta"],
        pre_candidates=["flag{demo}"],
        analysis_mode="Static Analysis",
        tool_evidence=["evidence-1"],
    )
    assert "CTF逆向Skill增强（项目内自定义，参考公开资料白名单化整理）:" in prompt
    assert "先做 strings 提取与关键词聚类" in prompt


def test_build_prompt_can_disable_ctf_skill(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    prompt = build_prompt(
        file_path=sample,
        strings=["alpha", "beta"],
        pre_candidates=["flag{demo}"],
        analysis_mode="Static Analysis",
        tool_evidence=["evidence-1"],
        ctf_skill_enabled=False,
    )
    assert "CTF逆向Skill增强（项目内自定义，参考公开资料白名单化整理）:" not in prompt


def test_build_prompt_full_skill_profile_has_extra_guidance(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    prompt = build_prompt(
        file_path=sample,
        strings=["alpha"],
        pre_candidates=[],
        analysis_mode="Dynamic Debug",
        tool_evidence=[],
        ctf_skill_profile="full",
    )
    assert "Frida/angr/Qiling" in prompt


def test_build_prompt_escapes_null_bytes_in_candidates(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    prompt = build_prompt(
        file_path=sample,
        strings=["alpha"],
        pre_candidates=["AB\x00CD"],
        analysis_mode="Static Analysis",
        tool_evidence=["candidate:AB\x00CD"],
    )
    assert "\x00" not in prompt
    assert "\\x00" in prompt


def test_find_binary_prefix_candidates_detects_utf16_prefix(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ" + b"f\x00l\x00a\x00g\x00{\x00")
    values = find_binary_prefix_candidates(sample)
    assert "flag{" in values


def test_candidate_to_gui_text_preserves_low_bytes_for_controls() -> None:
    mapped = _candidate_to_gui_text("A\x01\xff")
    raw = mapped.encode("utf-16le")
    # A keeps 0x41 low byte, control bytes are remapped to printable Unicode
    # with the same low byte in the UTF-16LE representation.
    assert raw[0] == 0x41
    assert raw[2] == 0x01
    assert raw[4] == 0xFF


def test_runtime_validation_disabled_does_not_execute_sample(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "ABCD\nreasoning",
    )

    def _should_not_be_called(file_path, candidate, success_markers, fail_markers, timeout_seconds=5):  # noqa: ARG001
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
        lambda file_path, candidate, success_markers, fail_markers, timeout_seconds=5: (candidate == "ABCDA", ""),  # noqa: ARG005
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


def test_sample_deadline_hard_stop_skips_angr_and_model(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )

    deadline_artifact = ToolRunArtifact(
        tool_name="CompareAwareBridge",
        enabled=True,
        attempted=True,
        success=True,
        summary="deadline reached",
        evidence=["runtime_probe:deadline_reached=1"],
    )
    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.CompareAwareSearchStrategy.run",
        lambda self, **kwargs: StrategyResult(  # noqa: ARG005
            strategy_name="CompareAwareSearchStrategy",
            summary="deadline reached",
            candidates=["flag{"],
            artifacts=[deadline_artifact],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("angr should be skipped")),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: (_ for _ in ()).throw(AssertionError("model should be skipped")),  # noqa: ARG005
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
    assert result.selected_flag == "NOT_FOUND"
    assert any(item["validated"] == "deadline_stop" for item in result.candidate_validations)


def test_runtime_validation_uses_gui_session_for_gui_subsystem_exe(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr("reverse_agent.pipeline._is_windows_gui_exe", lambda fp: True)  # noqa: ARG005

    def _should_not_be_called(file_path, candidate, success_markers, fail_markers, timeout_seconds=5):  # noqa: ARG001
        raise AssertionError("stdin runtime validation should be skipped for GUI EXE")

    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        _should_not_be_called,
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidates_with_gui_session",
        lambda file_path, candidates, success_markers, fail_markers, per_action_delay=0.12: (  # noqa: ARG005
            "SEPTA",
            [{"candidate": "SEPTA", "validated": "yes", "evidence": "ok"}],
        ),
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
    assert result.selected_flag == "SEPTA"
    assert result.candidate_validations[0]["validated"] == "yes"


def test_is_windows_gui_exe_detects_gui_subsystem(tmp_path: Path) -> None:
    sample = tmp_path / "gui.exe"
    data = bytearray(512)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\x00\x00"
    optional_header_off = 0x80 + 24
    subsystem_off = optional_header_off + 0x44
    data[subsystem_off:subsystem_off + 2] = (2).to_bytes(2, "little")
    sample.write_bytes(bytes(data))
    assert _is_windows_gui_exe(sample) is True


def test_probe_gui_runtime_outputs_skips_non_matching_sample(tmp_path: Path) -> None:
    sample = tmp_path / "gui.exe"
    data = bytearray(512)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\x00\x00"
    optional_header_off = 0x80 + 24
    subsystem_off = optional_header_off + 0x44
    data[subsystem_off:subsystem_off + 2] = (2).to_bytes(2, "little")
    sample.write_bytes(bytes(data))
    assert _probe_gui_runtime_outputs(sample, ["hello"], ["AAAA"]) is None


def test_run_pipeline_includes_gui_probe_artifact_for_matching_sample(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "samplereverse.exe"
    data = bytearray(512)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\x00\x00"
    optional_header_off = 0x80 + 24
    subsystem_off = optional_header_off + 0x44
    data[subsystem_off:subsystem_off + 2] = (2).to_bytes(2, "little")
    sample.write_bytes(bytes(data))

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._probe_gui_runtime_outputs",
        lambda file_path, strings, seed_candidates, per_action_delay=0.18: ToolRunArtifact(  # noqa: ARG005
            tool_name="GUIProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary="ok",
            evidence=["runtime_gui:title=CTF", "runtime_gui:probe_output=test"],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._run_compare_probe_if_needed",
        lambda file_path, strings, artifacts_dir, log: ToolRunArtifact(  # noqa: ARG005
            tool_name="CompareProbe",
            enabled=True,
            attempted=True,
            success=False,
            summary="compare miss",
            evidence=["runtime_compare:error=no_compare_hit"],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.CompareAwareSearchStrategy.run",
        lambda self, **kwargs: StrategyResult(  # noqa: ARG005
            strategy_name="CompareAwareSearchStrategy",
            summary="bridge ok",
            candidates=[],
            artifacts=[],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND",  # noqa: ARG001
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
    assert any(item.tool_name == "GUIProbe" for item in result.tool_artifacts)


def test_run_pipeline_prefers_compare_probe_and_skips_sample_probe(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )
    monkeypatch.setattr("reverse_agent.pipeline._is_windows_gui_exe", lambda fp: True)  # noqa: ARG005
    monkeypatch.setattr(
        "reverse_agent.pipeline._run_compare_probe_if_needed",
        lambda file_path, strings, artifacts_dir, log: ToolRunArtifact(  # noqa: ARG005
            tool_name="CompareProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary="compare ok",
            evidence=[
                "runtime_compare:site=0x40258c",
                "runtime_compare:input=AAAAAAA",
                "runtime_compare:lhs=flag{demo",
                "runtime_compare:rhs=flag{",
                "runtime_compare:lhs_ptr=0x1234",
                "runtime_compare:lhs_prefix_match=1",
                "runtime_candidate:AAAAAAA source=runtime_compare confidence=0.98",
            ],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_samplereverse_resumable_search",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("sample probe should be skipped")),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\ncompare evidence wins",  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
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
    assert result.selected_flag == "AAAAAAA"
    assert any(item.tool_name == "CompareProbe" for item in result.tool_artifacts)


def test_run_pipeline_compare_probe_failure_falls_back_to_sample_probe(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )
    monkeypatch.setattr("reverse_agent.pipeline._is_windows_gui_exe", lambda fp: True)  # noqa: ARG005
    monkeypatch.setattr(
        "reverse_agent.pipeline._run_compare_probe_if_needed",
        lambda file_path, strings, artifacts_dir, log: ToolRunArtifact(  # noqa: ARG005
            tool_name="CompareProbe",
            enabled=True,
            attempted=True,
            success=False,
            summary="compare miss",
            evidence=["runtime_compare:error=no_compare_hit"],
        ),
    )

    bridge_artifact = ToolRunArtifact(
        tool_name="CompareAwareBridge",
        enabled=True,
        attempted=True,
        success=True,
        summary="bridge ok",
    )
    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.CompareAwareSearchStrategy.run",
        lambda self, **kwargs: StrategyResult(  # noqa: ARG005
            strategy_name="CompareAwareSearchStrategy",
            summary="bridge ok",
            candidates=["BBBBBBB"],
            artifacts=[bridge_artifact],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._probe_gui_runtime_outputs",
        lambda file_path, strings, seed_candidates, per_action_delay=0.18: None,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\nfallback to sample",  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
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
    assert any(item.tool_name == "CompareAwareBridge" for item in result.tool_artifacts)
    assert result.selected_flag == "BBBBBBB"


def test_run_pipeline_compare_probe_truth_without_candidate_still_runs_sample_probe(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )
    monkeypatch.setattr("reverse_agent.pipeline._is_windows_gui_exe", lambda fp: True)  # noqa: ARG005
    monkeypatch.setattr(
        "reverse_agent.pipeline._run_compare_probe_if_needed",
        lambda file_path, strings, artifacts_dir, log: ToolRunArtifact(  # noqa: ARG005
            tool_name="CompareProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary="compare truth only",
            evidence=[
                "runtime_compare:site=0x40258c",
                "runtime_compare:input=o~\\xeb\\xb7\\xa207AAAAAA",
                "runtime_compare:lhs=f\\x286c",
                "runtime_compare:lhs_ptr=0x1234",
                "runtime_compare:lhs_prefix_match=0",
            ],
        ),
    )

    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.CompareAwareSearchStrategy.run",
        lambda self, **kwargs: StrategyResult(  # noqa: ARG005
            strategy_name="CompareAwareSearchStrategy",
            summary="bridge continued",
            candidates=["CCCCCCC"],
            artifacts=[
                ToolRunArtifact(
                    tool_name="CompareAwareBridge",
                    enabled=True,
                    attempted=True,
                    success=True,
                    summary="bridge continued",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._probe_gui_runtime_outputs",
        lambda file_path, strings, seed_candidates, per_action_delay=0.18: (_ for _ in ()).throw(AssertionError("gui probe should be skipped when compare truth exists")),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\ncompare truth should continue into bridge strategy",  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
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
    assert any(item.tool_name == "CompareProbe" for item in result.tool_artifacts)
    assert any(item.tool_name == "CompareAwareBridge" for item in result.tool_artifacts)
    assert result.selected_flag == "CCCCCCC"


def test_run_pipeline_records_profile_and_structured_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["输入的密钥是", "密钥不正确"],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._run_compare_probe_if_needed",
        lambda file_path, strings, artifacts_dir, log: ToolRunArtifact(  # noqa: ARG005
            tool_name="CompareProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary="compare ok",
            evidence=[
                "runtime_compare:site=0x40258c",
                "runtime_compare:input=AAAAAAA",
                "runtime_compare:lhs=flag{demo",
                "runtime_compare:rhs=flag{",
                "runtime_candidate:AAAAAAA source=runtime_compare confidence=0.98",
            ],
            structured_evidence=[
                StructuredEvidence(
                    kind="RuntimeCompareEvidence",
                    source_tool="CompareProbe",
                    summary="compare ok",
                    payload={"lhs_wide_hex": "66006c00610067007b00"},
                    confidence=0.98,
                    derived_candidates=["AAAAAAA"],
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_samplereverse_resumable_search",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("sample solver should be skipped")),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda **kwargs: [],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND",  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
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

    assert result.active_profile == "samplereverse"
    assert "samplereverse" in result.matched_profiles
    assert "CompareAwareSearchStrategy" in result.applied_strategies
    assert any(item.kind == "RuntimeCompareEvidence" for item in result.structured_evidence)


def test_copilot_timeout_retries_with_compact_prompt(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")

    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4: [f"S{i:03d}_VALUE" for i in range(900)],  # noqa: ARG005
    )

    calls: list[str] = []

    def _solve(self, prompt: str):  # noqa: ANN001
        calls.append(prompt)
        if len(calls) == 1:
            raise ModelError("Copilot CLI call timed out.")
        return "SEPTA\nreasoning"

    monkeypatch.setattr("reverse_agent.pipeline.CopilotCliBackend.solve", _solve)

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
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
    assert result.selected_flag == "SEPTA"
    assert len(calls) == 2
    assert len(calls[1]) < len(calls[0])


def test_copilot_timeout_retry_failure_falls_back_to_local_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: ["hello", "world"],  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: (_ for _ in ()).throw(ModelError("Copilot CLI call timed out.")),  # noqa: ARG001
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
    assert result.selected_flag == "NOT_FOUND"


def test_mass_candidates_can_skip_model_and_use_runtime_validation(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    many_tokens = [f"ZG{i:03d}" for i in range(220)]
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: many_tokens,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: (_ for _ in ()).throw(AssertionError("model should be skipped")),  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        lambda file_path, candidate, success_markers, fail_markers, timeout_seconds=5: (False, ""),  # noqa: ARG005
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
    assert result.selected_flag == "NOT_FOUND"
    assert len(result.candidate_validations) > 0


def test_mass_candidates_with_tool_candidates_still_calls_model(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    many_tokens = [f"ZG{i:03d}" for i in range(220)]
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: many_tokens,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [  # noqa: ARG001
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["runtime_candidate:SEPTA"],
            )
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        lambda file_path, candidate, success_markers, fail_markers, timeout_seconds=5: (candidate == "SEPTA", ""),  # noqa: ARG005
    )
    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Static Analysis",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=True, ida_enabled=False, ollydbg_enabled=True),
        runtime_validation_enabled=True,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "SEPTA"


def test_auto_mode_prefers_dynamic_when_runtime_hints_and_olly_ready(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "IsDebuggerPresent",
            "CheckRemoteDebuggerPresent",
            "decrypt_loop",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Auto",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ollydbg_enabled=True,
            ollydbg_executable=r"E:\Program Files\ollydbg\ollydbg.exe",
            ollydbg_script_path=r"F:\reverse-agent\reverse_agent\olly_scripts\collect_evidence.py",
        ),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.analysis_mode == "Dynamic Debug"


def test_auto_mode_prefers_dynamic_when_no_static_candidate_and_olly_ready(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "hello",
            "world",
            "plain_text",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Auto",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ollydbg_enabled=True,
            ollydbg_executable=r"E:\Program Files\ollydbg\ollydbg.exe",
            ollydbg_script_path=r"E:\scripts\my_olly_auto.py",
        ),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.analysis_mode == "Dynamic Debug"


def test_auto_mode_default_olly_script_does_not_force_dynamic(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "hello",
            "world",
            "plain_text",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Auto",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ollydbg_enabled=True,
            ollydbg_executable=r"E:\Program Files\ollydbg\ollydbg.exe",
            ollydbg_script_path=r"F:\reverse-agent\reverse_agent\olly_scripts\collect_evidence.py",
        ),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.analysis_mode == "Static Analysis"


def test_auto_mode_empty_olly_script_path_does_not_force_dynamic(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "hello",
            "world",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "SEPTA\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )
    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Auto",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ollydbg_enabled=True,
            ollydbg_executable=r"E:\Program Files\ollydbg\ollydbg.exe",
            ollydbg_script_path="",
        ),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.analysis_mode == "Static Analysis"


def test_auto_mode_prefers_static_when_local_flag_exists(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "flag{local_hit}",
            "IsDebuggerPresent",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "flag{local_hit}\nreasoning",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Auto",
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
    assert result.analysis_mode == "Static Analysis"


def test_static_mode_can_append_dynamic_olly_pass_when_no_tool_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "",  # noqa: ARG001
    )
    calls: list[str] = []

    def _fake_run_tool_automation(file_path, analysis_mode, config, artifacts_dir, log):  # noqa: ANN001, ARG001
        calls.append(analysis_mode)
        if analysis_mode == "Static Analysis":
            return []
        return [
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["runtime_candidate:SEPTA"],
            )
        ]

    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        _fake_run_tool_automation,
    )
    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Static Analysis",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ollydbg_enabled=True,
            ollydbg_executable=r"E:\Program Files\ollydbg\ollydbg.exe",
            ollydbg_script_path="",
        ),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert calls == ["Static Analysis", "Dynamic Debug"]
    assert result.selected_flag == "SEPTA"


def test_tool_runtime_candidate_can_be_selected(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [  # noqa: ARG001
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["runtime_candidate:flag{"],
            )
        ],
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=True, ida_enabled=False),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "flag{"


def test_model_not_found_does_not_override_tool_candidate(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\nno evidence",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [  # noqa: ARG001
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["prefix_candidate:flag{"],
            )
        ],
    )

    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=True, ida_enabled=False),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "flag{"


def test_placeholder_output_does_not_become_selected(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "flag{...}\n猜测",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [  # noqa: ARG001
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["prefix_candidate:flag{"],
            )
        ],
    )
    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=True, ida_enabled=False),
        runtime_validation_enabled=False,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "flag{"


def test_runtime_validation_enabled_prefix_only_candidate_not_selected(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\nno evidence",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [  # noqa: ARG001
            ToolRunArtifact(
                tool_name="OllyDbg",
                enabled=True,
                attempted=True,
                success=True,
                summary="ok",
                evidence=["prefix_candidate:flag{"],
            )
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        lambda file_path, candidate, success_markers, fail_markers, timeout_seconds=5: (False, ""),  # noqa: ARG005
    )
    result = run_pipeline(
        input_value=str(sample),
        analysis_mode="Dynamic Debug",
        model_type="Copilot CLI",
        copilot_command='copilot -p "{prompt}" -s',
        local_base_url="",
        local_model="",
        local_api_key="",
        tool_config=ToolAutomationConfig(enabled=True, ida_enabled=False),
        runtime_validation_enabled=True,
        reports_dir=tmp_path / "reports",
        log=lambda _: None,
    )
    assert result.selected_flag == "NOT_FOUND"


def test_runtime_validation_enabled_prefix_candidate_can_be_selected_when_validated(
    tmp_path: Path, monkeypatch
) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.extract_strings",
        lambda file_path, min_length=4, max_items=6000: [  # noqa: ARG005
            "flag{",
            "something",
        ],
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\nno evidence",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._is_windows_gui_exe",
        lambda fp: False,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline._validate_candidate_with_exe",
        lambda file_path, candidate, success_markers, fail_markers, timeout_seconds=5: (candidate == "flag{", ""),  # noqa: ARG005
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
    assert result.selected_flag == "flag{"


def test_angr_fallback_candidate_can_win(tmp_path: Path, monkeypatch) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    monkeypatch.setattr(
        "reverse_agent.pipeline.CopilotCliBackend.solve",
        lambda self, prompt: "NOT_FOUND\nno evidence",  # noqa: ARG001
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.solve_with_angr_stdin",
        lambda file_path, success_markers, fail_markers, max_input_len=32, timeout_seconds=70, log=None: ["REALFLAG123"],  # noqa: ARG001,ARG005
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_tool_automation",
        lambda file_path, analysis_mode, config, artifacts_dir, log: [],  # noqa: ARG001
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
    assert result.selected_flag == "REALFLAG123"
