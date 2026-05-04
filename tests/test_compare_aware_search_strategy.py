import json
import subprocess
from pathlib import Path

import pytest

from reverse_agent.evidence import StructuredEvidence
from reverse_agent.profiles.samplereverse import SamplereverseProfile
from reverse_agent.samplereverse_z3 import _optimize_ready, solve_targeted_prefix8
from reverse_agent.strategies import compare_aware_search
from reverse_agent.strategies.base import StrategyResult
from reverse_agent.strategies.compare_aware_search import (
    BRIDGE_RESULT_FILE_NAME,
    CompareAwareSearchStrategy,
    DYNAMIC_COMPARE_PATH_PROBE_FILE_NAME,
    FRONTIER_ANCHOR_MODE,
    FRONTIER_EXACT0_SUBMODE,
    FRONTIER_EXACT1_SUBMODE,
    FRONTIER_MAX_ANCHORS,
    GUIDED_POOL_EXPLORATION_SLOTS,
    GUIDED_POOL_TOP_VALUES,
    H1_H3_BOUNDARY_CANDIDATE_LIMIT,
    H1_H3_BOUNDARY_VALIDATION_FILE_NAME,
    PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT,
    PROFILE_TRANSFORM_HYPOTHESIS_MATRIX_FILE_NAME,
    PROJECTED_PRESERVE_SECOND_HOP_ROLE,
    RESULT_FILE_NAME,
    TRANSFORM_TRACE_CONSISTENCY_FILE_NAME,
    VALIDATION_FILE_NAME,
    _alternate_locked_pair_positions_for_exact1,
    _annotate_frontier_improvement_gate,
    _candidate_sort_key,
    _collect_validation_entries,
    _collect_frontier_promoted_anchors,
    _diverse_validation_candidates,
    _exact1_projected_competition_reason_from_runs,
    _exact1_projected_competition_summary,
    _extract_hot_positions,
    _feedback_value_pools_from_frontier_entries,
    _frontier_anchor_candidates,
    _frontier_continuation_candidates,
    _guided_pool_beam_entries,
    _improved_frontier_candidates,
    _mine_exact1_lineage_value_sources,
    _exact2_basin_smt_diagnostic_payload,
    _prefix_boundary_breakdown_from_prefix,
    _refine_anchor_plan,
    _select_smt_base_entry,
    _validated_projected_preserve_second_hop_candidates,
    run_compare_aware_smt,
    run_dynamic_compare_path_probe,
    run_exact2_basin_value_pool_evaluation,
    run_h1_h3_boundary_validation,
    run_profile_transform_hypothesis_audit,
    run_transform_trace_consistency_diagnostic,
    validate_compare_aware_results,
    resolve_compare_aware_anchors,
)
from reverse_agent.tool_runners import ToolRunArtifact
from reverse_agent.transforms.samplereverse import (
    SamplereverseTransformModel,
    score_compare_prefix,
    trace_candidate_transform,
)


def test_score_compare_prefix_counts_known_exact2_basins() -> None:
    assert score_compare_prefix(bytes.fromhex("66006c0038ac00000000"))["ci_exact_wchars"] == 2
    assert score_compare_prefix(bytes.fromhex("46004c007e4000000000"))["ci_exact_wchars"] == 2


def test_prefix_boundary_breakdown_explains_exact2_exact1_and_projected() -> None:
    exact2 = _prefix_boundary_breakdown_from_prefix(
        bytes.fromhex("46006c004464830d311c"),
        candidate_hex="78d540b49c59077041414141414141",
    )
    exact1 = _prefix_boundary_breakdown_from_prefix(
        bytes.fromhex("460061357f0b8c688502"),
        candidate_hex="5a3e7f46ddd474d041414141414141",
    )
    projected = _prefix_boundary_breakdown_from_prefix(
        bytes.fromhex("74934b156ba69ef3370f"),
        candidate_hex="5a3f7f46ddd474d041414141414141",
    )

    assert exact2["ci_exact_wchars"] == 2
    assert [item["exact_ci"] for item in exact2["wchar_deltas"][:3]] == [True, True, False]
    assert exact2["wchar_deltas"][2]["raw_pair_hex"] == "4464"
    assert exact2["wchar_deltas"][2]["target_pair_hex"] == "6100"
    assert exact1["ci_exact_wchars"] == 1
    assert [item["exact_ci"] for item in exact1["wchar_deltas"][:2]] == [True, False]
    assert projected["ci_exact_wchars"] == 0
    assert projected["wchar_deltas"][0]["raw_pair_hex"] == "7493"


def test_trace_candidate_transform_records_layout_boundaries_and_known_runtime_prefix() -> None:
    trace = trace_candidate_transform("78d540b49c59077041414141414141")

    assert trace["valid"] is True
    assert trace["candidate_raw_bytes"]["hex"] == "78d540b49c59077041414141414141"
    assert trace["candidate_layout"]["candidate_length_bytes"] == 15
    assert trace["candidate_layout"]["prefix_hex"] == "78d540b49c590770"
    assert trace["candidate_layout"]["suffix_is_all_A"] is True
    assert trace["nibble_expansion"]["prefix_expanded_length_bytes"] == 16
    assert trace["utf16_payload"]["prefix_raw_length_bytes"] == 32
    assert trace["base64_boundary"]["prefix_ends_on_base64_chunk_boundary"] is False
    assert trace["base64_boundary"]["prefix_last_chunk_raw_remainder"] == 2
    assert trace["rc4"]["key_length_bytes"] == trace["rc4"]["key_source_base64_chars"]
    assert trace["rc4"]["decrypt_prefix_hex"].startswith("46006c004464830d311c")
    assert trace["compare_boundary"]["raw_prefix_hex_10"] == "46006c004464830d311c"
    assert trace["compare_boundary"]["compare_window_hex"] == "46006c004464830d311c"
    assert trace["compare_boundary"]["ci_exact_wchars"] == 2
    assert len(trace["prefix_length_table"]) == 10
    assert trace["prefix_length_table"][0]["candidate_prefix_len_bytes"] == 1
    assert trace["prefix_length_table"][9]["candidate_prefix_len_bytes"] == 10
    assert {"utf16le_hex", "base64_text", "base64_len", "rc4_input_len"} <= set(
        trace["prefix_length_table"][7]
    )
    assert [item["exact_ci"] for item in trace["compare_boundary"]["wchar_deltas"][:3]] == [
        True,
        True,
        False,
    ]


def test_solve_targeted_prefix8_records_bounded_value_pools_with_base_value() -> None:
    if not _optimize_ready():
        pytest.skip("z3 optimize is not installed")

    base_anchor = "78d540b49c590770"
    result = solve_targeted_prefix8(
        base_anchor=base_anchor,
        variable_byte_positions=[0],
        variable_nibble_positions=[],
        value_pools={0: [0x00]},
        timeout_ms=10,
    )

    assert result.attempted is True
    assert any("value_pools=0:78/00" in item for item in result.evidence or [])
    assert result.diagnostics
    assert result.diagnostics["solver_type"] == "Optimize"
    assert result.diagnostics["timeout_ms"] == 10
    assert result.diagnostics["symbolic_compare_bytes"] == 10
    assert result.diagnostics["value_pool_sizes"]["0"] == 2


def test_score_prefix_exposes_long_window_structure_metrics() -> None:
    metrics = SamplereverseTransformModel().score_prefix(
        bytes.fromhex(
            "66006c00610067007b00410042005f007d00"
            "11223344556677889900aabbccddeeff"
        )
    )

    assert metrics["raw_prefix_hex"] == "66006c00610067007b00"
    assert metrics["raw_prefix_hex_64"].startswith("66006c00610067007b00410042005f007d00")
    assert metrics["wide_ascii_contiguous_16"] >= 8
    assert metrics["wide_ascii_total_16"] >= 8
    assert metrics["wide_zero_high_pairs_16"] >= 8
    assert metrics["flaglike_tail_pairs_16"] == 3


def test_candidate_sort_key_uses_long_window_tiebreakers() -> None:
    transform_model = SamplereverseTransformModel()
    stronger = {
        "candidate_hex": "111111111111111141414141414141",
        "raw_prefix_hex_64": "66006c00610067007b00410042005f007d00",
        "raw_prefix_hex": "66006c00610067007b00",
        "ci_exact_wchars": 5,
        "ci_distance5": 0,
        "raw_distance10": 0,
    }
    weaker = {
        "candidate_hex": "222222222222222241414141414141",
        "raw_prefix_hex_64": "66006c00610067007b00ff00ff00ff00",
        "raw_prefix_hex": "66006c00610067007b00",
        "ci_exact_wchars": 5,
        "ci_distance5": 0,
        "raw_distance10": 0,
    }

    assert _candidate_sort_key(stronger, transform_model) < _candidate_sort_key(weaker, transform_model)


def test_collect_validation_entries_prefers_explicit_validation_candidates() -> None:
    payload = {
        "top_entries": [
            {
                "candidate_hex": "aaaaaaaaaaaaaaaa41414141414141",
                "raw_prefix_hex": "66006c00000000000000",
                "ci_exact_wchars": 2,
                "ci_distance5": 999,
                "raw_distance10": 999,
            }
        ],
        "validation_candidates": [
            {
                "candidate_hex": "bbbbbbbbbbbbbbbb41414141414141",
                "raw_prefix_hex": "66006c00610000000000",
                "ci_exact_wchars": 3,
                "ci_distance5": 100,
                "raw_distance10": 100,
            },
            {
                "candidate_hex": "cccccccccccccccc41414141414141",
                "raw_prefix_hex": "66006c00610067000000",
                "ci_exact_wchars": 4,
                "ci_distance5": 10,
                "raw_distance10": 10,
            },
        ],
    }

    entries = _collect_validation_entries(payload, SamplereverseTransformModel(), validate_top=1)

    assert [entry["candidate_hex"] for entry in entries] == ["bbbbbbbbbbbbbbbb41414141414141"]


def test_diverse_validation_candidates_keeps_cross_basin_frontier() -> None:
    entries = [
        {
            "candidate_hex": "78d540b49c59077041414141414141",
            "raw_prefix_hex": "46006c004464830d311c",
            "ci_exact_wchars": 2,
            "ci_distance5": 246,
            "raw_distance10": 304,
        },
        {
            "candidate_hex": "95a3f65dcedb629041414141414141",
            "raw_prefix_hex": "6600583a481ab842862c",
            "ci_exact_wchars": 1,
            "ci_distance5": 305,
            "raw_distance10": 331,
        },
        {
            "candidate_hex": "e80c7471d342f6f041414141414141",
            "raw_prefix_hex": "7d0b4e0148099e048930",
            "ci_exact_wchars": 0,
            "ci_distance5": 174,
            "raw_distance10": 220,
        },
    ]

    frontier = _diverse_validation_candidates(
        entries,
        transform_model=SamplereverseTransformModel(),
        validate_top=4,
    )

    assert [entry["candidate_hex"] for entry in frontier] == [
        "78d540b49c59077041414141414141",
        "95a3f65dcedb629041414141414141",
        "e80c7471d342f6f041414141414141",
    ]


def test_frontier_guided_validation_candidates_preserve_projected_handoff_slot() -> None:
    guided_entries = [
        {
            "candidate_hex": f"{idx:016x}41414141414141",
            "cand8_hex": f"{idx:016x}",
            "ci_exact_wchars": 1,
            "ci_distance5": 200 + idx,
            "raw_distance10": 300 + idx,
        }
        for idx in range(1, 12)
    ]
    handoff = {
        "candidate_hex": "5a3f7f46ddd474d041414141414141",
        "cand8_hex": "5a3f7f46ddd474d0",
        "ci_exact_wchars": 0,
        "ci_distance5": 740,
        "raw_distance10": 820,
        "pair_candidate_origin": "exact1_projected_preserve_lane",
        "pair_projected_boundary_role": "projected_winner_with_base",
        "pair_projected_winner_gate_status": "projected_winner_promoted_to_near_local",
    }

    selected = compare_aware_search._frontier_guided_validation_candidates(
        [*guided_entries, handoff],
        [handoff],
        validate_top=10,
    )

    assert len(selected) == 10
    assert selected[-1]["cand8_hex"] == "5a3f7f46ddd474d0"
    assert selected[-1]["frontier_role"] == "projected_preserve_handoff"
    assert guided_entries[9]["cand8_hex"] not in {item["cand8_hex"] for item in selected}


def test_resolve_compare_aware_anchors_keeps_new_default_anchor_first(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_recent_compare_aware_payloads",
        lambda limit=16: [
            {
                "top_entries": [
                    {"candidate_hex": "4a78f0eaeb4f13b041414141414141"},
                    {"candidate_hex": "0123456789abcde041414141414141"},
                ]
            }
        ],
    )

    anchors = resolve_compare_aware_anchors(SamplereverseTransformModel(), ["4a78f0eaeb4f13b0"])

    assert anchors[:3] == [
        "78d540b49c590770",
        "4a78f0eaeb4f13b0",
        "95a3f65dcedb6290",
    ]
    assert "0123456789abcde0" in anchors


def test_extract_hot_positions_dedupes_and_limits_to_five() -> None:
    pair_entries = [
        {"positions_or_nibbles": [0, 1]},
        {"positions_or_nibbles": [0, 2]},
        {"positions_or_nibbles": [0, 3]},
        {"positions_or_nibbles": [1, 4]},
        {"positions_or_nibbles": [2, 4]},
        {"positions_or_nibbles": [5, 6]},
        {"positions_or_nibbles": [5, 7]},
        {"positions_or_nibbles": [6, 7]},
    ]

    hot = _extract_hot_positions(pair_entries, max_positions=5)

    assert hot == [0, 1, 2, 4, 5]


def test_refine_anchor_plan_only_keeps_main_promoted_and_frontier() -> None:
    anchors, sources = _refine_anchor_plan(
        "78d540b49c590770",
        [
            "789d40b49c310770",
            "95a3f65dcedb6290",
            "789d40b49c310770",
        ],
    )

    assert anchors == [
        "78d540b49c590770",
        "789d40b49c310770",
        "95a3f65dcedb6290",
    ]
    assert sources == {
        "78d540b49c590770": "seed_anchor",
        "789d40b49c310770": "bridge_promoted",
        "95a3f65dcedb6290": "bridge_promoted",
    }


