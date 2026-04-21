from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..evidence import StructuredEvidence
from ..tool_runners import ToolRunArtifact
from ..transforms.samplereverse import SamplereverseTransformModel, score_compare_prefix
from .base import SolverStrategy, StrategyResult

RESULT_FILE_NAME = "samplereverse_compare_aware_result.json"
RESULT_LOG_FILE_NAME = "samplereverse_compare_aware.log"
VALIDATION_FILE_NAME = "samplereverse_compare_aware_validation.json"
BASELINE_SUMMARY_FILE_NAME = "samplereverse_compare_aware_baseline_summary.json"
DEFAULT_ANCHORS = ("4a78f0eaeb4f13b0", "e05e579fca169e80")
DEFAULT_FIXED_SUFFIX_HEX = "41414141414141"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _source_path() -> Path:
    return _repo_root() / "tools" / "samplereverse_exact4_refine_prefix8_len15.c"


def _binary_path() -> Path:
    return _repo_root() / "tools" / "samplereverse_exact4_refine_prefix8_len15.exe"


def _compare_probe_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "olly_scripts" / "compare_probe.py"


def _candidate_text_from_hex(candidate_hex: str) -> str:
    return bytes.fromhex(candidate_hex).decode("latin1")


def _candidate_hex_from_entry(entry: dict[str, object]) -> str:
    candidate_hex = str(entry.get("candidate_hex", "")).strip().lower()
    cand8_hex = str(entry.get("cand8_hex", "")).strip().lower()
    if candidate_hex:
        return candidate_hex
    if len(cand8_hex) == 16:
        return f"{cand8_hex}{DEFAULT_FIXED_SUFFIX_HEX}"
    return ""


def _entry_metrics(entry: dict[str, object], transform_model: SamplereverseTransformModel) -> dict[str, int | str]:
    raw_prefix_hex = str(entry.get("raw_prefix_hex", "")).strip().lower()
    if raw_prefix_hex:
        try:
            metrics = transform_model.score_prefix(bytes.fromhex(raw_prefix_hex))
        except Exception:
            metrics = {}
    else:
        metrics = {}
    return {
        "ci_exact_wchars": int(metrics.get("ci_exact_wchars", entry.get("ci_exact_wchars", 0)) or 0),
        "ci_distance5": int(metrics.get("ci_distance5", entry.get("ci_distance5", 1 << 30)) or (1 << 30)),
        "raw_distance10": int(metrics.get("raw_distance10", entry.get("raw_distance10", 1 << 30)) or (1 << 30)),
        "raw_prefix_hex": str(metrics.get("raw_prefix_hex", raw_prefix_hex)),
    }


def _sort_key(entry: dict[str, object], transform_model: SamplereverseTransformModel) -> tuple[int, int, int, str]:
    metrics = _entry_metrics(entry, transform_model)
    return (
        int(metrics["ci_exact_wchars"]),
        -int(metrics["ci_distance5"]),
        -int(metrics["raw_distance10"]),
        _candidate_hex_from_entry(entry),
    )


def _collect_top_entries(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
    limit: int = 32,
) -> list[dict[str, object]]:
    entries = payload.get("top_entries", [])
    if not isinstance(entries, list):
        best = payload.get("best", {})
        entries = [best] if isinstance(best, dict) and best else []
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen:
            continue
        seen.add(candidate_hex)
        metrics = _entry_metrics(entry, transform_model)
        out.append({**entry, **metrics, "candidate_hex": candidate_hex})
    out.sort(key=lambda item: _sort_key(item, transform_model), reverse=True)
    return out[:limit]


def _collect_validation_entries(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
    validate_top: int,
) -> list[dict[str, object]]:
    ranked = _collect_top_entries(payload, transform_model, limit=64)
    if not ranked:
        return []
    qualified = [item for item in ranked if int(item.get("ci_exact_wchars", 0)) >= 2]
    if qualified:
        best_distance = min(int(item.get("ci_distance5", 1 << 30)) for item in qualified)
    else:
        best_distance = min(int(item.get("ci_distance5", 1 << 30)) for item in ranked)
    out: list[dict[str, object]] = []
    for idx, entry in enumerate(ranked, 1):
        if int(entry.get("ci_exact_wchars", 0)) < 2:
            continue
        if int(entry.get("ci_distance5", 1 << 30)) > best_distance:
            continue
        out.append({"label": f"top{idx}", **entry})
        if len(out) >= validate_top:
            break
    return out


