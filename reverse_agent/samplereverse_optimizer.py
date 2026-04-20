from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]

OPTIMIZER_RESULT_FILE_NAME = "samplereverse_optimize_result.json"
OPTIMIZER_LOG_FILE_NAME = "samplereverse_optimize.log"
OPTIMIZER_VALIDATION_FILE_NAME = "samplereverse_optimize_validation.json"
OPTIMIZER_BASELINE_SUMMARY_FILE_NAME = "samplereverse_optimize_baseline_summary.json"
TARGET_COMPARE_PREFIX_HEX = "66006c00610067007b00"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _optimizer_source_path() -> Path:
    return _repo_root() / "tools" / "samplereverse_optimize.c"


def _optimizer_binary_path() -> Path:
    return _repo_root() / "tools" / "samplereverse_optimize.exe"


def _compare_probe_script_path() -> Path:
    return Path(__file__).resolve().parent / "olly_scripts" / "compare_probe.py"


def _gui_markers() -> tuple[list[str], list[str]]:
    success_markers = [
        "correct!",
        "correct",
        "success",
        "flag :",
        "请输入的密钥是",
    ]
    fail_markers = [
        "密钥不正确",
        "incorrect",
        "wrong",
        "failed",
        "error",
    ]
    return success_markers, fail_markers


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _candidate_hex_from_entry(entry: dict[str, object]) -> str:
    cand7_hex = str(entry.get("cand7_hex", "")).strip().lower()
    return f"{cand7_hex}414141414141" if len(cand7_hex) == 14 else ""


def _best_distance4(
    payload: dict[str, object],
    min_exact_prefix_len: int = 0,
) -> int:
    best = 1 << 30
    for key in ("best_prefix", "best_dist4", "best_dist6", "best_dist10"):
        entry = payload.get(key)
        if isinstance(entry, dict):
            try:
                if int(entry.get("exact_prefix_len", 0)) < min_exact_prefix_len:
                    continue
                best = min(best, int(entry.get("distance4", best)))
            except Exception:
                continue
    return best if best < (1 << 30) else 1 << 30


def load_optimizer_seed_candidates(
    result_path: Path,
    limit: int = 32,
) -> list[str]:
    if not result_path.exists():
        return []
    try:
        payload = _load_json(result_path)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _push(entry: object) -> None:
        if len(out) >= limit or not isinstance(entry, dict):
            return
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen:
            return
        try:
            candidate = bytes.fromhex(candidate_hex).decode("latin1")
        except Exception:
            return
        seen.add(candidate_hex)
        out.append(candidate)

    for key in ("best_prefix", "best_dist4", "best_dist6", "best_dist10"):
        _push(payload.get(key))
    elite_prefixes = payload.get("elite_prefixes", [])
    if isinstance(elite_prefixes, list):
        for entry in elite_prefixes:
            _push(entry)
            if len(out) >= limit:
                break
    return out