def test_validate_compare_aware_results_persists_extended_runtime_prefix_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    compare_probe_script = tmp_path / "compare_probe.py"
    compare_probe_script.write_text("# mock compare probe\n", encoding="utf-8")
    result_path = tmp_path / RESULT_FILE_NAME
    result_path.write_text(
        json.dumps(
            {
                "validation_candidates": [
                    {
                        "candidate_hex": "78d540b49c59077041414141414141",
                        "cand8_hex": "78d540b49c590770",
                        "raw_prefix_hex": "46006c004464830d311c",
                        "ci_exact_wchars": 2,
                        "ci_distance5": 246,
                        "raw_distance10": 304,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run(command, capture_output, text, encoding, errors):
        calls.append(list(command))
        compare_out = Path(command[command.index("--out") + 1])
        compare_out.write_text(
            json.dumps(
                {
                    "summary": "compare ok",
                    "lhs_wide_hex": "46006c004464830d311c112233445566",
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "runtime_lhs_prefix_hex": "46006c004464830d311c112233445566",
                    "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
                    "runtime_lhs_prefix_hex_16": "46006c004464830d311c112233445566",
                    "runtime_lhs_prefix_bytes_captured": 16,
                    "compare_semantics_agree": True,
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(compare_aware_search, "_compare_probe_script_path", lambda: compare_probe_script)
    monkeypatch.setattr(compare_aware_search.subprocess, "run", fake_run)

    validation_path, validations = validate_compare_aware_results(
        target=target,
        artifacts_dir=tmp_path / "validation",
        result_path=result_path,
        transform_model=SamplereverseTransformModel(),
        validate_top=1,
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    written = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validations[0]["runtime_lhs_prefix_hex"] == "46006c004464830d311c112233445566"
    assert validations[0]["runtime_lhs_prefix_hex_10"] == "46006c004464830d311c"
    assert validations[0]["runtime_lhs_prefix_hex_16"] == "46006c004464830d311c112233445566"
    assert validations[0]["runtime_lhs_prefix_bytes_captured"] == 16
    assert written["validations"][0]["runtime_lhs_prefix_hex_16"] == "46006c004464830d311c112233445566"
    assert "--capture-prefix-bytes" in calls[0]
    assert calls[0][calls[0].index("--capture-prefix-bytes") + 1] == "64"


def test_compare_aware_strategy_stops_after_bridge_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    bridge_result = {
        "pairscan_path": str(tmp_path / "pairscan_summary.json"),
        "bridge_result_path": str(tmp_path / BRIDGE_RESULT_FILE_NAME),
        "bridge_validation_path": str(tmp_path / "bridge_validation.json"),
        "bridge_entries": [
            {
                "stage": "triad",
                "base_anchor": "78d540b49c590770",
                "positions_or_nibbles": [1, 3, 4],
                "candidate_hex": "7883401f9c59077041414141414141",
                "cand8_hex": "7883401f9c590770",
                "raw_prefix_hex": "46003e1dfd2bb62a3d09",
                "exact": 3,
                "dist4": 10,
                "dist6": 20,
                "dist10": 30,
                "ci_exact_wchars": 3,
                "ci_distance5": 120,
                "raw_distance10": 30,
            }
        ],
        "bridge_validations": [
            {
                "candidate_hex": "7883401f9c59077041414141414141",
                "cand8_hex": "7883401f9c590770",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 3,
                "runtime_ci_distance5": 120,
                "compare_summary": "bridge ok",
            }
        ],
        "hot_positions": [1, 3, 4],
        "hot_nibbles": [2, 3, 6, 7, 10],
    }

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_bridge", lambda **kwargs: bridge_result)
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_refine",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("refine should be skipped")),
    )
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_smt",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("smt should be skipped")),
    )

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
    )

    assert result.candidates == []
    assert [artifact.tool_name for artifact in result.artifacts] == [
        "CompareAwareBridge",
        "CompareAwareBridgeValidation",
    ]
    assert result.metadata["completed_stage"] == "bridge"


def test_compare_aware_strategy_stops_after_guided_pool_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_bridge",
        lambda **kwargs: {
            "pairscan_path": str(tmp_path / "pairscan_summary.json"),
            "bridge_result_path": str(tmp_path / BRIDGE_RESULT_FILE_NAME),
            "bridge_validation_path": str(tmp_path / "bridge_validation.json"),
            "bridge_entries": [],
            "bridge_validations": [],
            "hot_positions": [0, 1, 2],
            "hot_nibbles": [0, 1, 2, 3, 4],
        },
    )
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_guided_pool",
        lambda **kwargs: {
            "guided_pool_result_path": str(tmp_path / "guided_pool_result.json"),
            "guided_pool_validation_path": str(tmp_path / "guided_pool_validation.json"),
            "guided_entries": [
                {
                    "stage": "guided_pool",
                    "base_anchor": "78d540b49c590770",
                    "positions_or_nibbles": [0, 1, 2, 3, 4],
                    "candidate_hex": "7883401f9c59077041414141414141",
                    "cand8_hex": "7883401f9c590770",
                    "raw_prefix_hex": "46003e1dfd2bb62a3d09",
                    "raw_prefix_hex_64": "46003e1dfd2bb62a3d09",
                    "ci_exact_wchars": 3,
                    "ci_distance5": 120,
                    "raw_distance10": 30,
                }
            ],
            "guided_validations": [
                {
                    "candidate_hex": "7883401f9c59077041414141414141",
                    "cand8_hex": "7883401f9c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 3,
                    "runtime_ci_distance5": 120,
                    "compare_summary": "guided pool ok",
                }
            ],
            "positions": [0, 1, 2, 3, 4],
            "value_pools": {"0": [0x78], "1": [0x83]},
            "beam_limit": 16,
        },
    )
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_refine",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("refine should be skipped")),
    )
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_smt",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("smt should be skipped")),
    )

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
    )

    assert [artifact.tool_name for artifact in result.artifacts] == [
        "CompareAwareBridge",
        "CompareAwareBridgeValidation",
        "CompareAwareGuidedPool",
        "CompareAwareGuidedPoolValidation",
    ]
    assert result.metadata["completed_stage"] == "guided_pool"


def test_compare_aware_strategy_runs_refine_then_smt_and_uses_promoted_anchors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_bridge",
        lambda **kwargs: {
            "pairscan_path": str(tmp_path / "pairscan_summary.json"),
            "bridge_result_path": str(tmp_path / BRIDGE_RESULT_FILE_NAME),
            "bridge_validation_path": str(tmp_path / "bridge_validation.json"),
            "bridge_entries": [
                {
                    "stage": "triad",
                    "base_anchor": "78d540b49c590770",
                    "positions_or_nibbles": [1, 3, 4],
                    "candidate_hex": "789d40b49c31077041414141414141",
                    "cand8_hex": "789d40b49c310770",
                    "raw_prefix_hex": "6600439ab22150168897",
                    "exact": 2,
                    "dist4": 50,
                    "dist6": 80,
                    "dist10": 120,
                    "ci_exact_wchars": 2,
                    "ci_distance5": 260,
                    "raw_distance10": 120,
                }
            ],
            "bridge_validations": [
                {
                    "candidate_hex": "789d40b49c31077041414141414141",
                    "cand8_hex": "789d40b49c310770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 260,
                    "compare_summary": "bridge no progress",
                }
            ],
            "hot_positions": [1, 3, 4],
            "hot_nibbles": [2, 3, 6, 7, 10],
        },
    )
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_guided_pool",
        lambda **kwargs: {
            "guided_pool_result_path": str(tmp_path / "guided_pool_result.json"),
            "guided_pool_validation_path": str(tmp_path / "guided_pool_validation.json"),
            "guided_entries": [
                {
                    "stage": "guided_pool",
                    "base_anchor": "78d540b49c590770",
                    "positions_or_nibbles": [0, 1, 2, 3, 4],
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "raw_prefix_hex": "46006c004464830d311c",
                    "raw_prefix_hex_64": "46006c004464830d311c",
                    "ci_exact_wchars": 2,
                    "ci_distance5": 246,
                    "raw_distance10": 304,
                }
            ],
            "guided_validations": [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "compare_summary": "guided plateau",
                }
            ],
            "positions": [0, 1, 2, 3, 4],
            "value_pools": {"0": [0x78]},
            "beam_limit": 16,
        },
    )

    def fake_run_compare_aware_refine(
        *,
        artifacts_dir: Path,
        search_budget: int,
        seed: int,
        anchors: list[str],
        snapshot_interval: int,
        log,
    ) -> Path:
        _ = search_budget, seed, snapshot_interval, log
        captured["refine_anchors"] = anchors
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out = artifacts_dir / RESULT_FILE_NAME
        out.write_text(
            json.dumps(
                {
                    "best": {
                        "candidate_hex": "78d540b49c59077041414141414141",
                        "cand8_hex": "78d540b49c590770",
                        "raw_prefix_hex": "46006c004464830d311c",
                        "ci_exact_wchars": 2,
                        "ci_distance5": 246,
                        "raw_distance10": 304,
                    },
                    "top_entries": [
                        {
                            "candidate_hex": "78d540b49c59077041414141414141",
                            "cand8_hex": "78d540b49c590770",
                            "raw_prefix_hex": "46006c004464830d311c",
                            "ci_exact_wchars": 2,
                            "ci_distance5": 246,
                            "raw_distance10": 304,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return out

    def fake_validate_compare_aware_results(
        *,
        target: Path,
        artifacts_dir: Path,
        result_path: Path,
        transform_model: SamplereverseTransformModel,
        validate_top: int,
        per_probe_timeout: float,
        log,
        output_file_name: str = VALIDATION_FILE_NAME,
        compare_output_prefix: str = "samplereverse_compare_aware_compare",
    ) -> tuple[Path, list[dict[str, object]]]:
        _ = target, artifacts_dir, transform_model, validate_top, per_probe_timeout, log, compare_output_prefix
        out = result_path.parent / output_file_name
        validations = [
            {
                "candidate_hex": "78d540b49c59077041414141414141",
                "cand8_hex": "78d540b49c590770",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 2,
                "runtime_ci_distance5": 246,
                "compare_summary": "refine plateau",
            }
        ]
        out.write_text(json.dumps({"validations": validations}, ensure_ascii=False), encoding="utf-8")
        return out, validations

    def fake_run_compare_aware_smt(**kwargs):
        captured["smt_base"] = kwargs["base_entry"]["cand8_hex"]
        return {
            "result_path": str(tmp_path / "smt_result.json"),
            "validation_path": str(tmp_path / "smt_validation.json"),
            "entry": {
                "stage": "smt",
                "base_anchor": "78d540b49c590770",
                "positions_or_nibbles": [1, 3, 4],
                "candidate_hex": "78d540b49c59077041414141414141",
                "cand8_hex": "78d540b49c590770",
                "raw_prefix_hex": "46006c004464830d311c",
                "exact": 2,
                "dist4": 50,
                "dist6": 80,
                "dist10": 120,
                "ci_exact_wchars": 2,
                "ci_distance5": 246,
                "raw_distance10": 304,
            },
            "validations": [],
            "payload": {
                "summary": "smt attempted",
                "variable_byte_positions": [1, 3, 4],
                "variable_nibble_positions": [2, 3, 6],
            },
        }

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_refine", fake_run_compare_aware_refine)
    monkeypatch.setattr(compare_aware_search, "validate_compare_aware_results", fake_validate_compare_aware_results)
    monkeypatch.setattr(compare_aware_search, "run_compare_aware_smt", fake_run_compare_aware_smt)

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
    )

    assert captured["refine_anchors"] == [
        "78d540b49c590770",
        "789d40b49c310770",
        "95a3f65dcedb6290",
    ]
    assert captured["smt_base"] == "78d540b49c590770"
    assert result.metadata["completed_stage"] in {
        "transform_trace_consistency",
        "dynamic_compare_path_probe",
        "h1_h3_boundary_validation",
    }
    assert result.metadata["smt"]["payload"]["exact2_basin_smt"]["base_anchor"] == "78d540b49c590770"
    assert result.metadata["transform_trace_consistency"]["payload"]["classification"] in {
        "transform_model_confirmed",
        "evidence_insufficient",
        "transform_mismatch_found",
    }
    if result.metadata["h1_h3_boundary_validation"]:
        assert result.metadata["h1_h3_boundary_validation"]["payload"]["classification"] in {
            "h1_h3_boundary_contrast_exhausted_no_gain",
            "h1_h3_boundary_contrast_improved",
        }
    smt_payload = json.loads((tmp_path / "smt_result.json").read_text(encoding="utf-8"))
    assert smt_payload["exact2_basin_smt"]["base_anchor"] == "78d540b49c590770"
    assert smt_payload["exact2_basin_smt"]["prefix_boundary"]["ci_exact_wchars"] == 2
    assert any(artifact.tool_name == "CompareAwareSMT" for artifact in result.artifacts)


def test_guided_pool_uses_bounded_single_byte_pools(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}

    def fake_top_compare_aware_single_byte_entries(*, base_anchor, positions, transform_model, top_k=12):
        _ = transform_model
        base_bytes = bytes.fromhex(base_anchor)
        captured["positions"] = list(positions)
        captured["top_k"] = top_k
        return {
            pos: [
                {
                    "candidate_hex": bytes(base_bytes).hex() + "41414141414141",
                    "cand8_hex": bytes(base_bytes).hex(),
                    "mutated_position": pos,
                    "mutated_byte_value": base_bytes[pos],
                    "ci_exact_wchars": 2,
                    "ci_distance5": 246,
                    "raw_distance10": 304,
                    "wide_ascii_contiguous_16": 2,
                    "wide_ascii_total_16": 2,
                    "wide_zero_high_pairs_16": 2,
                    "flaglike_tail_pairs_16": 0,
                },
                {
                    "candidate_hex": (
                        bytes(base_bytes[:pos] + bytes([(base_bytes[pos] + 1) & 0xFF]) + base_bytes[pos + 1 :]).hex()
                        + "41414141414141"
                    ),
                    "cand8_hex": bytes(base_bytes[:pos] + bytes([(base_bytes[pos] + 1) & 0xFF]) + base_bytes[pos + 1 :]).hex(),
                    "mutated_position": pos,
                    "mutated_byte_value": (base_bytes[pos] + 1) & 0xFF,
                    "ci_exact_wchars": 1,
                    "ci_distance5": 300 + pos,
                    "raw_distance10": 320 + pos,
                    "wide_ascii_contiguous_16": 1,
                    "wide_ascii_total_16": 1,
                    "wide_zero_high_pairs_16": 1,
                    "flaglike_tail_pairs_16": 0,
                },
            ]
            for pos in positions
        }

    def fake_validate_compare_aware_results(**kwargs):
        out = Path(kwargs["artifacts_dir"]) / VALIDATION_FILE_NAME
        validations: list[dict[str, object]] = []
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"validations": validations}), encoding="utf-8")
        return out, validations

    monkeypatch.setattr(
        compare_aware_search,
        "_top_compare_aware_single_byte_entries",
        fake_top_compare_aware_single_byte_entries,
    )
    monkeypatch.setattr(compare_aware_search, "validate_compare_aware_results", fake_validate_compare_aware_results)

    result = compare_aware_search.run_compare_aware_guided_pool(
        target=target,
        artifacts_dir=tmp_path / "guided_pool",
        base_anchor="78d540b49c590770",
        bridge_entries=[
            {"candidate_hex": "789940b49c59077041414141414141"},
            {"candidate_hex": "78d541b49c59077041414141414141"},
            {"candidate_hex": "78d540b59c59077041414141414141"},
        ],
        transform_model=SamplereverseTransformModel(),
        validate_top=1,
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    assert captured["top_k"] == GUIDED_POOL_TOP_VALUES
    assert captured["positions"] == list(range(8))
    assert all(0 <= pos < 5 for pos in result["positions"])
    assert len(result["positions"]) <= 5
    assert Path(result["guided_pool_result_path"]).exists()


def test_guided_pool_beam_entries_keeps_small_exploration_tail() -> None:
    transform_model = SamplereverseTransformModel()
    candidates = [
        {
            "candidate_hex": "78d540b49c59077041414141414141",
            "raw_prefix_hex_64": "66006c00610067007b00",
            "raw_prefix_hex": "66006c00610067007b00",
            "ci_exact_wchars": 2,
            "ci_distance5": 246,
            "raw_distance10": 304,
            "wide_ascii_contiguous_16": 2,
            "wide_ascii_total_16": 2,
            "wide_zero_high_pairs_16": 2,
            "flaglike_tail_pairs_16": 0,
        },
        {
            "candidate_hex": "78d540b49c59077141414141414141",
            "raw_prefix_hex_64": "46006c00440000000000",
            "raw_prefix_hex": "46006c00440000000000",
            "ci_exact_wchars": 0,
            "ci_distance5": 250,
            "raw_distance10": 280,
            "wide_ascii_contiguous_16": 1,
            "wide_ascii_total_16": 1,
            "wide_zero_high_pairs_16": 1,
            "flaglike_tail_pairs_16": 0,
        },
        {
            "candidate_hex": "78d540b49c59077241414141414141",
            "raw_prefix_hex_64": "46006c00450000000000",
            "raw_prefix_hex": "46006c00450000000000",
            "ci_exact_wchars": 0,
            "ci_distance5": 255,
            "raw_distance10": 281,
            "wide_ascii_contiguous_16": 1,
            "wide_ascii_total_16": 1,
            "wide_zero_high_pairs_16": 1,
            "flaglike_tail_pairs_16": 0,
        },
    ]

    beam, stats = _guided_pool_beam_entries(
        candidates=candidates,
        transform_model=transform_model,
        exact_floor=1,
        anchor_mode=FRONTIER_ANCHOR_MODE,
        exploration_slots=GUIDED_POOL_EXPLORATION_SLOTS,
    )

    assert beam[0]["candidate_hex"] == "78d540b49c59077041414141414141"
    assert len(beam) == 3
    assert stats["primary_kept"] == 1
    assert stats["exploratory_kept"] == 2
    assert stats["floor_matched"] == 1


def test_frontier_anchor_candidates_keep_exact2_exact1_exact0_representatives() -> None:
    validations = [
        {
            "candidate_hex": "78d540b49c59077041414141414141",
            "cand8_hex": "78d540b49c590770",
            "runtime_ci_exact_wchars": 2,
            "runtime_ci_distance5": 246,
            "compare_semantics_agree": True,
        },
        {
            "candidate_hex": "95a3f65dcedb629041414141414141",
            "cand8_hex": "95a3f65dcedb6290",
            "runtime_ci_exact_wchars": 1,
            "runtime_ci_distance5": 305,
            "compare_semantics_agree": True,
        },
        {
            "candidate_hex": "a47a0a74bd35355041414141414141",
            "cand8_hex": "a47a0a74bd353550",
            "runtime_ci_exact_wchars": 0,
            "runtime_ci_distance5": 208,
            "compare_semantics_agree": True,
        },
    ]

    anchors = _frontier_anchor_candidates(validations)

    assert anchors == [
        {
            "anchor": "78d540b49c590770",
            "frontier_role": "exact2_seed",
            "candidate_hex": "78d540b49c59077041414141414141",
            "runtime_ci_exact_wchars": 2,
            "runtime_ci_distance5": 246,
            "compare_semantics_agree": True,
            "source_anchor": "78d540b49c590770",
            "anchor_mode": "exact2",
            "frontier_submode": "",
            "anchor_lineage": "exact2_seed(78d540b49c590770)",
        },
        {
            "anchor": "95a3f65dcedb6290",
            "frontier_role": "exact1_frontier",
            "candidate_hex": "95a3f65dcedb629041414141414141",
            "runtime_ci_exact_wchars": 1,
            "runtime_ci_distance5": 305,
            "compare_semantics_agree": True,
            "source_anchor": "95a3f65dcedb6290",
            "anchor_mode": "frontier",
            "frontier_submode": FRONTIER_EXACT1_SUBMODE,
            "anchor_lineage": "exact1_frontier(95a3f65dcedb6290)",
        },
        {
            "anchor": "a47a0a74bd353550",
            "frontier_role": "exact0_frontier",
            "candidate_hex": "a47a0a74bd35355041414141414141",
            "runtime_ci_exact_wchars": 0,
            "runtime_ci_distance5": 208,
            "compare_semantics_agree": True,
            "source_anchor": "a47a0a74bd353550",
            "anchor_mode": "frontier",
            "frontier_submode": FRONTIER_EXACT0_SUBMODE,
            "anchor_lineage": "exact0_frontier(a47a0a74bd353550)",
        },
    ]


def test_collect_frontier_promoted_anchors_uses_context_lineage() -> None:
    validations = [
        {
            "candidate_hex": "a47a0a74bd35355041414141414141",
            "cand8_hex": "a47a0a74bd353550",
            "runtime_ci_exact_wchars": 0,
            "runtime_ci_distance5": 208,
            "compare_semantics_agree": True,
        }
    ]

    anchors = _collect_frontier_promoted_anchors(
        validations,
        context_entries=[
            {
                "candidate_hex": "a47a0a74bd35355041414141414141",
                "cand8_hex": "a47a0a74bd353550",
                "source_anchor": "f649b64b5e97dbd0",
                "anchor_mode": "frontier",
                "anchor_lineage": "exact0_frontier(f649b64b5e97dbd0) -> refine(frontier)",
            }
        ],
    )

    assert anchors[0]["source_anchor"] == "f649b64b5e97dbd0"
    assert anchors[0]["anchor_lineage"] == "exact0_frontier(f649b64b5e97dbd0) -> refine(frontier)"
    assert anchors[0]["frontier_submode"] == FRONTIER_EXACT0_SUBMODE


def test_annotate_frontier_improvement_gate_marks_only_strict_distance_or_raw_improvement() -> None:
    annotated = _annotate_frontier_improvement_gate(
        [
            {"candidate_hex": "a" * 30, "ci_distance5": 200, "raw_distance10": 260},
            {"candidate_hex": "b" * 30, "ci_distance5": 210, "raw_distance10": 250},
            {"candidate_hex": "c" * 30, "ci_distance5": 210, "raw_distance10": 280},
        ],
        baseline_entry={"ci_distance5": 210, "raw_distance10": 270},
    )

    assert [item["improvement_gate_passed"] for item in annotated] == [True, True, False]


def test_annotate_frontier_improvement_gate_allows_exact1_raw_improvement_without_exact_regression() -> None:
    annotated = _annotate_frontier_improvement_gate(
        [
            {"candidate_hex": "a" * 30, "ci_distance5": 258, "raw_distance10": 300, "ci_exact_wchars": 1},
            {"candidate_hex": "b" * 30, "ci_distance5": 258, "raw_distance10": 300, "ci_exact_wchars": 0},
        ],
        baseline_entry={"ci_distance5": 258, "raw_distance10": 310, "ci_exact_wchars": 1},
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
    )

    assert [item["improvement_gate_passed"] for item in annotated] == [True, False]


def test_feedback_value_pools_merge_improved_pair_and_triad_values() -> None:
    pools, sources = _feedback_value_pools_from_frontier_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1, 2],
        position_profiles={
            0: [{"mutated_byte_value": 0x11}, {"mutated_byte_value": 0x22}],
            1: [{"mutated_byte_value": 0x33}],
            2: [{"mutated_byte_value": 0x44}],
        },
        pair_frontier_pool=[
            {
                "pair_positions": [0, 1],
                "pair_values": [0x99, 0x88],
                "improvement_gate_passed": True,
            },
            {
                "pair_positions": [0, 2],
                "pair_values": [0x99, 0x77],
                "improvement_gate_passed": True,
            },
        ],
        triad_frontier_pool=[
            {
                "pair_positions": [0, 1],
                "pair_values": [0x99, 0x88],
                "triad_positions": [0, 1, 2],
                "triad_value": 0x66,
                "improvement_gate_passed": True,
            }
        ],
        incoming_feedback_value_pools={1: [0x55]},
        frontier_submode=FRONTIER_EXACT0_SUBMODE,
    )

    assert pools[0][:4] == [0x5A, 0x11, 0x22, 0x99]
    assert 0x55 in pools[1]
    assert 0x66 in pools[2]
    assert sources["1"]["incoming_feedback"] == [0x55]
    assert sources["0"]["improved_pair_values"] == [0x99]
    assert sources["2"]["improved_triad_values"] == [0x66]