def compile_compare_aware_refine(log) -> Path:
    source_path = _source_path()
    binary_path = _binary_path()
    gcc_path = shutil.which("gcc")
    if not source_path.exists():
        raise RuntimeError(f"compare-aware refine source missing: {source_path}")
    if not gcc_path:
        raise RuntimeError("gcc not found in PATH")
    if binary_path.exists() and binary_path.stat().st_mtime >= source_path.stat().st_mtime:
        return binary_path
    log(f"编译 compare-aware refine 工具: {binary_path.name}")
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


def run_compare_aware_refine(
    *,
    artifacts_dir: Path,
    search_budget: int,
    seed: int,
    anchors: list[str],
    snapshot_interval: int,
    log,
) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    binary_path = compile_compare_aware_refine(log)
    out_path = artifacts_dir / RESULT_FILE_NAME
    log_path = artifacts_dir / RESULT_LOG_FILE_NAME
    command = [
        str(binary_path),
        "--out-json",
        str(out_path),
        "--max-evals",
        str(search_budget),
        "--seed",
        str(seed),
        "--snapshot-interval",
        str(snapshot_interval),
    ]
    for anchor in anchors:
        command.extend(["--anchor", anchor])
    log(f"运行 compare-aware refine: budget={search_budget} seed={seed}")
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
        raise RuntimeError((proc.stderr or proc.stdout or "compare-aware refine failed").strip())
    if not out_path.exists():
        raise RuntimeError(f"compare-aware refine did not produce result json: {out_path}")
    return out_path


def validate_compare_aware_results(
    *,
    target: Path,
    artifacts_dir: Path,
    result_path: Path,
    transform_model: SamplereverseTransformModel,
    validate_top: int,
    per_probe_timeout: float,
    log,
) -> tuple[Path, list[dict[str, object]]]:
    compare_probe_script = _compare_probe_script_path()
    if not compare_probe_script.exists():
        raise RuntimeError(f"compare probe script missing: {compare_probe_script}")

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    validation_entries = _collect_validation_entries(payload, transform_model, validate_top)
    summary: dict[str, object] = {
        "target": str(target),
        "result_path": str(result_path),
        "validation_gate": {
            "min_ci_exact_wchars": 2,
            "max_ci_distance5": min(
                (int(item.get("ci_distance5", 1 << 30)) for item in validation_entries),
                default=1 << 30,
            ),
        },
        "validations": [],
    }
    for idx, entry in enumerate(validation_entries, 1):
        candidate_hex = str(entry["candidate_hex"])
        compare_out = artifacts_dir / f"samplereverse_compare_aware_compare_{idx}.json"
        compare_log = artifacts_dir / f"samplereverse_compare_aware_compare_{idx}.log"
        offline_ci_exact_wchars = int(entry.get("ci_exact_wchars", 0))
        offline_ci_distance5 = int(entry.get("ci_distance5", 1 << 30))
        raw_prefix_hex = str(entry.get("raw_prefix_hex", "")).strip().lower()
        command = [
            sys.executable,
            str(compare_probe_script),
            "--target",
            str(target),
            "--out",
            str(compare_out),
            "--probe-hex",
            candidate_hex,
            "--offline-ci-exact-wchars",
            str(offline_ci_exact_wchars),
            "--offline-ci-distance5",
            str(offline_ci_distance5),
            "--offline-raw-prefix-hex",
            raw_prefix_hex,
            "--per-probe-timeout",
            str(per_probe_timeout),
        ]
        log(f"CompareProbe 回归 compare-aware 候选 {idx}: {candidate_hex}")
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
            "label": entry.get("label", f"top{idx}"),
            "candidate_hex": candidate_hex,
            "cand8_hex": str(entry.get("cand8_hex", "")),
            "offline_ci_exact_wchars": offline_ci_exact_wchars,
            "offline_ci_distance5": offline_ci_distance5,
            "offline_raw_prefix_hex": raw_prefix_hex,
            "compare_result_path": str(compare_out),
            "compare_log_path": str(compare_log),
        }
        if compare_out.exists():
            compare_payload = json.loads(compare_out.read_text(encoding="utf-8"))
            runtime_lhs_prefix_hex_10 = str(
                compare_payload.get("runtime_lhs_prefix_hex_10")
                or compare_payload.get("lhs_wide_hex", "")
            )[:20].lower()
            runtime_ci_exact_wchars = int(
                compare_payload.get("runtime_ci_exact_wchars")
                or score_compare_prefix(bytes.fromhex(runtime_lhs_prefix_hex_10)).get("ci_exact_wchars", 0)
            )
            runtime_ci_distance5 = int(
                compare_payload.get("runtime_ci_distance5")
                or score_compare_prefix(bytes.fromhex(runtime_lhs_prefix_hex_10)).get("ci_distance5", 1 << 30)
            )
            compare_semantics_agree = bool(
                compare_payload.get("compare_semantics_agree")
                if compare_payload.get("compare_semantics_agree") is not None
                else runtime_lhs_prefix_hex_10 == raw_prefix_hex
            )
            record.update(
                {
                    "compare_summary": compare_payload.get("summary", ""),
                    "runtime_lhs_prefix_hex_10": runtime_lhs_prefix_hex_10,
                    "runtime_ci_exact_wchars": runtime_ci_exact_wchars,
                    "runtime_ci_distance5": runtime_ci_distance5,
                    "compare_semantics_agree": compare_semantics_agree,
                    "matched_target_prefix": runtime_ci_exact_wchars >= 5,
                }
            )
        else:
            record.update(
                {
                    "compare_summary": f"compare probe failed with exit code {proc.returncode}",
                    "runtime_lhs_prefix_hex_10": "",
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 1 << 30,
                    "compare_semantics_agree": False,
                    "matched_target_prefix": False,
                }
            )
        summary["validations"].append(record)

    validation_path = artifacts_dir / VALIDATION_FILE_NAME
    validation_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return validation_path, list(summary["validations"])


