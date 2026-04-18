from pathlib import Path
import json

from reverse_agent.sample_solver import _decrypt_prefix
from reverse_agent.sample_solver import _objective_tuple
from reverse_agent.sample_solver import _prefix_distance
from reverse_agent.sample_solver import CHECKPOINT_FILE_NAME
from reverse_agent.sample_solver import run_samplereverse_resumable_search


def test_decrypt_prefix_known_vector() -> None:
    assert _decrypt_prefix("AAAA", 5).hex() == "243767ace0"


def test_objective_prefers_smaller_distance_on_tie() -> None:
    better = _objective_tuple("666c61677b", 3, 0b10101)
    worse = _objective_tuple("676d62687c", 3, 0b10101)
    assert better > worse
    assert _prefix_distance("666c61677b") < _prefix_distance("676d62687c")


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