def test_feedback_value_pools_exact1_ignores_incoming_feedback_and_keeps_small_perturbations() -> None:
    pools, sources = _feedback_value_pools_from_frontier_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={
            0: [{"mutated_byte_value": 0x11}, {"mutated_byte_value": 0x22}, {"mutated_byte_value": 0x33}],
            1: [{"mutated_byte_value": 0x44}],
        },
        pair_frontier_pool=[
            {
                "pair_positions": [0, 1],
                "pair_values": [0x99, 0x88],
                "improvement_gate_passed": True,
            }
        ],
        triad_frontier_pool=[],
        incoming_feedback_value_pools={0: [0xEE], 1: [0xDD]},
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
    )

    assert 0xEE not in pools[0]
    assert 0xDD not in pools[1]
    assert 0x99 in pools[0]
    assert sources["0"]["incoming_feedback"] == []
    assert sources["0"]["small_perturbation_values"][:3] == [0x5A, 0x59, 0x5B]


def test_mine_exact1_lineage_value_sources_prefers_exact1_lineage_and_source_diff() -> None:
    values, counts, origins, summary = _mine_exact1_lineage_value_sources(
        base_anchor="5a3e7f46ddd474d0",
        source_anchor="78d540b49c590770",
        positions=[0, 1, 2],
        transform_model=SamplereverseTransformModel(),
        lineage_entries=[
            {
                "candidate_hex": "5a997f46ddd474d041414141414141",
                "cand8_hex": "5a997f46ddd474d0",
                "ci_exact_wchars": 1,
                "source_anchor": "78d540b49c590770",
                "frontier_submode": FRONTIER_EXACT1_SUBMODE,
            },
            {
                "candidate_hex": "88997f46ddd474d041414141414141",
                "cand8_hex": "88997f46ddd474d0",
                "ci_exact_wchars": 0,
                "source_anchor": "78d540b49c590770",
                "frontier_submode": FRONTIER_EXACT0_SUBMODE,
            },
        ],
    )

    assert values[0][0] == 0x78
    assert 0x99 in values[1]
    assert 0x88 not in values[0]
    assert counts[1][0x99] >= 1
    assert "source_anchor_diff" in origins[0]
    assert summary["positions"]["1"]["values"]


def test_exact1_neighbor_value_maps_projects_distant_sources_into_local_candidates() -> None:
    projection_details: dict[str, object] = {}
    preserve, escape = compare_aware_search._exact1_neighbor_value_maps(
        base_value=0x5A,
        profile_values=[0x33],
        incoming_values=[0x99],
        lineage_values=[0x78, 0x51],
        projection_details=projection_details,
    )

    assert 0x78 not in escape
    assert 0x99 not in escape
    assert 0x59 in escape
    assert any(origin.endswith("_projected") for origin in escape[0x59])
    assert 0x5C in escape
    assert any(origin.endswith("_projected") for origin in escape[0x5C])
    assert 0x78 in projection_details["raw_source_present_but_too_far"]
    assert 0x99 in projection_details["raw_source_present_but_too_far"]
    assert sorted(projection_details["projected_values"]) == [0x58, 0x59, 0x5B, 0x5C]
    assert projection_details["projected_direction"]["92"] == "positive_projection"
    assert projection_details["projected_step"]["92"] == 2
    assert "lineage_projected" in projection_details["projected_origins"]["92"]


def test_top_compare_aware_pair_entries_exact1_respects_locked_pairs_and_feedback_values() -> None:
    pair_profiles, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1, 2, 3],
        position_profiles={
            0: [{"mutated_byte_value": 0x33}],
            1: [{"mutated_byte_value": 0x18}],
            2: [{"mutated_byte_value": 0x75}],
            3: [{"mutated_byte_value": 0x8F}],
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1), (1, 3)],
        incoming_feedback_value_pools={0: [0x99], 1: [0x88]},
        lineage_value_pools={0: [0x78, 0x51], 1: [0xD5, 0x99], 3: [0x07]},
        lineage_value_counts={0: {0x78: 2}, 1: {0xD5: 1}},
        lineage_value_origins={0: ["source_anchor_diff"], 1: ["lineage_context"], 3: ["recent_payload"]},
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
        },
        top_per_pair=2,
    )

    assert set(pair_profiles.keys()) == {(0, 1), (1, 3)}
    assert all(entry["pair_positions"] == [0, 1] for entry in pair_profiles[(0, 1)])
    assert all(entry["pair_positions"] == [1, 3] for entry in pair_profiles[(1, 3)])
    assert generation_details["pair_escape_mode"] == "exact1_dual_lane"
    assert "0,1" in generation_details["pair_preserve_pool"]
    assert generation_details["pair_escape_pool_strategy"] == "exact1_local_neighbors"
    assert generation_details["pair_escape_source_values"]["0,1"]["0"][0] == 0x78
    assert "source_anchor_diff" in generation_details["pair_escape_source_origins"]["0,1"]["0"]
    assert generation_details["pair_escape_source_projected_values"]["0,1"]["0"] == [0x59, 0x58, 0x5B, 0x5C]
    assert "lineage_projected" in generation_details["pair_escape_source_projected_origins"]["0,1"]["0"]["92"]
    assert 0x78 in generation_details["pair_escape_source_reject_reasons"]["0,1"]["0"]["raw_source_present_but_too_far"]
    assert generation_details["lineage_projection_summary"]["0,1"]["0"]["projected_local_value_generated"]
    assert generation_details["pair_escape_source_projected_direction"]["0,1"]["0"]["92"] == "positive_projection"
    assert generation_details["pair_escape_source_projected_step"]["0,1"]["0"]["92"] == 2
    assert 0x33 not in generation_details["pair_escape_pool"]["0,1"]["0"]
    assert 0x18 not in generation_details["pair_escape_pool"]["0,1"]["1"]
    assert all(
        abs(int(value) - 0x5A) <= compare_aware_search.EXACT1_ESCAPE_NEIGHBOR_RADIUS
        for value in generation_details["pair_escape_pool"]["0,1"]["0"]
    )
    assert generation_details["pair_neighbor_generation_summary"]["0,1"]["escape_neighbor_mode"] == "escape_neighbors"


def test_top_compare_aware_pair_entries_exact1_pair_local_sources_do_not_share_values() -> None:
    _, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1, 2, 3],
        position_profiles={0: [], 1: [], 2: [], 3: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1), (0, 3)],
        incoming_feedback_value_pools={},
        lineage_value_pools={0: [0x78], 1: [], 3: [0x07]},
        lineage_value_counts={0: {0x78: 1}, 3: {0x07: 1}},
        lineage_value_origins={0: ["source_anchor_diff"], 3: ["recent_payload"]},
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
        },
    )

    assert generation_details["pair_escape_source_values"]["0,1"]["1"] == []
    assert generation_details["pair_escape_source_values"]["0,3"]["3"] == [0x07]


def test_top_compare_aware_pair_entries_exact1_keeps_escape_lane_in_pair_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_exact1_neighbor_value_maps",
        lambda *, base_value, profile_values, incoming_values, lineage_values: (
            {int(base_value) & 0xFF: ["anchor"]},
            {0x5A: ["anchor"], 0x3B: ["escape_neighbor"]}
            if (int(base_value) & 0xFF) == 0x3E
            else {int(base_value) & 0xFF: ["anchor"]},
        ),
    )

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        second_byte = int(cand8[2:4], 16)
        if cand8 == "5a3e7f46ddd474d0":
            return {
                "candidate_hex": candidate_hex,
                "cand8_hex": cand8,
                "ci_exact_wchars": 1,
                "ci_distance5": 258,
                "raw_distance10": 290,
                "pair_wide_ascii_contiguous_8": 1,
                "pair_wide_zero_high_pairs_8": 1,
                "pair_flaglike_tail_pairs_8": 0,
            }
        if second_byte == 0x3B:
            return {
                "candidate_hex": candidate_hex,
                "cand8_hex": cand8,
                "ci_exact_wchars": 0,
                "ci_distance5": 252,
                "raw_distance10": 282,
                "pair_wide_ascii_contiguous_8": 2,
                "pair_wide_zero_high_pairs_8": 2,
                "pair_flaglike_tail_pairs_8": 1,
            }
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0,
            "ci_distance5": 300,
            "raw_distance10": 320,
            "pair_wide_ascii_contiguous_8": 0,
            "pair_wide_zero_high_pairs_8": 0,
            "pair_flaglike_tail_pairs_8": 0,
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    pair_profiles, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=2,
    )

    kept_escape = generation_details["pair_profile_kept_escape"]["0,1"]
    assert kept_escape
    assert kept_escape[0]["cand8_hex"] == "5a3b7f46ddd474d0"
    assert kept_escape[0]["pair_candidate_origin"] == "exact1_escape_neighbors"
    assert kept_escape[0]["pair_neighbor_mode"] == "escape_neighbors"
    assert kept_escape[0]["pair_mutation_radius"] <= compare_aware_search.EXACT1_ESCAPE_NEIGHBOR_RADIUS
    assert kept_escape[0]["pair_value_origin_by_pos"]["1"] == ["escape_neighbor"]
    assert any(entry["pair_escape_mode"] == "escape" for entry in pair_profiles[(0, 1)])
    assert generation_details["pair_profile_drop_reasons"]["0,1"]["escape"] == "profile_kept"