def run_compare_aware_baselines(
    *,
    target: Path,
    artifacts_dir: Path,
    search_budget: int,
    seeds: list[int],
    anchors: list[str],
    snapshot_interval: int,
    validate_top: int,
    per_probe_timeout: float,
    log,
) -> Path:
    transform_model = SamplereverseTransformModel()
    runs: list[dict[str, object]] = []
    unique_prefixes: set[str] = set()
    ci_thresholds = {"ci_exact_wchars_3": None, "ci_exact_wchars_4": None, "ci_exact_wchars_5": None}
    for seed in seeds:
        run_dir = artifacts_dir / f"seed_{seed}"
        result_path = run_compare_aware_refine(
            artifacts_dir=run_dir,
            search_budget=search_budget,
            seed=seed,
            anchors=anchors,
            snapshot_interval=snapshot_interval,
            log=log,
        )
        validation_path, validations = validate_compare_aware_results(
            target=target,
            artifacts_dir=run_dir,
            result_path=result_path,
            transform_model=transform_model,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        top_entries = _collect_top_entries(result_payload, transform_model, limit=5)
        for entry in top_entries:
            raw_prefix_hex = str(entry.get("raw_prefix_hex", "")).strip().lower()
            if raw_prefix_hex:
                unique_prefixes.add(raw_prefix_hex)
        for threshold in (3, 4, 5):
            if ci_thresholds[f"ci_exact_wchars_{threshold}"] is None and any(
                int(item.get("runtime_ci_exact_wchars", 0)) >= threshold for item in validations
            ):
                ci_thresholds[f"ci_exact_wchars_{threshold}"] = seed
        runs.append(
            {
                "seed": seed,
                "result_path": str(result_path),
                "validation_path": str(validation_path),
                "best": result_payload.get("best", {}),
                "top_entries": top_entries,
            }
        )
    summary = {
        "target": str(target),
        "search_budget": search_budget,
        "anchors": anchors,
        "seeds": seeds,
        "runs": runs,
        "unique_raw_prefix_hex": sorted(unique_prefixes),
        "milestones": ci_thresholds,
    }
    summary_path = artifacts_dir / BASELINE_SUMMARY_FILE_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


class CompareAwareSearchStrategy(SolverStrategy):
    name = "CompareAwareSearchStrategy"

    def preconditions(self, **kwargs: Any) -> bool:
        file_path = kwargs.get("file_path")
        transform_model = kwargs.get("transform_model")
        return isinstance(file_path, Path) and isinstance(transform_model, SamplereverseTransformModel)

    def estimate_cost(self, **kwargs: Any) -> float:
        return float(kwargs.get("search_budget", 200_000_000))

    def run(self, **kwargs: Any) -> StrategyResult:
        file_path = Path(kwargs["file_path"])
        artifacts_dir = Path(kwargs["artifacts_dir"])
        log = kwargs["log"]
        transform_model = kwargs.get("transform_model") or SamplereverseTransformModel()
        anchors = [str(item).strip().lower() for item in kwargs.get("anchors", DEFAULT_ANCHORS) if str(item).strip()]
        search_budget = max(1, int(kwargs.get("search_budget", 200_000_000)))
        seed = max(1, int(kwargs.get("seed", 20260420)))
        snapshot_interval = max(1, int(kwargs.get("snapshot_interval", 10_000_000)))
        validate_top = max(1, int(kwargs.get("validate_top", 5)))
        per_probe_timeout = max(0.5, float(kwargs.get("per_probe_timeout", 2.0)))

        result_path = run_compare_aware_refine(
            artifacts_dir=artifacts_dir,
            search_budget=search_budget,
            seed=seed,
            anchors=anchors,
            snapshot_interval=snapshot_interval,
            log=log,
        )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        best_entry = payload.get("best", {}) if isinstance(payload.get("best"), dict) else {}
        validation_path, validations = validate_compare_aware_results(
            target=file_path,
            artifacts_dir=artifacts_dir,
            result_path=result_path,
            transform_model=transform_model,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        top_entries = _collect_top_entries(payload, transform_model, limit=32)
        search_artifact = ToolRunArtifact(
            tool_name="CompareAwareRefine",
            enabled=True,
            attempted=True,
            success=True,
            summary=(
                f"compare-aware refine complete: best ci_exact_wchars={best_entry.get('ci_exact_wchars', 0)} "
                f"ci_distance5={best_entry.get('ci_distance5', 1 << 30)}"
            ),
            output_path=str(result_path),
            strategy_name=self.name,
        )
        search_artifact.structured_evidence.append(
            StructuredEvidence(
                kind="TransformEvidence",
                source_tool="CompareAwareRefine",
                summary="samplereverse compare-aware refine search",
                confidence=0.92,
                payload={
                    "anchors": anchors,
                    "search_budget": search_budget,
                    "snapshot_interval": snapshot_interval,
                    "transform": transform_model.describe(),
                    "top_entries": top_entries[:8],
                },
                derived_candidates=[],
            )
        )
        validation_artifact = ToolRunArtifact(
            tool_name="CompareAwareValidation",
            enabled=True,
            attempted=True,
            success=True,
            summary="compare-aware validation complete",
            output_path=str(validation_path),
            strategy_name=self.name,
            evidence=[
                f"runtime_compare:validation_candidate={item.get('candidate_hex', '')}"
                for item in validations[:5]
            ],
        )
        for item in validations[:5]:
            validation_artifact.structured_evidence.append(
                StructuredEvidence(
                    kind="RuntimeCompareEvidence",
                    source_tool="CompareAwareValidation",
                    summary=str(item.get("compare_summary", "")).strip() or "compare-aware validation",
                    confidence=0.96 if item.get("compare_semantics_agree") else 0.65,
                    payload=item,
                    derived_candidates=[
                        _candidate_text_from_hex(str(item["candidate_hex"]))
                    ]
                    if item.get("runtime_ci_exact_wchars", 0) >= 5 and item.get("compare_semantics_agree")
                    else [],
                )
            )
        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for item in validations:
            if item.get("runtime_ci_exact_wchars", 0) < 5 or not item.get("compare_semantics_agree"):
                continue
            candidate_text = _candidate_text_from_hex(str(item["candidate_hex"]))
            if candidate_text in seen_candidates:
                continue
            seen_candidates.add(candidate_text)
            candidates.append(candidate_text)
        return StrategyResult(
            strategy_name=self.name,
            summary=search_artifact.summary,
            candidates=candidates,
            artifacts=[search_artifact, validation_artifact],
            metadata={
                "result_path": str(result_path),
                "validation_path": str(validation_path),
                "best": best_entry,
                "top_entries": top_entries,
                "validations": validations,
            },
        )
