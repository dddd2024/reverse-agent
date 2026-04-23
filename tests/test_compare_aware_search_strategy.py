import json
import subprocess
from pathlib import Path

from reverse_agent.evidence import StructuredEvidence
from reverse_agent.profiles.samplereverse import SamplereverseProfile
from reverse_agent.strategies import compare_aware_search
from reverse_agent.strategies.base import StrategyResult
from reverse_agent.strategies.compare_aware_search import (
    BRIDGE_RESULT_FILE_NAME,
    CompareAwareSearchStrategy,
    FRONTIER_ANCHOR_MODE,
    FRONTIER_EXACT0_SUBMODE,
    FRONTIER_EXACT1_SUBMODE,
    GUIDED_POOL_EXPLORATION_SLOTS,
    GUIDED_POOL_TOP_VALUES,
    RESULT_FILE_NAME,
    VALIDATION_FILE_NAME,
    _alternate_locked_pair_positions_for_exact1,
    _annotate_frontier_improvement_gate,
    _candidate_sort_key,
    _collect_validation_entries,
    _collect_frontier_promoted_anchors,
    _diverse_validation_candidates,
    _extract_hot_positions,
    _feedback_value_pools_from_frontier_entries,
    _frontier_anchor_candidates,
    _guided_pool_beam_entries,
    _improved_frontier_candidates,
    _mine_exact1_lineage_value_sources,
    _refine_anchor_plan,
    _select_smt_base_entry,
    run_compare_aware_smt,
    validate_compare_aware_results,
    resolve_compare_aware_anchors,
)
from reverse_agent.tool_runners import ToolRunArtifact
from reverse_agent.transforms.samplereverse import SamplereverseTransformModel, score_compare_prefix


def test_score_compare_prefix_counts_known_exact2_basins() -> None:
    assert score_compare_prefix(bytes.fromhex("66006c0038ac00000000"))["ci_exact_wchars"] == 2
    assert score_compare_prefix(bytes.fromhex("46004c007e4000000000"))["ci_exact_wchars"] == 2


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
    assert result.metadata["completed_stage"] == "smt"
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
    assert diagnostics["pair_escape_status_by_lane"]["0,1"]["local_escape"] == "gate_borderline_escape"
    assert diagnostics["pair_escape_source_statuses"]["0,1"] == "gate_borderline_escape"
    assert diagnostics["pair_local_escape_borderline_count"] == 1
    assert drop_reasons == {}


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


def test_run_compare_aware_smt_records_feedback_value_pools_from_improved_frontier_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    monkeypatch.setattr(
        compare_aware_search,
        "solve_targeted_prefix8",
        lambda **kwargs: type(
            "Z3Result",
            (),
            {
                "attempted": True,
                "summary": "ok",
                "evidence": [],
                "candidate_hex": "5a3e7f46ddd474d041414141414141",
            },
        )(),
    )
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
            "pair_borderline_escape_candidates": [
                {
                    "cand8_hex": "5a667f46ddd474d0",
                    "pair_positions": [0, 1],
                    "pair_values": [0x5A, 0x66],
                    "pair_escape_status": "borderline",
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
    assert 0x55 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x99 in result["payload"]["feedback_value_pools"]["1"]
    assert 0x78 in result["payload"]["feedback_value_pools"]["0"]


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
    monkeypatch.setattr(
        compare_aware_search,
        "run_compare_aware_smt",
        lambda **kwargs: {
            "result_path": str(tmp_path / "smt_result.json"),
            "validation_path": "",
            "entry": None,
            "validations": [],
            "payload": {"summary": "smt attempted"},
        },
    )

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
