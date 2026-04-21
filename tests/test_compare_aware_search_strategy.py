import json
from pathlib import Path

from reverse_agent.evidence import StructuredEvidence
from reverse_agent.profiles.samplereverse import SamplereverseProfile
from reverse_agent.sample_solver import _decrypt_prefix
from reverse_agent.strategies import compare_aware_search
from reverse_agent.strategies.base import StrategyResult
from reverse_agent.strategies.compare_aware_search import (
    CompareAwareSearchStrategy,
    RESULT_FILE_NAME,
    VALIDATION_FILE_NAME,
    _collect_validation_entries,
)
from reverse_agent.tool_runners import ToolRunArtifact
from reverse_agent.transforms.samplereverse import SamplereverseTransformModel, score_compare_prefix


def test_score_compare_prefix_counts_known_exact2_basins() -> None:
    assert score_compare_prefix(bytes.fromhex("66006c0038ac00000000"))["ci_exact_wchars"] == 2
    assert score_compare_prefix(bytes.fromhex("46004c007e4000000000"))["ci_exact_wchars"] == 2


def test_score_compare_prefix_is_case_insensitive_for_ascii_letters() -> None:
    assert score_compare_prefix(bytes.fromhex("66006c00610067007b00"))["ci_exact_wchars"] == 5
    assert score_compare_prefix(bytes.fromhex("46004c00610067007b00"))["ci_exact_wchars"] == 5


def test_l15_byte7_low_nibble_does_not_change_compare_score() -> None:
    candidate_a = bytes.fromhex("4a78f0eaeb4f13b041414141414141").decode("latin1")
    candidate_b = bytes.fromhex("4a78f0eaeb4f13b141414141414141").decode("latin1")
    prefix_a = _decrypt_prefix(candidate_a, 15)[:10]
    prefix_b = _decrypt_prefix(candidate_b, 15)[:10]
    assert score_compare_prefix(prefix_a) == score_compare_prefix(prefix_b)


def test_collect_validation_entries_uses_best_distance_among_exact2_plus() -> None:
    payload = {
        "top_entries": [
            {
                "cand8_hex": "4a78f0eaeb4f13b0",
                "candidate_hex": "4a78f0eaeb4f13b041414141414141",
                "raw_prefix_hex": "46004c007e40b92886f5",
                "ci_exact_wchars": 2,
                "ci_distance5": 471,
                "raw_distance10": 535,
            },
            {
                "cand8_hex": "12c49959ff8f7cc0",
                "candidate_hex": "12c49959ff8f7cc041414141414141",
                "raw_prefix_hex": "61358204600f7c0b9324",
                "ci_exact_wchars": 0,
                "ci_distance5": 192,
                "raw_distance10": 192,
            },
        ]
    }
    entries = _collect_validation_entries(payload, SamplereverseTransformModel(), validate_top=3)
    assert [entry["candidate_hex"] for entry in entries] == ["4a78f0eaeb4f13b041414141414141"]


def test_compare_aware_strategy_emits_compare_metrics_and_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")
    candidate_hex = "4a78f0eaeb4f13b041414141414141"

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
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        out = artifacts_dir / RESULT_FILE_NAME
        out.write_text(
            json.dumps(
                {
                    "anchors": anchors,
                    "best": {
                        "cand8_hex": "4a78f0eaeb4f13b0",
                        "candidate_hex": candidate_hex,
                        "raw_prefix_hex": "46004c007e40b92886f5",
                        "ci_exact_wchars": 2,
                        "ci_distance5": 471,
                        "raw_distance10": 535,
                    },
                    "top_entries": [
                        {
                            "cand8_hex": "4a78f0eaeb4f13b0",
                            "candidate_hex": candidate_hex,
                            "raw_prefix_hex": "46004c007e40b92886f5",
                            "ci_exact_wchars": 2,
                            "ci_distance5": 471,
                            "raw_distance10": 535,
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
    ) -> tuple[Path, list[dict[str, object]]]:
        _ = target, result_path, transform_model, validate_top, per_probe_timeout, log
        out = artifacts_dir / VALIDATION_FILE_NAME
        validations = [
            {
                "label": "top1",
                "candidate_hex": candidate_hex,
                "cand8_hex": "4a78f0eaeb4f13b0",
                "offline_ci_exact_wchars": 2,
                "offline_ci_distance5": 471,
                "offline_raw_prefix_hex": "46004c007e40b92886f5",
                "compare_summary": "compare ok",
                "runtime_lhs_prefix_hex_10": "46004c007e40b92886f5",
                "runtime_ci_exact_wchars": 5,
                "runtime_ci_distance5": 0,
                "compare_semantics_agree": True,
                "matched_target_prefix": True,
            }
        ]
        out.write_text(json.dumps({"validations": validations}, ensure_ascii=False), encoding="utf-8")
        return out, validations

    monkeypatch.setattr(compare_aware_search, "run_compare_aware_refine", fake_run_compare_aware_refine)
    monkeypatch.setattr(compare_aware_search, "validate_compare_aware_results", fake_validate_compare_aware_results)

    result = CompareAwareSearchStrategy().run(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        transform_model=SamplereverseTransformModel(),
        anchors=["4a78f0eaeb4f13b0", "e05e579fca169e80"],
        search_budget=1000,
        seed=1,
        snapshot_interval=100,
        validate_top=3,
        per_probe_timeout=1.0,
    )

    assert result.strategy_name == "CompareAwareSearchStrategy"
    assert result.candidates[0].encode("latin1").hex() == candidate_hex
    assert [artifact.tool_name for artifact in result.artifacts] == [
        "CompareAwareRefine",
        "CompareAwareValidation",
    ]
    assert any(item.kind == "TransformEvidence" for item in result.artifacts[0].structured_evidence)
    runtime_evidence = next(
        item for item in result.artifacts[1].structured_evidence if item.kind == "RuntimeCompareEvidence"
    )
    assert runtime_evidence.payload["runtime_ci_exact_wchars"] == 5
    assert runtime_evidence.payload["compare_semantics_agree"] is True
    assert runtime_evidence.derived_candidates[0].encode("latin1").hex() == candidate_hex


def test_samplereverse_profile_skips_solver_when_compare_probe_already_recovered_candidate(
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
            StructuredEvidence(
                kind="CandidateEvidence",
                source_tool="CompareProbe",
                derived_candidates=["flag{"],
            ),
        ],
    )

    monkeypatch.setattr(
        CompareAwareSearchStrategy,
        "run",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("strategy should be skipped")),
    )
    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.run_samplereverse_resumable_search",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("sample solver should be skipped")),
    )

    result = profile.run_specialized_solver(
        file_path=sample,
        strings=["输入的密钥是", "密钥不正确"],
        seed_candidates=[],
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        prior_artifacts=[compare_artifact],
    )

    assert result is not None
    assert result.enabled is False
    assert "recovered candidates" in result.summary


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
        tool_name="CompareAwareRefine",
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
    def should_not_run_sample_solver(**kwargs):
        raise AssertionError("sample solver should not run")

    monkeypatch.setattr(
        "reverse_agent.profiles.samplereverse.run_samplereverse_resumable_search",
        should_not_run_sample_solver,
    )
    monkeypatch.setattr(
        "reverse_agent.pipeline.run_samplereverse_resumable_search",
        should_not_run_sample_solver,
    )

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
