import json
import zipfile
from pathlib import Path

from reverse_agent.project_state import archive_round, build_project_state, main, pack_context


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _make_minimal_harness_run(reports_dir: Path, run_name: str = "samplereverse_stalled") -> Path:
    run_dir = reports_dir / "harness_runs" / run_name
    artifacts_dir = run_dir / "reports" / "tool_artifacts" / "samplereverse"
    _write_json(
        run_dir / "summary.json",
        {
            "run_name": run_name,
            "run_dir": str(run_dir),
            "error_cases": 0,
            "case_result_paths": [str(run_dir / "case_results" / "samplereverse.json")],
        },
    )
    _write_json(
        run_dir / "run_manifest.json",
        {
            "run_name": run_name,
            "status": "completed",
            "case_ids": ["samplereverse"],
        },
    )
    _write_json(
        run_dir / "case_results" / "samplereverse.json",
        {
            "case_id": "samplereverse",
            "status": "completed_no_expected",
            "profile_name": "samplereverse",
            "matched_profiles": ["samplereverse"],
            "applied_strategies": ["CompareAwareSearchStrategy"],
            "error": "",
        },
    )
    _write_json(
        artifacts_dir / "samplereverse_compare_aware_frontier_summary.json",
        {
            "frontier_active_lane": "frontier_exact1",
            "frontier_stall_stage": "pair_pool",
            "frontier_exact1_stall_reason": "distance_not_improved",
            "frontier_converged_reason": "distance_not_improved",
            "frontier_anchor_candidates": [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "compare_semantics_agree": True,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "runtime_ci_exact_wchars": 1,
                    "runtime_ci_distance5": 258,
                    "compare_semantics_agree": True,
                    "frontier_role": "exact1_frontier",
                },
            ],
        },
    )
    _write_json(
        artifacts_dir / "samplereverse_compare_aware_strata_summary.json",
        {
            "frontier_stall_stage": "pair_pool",
            "frontier_exact1_stall_reason": "distance_not_improved",
            "best_exact2_runtime": {
                "candidate_hex": "78d540b49c59077041414141414141",
                "cand8_hex": "78d540b49c590770",
                "runtime_ci_exact_wchars": 2,
                "runtime_ci_distance5": 246,
                "compare_semantics_agree": True,
            },
            "best_frontier_runtime": {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "runtime_ci_exact_wchars": 1,
                "runtime_ci_distance5": 258,
                "compare_semantics_agree": True,
                "frontier_role": "exact1_frontier",
            },
        },
    )
    _write_json(
        artifacts_dir / "samplereverse_compare_aware_guided_pool_result.json",
        {"large_payload": "SOLVE_REPORTS_FULL_SENTINEL"},
    )
    _write_json(
        artifacts_dir / "samplereverse_compare_aware_guided_pool_validation.json",
        {"validations": [{"candidate_hex": "5a3e7f46ddd474d041414141414141"}]},
    )
    return run_dir


def test_build_missing_solve_reports_does_not_crash_and_writes_files(tmp_path: Path) -> None:
    state_dir = tmp_path / "project_state"

    build_project_state(
        reports_dir=tmp_path / "missing_reports",
        state_dir=state_dir,
        sample="samplereverse",
    )

    expected = {
        "artifact_index.json",
        "current_state.json",
        "negative_results.json",
        "model_gate.json",
        "task_packet.json",
    }
    assert expected.issubset({item.name for item in state_dir.iterdir()})
    assert (state_dir / "decision_packet.md").exists()
    assert (state_dir / "codex_execution_report.md").exists()
    assert (state_dir / "README.md").exists()
    assert (state_dir / "rounds" / ".gitkeep").exists()
    assert _read_json(state_dir / "artifact_index.json")["missing"] == ["reports_dir"]
    assert _read_json(state_dir / "model_gate.json")["should_call_model"] is False


