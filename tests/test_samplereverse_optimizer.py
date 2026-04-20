import json
from pathlib import Path

from reverse_agent.samplereverse_optimizer import (
    OPTIMIZER_BASELINE_SUMMARY_FILE_NAME,
    OPTIMIZER_RESULT_FILE_NAME,
    _collect_validation_entries,
    load_optimizer_seed_candidates,
    run_optimizer_baselines,
)


def _write_result(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_optimizer_seed_loader_reads_unique_candidate_hexes_with_best_dist6(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / OPTIMIZER_RESULT_FILE_NAME
    _write_result(
        result_path,
        {
            "best_prefix": {"cand7_hex": "6f7eebb7a23037"},
            "best_dist4": {"cand7_hex": "6f7ec7b7a228a2"},
            "best_dist6": {"cand7_hex": "6f9debb74a3837"},
            "elite_prefixes": [
                {"cand7_hex": "6f7ec7b7a228a2"},
                {"cand7_hex": "017eebb7043021"},
                {"cand7_hex": "invalid"},
            ],
        },
    )
    seeds = load_optimizer_seed_candidates(result_path)
    assert [seed.encode("latin1").hex() for seed in seeds] == [
        "6f7eebb7a23037414141414141",
        "6f7ec7b7a228a2414141414141",
        "6f9debb74a3837414141414141",
        "017eebb7043021414141414141",
    ]


def test_optimizer_seed_loader_falls_back_to_best_dist10(tmp_path: Path) -> None:
    result_path = tmp_path / OPTIMIZER_RESULT_FILE_NAME
    _write_result(
        result_path,
        {
            "best_prefix": {"cand7_hex": "6f7eebb7a23037"},
            "best_dist4": {"cand7_hex": "6f7ec7b7a228a2"},
            "best_dist10": {"cand7_hex": "6f9debb74a3837"},
        },
    )
    seeds = load_optimizer_seed_candidates(result_path)
    assert [seed.encode("latin1").hex() for seed in seeds] == [
        "6f7eebb7a23037414141414141",
        "6f7ec7b7a228a2414141414141",
        "6f9debb74a3837414141414141",
    ]


def test_collect_validation_entries_prefers_mode_bests_then_elites_with_gate() -> None:
    payload = {
        "best_prefix": {
            "cand7_hex": "6f7eebb7a23037",
            "exact_prefix_len": 3,
            "distance4": 4,
        },
        "best_dist4": {
            "cand7_hex": "6f7ec7b7a228a2",
            "exact_prefix_len": 3,
            "distance4": 3,
        },
        "best_dist6": {
            "cand7_hex": "6f9debb74a3837",
            "exact_prefix_len": 4,
            "distance4": 3,
        },
        "elite_prefixes": [
            {
                "cand7_hex": "6f93e0cfa23037",
                "exact_prefix_len": 3,
                "distance4": 3,
            },
            {
                "cand7_hex": "017eebb7043021",
                "exact_prefix_len": 2,
                "distance4": 2,
            },
            {
                "cand7_hex": "163febb7a24737",
                "exact_prefix_len": 3,
                "distance4": 4,
            },
        ],
    }
    entries = _collect_validation_entries(payload, validate_top=5)
    assert [entry["label"] for entry in entries] == ["dist4", "dist6", "elite1"]
    assert [entry["candidate_hex"] for entry in entries] == [
        "6f7ec7b7a228a2414141414141",
        "6f9debb74a3837414141414141",
        "6f93e0cfa23037414141414141",
    ]


def test_collect_validation_entries_ignores_exact2_for_distance_gate() -> None:
    payload = {
        "best_prefix": {
            "cand7_hex": "6f7eebb7a23037",
            "exact_prefix_len": 3,
            "distance4": 8,
        },
        "best_dist4": {
            "cand7_hex": "6f7ec7b7a228a2",
            "exact_prefix_len": 2,
            "distance4": 3,
        },
        "best_dist6": {
            "cand7_hex": "6f93e0cfa23037",
            "exact_prefix_len": 3,
            "distance4": 8,
        },
    }
    entries = _collect_validation_entries(payload, validate_top=5)
    assert [entry["label"] for entry in entries] == ["prefix", "dist6"]


def test_run_optimizer_baselines_writes_summary_with_unique_prefixes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    def fake_run_optimizer(
        artifacts_dir: Path,
        max_evals: int,
        seed: int,
        log,
    ) -> Path:
        result_path = artifacts_dir / OPTIMIZER_RESULT_FILE_NAME
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        suffix = f"{seed:02x}"[-2:]
        _write_result(
            result_path,
            {
                "best_prefix": {
                    "cand7_hex": "6f7eebb7a23037",
                    "lhs_prefix_hex": "66006c28824165aca053",
                    "distance4": 40,
                },
                "best_dist4": {
                    "cand7_hex": "6f7ec7b7a228a2",
                    "lhs_prefix_hex": f"66006b{suffix}4002c354bb7d",
                    "distance4": 3,
                },
                "best_dist6": {
                    "cand7_hex": "6f9debb74a3837",
                    "lhs_prefix_hex": f"66004c{suffix}41526810556e",
                    "distance4": 55,
                },
            },
        )
        return result_path

    def fake_validate_optimizer_results(
        target: Path,
        artifacts_dir: Path,
        result_path: Path,
        validate_top: int,
        per_probe_timeout: float,
        log,
    ) -> Path:
        out = artifacts_dir / "validation.json"
        _write_result(out, {"ok": True, "result_path": str(result_path)})
        return out

    monkeypatch.setattr(
        "reverse_agent.samplereverse_optimizer.run_optimizer",
        fake_run_optimizer,
    )
    monkeypatch.setattr(
        "reverse_agent.samplereverse_optimizer.validate_optimizer_results",
        fake_validate_optimizer_results,
    )

    summary_path = run_optimizer_baselines(
        target=target,
        artifacts_dir=tmp_path / "baseline",
        max_evals=5_000_000,
        seeds=[20260420, 20260421, 20260422],
        validate_top=5,
        per_probe_timeout=1.8,
        log=lambda _: None,
    )

    assert summary_path.name == OPTIMIZER_BASELINE_SUMMARY_FILE_NAME
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert [run["seed"] for run in summary["runs"]] == [20260420, 20260421, 20260422]
    assert len(summary["unique_lhs_prefix_hex"]) == 7
    assert summary["repeated_first4_patterns"]["66006c28"] == 3
