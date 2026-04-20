import json
from pathlib import Path

from reverse_agent.sample_solver import (
    _dedupe_top_scored_by_prefix,
    _decrypt_prefix,
    _key_length_for_input_length,
    _load_optimizer_seed_candidates,
    _score_candidate_bytes,
    _score_candidate_prefix,
    _top_single_byte_values,
)
from reverse_agent.sample_solver import _objective_tuple
from reverse_agent.sample_solver import _prefix_distance
from reverse_agent.sample_solver import _wide_prefix_metrics
from reverse_agent.sample_solver import CHECKPOINT_FILE_NAME
from reverse_agent.sample_solver import OPTIMIZER_RESULT_FILE_NAME
from reverse_agent.sample_solver import run_samplereverse_resumable_search


def test_decrypt_prefix_known_vector() -> None:
    assert _decrypt_prefix("AAAA", 5).hex() == "243767ace0"


def test_key_length_for_input_length_matches_base64_utf16_shape() -> None:
    assert _key_length_for_input_length(12) == 64
    assert _key_length_for_input_length(13) == 72
    assert _key_length_for_input_length(14) == 76
    assert _key_length_for_input_length(15) == 80


def test_decrypt_prefix_matches_runtime_compare_bytes_for_known_candidate() -> None:
    assert _decrypt_prefix("AAAAAAA", 16).hex() == "d4d2e5f8a5a7e64e367ca8284e098d8f"


def test_score_candidate_bytes_matches_text_variant() -> None:
    candidate = bytes.fromhex("6f7eebb7a23037414141414141")
    assert _score_candidate_bytes(candidate) == _score_candidate_prefix(
        candidate.decode("latin1")
    )


def test_objective_prefers_smaller_distance_on_tie() -> None:
    better = _objective_tuple("66006c00610067007b00", 3, 0b10101)
    worse = _objective_tuple("67006d00620068007c00", 3, 0b10101)
    assert better > worse
    assert _prefix_distance("66006c00610067007b00") < _prefix_distance("67006d00620068007c00")


def test_wide_prefix_metrics_count_contiguous_wchars() -> None:
    assert _wide_prefix_metrics("66006c00610067007b00") == (5, 5)
    assert _wide_prefix_metrics("66006c00620067007b00") == (2, 4)


def test_top_single_byte_values_includes_original_candidates() -> None:
    candidate = bytes.fromhex("6f7eebb7a23037414141414141")
    shortlist = _top_single_byte_values(candidate, [0, 1, 2], top_k=4)
    assert set(shortlist) == {0, 1, 2}
    for pos, values in shortlist.items():
        assert candidate[pos] in values
        assert 1 <= len(values) <= 4


def test_dedupe_top_scored_by_prefix_keeps_single_representative() -> None:
    items = [
        (3, 896, "AAAA", "66006c28824165aca053"),
        (3, 896, "AAAB", "66006c28824165aca053"),
        (2, 768, "BBBB", "66006490faf0fd0b7e10"),
    ]
    deduped = _dedupe_top_scored_by_prefix(items, limit=10)
    assert len(deduped) == 2
    assert deduped[0][3] == "66006c28824165aca053"


def test_load_optimizer_seed_candidates_reads_unique_cand7_entries(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / OPTIMIZER_RESULT_FILE_NAME).write_text(
        json.dumps(
            {
                "best_prefix": {"cand7_hex": "6f7eebb7a23037"},
                "best_dist4": {"cand7_hex": "6f7ec7b7a228a2"},
                "best_dist6": {"cand7_hex": "6f9debb74a3837"},
                "elite_prefixes": [
                    {"cand7_hex": "6f7eebb7a23037"},
                    {"cand7_hex": "017eebb7043021"},
                    {"cand7_hex": "invalid"},
                ],
            }
        ),
        encoding="utf-8",
    )
    seeds = _load_optimizer_seed_candidates(artifacts)
    assert [seed.encode("latin1").hex() for seed in seeds] == [
        "6f7eebb7a23037414141414141",
        "6f7ec7b7a228a2414141414141",
        "6f9debb74a3837414141414141",
        "017eebb7043021414141414141",
    ]


def test_samplereverse_probe_skips_non_matching_file(tmp_path: Path) -> None:
    sample = tmp_path / "sample.exe"
    sample.write_bytes(b"MZ")
    result = run_samplereverse_resumable_search(
        file_path=sample,
        strings=["hello", "world"],
        seed_candidates=[],
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        max_attempts=10,
    )
    assert result.enabled is False
    assert result.candidates == []


def test_samplereverse_probe_writes_checkpoint(tmp_path: Path) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ" + b"\x00" * 64 + bytes.fromhex("698b8fb18f3b4f9961726ba869132942e6ff36b8"))
    result = run_samplereverse_resumable_search(
        file_path=sample,
        strings=["输入的密钥是", "密钥不正确"],
        seed_candidates=["flag{"],
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        max_attempts=10,
    )
    assert result.enabled is True
    checkpoint = tmp_path / "artifacts" / "samplereverse_search_checkpoint.json"
    assert checkpoint.exists()


def test_samplereverse_probe_preserves_control_byte_seed_candidates(tmp_path: Path) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ" + b"\x00" * 64 + bytes.fromhex("698b8fb18f3b4f9961726ba869132942e6ff36b8"))
    control_seed = bytes.fromhex("0b89171efb805c414141414141").decode("latin1")
    result = run_samplereverse_resumable_search(
        file_path=sample,
        strings=["输入的密钥是", "密钥不正确"],
        seed_candidates=[control_seed],
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        max_attempts=10,
    )
    assert result.candidates[0].encode("latin1").hex() == "0b89171efb805c414141414141"


def test_samplereverse_deadline_budget_change_resets_window(tmp_path: Path) -> None:
    sample = tmp_path / "samplereverse.exe"
    sample.write_bytes(b"MZ" + b"\x00" * 64 + bytes.fromhex("698b8fb18f3b4f9961726ba869132942e6ff36b8"))
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    checkpoint_path = artifacts / CHECKPOINT_FILE_NAME
    checkpoint_path.write_text(
        json.dumps(
            {
                "cartesian_length": 4,
                "cartesian_index": 0,
                "dep_tier": 0,
                "dep_index": 0,
                "m44_tier": 0,
                "m44_index": 0,
                "m48_tier": 0,
                "m48_index": 0,
                "best_hex": "",
                "best_score": 0,
                "best_mask": 0,
                "started_epoch": 1000.0,
                "deadline_epoch": 1030.0,
                "deadline_budget_seconds": 30.0,
            }
        ),
        encoding="utf-8",
    )
    run_samplereverse_resumable_search(
        file_path=sample,
        strings=["输入的密钥是", "密钥不正确"],
        seed_candidates=[],
        artifacts_dir=artifacts,
        log=lambda _: None,
        max_attempts=0,
        max_seconds=60,
    )
    updated = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert int(updated["deadline_budget_seconds"]) == 60
    assert float(updated["deadline_epoch"]) > 1030.0