def test_top_compare_aware_pair_entries_exact1_soft_guard_promotes_one_low_radius_value(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_exact1_neighbor_value_maps",
        lambda *, base_value, profile_values, incoming_values, lineage_values: (
            {int(base_value) & 0xFF: ["anchor"]},
            {int(base_value) & 0xFF: ["anchor"], (int(base_value) + 2) & 0xFF: ["soft"], (int(base_value) + 3) & 0xFF: ["soft2"]},
        ),
    )

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0 if cand8 != "5a3e7f46ddd474d0" else 1,
            "ci_distance5": 500,
            "raw_distance10": 520,
            "pair_wide_ascii_contiguous_8": 0,
            "pair_wide_zero_high_pairs_8": 0,
            "pair_flaglike_tail_pairs_8": 0,
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    pair_profiles, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=4,
    )

    guard = generation_details["pair_single_byte_guard_status_counts"]["0,1"]
    assert guard["0"]["guard_soft_rejected"] >= 1
    assert generation_details["pair_guard_soft_promoted_values"]["0,1"]["0"] == [0x5C]
    assert generation_details["pair_guard_nonbase_starved"]["0,1"]["0"] is False
    assert generation_details["pair_guard_soft_quality_band"]["0,1"]["0"]["92"] == "distance_explosive_soft"
    assert generation_details["pair_guard_soft_distance_delta"]["0,1"]["0"]["92"] == 242
    assert generation_details["pair_guard_soft_raw_delta"]["0,1"]["0"]["92"] == 230
    assert generation_details["pair_guard_soft_structure_delta"]["0,1"]["0"]["92"] == [-1, -1, 0, 0, 0, 0]
    assert 0x5C in generation_details["pair_escape_pool"]["0,1"]["0"]
    assert any(0x5C in entry["pair_values"] for entry in generation_details["pair_profile_escape_entries"]["0,1"])
    assert any(entry["pair_escape_mode"] == "escape" for entry in pair_profiles[(0, 1)])


def test_top_compare_aware_pair_entries_exact1_soft_guard_prefers_better_quality_over_smaller_radius(monkeypatch) -> None:
    def fake_maps(*, base_value, profile_values, incoming_values, lineage_values):
        base = int(base_value) & 0xFF
        if base == 0x5A:
            return (
                {base: ["anchor"]},
                {
                    base: ["anchor"],
                    (base + 2) & 0xFF: ["escape_neighbor"],
                    (base + 4) & 0xFF: ["escape_neighbor"],
                },
            )
        return ({base: ["anchor"]}, {base: ["anchor"]})

    monkeypatch.setattr(compare_aware_search, "_exact1_neighbor_value_maps", fake_maps)

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        left = int(cand8[:2], 16)
        if left == 0x5C:
            distance = 520
            raw = 540
            pair_rank = (0, 0, 0)
        elif left == 0x5E:
            distance = 380
            raw = 400
            pair_rank = (1, 1, 0)
        else:
            distance = 500
            raw = 520
            pair_rank = (0, 0, 0)
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0 if cand8 != "5a3e7f46ddd474d0" else 1,
            "ci_distance5": distance,
            "raw_distance10": raw,
            "pair_wide_ascii_contiguous_8": pair_rank[0],
            "pair_wide_zero_high_pairs_8": pair_rank[1],
            "pair_flaglike_tail_pairs_8": pair_rank[2],
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    pair_profiles, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=4,
    )

    assert generation_details["pair_guard_soft_promoted_values"]["0,1"]["0"] == [0x5E]
    assert generation_details["pair_guard_soft_quality_band"]["0,1"]["0"]["94"] == "local_compatible_soft"
    assert generation_details["pair_guard_soft_quality_band"]["0,1"]["0"]["92"] == "distance_explosive_soft"
    ranked = generation_details["pair_guard_soft_rank_summary"]["0,1"]["0"]
    assert ranked[0]["value"] == 0x5E
    assert ranked[0]["quality_band"] == "local_compatible_soft"
    assert any(entry["pair_values"][0] == 0x5E for entry in generation_details["pair_profile_escape_entries"]["0,1"])
    assert any(entry["pair_values"][0] == 0x5E for entry in pair_profiles[(0, 1)])


def test_top_compare_aware_pair_entries_exact1_soft_guard_prefers_projected_origin_over_escape_neighbor(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_exact1_neighbor_value_maps",
        lambda *, base_value, profile_values, incoming_values, lineage_values: (
            {int(base_value) & 0xFF: ["anchor"]},
            (
                {
                    int(base_value) & 0xFF: ["anchor"],
                    (int(base_value) - 3) & 0xFF: ["escape_neighbor"],
                    (int(base_value) + 3) & 0xFF: ["lineage_projected"],
                }
                if (int(base_value) & 0xFF) == 0x5A
                else {int(base_value) & 0xFF: ["anchor"]}
            ),
        ),
    )

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        left = int(cand8[:2], 16)
        if left in {0x57, 0x5D}:
            distance = 430
            raw = 450
            pair_rank = (0, 1, 0)
        else:
            distance = 500
            raw = 520
            pair_rank = (0, 0, 0)
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0 if cand8 != "5a3e7f46ddd474d0" else 1,
            "ci_distance5": distance,
            "raw_distance10": raw,
            "pair_wide_ascii_contiguous_8": pair_rank[0],
            "pair_wide_zero_high_pairs_8": pair_rank[1],
            "pair_flaglike_tail_pairs_8": pair_rank[2],
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    _, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=4,
    )

    assert generation_details["pair_guard_soft_promoted_values"]["0,1"]["0"] == [0x5D]
    ranked = generation_details["pair_guard_soft_rank_summary"]["0,1"]["0"]
    assert ranked[0]["value"] == 0x5D
    assert ranked[0]["origins"] == ["lineage_projected"]


def test_top_compare_aware_pair_entries_exact1_projected_family_competition_prefers_best_projected_value(monkeypatch) -> None:
    def fake_maps(*, base_value, profile_values, incoming_values, lineage_values, projection_details=None):
        if projection_details is not None:
            projection_details.update(
                {
                    "raw_source_present_but_too_far": [0x20, 0xE0],
                    "projected_values": [0x59, 0x58, 0x5B, 0x5C],
                    "projected_origins": {
                        "89": ["lineage_projected"],
                        "88": ["lineage_projected"],
                        "91": ["lineage_projected"],
                        "92": ["lineage_projected"],
                    },
                    "projected_direction": {
                        "89": "negative_projection",
                        "88": "negative_projection",
                        "91": "positive_projection",
                        "92": "positive_projection",
                    },
                    "projected_step": {"89": 1, "88": 2, "91": 1, "92": 2},
                }
            )
        return (
            {int(base_value) & 0xFF: ["anchor"]},
            {
                int(base_value) & 0xFF: ["anchor"],
                0x59: ["lineage_projected"],
                0x58: ["lineage_projected"],
                0x5B: ["lineage_projected"],
                0x5C: ["lineage_projected"],
            },
        )

    monkeypatch.setattr(compare_aware_search, "_exact1_neighbor_value_maps_with_optional_details", fake_maps)

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        left = int(cand8[:2], 16)
        mapping = {
            0x59: (330, 340, (1, 1, 0)),
            0x58: (390, 410, (0, 1, 0)),
            0x5B: (320, 330, (1, 1, 0)),
            0x5C: (420, 430, (0, 0, 0)),
        }
        distance, raw, pair_rank = mapping.get(left, (500, 520, (0, 0, 0)))
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0 if cand8 != "5a3e7f46ddd474d0" else 1,
            "ci_distance5": distance,
            "raw_distance10": raw,
            "pair_wide_ascii_contiguous_8": pair_rank[0],
            "pair_wide_zero_high_pairs_8": pair_rank[1],
            "pair_flaglike_tail_pairs_8": pair_rank[2],
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    _, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=4,
    )

    assert generation_details["pair_escape_source_projected_kept_values"]["0,1"]["0"] == [0x5B]
    assert generation_details["pair_escape_source_projected_dropped_values"]["0,1"]["0"] == [0x59, 0x58, 0x5C]
    assert generation_details["pair_escape_source_projected_quality_band"]["0,1"]["0"]["89"] == "projected_local_compatible"
    assert generation_details["pair_escape_source_projected_quality_band"]["0,1"]["0"]["92"] == "projected_distance_explosive"
    assert generation_details["pair_projected_competitive_status"]["0,1"]["0"] == "projected_beats_neighbor"
    assert generation_details["pair_projected_competitive_winner"]["0,1"]["0"]["value"] == 0x5B
    assert generation_details["pair_guard_soft_promoted_values"]["0,1"]["0"] == [0x5B]
    preserve_lane = generation_details["pair_projected_preserve_candidates"]["0,1"]
    assert preserve_lane
    assert preserve_lane[0]["pair_candidate_origin"] == "exact1_projected_preserve_lane"
    assert preserve_lane[0]["pair_projected_boundary_role"] == "projected_winner_with_base"


def test_top_compare_aware_pair_entries_exact1_projected_family_competition_reports_raw_loss(monkeypatch) -> None:
    def fake_maps(*, base_value, profile_values, incoming_values, lineage_values, projection_details=None):
        if projection_details is not None:
            projection_details.update(
                {
                    "raw_source_present_but_too_far": [0x20],
                    "projected_values": [0x5D],
                    "projected_origins": {"93": ["lineage_projected"]},
                    "projected_direction": {"93": "positive_projection"},
                    "projected_step": {"93": 1},
                }
            )
        base = int(base_value) & 0xFF
        return (
            {base: ["anchor"]},
            {
                base: ["anchor"],
                0x5D: ["lineage_projected"],
                0x57: ["escape_neighbor"],
            },
        )

    monkeypatch.setattr(compare_aware_search, "_exact1_neighbor_value_maps_with_optional_details", fake_maps)

    def fake_eval(candidate_hex: str, transform_model) -> dict[str, object]:
        cand8 = candidate_hex[:16]
        left = int(cand8[:2], 16)
        if left == 0x5D:
            distance = 420
            raw = 470
            pair_rank = (1, 1, 0)
        elif left == 0x57:
            distance = 420
            raw = 430
            pair_rank = (1, 1, 0)
        else:
            distance = 500
            raw = 520
            pair_rank = (0, 0, 0)
        return {
            "candidate_hex": candidate_hex,
            "cand8_hex": cand8,
            "ci_exact_wchars": 0 if cand8 != "5a3e7f46ddd474d0" else 1,
            "ci_distance5": distance,
            "raw_distance10": raw,
            "pair_wide_ascii_contiguous_8": pair_rank[0],
            "pair_wide_zero_high_pairs_8": pair_rank[1],
            "pair_flaglike_tail_pairs_8": pair_rank[2],
        }

    monkeypatch.setattr(compare_aware_search, "_evaluate_candidate_hex", fake_eval)

    _, generation_details = compare_aware_search._top_compare_aware_pair_entries(
        base_anchor="5a3e7f46ddd474d0",
        positions=[0, 1],
        position_profiles={0: [], 1: []},
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        locked_pair_positions=[(0, 1)],
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        top_per_pair=4,
    )

    assert generation_details["pair_projected_competitive_status"]["0,1"]["0"] == "projected_loses_on_raw"
    assert generation_details["pair_projected_blocked_by_neighbor"]["0,1"]["0"]["value"] == 0x57
    assert generation_details["pair_guard_soft_promoted_values"]["0,1"]["0"] == [0x57]


def test_diverse_pair_frontier_pool_exact1_drops_exact_regression_without_distance_escape(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 1): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x3E],
                },
                {
                    "candidate_hex": "333e7f46ddd474d041414141414141",
                    "cand8_hex": "333e7f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 258,
                    "raw_distance10": 280,
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 1],
                    "pair_values": [0x33, 0x3E],
                },
                {
                    "candidate_hex": "5a187f46ddd474d041414141414141",
                    "cand8_hex": "5a187f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 230,
                    "raw_distance10": 260,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 2,
                    "pair_wide_zero_high_pairs_8": 2,
                    "pair_flaglike_tail_pairs_8": 1,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x18],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=3,
    )

    assert any(entry["cand8_hex"] == "5a3e7f46ddd474d0" for entry in selected)
    assert any(entry["cand8_hex"] == "5a187f46ddd474d0" for entry in selected)
    assert all(entry["cand8_hex"] != "333e7f46ddd474d0" for entry in selected)
    assert diagnostics["pair_gate_kept_escape"]
    assert diagnostics["pair_gate_kept_escape"][0]["cand8_hex"] == "5a187f46ddd474d0"
    assert diagnostics["pair_escape_source_statuses"]["0,1"] == "gate_kept_escape"
    assert diagnostics["pair_best_escape_candidate"]["cand8_hex"] == "5a187f46ddd474d0"


def test_diverse_pair_frontier_pool_exact1_records_escape_ranked_out(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 1): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x3E],
                },
                {
                    "candidate_hex": "5a187f46ddd474d041414141414141",
                    "cand8_hex": "5a187f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 230,
                    "raw_distance10": 260,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 2,
                    "pair_wide_zero_high_pairs_8": 2,
                    "pair_flaglike_tail_pairs_8": 1,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x18],
                },
            ],
            (0, 2): [
                {
                    "candidate_hex": "5a387f46ddd474d041414141414141",
                    "cand8_hex": "5a387f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 228,
                    "raw_distance10": 255,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 3,
                    "pair_wide_zero_high_pairs_8": 2,
                    "pair_flaglike_tail_pairs_8": 1,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5A, 0x38],
                },
            ],
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert len(selected) == 2
    assert all(entry["pair_escape_mode"] == "escape" for entry in selected)
    assert "escape_signal_but_ranked_out" not in drop_reasons
    assert diagnostics["pair_escape_source_statuses"]["0,2"] == "gate_kept_escape"
    assert diagnostics["pair_escape_status_by_lane"]["0,2"]["local_escape"] == "gate_kept_escape"


def test_diverse_pair_frontier_pool_exact1_allows_borderline_local_escape(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 1): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x3E],
                },
                {
                    "candidate_hex": "5a417f46ddd474d041414141414141",
                    "cand8_hex": "5a417f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 330,
                    "raw_distance10": 310,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x41],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert any(entry["cand8_hex"] == "5a417f46ddd474d0" for entry in selected)
    assert not diagnostics["pair_gate_kept_escape"]
    assert diagnostics["pair_borderline_escape_candidates"][0]["cand8_hex"] == "5a417f46ddd474d0"
    assert diagnostics["pair_borderline_escape_candidates"][0]["pair_escape_status"] == "borderline"
    assert diagnostics["pair_borderline_escape_candidates"][0]["pair_escape_quality_band"] == "near_local_escape"
    assert diagnostics["pair_near_local_escape_candidates"][0]["cand8_hex"] == "5a417f46ddd474d0"
    assert diagnostics["pair_near_local_escape_count"] == 1
    assert diagnostics["pair_wide_local_escape_count"] == 0
    assert diagnostics["pair_escape_status_by_lane"]["0,1"]["local_escape"] == "gate_borderline_escape"
    assert diagnostics["pair_escape_source_statuses"]["0,1"] == "gate_borderline_escape"
    assert diagnostics["pair_local_escape_borderline_count"] == 1
    assert drop_reasons == {}


def test_diverse_pair_frontier_pool_exact1_keeps_wide_local_escape_diagnostic_only(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 2): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5A, 0x7F],
                },
                {
                    "candidate_hex": "563e7b46ddd474d041414141414141",
                    "cand8_hex": "563e7b46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 558,
                    "raw_distance10": 558,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 2],
                    "pair_values": [0x56, 0x7B],
                    "pair_mutation_radius": 4,
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert all(entry["cand8_hex"] != "563e7b46ddd474d0" for entry in selected)
    assert diagnostics["pair_wide_local_escape_candidates"][0]["cand8_hex"] == "563e7b46ddd474d0"
    assert diagnostics["pair_wide_local_escape_candidates"][0]["pair_escape_quality_band"] == "wide_local_escape"
    assert not diagnostics["pair_near_local_escape_candidates"]
    assert diagnostics["pair_escape_source_statuses"]["0,2"] == "gate_filtered_wide_local_escape"
    assert diagnostics["pair_wide_local_escape_count"] == 1
    assert drop_reasons["gate_filtered_wide_local_escape"] == 1