def _collect_validation_entries(
    result_payload: dict[str, object],
    validate_top: int,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    max_distance4 = _best_distance4(result_payload, min_exact_prefix_len=3)
    if max_distance4 >= (1 << 30):
        max_distance4 = _best_distance4(result_payload)

    def _push(label: str, entry: object) -> None:
        if len(out) >= validate_top or not isinstance(entry, dict):
            return
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen:
            return
        try:
            exact_prefix_len = int(entry.get("exact_prefix_len", 0))
            distance4 = int(entry.get("distance4", 1 << 30))
        except Exception:
            return
        if exact_prefix_len < 3 or distance4 > max_distance4:
            return
        seen.add(candidate_hex)
        out.append({"label": label, **entry, "candidate_hex": candidate_hex})

    _push("prefix", result_payload.get("best_prefix"))
    _push("dist4", result_payload.get("best_dist4"))
    _push("dist6", result_payload.get("best_dist6", result_payload.get("best_dist10")))
    elite_prefixes = result_payload.get("elite_prefixes", [])
    if isinstance(elite_prefixes, list):
        for idx, entry in enumerate(elite_prefixes, 1):
            _push(f"elite{idx}", entry)
            if len(out) >= validate_top:
                break
    return out


def compile_optimizer(log: LogFn) -> Path:
    source_path = _optimizer_source_path()
    binary_path = _optimizer_binary_path()
    gcc_path = shutil.which("gcc")
    if not source_path.exists():
        raise RuntimeError(f"optimizer source missing: {source_path}")
    if not gcc_path:
        raise RuntimeError("gcc not found in PATH")
    if binary_path.exists() and binary_path.stat().st_mtime >= source_path.stat().st_mtime:
        return binary_path
    log(f"编译 C 优化器: {binary_path.name}")
    proc = subprocess.run(
        [gcc_path, "-O3", str(source_path), "-o", str(binary_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gcc failed").strip())
    return binary_path


def run_optimizer(
    artifacts_dir: Path,
    max_evals: int,
    seed: int,
    log: LogFn,
) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    binary_path = compile_optimizer(log)
    out_path = artifacts_dir / OPTIMIZER_RESULT_FILE_NAME
    log_path = artifacts_dir / OPTIMIZER_LOG_FILE_NAME
    command = [
        str(binary_path),
        "--out-json",
        str(out_path),
        "--max-evals",
        str(max_evals),
        "--seed",
        str(seed),
    ]
    log(f"运行 C 优化器: max_evals={max_evals} seed={seed}")
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.write_text(
        f"[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "optimizer failed").strip())
    if not out_path.exists():
        raise RuntimeError(f"optimizer did not produce result json: {out_path}")
    return out_path


def validate_optimizer_results(
    target: Path,
    artifacts_dir: Path,
    result_path: Path,
    validate_top: int,
    per_probe_timeout: float,
    log: LogFn,
) -> Path:
    from .pipeline import _validate_candidates_with_gui_session
    from .sample_solver import _decrypt_prefix

    result_payload = _load_json(result_path)
    validation_entries = _collect_validation_entries(result_payload, validate_top)
    compare_probe_script = _compare_probe_script_path()
    if not compare_probe_script.exists():
        raise RuntimeError(f"compare probe script missing: {compare_probe_script}")

    summary: dict[str, object] = {
        "target": str(target),
        "result_path": str(result_path),
        "validations": [],
    }
    success_markers, fail_markers = _gui_markers()

    for idx, entry in enumerate(validation_entries, 1):
        candidate_hex = str(entry.get("candidate_hex", ""))
        candidate = bytes.fromhex(candidate_hex).decode("latin1")
        offline_lhs_prefix_hex = _decrypt_prefix(candidate, 10).hex()
        compare_out = artifacts_dir / f"samplereverse_optimize_compare_{idx}_{entry['label']}.json"
        compare_log = artifacts_dir / f"samplereverse_optimize_compare_{idx}_{entry['label']}.log"
        command = [
            sys.executable,
            str(compare_probe_script),
            "--target",
            str(target),
            "--out",
            str(compare_out),
            "--probe-hex",
            candidate_hex,
            "--per-probe-timeout",
            str(per_probe_timeout),
        ]
        log(f"CompareProbe 回归: {entry['label']} {candidate_hex}")
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        compare_log.write_text(
            f"[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}",
            encoding="utf-8",
        )
        record: dict[str, object] = {
            "label": entry["label"],
            "cand7_hex": entry.get("cand7_hex", ""),
            "candidate_hex": candidate_hex,
            "offline_lhs_prefix_hex": offline_lhs_prefix_hex,
            "compare_result_path": str(compare_out),
            "compare_log_path": str(compare_log),
        }
        if compare_out.exists():
            compare_payload = _load_json(compare_out)
            record["compare_summary"] = compare_payload.get("summary", "")
            record["lhs_wide_hex"] = compare_payload.get("lhs_wide_hex", "")
            record["runtime_lhs_prefix_hex_10"] = str(compare_payload.get("lhs_wide_hex", ""))[:20]
            record["prefix_agrees_with_offline"] = (
                record["runtime_lhs_prefix_hex_10"] == offline_lhs_prefix_hex
            )
            record["matched_target_prefix"] = str(compare_payload.get("lhs_wide_hex", "")).startswith(
                TARGET_COMPARE_PREFIX_HEX
            )
        else:
            record["compare_summary"] = f"compare probe failed with exit code {proc.returncode}"
            record["lhs_wide_hex"] = ""
            record["runtime_lhs_prefix_hex_10"] = ""
            record["prefix_agrees_with_offline"] = False
            record["matched_target_prefix"] = False

        if record["matched_target_prefix"] and record["prefix_agrees_with_offline"]:
            try:
                chosen, gui_records = _validate_candidates_with_gui_session(
                    file_path=target,
                    candidates=[candidate],
                    success_markers=success_markers,
                    fail_markers=fail_markers,
                )
                record["gui_selected"] = chosen
                record["gui_records"] = gui_records
            except Exception as exc:
                record["gui_validation_error"] = str(exc)
        summary["validations"].append(record)

    validation_path = artifacts_dir / OPTIMIZER_VALIDATION_FILE_NAME
    validation_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return validation_path


def run_optimizer_baselines(
    target: Path,
    artifacts_dir: Path,
    max_evals: int,
    seeds: list[int],
    validate_top: int,
    per_probe_timeout: float,
    log: LogFn,
) -> Path:
    runs: list[dict[str, object]] = []
    unique_lhs_prefix_hex: set[str] = set()
    first4_patterns: dict[str, int] = {}
    for seed in seeds:
        run_dir = artifacts_dir / f"seed_{seed}"
        result_path = run_optimizer(
            artifacts_dir=run_dir,
            max_evals=max_evals,
            seed=seed,
            log=log,
        )
        validation_path = validate_optimizer_results(
            target=target,
            artifacts_dir=run_dir,
            result_path=result_path,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        result_payload = _load_json(result_path)
        run_record = {
            "seed": seed,
            "result_path": str(result_path),
            "validation_path": str(validation_path),
            "best_prefix": result_payload.get("best_prefix", {}),
            "best_dist4": result_payload.get("best_dist4", {}),
            "best_dist6": result_payload.get("best_dist6", result_payload.get("best_dist10", {})),
        }
        for key in ("best_prefix", "best_dist4", "best_dist6", "best_dist10"):
            entry = result_payload.get(key)
            if isinstance(entry, dict):
                lhs_prefix_hex = str(entry.get("lhs_prefix_hex", "")).strip().lower()
                if lhs_prefix_hex:
                    unique_lhs_prefix_hex.add(lhs_prefix_hex)
                    pattern = lhs_prefix_hex[:8]
                    first4_patterns[pattern] = first4_patterns.get(pattern, 0) + 1
        runs.append(run_record)

    summary = {
        "target": str(target),
        "max_evals": max_evals,
        "seeds": seeds,
        "runs": runs,
        "unique_lhs_prefix_hex": sorted(unique_lhs_prefix_hex),
        "repeated_first4_patterns": dict(sorted(first4_patterns.items(), key=lambda item: (-item[1], item[0]))),
    }
    summary_path = artifacts_dir / OPTIMIZER_BASELINE_SUMMARY_FILE_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run samplereverse C optimizer and compare validation")
    parser.add_argument("--target", required=True, help="Path to samplereverse.exe")
    parser.add_argument("--artifacts-dir", required=True, help="Directory for optimizer artifacts")
    parser.add_argument("--max-evals", type=int, default=3_000_000, help="Maximum optimizer evaluations")
    parser.add_argument("--seed", type=int, default=20260420, help="Deterministic optimizer seed")
    parser.add_argument("--validate-top", type=int, default=5, help="How many candidates to validate with CompareProbe")
    parser.add_argument(
        "--per-probe-timeout",
        type=float,
        default=2.0,
        help="Per-candidate CompareProbe timeout",
    )
    parser.add_argument(
        "--baseline-seeds",
        default="",
        help="Comma-separated seeds for multi-run baseline mode.",
    )
    args = parser.parse_args(argv)

    target = Path(args.target)
    artifacts_dir = Path(args.artifacts_dir)
    if not target.exists():
        raise SystemExit(f"target not found: {target}")

    max_evals = max(1, int(args.max_evals))
    validate_top = max(1, int(args.validate_top))
    per_probe_timeout = max(0.5, float(args.per_probe_timeout))
    baseline_seeds = [
        int(item)
        for item in args.baseline_seeds.split(",")
        if item.strip()
    ]

    if baseline_seeds:
        summary_path = run_optimizer_baselines(
            target=target,
            artifacts_dir=artifacts_dir,
            max_evals=max_evals,
            seeds=baseline_seeds,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=print,
        )
        print(f"baseline summary: {summary_path}")
        return 0

    result_path = run_optimizer(
        artifacts_dir=artifacts_dir,
        max_evals=max_evals,
        seed=max(1, int(args.seed)),
        log=print,
    )
    validation_path = validate_optimizer_results(
        target=target,
        artifacts_dir=artifacts_dir,
        result_path=result_path,
        validate_top=validate_top,
        per_probe_timeout=per_probe_timeout,
        log=print,
    )
    print(f"optimizer result: {result_path}")
    print(f"validation result: {validation_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