def test_project_state_indexes_pre_rc4_material_probe_and_negative_result(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    run_dir = _make_minimal_harness_run(reports_dir, run_name="samplereverse_pre_rc4")
    artifacts_dir = run_dir / "reports" / "tool_artifacts" / "samplereverse"
    _write_json(
        artifacts_dir / "pre_rc4_material_probe" / "pre_rc4_material_probe.json",
        {
            "artifact_kind": "pre_rc4_material_probe",
            "classification": "pre_rc4_probe_unavailable",
            "runtime_backed_count": 0,
            "candidate_count": 3,
            "probe_points": {
                "base64_material": "unavailable",
                "rc4_ksa_key": "unavailable",
                "compare_buffer": "unavailable",
            },
            "rc4_key_status": "unknown",
            "rc4_input_status": "unknown",
            "next_bounded_action": "switch to manual breakpoints",
        },
    )

    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")

    artifact_index = _read_json(state_dir / "artifact_index.json")
    current_state = _read_json(state_dir / "current_state.json")
    negative_results = _read_json(state_dir / "negative_results.json")
    assert artifact_index["latest_artifacts"]["pre_rc4_material_probe"].endswith("pre_rc4_material_probe.json")
    assert current_state["current_bottleneck"]["stage"] == "pre_rc4_material_probe"
    assert current_state["latest_pre_rc4_material_probe"]["classification"] == "pre_rc4_probe_unavailable"
    assert any("memory-scan lower-level pre-RC4" in item["direction"] for item in negative_results)


def test_build_generates_state_files_and_artifact_index(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    _make_minimal_harness_run(reports_dir)

    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")

    artifact_index = _read_json(state_dir / "artifact_index.json")
    assert artifact_index["latest_harness_run"]
    assert artifact_index["latest_summary"].endswith("summary.json")
    assert len(artifact_index["latest_case_results"]) == 1
    assert artifact_index["latest_artifacts"]["frontier_summary"].endswith(
        "samplereverse_compare_aware_frontier_summary.json"
    )
    assert artifact_index["latest_artifacts"]["guided_pool_validation"].endswith(
        "samplereverse_compare_aware_guided_pool_validation.json"
    )


def test_current_state_negative_results_model_gate_and_task_packet_are_generated(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    _make_minimal_harness_run(reports_dir)

    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")

    current_state = _read_json(state_dir / "current_state.json")
    negative_results = _read_json(state_dir / "negative_results.json")
    model_gate = _read_json(state_dir / "model_gate.json")
    task_packet = _read_json(state_dir / "task_packet.json")
    assert current_state["active_strategy"] == "CompareAwareSearchStrategy"
    assert current_state["best_candidates"]["exact2"]["runtime_ci_exact_wchars"] == 2
    assert current_state["current_bottleneck"]["stage"] == "pair_pool"
    assert any(item["severity"] == "hard_block" for item in negative_results)
    assert model_gate["should_call_model"] is True
    assert model_gate["context_level"] == 2
    assert task_packet["task"] == "Generate next decision for exact1 pair_pool bottleneck"
    assert task_packet["sufficiency_check"]["has_runtime_validation"] is True
    assert task_packet["expected_gpt_output"] == "project_state/decision_packet.md"
    assert "do not commit full solve_reports directory" in task_packet["do_not_do"]


def test_task_packet_omits_full_progress_log_and_full_solve_reports(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    progress_log = tmp_path / "PROJECT_PROGRESS_LOG.txt"
    progress_log.write_text("VERY_LONG_SECRET_PROGRESS_LOG_SENTINEL", encoding="utf-8")
    _make_minimal_harness_run(reports_dir)

    build_project_state(
        reports_dir=reports_dir,
        state_dir=state_dir,
        sample="samplereverse",
        progress_log=progress_log,
    )

    packet_text = (state_dir / "task_packet.json").read_text(encoding="utf-8")
    assert "VERY_LONG_SECRET_PROGRESS_LOG_SENTINEL" not in packet_text
    assert "SOLVE_REPORTS_FULL_SENTINEL" not in packet_text
    task_packet = _read_json(state_dir / "task_packet.json")
    assert "artifact_refs" in task_packet
    assert any(item["name"] == "full solve_reports" for item in task_packet["omitted"])


def test_model_gate_returns_false_when_artifacts_are_missing(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    run_dir = reports_dir / "harness_runs" / "incomplete"
    _write_json(run_dir / "summary.json", {"run_name": "incomplete", "error_cases": 0})
    _write_json(run_dir / "run_manifest.json", {"run_name": "incomplete", "status": "completed"})
    _write_json(run_dir / "case_results" / "samplereverse.json", {"status": "completed_no_expected"})

    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")

    model_gate = _read_json(state_dir / "model_gate.json")
    task_packet = _read_json(state_dir / "task_packet.json")
    assert model_gate["should_call_model"] is False
    assert model_gate["next_local_action"] == "collect_artifacts"
    assert task_packet["task"] == "collect_missing_evidence"
    assert "frontier_summary" in model_gate["missing_evidence"]


def test_windows_path_style_outputs_are_compatible(tmp_path: Path) -> None:
    reports_dir = tmp_path / "solve_reports"
    state_dir = tmp_path / "project_state"
    _make_minimal_harness_run(reports_dir)

    build_project_state(
        reports_dir=Path(str(reports_dir)),
        state_dir=Path(str(state_dir)),
        sample="samplereverse",
        run_name="samplereverse_stalled",
    )

    artifact_index = _read_json(state_dir / "artifact_index.json")
    assert Path(artifact_index["latest_summary"]).name == "summary.json"
    assert Path(artifact_index["latest_artifacts"]["frontier_summary"]).name == (
        "samplereverse_compare_aware_frontier_summary.json"
    )


def test_new_round_status_and_archive_round_create_expected_files(tmp_path: Path, capsys) -> None:
    state_dir = tmp_path / "project_state"
    reports_dir = tmp_path / "solve_reports"
    _make_minimal_harness_run(reports_dir)
    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")

    assert main(["status", "--state-dir", str(state_dir)]) == 0
    output = capsys.readouterr().out
    assert "latest_harness_run:" in output
    assert "expected_gpt_output:" in output

    result = archive_round(state_dir=state_dir)
    round_dir = state_dir / "rounds" / "round_001"
    assert result["round_id"] == "round_001"
    assert round_dir.exists()
    assert (round_dir / "current_state.json").exists()
    assert (round_dir / "artifact_index.json").exists()
    assert (round_dir / "negative_results.json").exists()
    assert (round_dir / "model_gate.json").exists()
    assert (round_dir / "task_packet.json").exists()
    assert (round_dir / "decision_packet.md").exists()
    assert (round_dir / "codex_execution_report.md").exists()
    assert (round_dir / "git_diff.patch").exists()
    assert (round_dir / "pytest_result.txt").exists()


def test_archive_round_does_not_overwrite_existing_round(tmp_path: Path) -> None:
    state_dir = tmp_path / "project_state"
    reports_dir = tmp_path / "solve_reports"
    _make_minimal_harness_run(reports_dir)
    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")
    archive_round(state_dir=state_dir)
    sentinel = state_dir / "rounds" / "round_001" / "sentinel.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    result = archive_round(state_dir=state_dir)

    assert result["round_id"] == "round_002"
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert (state_dir / "rounds" / "round_002" / "task_packet.json").exists()


def test_pack_contains_only_allowed_project_state_files(tmp_path: Path) -> None:
    state_dir = tmp_path / "project_state"
    reports_dir = tmp_path / "solve_reports"
    _make_minimal_harness_run(reports_dir)
    (reports_dir / "secret.exe").write_bytes(b"MZ")
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")
    build_project_state(reports_dir=reports_dir, state_dir=state_dir, sample="samplereverse")
    archive_round(state_dir=state_dir)
    out_path = tmp_path / "gpt_context_pack.zip"

    result = pack_context(state_dir=state_dir, out_path=out_path)

    assert out_path.exists()
    assert "project_state/task_packet.json" in result["files"]
    with zipfile.ZipFile(out_path) as archive:
        names = archive.namelist()
    assert "project_state/current_state.json" in names
    assert "project_state/decision_packet.md" in names
    assert any(name.endswith("git_diff.patch") for name in names)
    assert not any(name.startswith("solve_reports/") for name in names)
    assert not any(name.endswith(".exe") for name in names)
    assert ".env" not in names