def test_diverse_pair_frontier_pool_exact1_reports_projected_winner_mixed_with_neighbor_wide(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 2): [
                {
                    "candidate_hex": "5b3e7b46ddd474d041414141414141",
                    "cand8_hex": "5b3e7b46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 558,
                    "raw_distance10": 558,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5B, 0x7B],
                    "pair_mutation_radius": 4,
                    "pair_projected_winner_available": [
                        {"position": 0, "value": 0x5B, "base_value": 0x5A}
                    ],
                    "pair_projected_winner_contributions": [
                        {
                            "position": 0,
                            "value": 0x5B,
                            "paired_position": 2,
                            "paired_value": 0x7B,
                            "paired_source": "neighbor",
                        }
                    ],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert not selected
    assert diagnostics["pair_wide_local_escape_candidates"][0]["pair_projected_winner_gate_status"] == (
        "projected_winner_mixed_with_neighbor_wide"
    )
    assert diagnostics["pair_projected_winner_gate_status_counts"]["0,2"][
        "projected_winner_mixed_with_neighbor_wide"
    ] == 1
    assert drop_reasons["gate_filtered_wide_local_escape"] == 1


def test_diverse_pair_frontier_pool_exact1_promotes_projected_boundary_base_to_near_local(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 2): [
                {
                    "candidate_hex": "5b3e7f46ddd474d041414141414141",
                    "cand8_hex": "5b3e7f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 330,
                    "raw_distance10": 330,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5B, 0x7F],
                    "pair_mutation_radius": 1,
                    "pair_candidate_origin": "exact1_projected_preserve_lane",
                    "pair_projected_boundary_role": "projected_winner_with_base",
                    "pair_projected_winner_available": [
                        {"position": 0, "value": 0x5B, "base_value": 0x5A}
                    ],
                    "pair_projected_winner_contributions": [
                        {
                            "position": 0,
                            "value": 0x5B,
                            "paired_position": 2,
                            "paired_value": 0x7F,
                            "paired_source": "base",
                        }
                    ],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert any(entry["cand8_hex"] == "5b3e7f46ddd474d0" for entry in selected)
    assert diagnostics["pair_near_local_escape_candidates"][0]["pair_projected_winner_gate_status"] == (
        "projected_winner_promoted_to_near_local"
    )
    assert diagnostics["pair_projected_preserve_entries"][0]["pair_projected_boundary_role"] == (
        "projected_winner_with_base"
    )
    assert drop_reasons == {}


def test_diverse_pair_frontier_pool_exact1_projected_preserve_gets_handoff_slot_when_pool_tight(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    selected, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 2): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 1,
                    "pair_wide_zero_high_pairs_8": 1,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5A, 0x7F],
                    "pair_mutation_radius": 0,
                },
                {
                    "candidate_hex": "5b3e7f46ddd474d041414141414141",
                    "cand8_hex": "5b3e7f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 330,
                    "raw_distance10": 330,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 2],
                    "pair_values": [0x5B, 0x7F],
                    "pair_mutation_radius": 1,
                    "pair_candidate_origin": "exact1_projected_preserve_lane",
                    "pair_projected_boundary_role": "projected_winner_with_base",
                    "pair_projected_winner_available": [
                        {"position": 0, "value": 0x5B, "base_value": 0x5A}
                    ],
                    "pair_projected_winner_contributions": [
                        {
                            "position": 0,
                            "value": 0x5B,
                            "paired_position": 2,
                            "paired_value": 0x7F,
                            "paired_source": "base",
                        }
                    ],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=1,
    )

    assert selected[0]["cand8_hex"] == "5b3e7f46ddd474d0"
    assert diagnostics["pair_projected_preserve_entries"][0]["cand8_hex"] == "5b3e7f46ddd474d0"
    assert diagnostics["pair_projected_preserve_entries"][0]["pair_projected_winner_gate_status"] == (
        "projected_winner_promoted_to_near_local"
    )
    assert drop_reasons == {}


def test_exact1_pair_set_selection_prefers_near_local_over_wide_borderline() -> None:
    near_result = {
        "pair_frontier_pool": [],
        "pair_drop_reasons": {},
        "pair_frontier_diagnostics": {
            "pair_gate_kept_escape": [],
            "pair_near_local_escape_candidates": [{"ci_distance5": 330}],
            "pair_wide_local_escape_count": 0,
            "pair_best_local_escape": {"0,1": {"pair_escape_signal_score": 7}},
        },
    }
    wide_result = {
        "pair_frontier_pool": [],
        "pair_drop_reasons": {},
        "pair_frontier_diagnostics": {
            "pair_gate_kept_escape": [],
            "pair_near_local_escape_candidates": [],
            "pair_borderline_escape_candidates": [{"ci_distance5": 558}],
            "pair_wide_local_escape_count": 1,
            "pair_best_local_escape": {"0,2": {"pair_escape_signal_score": 7}},
        },
    }

    assert compare_aware_search._exact1_pair_set_selection_key(near_result) < compare_aware_search._exact1_pair_set_selection_key(wide_result)


def test_diverse_pair_frontier_pool_exact1_tracks_local_and_hard_escape_per_pair(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    _, drop_reasons, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 1): [
                {
                    "candidate_hex": "5a187f46ddd474d041414141414141",
                    "cand8_hex": "5a187f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 258,
                    "raw_distance10": 292,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 2,
                    "pair_wide_zero_high_pairs_8": 2,
                    "pair_flaglike_tail_pairs_8": 1,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x18],
                },
                {
                    "candidate_hex": "a4707f46ddd474d041414141414141",
                    "cand8_hex": "a4707f46ddd474d0",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 677,
                    "raw_distance10": 675,
                    "pair_escape_mode": "escape",
                    "pair_wide_ascii_contiguous_8": 0,
                    "pair_wide_zero_high_pairs_8": 0,
                    "pair_flaglike_tail_pairs_8": 0,
                    "pair_positions": [0, 1],
                    "pair_values": [0xA4, 0x70],
                },
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        keep_limit=2,
    )

    assert diagnostics["pair_escape_lane_counts"]["0,1"] == {"local_escape": 1, "hard_escape": 1}
    assert diagnostics["pair_escape_status_by_lane"]["0,1"]["local_escape"] == "gate_kept_escape"
    assert diagnostics["pair_escape_status_by_lane"]["0,1"]["hard_escape"] == "gate_filtered_hard_escape"
    assert diagnostics["pair_best_local_escape"]["0,1"]["cand8_hex"] == "5a187f46ddd474d0"
    assert diagnostics["pair_best_hard_escape"]["0,1"]["cand8_hex"] == "a4707f46ddd474d0"
    assert drop_reasons["gate_filtered_hard_escape"] == 1


def test_diverse_pair_frontier_pool_exact1_reports_profile_ranked_out_before_frontier_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        compare_aware_search,
        "_guided_sort_key",
        lambda entry, transform_model, **kwargs: (
            int(entry.get("ci_distance5", 1 << 30)),
            int(entry.get("raw_distance10", 1 << 30)),
            -int(entry.get("ci_exact_wchars", 0)),
            str(entry.get("candidate_hex", "")),
        ),
    )
    _, _, diagnostics = compare_aware_search._diverse_pair_frontier_pool(
        {
            (0, 1): [
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "ci_exact_wchars": 1,
                    "ci_distance5": 258,
                    "raw_distance10": 290,
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x3E],
                }
            ]
        },
        transform_model=SamplereverseTransformModel(),
        anchor_mode=FRONTIER_ANCHOR_MODE,
        frontier_submode=FRONTIER_EXACT1_SUBMODE,
        pair_profile_details={
            "pair_profile_escape_entries": {
                "0,1": [
                    {
                        "candidate_hex": "5a187f46ddd474d041414141414141",
                        "cand8_hex": "5a187f46ddd474d0",
                        "pair_positions": [0, 1],
                        "pair_values": [0x5A, 0x18],
                    }
                ]
            },
            "pair_profile_kept_escape": {"0,1": []},
            "pair_profile_kept_preserve": {"0,1": []},
            "pair_profile_preserve_entries": {"0,1": []},
            "pair_profile_drop_reasons": {"0,1": {"escape": "profile_ranked_out"}},
            "pair_profile_truncation_summary": {"0,1": {"escape_total": 1, "escape_kept": 0}},
        },
        baseline_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
        },
        keep_limit=1,
    )

    assert diagnostics["pair_escape_source_statuses"]["0,1"] == "profile_ranked_out"


def test_alternate_locked_pair_positions_for_exact1_prefers_local_escape_heavy_pairs() -> None:
    alternate, details = _alternate_locked_pair_positions_for_exact1(
        primary_locked_pairs=[(0, 1), (0, 2), (0, 3)],
        source_details={
            "candidate_pairs": [[0, 1], [1, 2], [2, 3], [3, 4]],
        },
        pair_gate_input_summary={
            "1,2": [
                {"pair_escape_lane": "local_escape"},
                {"pair_escape_lane": "local_escape"},
            ],
            "2,3": [
                {"pair_escape_lane": "local_escape"},
            ],
            "0,3": [
                {"pair_escape_lane": "hard_escape"},
            ],
        },
    )

    assert alternate[0] == (1, 2)
    assert (2, 3) in alternate
    assert all(pair not in {(0, 1), (0, 2), (0, 3)} for pair in alternate)
    assert details["local_escape_counts"]["1,2"] == 2


def test_exact1_pair_escape_signal_classifies_hard_and_local_escape() -> None:
    hard_signal = compare_aware_search._exact1_pair_escape_signal(
        {
            "candidate_hex": "a43e7f3bddd474d041414141414141",
            "cand8_hex": "a43e7f3bddd474d0",
            "ci_exact_wchars": 0,
            "ci_distance5": 677,
            "raw_distance10": 675,
            "pair_positions": [0, 3],
            "pair_values": [0xA4, 0x3B],
            "pair_wide_ascii_contiguous_8": 0,
            "pair_wide_zero_high_pairs_8": 0,
            "pair_flaglike_tail_pairs_8": 0,
        },
        {
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_positions": [0, 3],
            "pair_values": [0x5A, 0x46],
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        transform_model=SamplereverseTransformModel(),
    )
    local_signal = compare_aware_search._exact1_pair_escape_signal(
        {
            "candidate_hex": "5a187f46ddd474d041414141414141",
            "cand8_hex": "5a187f46ddd474d0",
            "ci_exact_wchars": 0,
            "ci_distance5": 230,
            "raw_distance10": 260,
            "pair_positions": [0, 1],
            "pair_values": [0x5A, 0x18],
            "pair_wide_ascii_contiguous_8": 2,
            "pair_wide_zero_high_pairs_8": 2,
            "pair_flaglike_tail_pairs_8": 1,
        },
        {
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "raw_distance10": 290,
            "pair_positions": [0, 1],
            "pair_values": [0x5A, 0x3E],
            "pair_wide_ascii_contiguous_8": 1,
            "pair_wide_zero_high_pairs_8": 1,
            "pair_flaglike_tail_pairs_8": 0,
        },
        transform_model=SamplereverseTransformModel(),
    )

    assert hard_signal["lane"] == "hard_escape"
    assert hard_signal["passed"] is False
    assert hard_signal["status"] == "reject"
    assert local_signal["lane"] == "local_escape"
    assert local_signal["passed"] is True
    assert local_signal["status"] == "keep"


def test_improved_frontier_candidates_only_promote_runtime_improved_lineages() -> None:
    improved = _improved_frontier_candidates(
        [
            {
                "candidate_hex": "78d540b49c59077041414141414141",
                "cand8_hex": "78d540b49c590770",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 2,
                "runtime_ci_distance5": 246,
            },
            {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 1,
                "runtime_ci_distance5": 258,
                "source_anchor": "f649b64b5e97dbd0",
                "frontier_role": "exact1_frontier",
            },
            {
                "candidate_hex": "788940b49c59077041414141414141",
                "cand8_hex": "788940b49c590770",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 293,
                "source_anchor": "788940b49c590770",
                "frontier_role": "exact0_frontier",
            },
        ],
        context_entries=[
            {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "source_anchor": "f649b64b5e97dbd0",
                "anchor_mode": "frontier",
                "anchor_lineage": "exact0_frontier(f649b64b5e97dbd0) -> refine(frontier)",
            },
            {
                "candidate_hex": "788940b49c59077041414141414141",
                "cand8_hex": "788940b49c590770",
                "source_anchor": "788940b49c590770",
                "anchor_mode": "frontier",
                "anchor_lineage": "exact0_frontier(788940b49c590770)",
            },
        ],
        baseline_validations=[
            {
                "candidate_hex": "f649b64b5e97dbd041414141414141",
                "cand8_hex": "f649b64b5e97dbd0",
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 280,
                "compare_semantics_agree": True,
            },
            {
                "candidate_hex": "788940b49c59077041414141414141",
                "cand8_hex": "788940b49c590770",
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 293,
                "compare_semantics_agree": True,
            },
        ],
    )

    assert [item["anchor"] for item in improved] == ["5a3e7f46ddd474d0"]
    assert improved[0]["improvement_gate_passed"] is True


def test_validated_projected_preserve_handoff_can_seed_second_hop_composition() -> None:
    candidates = _validated_projected_preserve_second_hop_candidates(
        [
            {
                "candidate_hex": "5a3f7f46ddd474d041414141414141",
                "cand8_hex": "5a3f7f46ddd474d0",
                "frontier_role": "projected_preserve_handoff",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 740,
                "offline_raw_distance10": 772,
            }
        ],
        context_entries=[
            {
                "candidate_hex": "5a3f7f46ddd474d041414141414141",
                "cand8_hex": "5a3f7f46ddd474d0",
                "source_anchor": "78d540b49c590770",
                "anchor_mode": FRONTIER_ANCHOR_MODE,
                "anchor_lineage": "exact2_seed(78d540b49c590770) -> guided(frontier)",
                "pair_candidate_origin": "exact1_projected_preserve_lane",
                "pair_projected_boundary_role": "projected_winner_with_base",
                "pair_projected_winner_gate_status": "projected_winner_promoted_to_near_local",
            }
        ],
    )

    assert len(candidates) == 1
    assert candidates[0]["anchor"] == "5a3f7f46ddd474d0"
    assert candidates[0]["frontier_role"] == PROJECTED_PRESERVE_SECOND_HOP_ROLE
    assert candidates[0]["frontier_submode"] == FRONTIER_EXACT1_SUBMODE
    assert candidates[0]["source_anchor"] == "78d540b49c590770"
    assert candidates[0]["improvement_gate_passed"] is False

    continuation, reason, used_second_hop = _frontier_continuation_candidates(
        improved_frontier_candidates=[],
        second_hop_frontier_candidates=candidates,
        frontier_converged_reason="distance_not_improved",
        iteration_index=1,
    )

    assert reason == "continue"
    assert used_second_hop is True
    assert continuation[0]["frontier_role"] == PROJECTED_PRESERVE_SECOND_HOP_ROLE


def test_second_hop_composition_does_not_admit_compare_disagree_candidate() -> None:
    candidates = _validated_projected_preserve_second_hop_candidates(
        [
            {
                "candidate_hex": "5a3f7f46ddd474d041414141414141",
                "cand8_hex": "5a3f7f46ddd474d0",
                "frontier_role": "projected_preserve_handoff",
                "compare_semantics_agree": False,
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 740,
            }
        ],
        context_entries=[
            {
                "candidate_hex": "5a3f7f46ddd474d041414141414141",
                "cand8_hex": "5a3f7f46ddd474d0",
                "pair_candidate_origin": "exact1_projected_preserve_lane",
                "pair_projected_boundary_role": "projected_winner_with_base",
                "pair_projected_winner_gate_status": "projected_winner_promoted_to_near_local",
            }
        ],
    )

    assert candidates == []


def test_second_hop_composition_does_not_expand_budget() -> None:
    validations = []
    context_entries = []
    for idx in range(FRONTIER_MAX_ANCHORS + 3):
        cand8 = f"5a3f7f46ddd47{idx:02x}0"[:16]
        candidate_hex = f"{cand8}41414141414141"
        validations.append(
            {
                "candidate_hex": candidate_hex,
                "cand8_hex": cand8,
                "frontier_role": "projected_preserve_handoff",
                "compare_semantics_agree": True,
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 740 + idx,
            }
        )
        context_entries.append(
            {
                "candidate_hex": candidate_hex,
                "cand8_hex": cand8,
                "source_anchor": "78d540b49c590770",
                "pair_candidate_origin": "exact1_projected_preserve_lane",
                "pair_projected_boundary_role": "projected_winner_with_base",
                "pair_projected_winner_gate_status": "projected_winner_promoted_to_near_local",
            }
        )

    candidates = _validated_projected_preserve_second_hop_candidates(
        validations,
        context_entries=context_entries,
    )

    assert len(candidates) == max(1, FRONTIER_MAX_ANCHORS - 1)


def test_select_smt_base_entry_prefers_better_compare_agree_frontier() -> None:
    selected = _select_smt_base_entry(
        best_exact2_entry={"runtime_ci_distance5": 246},
        frontier_validations=[
            {
                "candidate_hex": "a47a0a74bd35355041414141414141",
                "cand8_hex": "a47a0a74bd353550",
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 208,
                "offline_raw_distance10": 266,
                "compare_semantics_agree": True,
            }
        ],
        fallback_entry={"cand8_hex": "78d540b49c590770"},
    )

    assert selected["cand8_hex"] == "a47a0a74bd353550"
    assert selected["ci_distance5"] == 208


def test_exact1_projected_competition_summary_marks_single_byte_bottleneck_when_pair_sets_have_no_winner() -> None:
    summary = _exact1_projected_competition_summary(
        pair_stage_stats={
            "projected_beats_neighbor_count": 0,
            "pair_gate_kept_escape": 0,
            "pair_near_local_escape_count": 0,
            "pair_wide_local_escape_count": 2,
        },
        pair_set_comparison_summary={
            "primary_pair_set": {"projected_beats_neighbor_count": 0},
            "alternate_pair_set": {"projected_beats_neighbor_count": 0},
        },
    )

    assert summary == {
        "stall_reason": "single_byte_projected_competition",
        "pair_set_diagnosis": "pair_set_not_limiting_single_byte_competition",
        "projected_beats_neighbor_count": 0,
        "pair_gate_kept_escape_count": 0,
        "near_local_escape_count": 0,
        "wide_local_escape_count": 2,
    }


def test_exact1_projected_competition_reason_prefers_pair_refine_after_projected_winner() -> None:
    reason = _exact1_projected_competition_reason_from_runs(
        [
            {
                "pair_stage_stats": {
                    "exact1_projected_competition_summary": {
                        "stall_reason": "single_byte_projected_competition"
                    }
                }
            },
            {
                "pair_stage_stats": {
                    "exact1_projected_competition_summary": {
                        "stall_reason": "pair_refine_after_projected_winner"
                    }
                }
            },
        ]
    )

    assert reason == "pair_refine_after_projected_winner"


def test_select_smt_base_entry_prefers_exact1_frontier_over_exact0_distance_basin() -> None:
    selected = _select_smt_base_entry(
        best_exact2_entry={"runtime_ci_distance5": 246},
        frontier_validations=[
            {
                "candidate_hex": "a47a0a74bd35355041414141414141",
                "cand8_hex": "a47a0a74bd353550",
                "runtime_ci_exact_wchars": 0,
                "runtime_ci_distance5": 208,
                "offline_raw_distance10": 266,
                "compare_semantics_agree": True,
            },
            {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "runtime_ci_exact_wchars": 1,
                "runtime_ci_distance5": 258,
                "offline_raw_distance10": 300,
                "compare_semantics_agree": True,
                "frontier_role": "exact1_frontier",
            },
        ],
        fallback_entry={"cand8_hex": "78d540b49c590770"},
    )

    assert selected["cand8_hex"] == "5a3e7f46ddd474d0"
    assert selected["frontier_submode"] == FRONTIER_EXACT1_SUBMODE


def test_exact2_basin_smt_diagnostic_does_not_replace_primary_frontier_base() -> None:
    diagnostic = _exact2_basin_smt_diagnostic_payload(
        best_exact2_entry={
            "candidate_hex": "78d540b49c59077041414141414141",
            "cand8_hex": "78d540b49c590770",
            "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
            "runtime_ci_exact_wchars": 2,
            "runtime_ci_distance5": 246,
            "offline_raw_distance10": 304,
            "compare_semantics_agree": True,
        },
        primary_smt_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "frontier_submode": FRONTIER_EXACT1_SUBMODE,
        },
        comparison_entries=[
            {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "improvement_gate_passed": True,
            },
            {
                "candidate_hex": "5a3f7f46ddd474d041414141414141",
                "cand8_hex": "5a3f7f46ddd474d0",
                "improvement_gate_passed": False,
            },
        ],
        lineage_entries=[],
        transform_model=SamplereverseTransformModel(),
    )

    assert diagnostic["attempted"] is False
    assert diagnostic["recommended"] is True
    assert diagnostic["base_anchor"] == "78d540b49c590770"
    assert diagnostic["primary_base_anchor"] == "5a3e7f46ddd474d0"
    assert diagnostic["prefix_boundary"]["ci_exact_wchars"] == 2
    assert diagnostic["variable_byte_positions"]


def test_run_compare_aware_smt_records_feedback_value_pools_from_improved_frontier_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured_z3: dict[str, object] = {}

    def fake_solve_targeted_prefix8(**kwargs):
        captured_z3.update(kwargs)
        return type(
            "Z3Result",
            (),
            {
                "attempted": True,
                "summary": "ok",
                "evidence": [],
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
            },
        )()

    monkeypatch.setattr(compare_aware_search, "solve_targeted_prefix8", fake_solve_targeted_prefix8)
    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        lambda **kwargs: (tmp_path / "smt_validation.json", []),
    )

    result = run_compare_aware_smt(
        target=target,
        artifacts_dir=tmp_path / "smt",
        base_entry={
            "candidate_hex": "5a3e7f46ddd474d041414141414141",
            "cand8_hex": "5a3e7f46ddd474d0",
            "ci_exact_wchars": 1,
            "ci_distance5": 258,
            "source_anchor": "f649b64b5e97dbd0",
            "frontier_role": "exact1_frontier",
            "anchor_lineage": "exact0_frontier(f649b64b5e97dbd0) -> refine(frontier)",
            "pair_gate_kept_escape": [
                {
                    "cand8_hex": "5a447f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x44],
                }
            ],
            "pair_near_local_escape_candidates": [
                {
                    "cand8_hex": "5a667f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x66],
                    "pair_escape_status": "borderline",
                    "pair_escape_quality_band": "near_local_escape",
                }
            ],
            "pair_projected_boundary_entries": [
                {
                    "cand8_hex": "5a427f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x42],
                    "pair_candidate_origin": "exact1_projected_boundary",
                    "pair_projected_boundary_role": "projected_winner_with_base",
                }
            ],
            "pair_projected_winner_available": [
                {"position": 2, "value": 0x43, "base_value": 0x7F}
            ],
            "pair_wide_local_escape_candidates": [
                {
                    "cand8_hex": "5a777f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x77],
                    "pair_escape_status": "borderline",
                    "pair_escape_quality_band": "wide_local_escape",
                }
            ],
            "pair_profile_kept_escape": [
                {
                    "cand8_hex": "5a447f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x44],
                }
            ],
            "pair_profile_kept_preserve": [
                {
                    "cand8_hex": "5a557f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x55],
                }
            ],
            "pair_best_local_escape": {
                "0,1": {
                    "cand8_hex": "5a777f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x77],
                    "pair_escape_quality_band": "wide_local_escape",
                }
            },
            "pair_projected_competitive_status": {
                "0,1": {
                    "0": "projected_beats_neighbor",
                    "1": "projected_loses_on_raw",
                },
            },
            "pair_projected_competitive_winner": {
                "0,1": {
                    "0": {"family": "projected_soft_family", "value": 0x41},
                    "1": {"family": "escape_neighbor_soft_family", "value": 0x57},
                },
            },
        },
        comparison_entries=[
            {
                "cand8_hex": "5a997f46ddd474d0",
                "pair_positions": [0, 1],
                "pair_values": [0x5A, 0x99],
                "improvement_gate_passed": True,
            },
            {
                "cand8_hex": "5a998846ddd474d0",
                "pair_positions": [0, 1],
                "pair_values": [0x5A, 0x99],
                "triad_positions": [0, 1, 2],
                "triad_value": 0x88,
                "improvement_gate_passed": True,
            },
        ],
        lineage_entries=[
            {
                "candidate_hex": "78997f46ddd474d041414141414141",
                "cand8_hex": "78997f46ddd474d0",
                "ci_exact_wchars": 1,
                "source_anchor": "f649b64b5e97dbd0",
                "frontier_submode": FRONTIER_EXACT1_SUBMODE,
            }
        ],
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    assert result["payload"]["feedback_value_pools"]["0"][0] == 0x5A
    assert 0x44 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x66 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x42 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x77 not in result["payload"]["feedback_value_pools"]["1"]
    assert 0x55 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x99 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x78 in result["payload"]["feedback_value_pools"]["0"]
    assert 0x41 in result["payload"]["feedback_value_pools"]["0"]
    assert 0x43 in result["payload"]["feedback_value_pools"]["2"]
    assert 0x57 not in result["payload"]["feedback_value_pools"]["1"]
    assert result["payload"]["prefix_boundary"]["cand8_hex"] == "5a3e7f46ddd474d0"
    assert result["payload"]["prefix_boundary"]["ci_exact_wchars"] == 1
    assert captured_z3["value_pools"][1][0] == 0x3E
    assert 0x44 in captured_z3["value_pools"][1]


def test_run_compare_aware_smt_records_z3_unknown_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    def fake_solve_targeted_prefix8(**kwargs):
        return type(
            "Z3Result",
            (),
            {
                "attempted": True,
                "summary": "targeted z3 finished with unknown",
                "evidence": ["runtime_probe:z3_targeted reason_unknown=timeout"],
                "candidate_hex": "",
                "diagnostics": {
                    "z3_reason_unknown": "timeout",
                    "estimated_value_pool_combinations": 18,
                    "value_pool_sizes": {"0": 1, "1": 3},
                    "symbolic_compare_bytes": 10,
                    "solver_type": "Optimize",
                    "timeout_ms": 1500,
                },
            },
        )()

    monkeypatch.setattr(compare_aware_search, "solve_targeted_prefix8", fake_solve_targeted_prefix8)

    result = run_compare_aware_smt(
        target=target,
        artifacts_dir=tmp_path / "smt",
        base_entry={
            "candidate_hex": "78d540b49c59077041414141414141",
            "cand8_hex": "78d540b49c590770",
            "ci_exact_wchars": 2,
            "ci_distance5": 246,
            "anchor_mode": "exact2",
        },
        comparison_entries=[],
        variable_byte_positions_override=[0, 1],
        variable_nibble_positions_override=[0, 1],
        value_pools_override={0: [0x78], 1: [0xD5, 0x3E, 0x3C]},
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    assert result["payload"]["summary"] == "targeted z3 finished with unknown"
    assert result["payload"]["z3_reason_unknown"] == "timeout"
    assert result["payload"]["estimated_value_pool_combinations"] == 18
    assert result["payload"]["value_pool_sizes"] == {"0": 1, "1": 3}
    assert result["payload"]["validation_candidates"] == []


def test_exact2_basin_value_pool_evaluation_enumerates_bounded_pool_and_requires_improvement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}

    def fake_validate_compare_aware_results(**kwargs):
        payload = json.loads(Path(kwargs["result_path"]).read_text(encoding="utf-8"))
        candidates = list(payload["validation_candidates"])
        captured["validate_top"] = kwargs["validate_top"]
        captured["candidate_count"] = len(candidates)
        validations = []
        for entry in candidates:
            candidate_hex = str(entry["candidate_hex"])
            is_base = candidate_hex.startswith("78d540b49c590770")
            validations.append(
                {
                    **entry,
                    "candidate_hex": candidate_hex,
                    "cand8_hex": candidate_hex[:16],
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2 if is_base else 1,
                    "runtime_ci_distance5": 246 if is_base else 258,
                    "offline_ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                    "offline_raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                }
            )
        return tmp_path / "value_pool_validation.json", validations

    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        fake_validate_compare_aware_results,
    )

    result = run_exact2_basin_value_pool_evaluation(
        target=target,
        artifacts_dir=tmp_path / "exact2_basin_value_pool",
        base_entry={
            "candidate_hex": "78d540b49c59077041414141414141",
            "cand8_hex": "78d540b49c590770",
            "runtime_ci_exact_wchars": 2,
            "runtime_ci_distance5": 246,
            "ci_exact_wchars": 2,
            "ci_distance5": 246,
        },
        exact2_basin_smt={
            "base_anchor": "78d540b49c590770",
            "variable_byte_positions": [1, 2, 3, 0, 4],
            "feedback_value_pools": {
                "1": [0xD5, 0x3E, 0x3C],
                "2": [0x40, 0x7F, 0x80],
                "3": [0xB4, 0x8F],
                "0": [0x78],
                "4": [0x9C],
            },
        },
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["generated_count"] == 18
    assert payload["unique_count"] == 18
    assert payload["validated_count"] == 18
    assert captured["validate_top"] == 18
    assert captured["candidate_count"] == 18
    assert payload["value_pools"]["1"][0] == 0xD5
    assert payload["value_pools"]["0"] == [0x78]
    assert payload["best_runtime_candidate"]["cand8_hex"] == "78d540b49c590770"
    assert payload["improved_over_exact2"] is False
    assert payload["classification"] == "exact2_basin_value_pools_exhausted_no_gain"
    assert result["promotable_validations"] == []


def test_profile_transform_hypothesis_audit_writes_bounded_metadata_only_matrix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(compare_aware_search, "_negative_exact2_value_pool_recorded", lambda: True)
    monkeypatch.setattr(
        compare_aware_search,
        "_indexed_artifact_payload",
        lambda kind: (
            {
                "attempted": True,
                "classification": "exact2_basin_value_pools_exhausted_no_gain",
                "generated_count": 18,
                "unique_count": 18,
                "validated_count": 18,
                "best_runtime_candidate": {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                },
                "improved_over_exact2": False,
            },
            "indexed/value_pool.json",
        ),
    )

    result = run_profile_transform_hypothesis_audit(
        artifacts_dir=tmp_path,
        transform_model=SamplereverseTransformModel(),
        runtime_validations=[
            {
                "candidate_hex": "78d540b49c59077041414141414141",
                "cand8_hex": "78d540b49c590770",
                "compare_semantics_agree": True,
                "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
                "runtime_ci_exact_wchars": 2,
                "runtime_ci_distance5": 246,
            },
            {
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
                "cand8_hex": "5a3e7f46ddd474d0",
                "compare_semantics_agree": True,
                "runtime_lhs_prefix_hex_10": "460061357f0b8c688502",
                "runtime_ci_exact_wchars": 1,
                "runtime_ci_distance5": 258,
            },
        ],
        top_entries=[],
        exact2_basin_value_pool_run=None,
        search_budget=200_000_000,
        snapshot_interval=10_000_000,
        validate_top=5,
        per_probe_timeout=2.0,
        log=lambda _: None,
    )

    path = Path(result["result_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == PROFILE_TRANSFORM_HYPOTHESIS_MATRIX_FILE_NAME
    assert payload["audit_only"] is True
    assert payload["candidate_generation_changed"] is False
    assert payload["ranking_changed"] is False
    assert payload["final_selection_changed"] is False
    assert payload["beam_budget_topn_timeout_frontier_limit_expanded"] is False
    assert payload["candidate_count"] <= PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT
    assert {item["id"] for item in payload["hypotheses"]} == {"H1", "H2", "H3", "H4", "H5", "H6"}
    assert payload["exhausted_branch_confirmation"]["generated_count"] == 18
    assert payload["exhausted_branch_confirmation"]["negative_result_recorded"] is True
    assert payload["read_scope"]["uses_latest_indexed_artifacts_only"] is True
    assert payload["read_scope"]["scans_full_solve_reports"] is False
    exact2 = next(item for item in payload["candidates"] if item["label"] == "current_exact2_best")
    assert exact2["offline_runtime_prefix_agree_10"] is True
    assert exact2["trace"]["rc4"]["decrypt_prefix_hex"].startswith("46006c004464830d311c")
    assert any(item["promotion_allowed"] is False for item in payload["candidates"])
    assert payload["next_bounded_validation_target"]["selected_hypotheses"] == ["H1", "H3"]


def test_transform_trace_consistency_confirms_runtime_backed_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_indexed_artifact_payload(kind):
        if kind == "h1_h3_boundary_validation_runtime":
            return (
                {
                    "validations": [
                        {
                            "candidate_hex": "78d540b49c59077040414141414141",
                            "cand8_hex": "78d540b49c590770",
                            "compare_semantics_agree": True,
                            "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
                            "runtime_ci_exact_wchars": 2,
                            "runtime_ci_distance5": 246,
                            "stage": "h1_h3_boundary_validation",
                        },
                        {
                            "candidate_hex": "78d540b49c59077042414141414141",
                            "cand8_hex": "78d540b49c590770",
                            "compare_semantics_agree": True,
                            "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
                            "runtime_ci_exact_wchars": 2,
                            "runtime_ci_distance5": 246,
                            "stage": "h1_h3_boundary_validation",
                        },
                    ]
                },
                "indexed/h1_h3_runtime.json",
            )
        if kind == "exact2_basin_value_pool_validation":
            return (
                {
                    "validations": [
                        {
                            "candidate_hex": "78d540b49c59077041414141414141",
                            "cand8_hex": "78d540b49c590770",
                            "compare_semantics_agree": True,
                            "runtime_lhs_prefix_hex_10": "46006c004464830d311c",
                            "runtime_ci_exact_wchars": 2,
                            "runtime_ci_distance5": 246,
                            "stage": "exact2_basin_value_pool",
                        }
                    ]
                },
                "indexed/value_pool_validation.json",
            )
        return {}, ""

    monkeypatch.setattr(compare_aware_search, "_indexed_artifact_payload", fake_indexed_artifact_payload)

    result = run_transform_trace_consistency_diagnostic(
        artifacts_dir=tmp_path,
        runtime_validations=[],
        transform_model=SamplereverseTransformModel(),
        log=lambda _: None,
    )

    path = Path(result["result_path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == TRANSFORM_TRACE_CONSISTENCY_FILE_NAME
    assert payload["classification"] == "transform_model_confirmed"
    assert payload["candidate_generation_changed"] is False
    assert payload["ranking_changed"] is False
    assert payload["final_selection_changed"] is False
    assert payload["beam_budget_topn_timeout_frontier_limit_expanded"] is False
    assert payload["runtime_backed_count"] == 3
    assert payload["promotable_validations"] == []

    baseline = next(
        item for item in payload["candidates"] if item["candidate_hex"] == "78d540b49c59077041414141414141"
    )
    verdict = baseline["verdict"]
    assert verdict["offline_runtime_prefix_agree_10"] is True
    assert verdict["offline_runtime_metrics_agree"] is True
    assert verdict["compare_semantics_agree"] is True
    assert verdict["first_unsupported_stage"] == ""
    assert verdict["evidence_status"] == "supported_by_runtime"
    assert len(baseline["trace"]["prefix_length_table"]) == 10


def test_transform_trace_consistency_reports_missing_runtime_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(compare_aware_search, "_indexed_artifact_payload", lambda kind: ({}, ""))

    result = run_transform_trace_consistency_diagnostic(
        artifacts_dir=tmp_path,
        runtime_validations=[],
        transform_model=SamplereverseTransformModel(),
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["classification"] == "evidence_insufficient"
    assert payload["runtime_backed_count"] == 0
    assert all(
        item["verdict"]["evidence_status"] == "missing_runtime_artifact"
        for item in payload["candidates"]
    )


def _fake_dynamic_probe_validate(tmp_path: Path, captured: dict[str, object] | None = None):
    def fake_validate_compare_aware_results(**kwargs):
        payload = json.loads(Path(kwargs["result_path"]).read_text(encoding="utf-8"))
        candidates = list(payload["validation_candidates"])
        if captured is not None:
            captured["validate_top"] = kwargs["validate_top"]
            captured["candidate_count"] = len(candidates)
            captured["output_file_name"] = kwargs["output_file_name"]
            captured["capture_prefix_bytes"] = kwargs["capture_prefix_bytes"]
        validations = []
        for entry in candidates:
            candidate_hex = str(entry["candidate_hex"])
            trace = trace_candidate_transform(candidate_hex)
            compare_boundary = trace["compare_boundary"]
            validations.append(
                {
                    **entry,
                    "candidate_hex": candidate_hex,
                    "cand8_hex": candidate_hex[:16],
                    "compare_semantics_agree": True,
                    "runtime_lhs_prefix_hex": compare_boundary["raw_prefix_hex_64"],
                    "runtime_lhs_prefix_hex_10": compare_boundary["raw_prefix_hex_10"],
                    "runtime_lhs_prefix_hex_16": compare_boundary["raw_prefix_hex_64"][:32],
                    "runtime_lhs_prefix_bytes_captured": 64,
                    "runtime_lhs_ptr": "0x1000",
                    "runtime_rhs_ptr": "0x2000",
                    "runtime_compare_count": 5,
                    "runtime_rhs_prefix_hex": "66006c00610067007b00",
                    "runtime_rhs_wide_text": "flag{",
                    "runtime_lhs_wide_text": "",
                    "runtime_ci_exact_wchars": compare_boundary["ci_exact_wchars"],
                    "runtime_ci_distance5": compare_boundary["ci_distance5"],
                    "offline_ci_distance5": compare_boundary["ci_distance5"],
                    "offline_raw_distance10": compare_boundary["raw_distance10"],
                    "prefix_boundary": compare_boundary,
                }
            )
        return tmp_path / "dynamic_probe_validation.json", validations

    return fake_validate_compare_aware_results


def test_dynamic_compare_path_probe_has_bounded_candidate_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        _fake_dynamic_probe_validate(tmp_path, captured),
    )

    result = run_dynamic_compare_path_probe(
        target=target,
        artifacts_dir=tmp_path / "dynamic_compare_path_probe",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert Path(result["result_path"]).name == DYNAMIC_COMPARE_PATH_PROBE_FILE_NAME
    assert payload["candidate_count"] == 3
    assert captured["validate_top"] == 3
    assert captured["candidate_count"] == 3
    assert captured["output_file_name"] == DYNAMIC_COMPARE_PATH_PROBE_FILE_NAME


def test_dynamic_compare_path_probe_does_not_expand_search_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        _fake_dynamic_probe_validate(tmp_path, captured),
    )

    result = run_dynamic_compare_path_probe(
        target=target,
        artifacts_dir=tmp_path / "dynamic_compare_path_probe",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["candidate_generation_changed"] is False
    assert payload["ranking_changed"] is False
    assert payload["final_selection_changed"] is False
    assert payload["beam_budget_topn_timeout_frontier_limit_expanded"] is False
    assert captured["capture_prefix_bytes"] == 64


def test_dynamic_compare_path_probe_records_probe_point_availability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        _fake_dynamic_probe_validate(tmp_path),
    )

    result = run_dynamic_compare_path_probe(
        target=target,
        artifacts_dir=tmp_path / "dynamic_compare_path_probe",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["classification"] == "dynamic_probe_complete"
    assert payload["runtime_backed_count"] == 3
    assert payload["probe_points"]["raw_input"] == "available"
    assert payload["probe_points"]["post_rc4_compare_buffer"] == "available"
    assert payload["probe_points"]["compare_target"] == "available"
    assert payload["probe_points"]["compare_length"] == "available"
    assert payload["probe_points"]["compare_unit"] == "available"
    assert payload["probe_points"]["utf16le_payload"] == "inferred"
    assert payload["probe_points"]["base64_material"] == "inferred"
    assert payload["probe_points"]["rc4_key"] == "inferred"
    assert payload["probe_points"]["pre_rc4_runtime_material"] == "unavailable"
    assert payload["candidate_results"][0]["first_failing_wchar"]["index"] == 2


def test_dynamic_compare_path_probe_preserves_existing_selection_behavior(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        _fake_dynamic_probe_validate(tmp_path),
    )

    result = run_dynamic_compare_path_probe(
        target=target,
        artifacts_dir=tmp_path / "dynamic_compare_path_probe",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["promotable_validations"] == []
    assert result["promotable_validations"] == []
    assert payload["final_selection_changed"] is False


def test_h1_h3_boundary_validation_runtime_validates_fixed_contrast_set(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    captured: dict[str, object] = {}

    def fake_validate_compare_aware_results(**kwargs):
        payload = json.loads(Path(kwargs["result_path"]).read_text(encoding="utf-8"))
        candidates = list(payload["validation_candidates"])
        captured["validate_top"] = kwargs["validate_top"]
        captured["candidate_count"] = len(candidates)
        captured["output_file_name"] = kwargs["output_file_name"]
        captured["artifacts_dir_name"] = Path(kwargs["artifacts_dir"]).name
        validations = []
        for entry in candidates:
            candidate_hex = str(entry["candidate_hex"])
            validations.append(
                {
                    **entry,
                    "candidate_hex": candidate_hex,
                    "cand8_hex": candidate_hex[:16],
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "offline_ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                    "offline_raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                }
            )
        return tmp_path / "h1_h3_validation.json", validations

    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        fake_validate_compare_aware_results,
    )

    result = run_h1_h3_boundary_validation(
        target=target,
        artifacts_dir=tmp_path / "h1_h3_boundary_validation",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    candidates = payload["validation_candidates"]
    assert Path(result["result_path"]).name == H1_H3_BOUNDARY_VALIDATION_FILE_NAME
    assert captured["validate_top"] == H1_H3_BOUNDARY_CANDIDATE_LIMIT
    assert captured["candidate_count"] == H1_H3_BOUNDARY_CANDIDATE_LIMIT
    assert captured["output_file_name"] == H1_H3_BOUNDARY_VALIDATION_FILE_NAME
    assert captured["artifacts_dir_name"] == "validation"
    assert payload["candidate_count"] == H1_H3_BOUNDARY_CANDIDATE_LIMIT
    assert candidates[0]["candidate_hex"] == "78d540b49c59077041414141414141"
    assert {item["candidate_hex"] for item in candidates} == {
        "78d540b49c59077041414141414141",
        "78d540b49c59076f41414141414141",
        "78d540b49c59077141414141414141",
        "78d540b49c5907b041414141414141",
        "78d540b49c5907d041414141414141",
        "78d540b49c59077040414141414141",
        "78d540b49c59077042414141414141",
        "78d540b49c59076f42414141414141",
    }
    first = candidates[0]
    assert first["trace_prefix7"]["base64_boundary"]["prefix_last_chunk_raw_remainder"] == 1
    assert first["trace_prefix8"]["base64_boundary"]["prefix_last_chunk_raw_remainder"] == 2
    assert first["trace_prefix9"]["base64_boundary"]["prefix_last_chunk_raw_remainder"] == 0
    assert payload["improved_over_exact2"] is False
    assert payload["classification"] == "h1_h3_boundary_contrast_exhausted_no_gain"
    assert result["promotable_validations"] == []


def test_h1_h3_boundary_validation_promotes_runtime_improvement_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    def fake_validate_compare_aware_results(**kwargs):
        payload = json.loads(Path(kwargs["result_path"]).read_text(encoding="utf-8"))
        validations = []
        for entry in payload["validation_candidates"]:
            candidate_hex = str(entry["candidate_hex"])
            improved = candidate_hex == "78d540b49c59077042414141414141"
            validations.append(
                {
                    **entry,
                    "candidate_hex": candidate_hex,
                    "cand8_hex": candidate_hex[:16],
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 3 if improved else 2,
                    "runtime_ci_distance5": 200 if improved else 246,
                    "offline_ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                    "offline_raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                }
            )
        return tmp_path / "h1_h3_validation.json", validations

    monkeypatch.setattr(
        compare_aware_search,
        "validate_compare_aware_results",
        fake_validate_compare_aware_results,
    )

    result = run_h1_h3_boundary_validation(
        target=target,
        artifacts_dir=tmp_path / "h1_h3_boundary_validation",
        transform_model=SamplereverseTransformModel(),
        per_probe_timeout=0.5,
        log=lambda _: None,
    )

    payload = result["payload"]
    assert payload["classification"] == "h1_h3_boundary_contrast_improved"
    assert payload["improved_over_exact2"] is True
    assert [item["candidate_hex"] for item in result["promotable_validations"]] == [
        "78d540b49c59077042414141414141"
    ]


def test_compare_aware_strategy_runs_second_frontier_guided_round_on_improved_frontier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    guided_calls: list[str] = []
    refine_calls: list[str] = []

    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_bridge",
        lambda **kwargs: {
            "pairscan_path": str(tmp_path / "pairscan_summary.json"),
            "bridge_result_path": str(tmp_path / BRIDGE_RESULT_FILE_NAME),
            "bridge_validation_path": str(tmp_path / "bridge_validation.json"),
            "bridge_entries": [],
            "bridge_validations": [],
            "hot_positions": [0, 1, 2],
            "hot_nibbles": [0, 1, 2, 3, 4],
        },
    )

    def fake_guided_pool(**kwargs):
        base_anchor = kwargs["base_anchor"]
        guided_calls.append(base_anchor)
        entry = {
            "stage": "guided_pool",
            "base_anchor": base_anchor,
            "positions_or_nibbles": [0, 1, 2, 3, 4],
            "candidate_hex": f"{base_anchor}41414141414141",
            "cand8_hex": base_anchor,
            "raw_prefix_hex": "46006c004464830d311c",
            "raw_prefix_hex_64": "46006c004464830d311c",
            "ci_exact_wchars": 2 if base_anchor == "78d540b49c590770" else 0,
            "ci_distance5": 246 if base_anchor == "78d540b49c590770" else 220,
            "raw_distance10": 304 if base_anchor == "78d540b49c590770" else 266,
            "source_anchor": kwargs.get("source_anchor", base_anchor),
            "frontier_role": kwargs.get("frontier_role", ""),
            "anchor_mode": "exact2" if base_anchor == "78d540b49c590770" else "frontier",
            "anchor_lineage": kwargs.get("anchor_lineage", ""),
        }
        return {
            "guided_pool_result_path": str(tmp_path / f"{base_anchor}_guided_pool_result.json"),
            "guided_pool_validation_path": str(tmp_path / f"{base_anchor}_guided_pool_validation.json"),
            "guided_entries": [entry],
            "guided_validations": [],
            "positions": [0, 1, 2, 3, 4],
            "value_pools": {"0": [0x41]},
            "beam_limit": 16,
            "anchor_mode": entry["anchor_mode"],
            "source_anchor": entry["source_anchor"],
            "frontier_role": entry["frontier_role"],
            "anchor_lineage": entry["anchor_lineage"],
            "pair_frontier_pool": [],
            "triad_frontier_pool": [],
            "pair_stage_stats": {},
            "stage_stats": [],
        }

    def fake_run_compare_aware_refine(
        *,
        artifacts_dir: Path,
        search_budget: int,
        seed: int,
        anchors: list[str],
        snapshot_interval: int,
        log,
    ) -> Path:
        _ = search_budget, seed, snapshot_interval, log, anchors
        refine_calls.append(artifacts_dir.name)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out = artifacts_dir / RESULT_FILE_NAME
        candidate_hex = (
            "78d540b49c59077041414141414141"
            if artifacts_dir.name == "artifacts"
            else "f649b64b5e97dbd041414141414141"
            if artifacts_dir.name == "frontier_refine_1"
            else "a47a0a74bd35355041414141414141"
        )
        out.write_text(
            json.dumps(
                {
                    "best": {
                        "candidate_hex": candidate_hex,
                        "cand8_hex": candidate_hex[:16],
                        "raw_prefix_hex": "46006c004464830d311c",
                        "ci_exact_wchars": 2 if candidate_hex.startswith("78d540") else 0,
                        "ci_distance5": 246 if candidate_hex.startswith("78d540") else 218 if candidate_hex.startswith("f649") else 208,
                        "raw_distance10": 304 if candidate_hex.startswith("78d540") else 266,
                    },
                    "top_entries": [
                        {
                            "candidate_hex": candidate_hex,
                            "cand8_hex": candidate_hex[:16],
                            "raw_prefix_hex": "46006c004464830d311c",
                            "ci_exact_wchars": 2 if candidate_hex.startswith("78d540") else 0,
                            "ci_distance5": 246 if candidate_hex.startswith("78d540") else 218 if candidate_hex.startswith("f649") else 208,
                            "raw_distance10": 304 if candidate_hex.startswith("78d540") else 266,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return out

    def fake_validate_compare_aware_results(
        *,
        target: Path,
        artifacts_dir: Path,
        result_path: Path,
        transform_model: SamplereverseTransformModel,
        validate_top: int,
        per_probe_timeout: float,
        log,
        output_file_name: str = VALIDATION_FILE_NAME,
        compare_output_prefix: str = "samplereverse_compare_aware_compare",
    ) -> tuple[Path, list[dict[str, object]]]:
        _ = target, result_path, transform_model, validate_top, per_probe_timeout, log, compare_output_prefix
        out = artifacts_dir / output_file_name
        out.parent.mkdir(parents=True, exist_ok=True)
        if artifacts_dir == tmp_path / "artifacts":
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "f649b64b5e97dbd041414141414141",
                    "cand8_hex": "f649b64b5e97dbd0",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 218,
                    "frontier_role": "exact0_frontier",
                },
            ]
        elif artifacts_dir == tmp_path / "artifacts" / "frontier_refine_1":
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "a47a0a74bd35355041414141414141",
                    "cand8_hex": "a47a0a74bd353550",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 208,
                    "frontier_role": "exact0_frontier",
                    "source_anchor": "f649b64b5e97dbd0",
                    "anchor_lineage": "exact0_frontier(f649b64b5e97dbd0) -> refine(frontier)",
                },
            ]
        else:
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "a47a0a74bd35355041414141414141",
                    "cand8_hex": "a47a0a74bd353550",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 208,
                    "frontier_role": "exact0_frontier",
                    "source_anchor": "a47a0a74bd353550",
                    "anchor_lineage": "exact0_frontier(a47a0a74bd353550) -> refine(frontier)",
                },
            ]
        out.write_text(json.dumps({"validations": validations}, ensure_ascii=False), encoding="utf-8")
        return out, validations

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_guided_pool", fake_guided_pool)
    monkeypatch.setattr(compare_aware_search, "run_compare_aware_refine", fake_run_compare_aware_refine)
    monkeypatch.setattr(compare_aware_search, "validate_compare_aware_results", fake_validate_compare_aware_results)
    smt_calls: list[dict[str, object]] = []

    def fake_run_compare_aware_smt(**kwargs):
        smt_calls.append(kwargs)
        result_path = tmp_path / f"smt_result_{len(smt_calls)}.json"
        return {
            "result_path": str(result_path),
            "validation_path": "",
            "entry": None,
            "validations": [],
            "payload": {"summary": "smt attempted"},
        }

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_smt", fake_run_compare_aware_smt)

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
    )

    assert guided_calls == [
        "78d540b49c590770",
        "f649b64b5e97dbd0",
        "a47a0a74bd353550",
    ]
    assert refine_calls == ["artifacts", "frontier_refine_1", "frontier_refine_2"]
    assert len(result.metadata["frontier_iterations"]) == 2
    assert result.metadata["frontier_converged_reason"] == "iteration_limit"


def test_compare_aware_strategy_runs_second_frontier_guided_round_on_second_hop_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    guided_calls: list[str] = []
    refine_calls: list[str] = []

    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_bridge",
        lambda **kwargs: {
            "pairscan_path": str(tmp_path / "pairscan_summary.json"),
            "bridge_result_path": str(tmp_path / BRIDGE_RESULT_FILE_NAME),
            "bridge_validation_path": str(tmp_path / "bridge_validation.json"),
            "bridge_entries": [],
            "bridge_validations": [],
            "hot_positions": [0, 1, 2],
            "hot_nibbles": [0, 1, 2, 3, 4],
        },
    )

    def fake_guided_pool(**kwargs):
        base_anchor = kwargs["base_anchor"]
        guided_calls.append(base_anchor)
        entry = {
            "stage": "guided_pool",
            "base_anchor": base_anchor,
            "positions_or_nibbles": [1, 2, 3],
            "candidate_hex": f"{base_anchor}41414141414141",
            "cand8_hex": base_anchor,
            "raw_prefix_hex": "460061357f0b8c688502",
            "raw_prefix_hex_64": "460061357f0b8c688502",
            "ci_exact_wchars": 1 if base_anchor == "5a3e7f46ddd474d0" else 0,
            "ci_distance5": 258 if base_anchor == "5a3e7f46ddd474d0" else 740,
            "raw_distance10": 290 if base_anchor == "5a3e7f46ddd474d0" else 772,
            "source_anchor": kwargs.get("source_anchor", base_anchor),
            "frontier_role": kwargs.get("frontier_role", ""),
            "anchor_mode": "frontier",
            "anchor_lineage": kwargs.get("anchor_lineage", ""),
        }
        guided_entries = [entry]
        if base_anchor == "5a3e7f46ddd474d0":
            guided_entries.append(
                {
                    "stage": "guided_pool",
                    "base_anchor": base_anchor,
                    "positions_or_nibbles": [1, 2, 3],
                    "candidate_hex": "5a3f7f46ddd474d041414141414141",
                    "cand8_hex": "5a3f7f46ddd474d0",
                    "raw_prefix_hex": "74934b156ba69ef3370f",
                    "raw_prefix_hex_64": "74934b156ba69ef3370f",
                    "ci_exact_wchars": 0,
                    "ci_distance5": 740,
                    "raw_distance10": 772,
                    "source_anchor": kwargs.get("source_anchor", base_anchor),
                    "frontier_role": "projected_preserve_handoff",
                    "anchor_mode": "frontier",
                    "anchor_lineage": "exact2_seed(78d540b49c590770) -> guided(frontier)",
                    "pair_candidate_origin": "exact1_projected_preserve_lane",
                    "pair_projected_boundary_role": "projected_winner_with_base",
                    "pair_projected_winner_gate_status": "projected_winner_promoted_to_near_local",
                }
            )
        return {
            "guided_pool_result_path": str(tmp_path / f"{base_anchor}_guided_pool_result.json"),
            "guided_pool_validation_path": str(tmp_path / f"{base_anchor}_guided_pool_validation.json"),
            "guided_entries": guided_entries,
            "guided_validations": [],
            "positions": [1, 2, 3],
            "value_pools": {"1": [0x3E, 0x3F]},
            "beam_limit": 16,
            "anchor_mode": entry["anchor_mode"],
            "source_anchor": entry["source_anchor"],
            "frontier_role": entry["frontier_role"],
            "anchor_lineage": entry["anchor_lineage"],
            "pair_frontier_pool": [],
            "triad_frontier_pool": [],
            "pair_stage_stats": {},
            "stage_stats": [],
        }

    def fake_run_compare_aware_refine(
        *,
        artifacts_dir: Path,
        search_budget: int,
        seed: int,
        anchors: list[str],
        snapshot_interval: int,
        log,
    ) -> Path:
        _ = search_budget, seed, snapshot_interval, log, anchors
        refine_calls.append(artifacts_dir.name)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out = artifacts_dir / RESULT_FILE_NAME
        candidate_hex = (
            "78d540b49c59077041414141414141"
            if artifacts_dir.name == "artifacts"
            else "5a3e7f46ddd474d041414141414141"
            if artifacts_dir.name == "frontier_refine_1"
            else "5a3f7f46ddd474d041414141414141"
        )
        out.write_text(
            json.dumps(
                {
                    "best": {
                        "candidate_hex": candidate_hex,
                        "cand8_hex": candidate_hex[:16],
                        "raw_prefix_hex": "460061357f0b8c688502",
                        "ci_exact_wchars": 2 if candidate_hex.startswith("78d540") else 1 if candidate_hex.startswith("5a3e") else 0,
                        "ci_distance5": 246 if candidate_hex.startswith("78d540") else 258 if candidate_hex.startswith("5a3e") else 740,
                        "raw_distance10": 304 if candidate_hex.startswith("78d540") else 290 if candidate_hex.startswith("5a3e") else 772,
                    },
                    "top_entries": [
                        {
                            "candidate_hex": candidate_hex,
                            "cand8_hex": candidate_hex[:16],
                            "raw_prefix_hex": "460061357f0b8c688502",
                            "ci_exact_wchars": 2 if candidate_hex.startswith("78d540") else 1 if candidate_hex.startswith("5a3e") else 0,
                            "ci_distance5": 246 if candidate_hex.startswith("78d540") else 258 if candidate_hex.startswith("5a3e") else 740,
                            "raw_distance10": 304 if candidate_hex.startswith("78d540") else 290 if candidate_hex.startswith("5a3e") else 772,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return out

    def fake_validate_compare_aware_results(
        *,
        target: Path,
        artifacts_dir: Path,
        result_path: Path,
        transform_model: SamplereverseTransformModel,
        validate_top: int,
        per_probe_timeout: float,
        log,
        output_file_name: str = VALIDATION_FILE_NAME,
        compare_output_prefix: str = "samplereverse_compare_aware_compare",
    ) -> tuple[Path, list[dict[str, object]]]:
        _ = target, result_path, transform_model, validate_top, per_probe_timeout, log, compare_output_prefix
        out = artifacts_dir / output_file_name
        out.parent.mkdir(parents=True, exist_ok=True)
        if artifacts_dir == tmp_path / "artifacts":
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 1,
                    "runtime_ci_distance5": 258,
                    "frontier_role": "exact1_frontier",
                    "source_anchor": "78d540b49c590770",
                    "anchor_lineage": "exact2_seed(78d540b49c590770) -> guided(frontier)",
                },
            ]
        elif artifacts_dir == tmp_path / "artifacts" / "frontier_refine_1":
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "5a3e7f46ddd474d041414141414141",
                    "cand8_hex": "5a3e7f46ddd474d0",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 1,
                    "runtime_ci_distance5": 258,
                    "frontier_role": "exact1_frontier",
                    "source_anchor": "78d540b49c590770",
                },
                {
                    "candidate_hex": "5a3f7f46ddd474d041414141414141",
                    "cand8_hex": "5a3f7f46ddd474d0",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 740,
                    "frontier_role": "projected_preserve_handoff",
                    "source_anchor": "78d540b49c590770",
                },
            ]
        else:
            validations = [
                {
                    "candidate_hex": "78d540b49c59077041414141414141",
                    "cand8_hex": "78d540b49c590770",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 2,
                    "runtime_ci_distance5": 246,
                    "frontier_role": "exact2_seed",
                },
                {
                    "candidate_hex": "5a3f7f46ddd474d041414141414141",
                    "cand8_hex": "5a3f7f46ddd474d0",
                    "compare_semantics_agree": True,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 740,
                    "frontier_role": PROJECTED_PRESERVE_SECOND_HOP_ROLE,
                    "source_anchor": "78d540b49c590770",
                },
            ]
        out.write_text(json.dumps({"validations": validations}, ensure_ascii=False), encoding="utf-8")
        return out, validations

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_guided_pool", fake_guided_pool)
    monkeypatch.setattr(compare_aware_search, "run_compare_aware_refine", fake_run_compare_aware_refine)
    monkeypatch.setattr(compare_aware_search, "validate_compare_aware_results", fake_validate_compare_aware_results)
    smt_calls: list[dict[str, object]] = []

    def fake_run_compare_aware_smt(**kwargs):
        smt_calls.append(kwargs)
        result_path = tmp_path / f"smt_second_hop_result_{len(smt_calls)}.json"
        return {
            "result_path": str(result_path),
            "validation_path": "",
            "entry": None,
            "validations": [],
            "payload": {"summary": "smt attempted"},
        }

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_smt", fake_run_compare_aware_smt)

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
    )

    assert guided_calls == [
        "78d540b49c590770",
        "5a3e7f46ddd474d0",
        "5a3f7f46ddd474d0",
    ]
    assert refine_calls == ["artifacts", "frontier_refine_1", "frontier_refine_2"]
    assert len(result.metadata["frontier_iterations"]) == 2
    assert result.metadata["frontier_iterations"][0]["used_second_hop_frontier_candidates"] is True
    assert result.metadata["frontier_iterations"][0]["frontier_converged_reason"] == "continue"
    assert result.metadata["frontier_guided_runs"][1]["frontier_role"] == PROJECTED_PRESERVE_SECOND_HOP_ROLE
    assert result.metadata["frontier_guided_runs"][1]["anchor"] == "5a3f7f46ddd474d0"
    assert [Path(call["artifacts_dir"]).name for call in smt_calls] == ["smt", "smt_exact2_basin"]
    assert smt_calls[1]["base_entry"]["cand8_hex"] == "78d540b49c590770"
    assert set(smt_calls[1]["variable_byte_positions_override"]) == {0, 1, 2, 3, 4}
    assert smt_calls[1]["value_pools_override"]["1"][0] == 0xD5
    assert result.metadata["exact2_basin_smt"]["payload"]["exact2_basin_smt"]["attempted"] is True


def test_samplereverse_profile_runs_compare_aware_strategy_when_only_compare_truth_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ")
    profile = SamplereverseProfile()
    compare_artifact = ToolRunArtifact(
        tool_name="CompareProbe",
        enabled=True,
        attempted=True,
        success=True,
        structured_evidence=[
            StructuredEvidence(kind="RuntimeCompareEvidence", source_tool="CompareProbe"),
        ],
    )
    strategy_artifact = ToolRunArtifact(
        tool_name="CompareAwareBridge",
        enabled=True,
        attempted=True,
        success=True,
        summary="compare-aware ok",
    )
    called: dict[str, bool] = {"strategy": False}

    def fake_strategy_run(self, **kwargs) -> StrategyResult:
        _ = kwargs
        called["strategy"] = True
        return StrategyResult(
            strategy_name="CompareAwareSearchStrategy",
            summary="compare-aware ok",
            candidates=[bytes.fromhex("4a78f0eaeb4f13b041414141414141").decode("latin1")],
            artifacts=[strategy_artifact],
        )

    monkeypatch.setattr(CompareAwareSearchStrategy, "run", fake_strategy_run)

    result = profile.run_specialized_solver(
        file_path=sample,
        strings=["输入的密钥是", "密钥不正确"],
        seed_candidates=[],
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        prior_artifacts=[compare_artifact],
    )

    assert result is not None
    assert result.enabled is True
    assert called["strategy"] is True
    assert result.strategies == ["CompareAwareSearchStrategy"]
    assert result.candidates[0].encode("latin1").hex() == "4a78f0eaeb4f13b041414141414141"
