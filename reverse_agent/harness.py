from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .pipeline import SolveResult, run_pipeline
from .tool_runners import ToolAutomationConfig

LogFn = Callable[[str], None]
SCHEMA_VERSION = 1


@dataclass
class HarnessCase:
    case_id: str
    input_value: str
    expected_flag: str = ""
    analysis_mode: str | None = None
    runtime_validation_enabled: bool | None = None
    category: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class HarnessConfig:
    cases: list[HarnessCase]
    reports_dir: Path
    run_name: str = ""
    dataset_path: str = ""
    analysis_mode: str = "Auto"
    model_type: str = "Copilot CLI"
    copilot_command: str = 'gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s'
    local_base_url: str = "http://127.0.0.1:11434"
    local_model: str = "qwen2.5-coder:7b"
    local_api_key: str = ""
    tool_config: ToolAutomationConfig = field(default_factory=ToolAutomationConfig)
    runtime_validation_enabled: bool = False
    copilot_timeout_seconds: int = 300
    ctf_skill_enabled: bool = True
    ctf_skill_profile: str = "compact"
    resume: bool = True
    fail_fast: bool = False


@dataclass
class HarnessCaseResult:
    case_id: str
    input_value: str
    expected_flag: str
    selected_flag: str
    matched_expected: bool | None
    status: str
    elapsed_seconds: float
    analysis_mode: str
    report_path: str
    resolved_path: str
    model_name: str
    candidate_count: int
    extracted_strings_count: int
    tool_artifact_count: int
    structured_evidence_count: int
    validation_count: int
    profile_name: str = ""
    matched_profiles: list[str] = field(default_factory=list)
    applied_strategies: list[str] = field(default_factory=list)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    error: str = ""
    traceback_text: str = ""
    cached: bool = False


@dataclass
class HarnessSummary:
    run_name: str
    run_dir: str
    total_cases: int
    executed_cases: int
    resumed_cases: int
    passed_cases: int
    failed_cases: int
    completed_without_expected: int
    error_cases: int
    not_found_cases: int
    labeled_cases: int
    accuracy_when_labeled: float | None
    evidence_coverage: float | None
    candidate_quality: float | None
    solve_rate_by_category: dict[str, float]
    elapsed_seconds: float
    manifest_path: str
    summary_path: str
    case_result_paths: list[str]


def load_harness_cases(dataset_path: Path) -> list[HarnessCase]:
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    items = raw.get("cases", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Harness dataset must be a JSON list or an object with a 'cases' list.")

    cases: list[HarnessCase] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Case #{idx} is not a JSON object.")
        case_id = str(item.get("case_id") or item.get("id") or f"case-{idx:03d}").strip()
        input_value = str(item.get("input_value") or item.get("input") or "").strip()
        if not case_id:
            raise ValueError(f"Case #{idx} is missing 'case_id'.")
        if not input_value:
            raise ValueError(f"Case '{case_id}' is missing 'input_value'.")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError(f"Case '{case_id}' has non-list 'tags'.")
        cases.append(
            HarnessCase(
                case_id=case_id,
                input_value=input_value,
                expected_flag=str(item.get("expected_flag") or item.get("expected") or "").strip(),
                analysis_mode=_optional_str(item.get("analysis_mode")),
                runtime_validation_enabled=_optional_bool(item.get("runtime_validation_enabled")),
                category=str(item.get("category") or "").strip(),
                tags=[str(tag) for tag in tags],
                notes=str(item.get("notes") or "").strip(),
            )
        )
    return cases


def filter_harness_cases(
    cases: list[HarnessCase],
    case_ids: list[str] | None = None,
    tags: list[str] | None = None,
    limit: int | None = None,
) -> list[HarnessCase]:
    selected = cases
    if case_ids:
        wanted = {item.strip() for item in case_ids if item.strip()}
        selected = [case for case in selected if case.case_id in wanted]
    if tags:
        wanted_tags = {item.strip() for item in tags if item.strip()}
        selected = [
            case
            for case in selected
            if wanted_tags.intersection(case.tags)
        ]
    if limit is not None:
        selected = selected[: max(0, limit)]
    return selected


def run_harness(config: HarnessConfig, log: LogFn) -> HarnessSummary:
    if not config.cases:
        raise ValueError("Harness config contains no cases.")

    run_name = _resolve_run_name(config)
    run_dir = config.reports_dir / "harness_runs" / run_name
    reports_dir = run_dir / "reports"
    case_results_dir = run_dir / "case_results"
    manifest_path = run_dir / "run_manifest.json"
    summary_path = run_dir / "summary.json"
    started_at = _now_iso()
    manifest = _build_manifest(config=config, run_name=run_name, run_dir=run_dir, started_at=started_at)
    if manifest_path.exists():
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_digest = str(existing_manifest.get("config_digest") or "")
        current_digest = str(manifest.get("config_digest") or "")
        if existing_digest and existing_digest != current_digest:
            raise ValueError(
                f"Run '{run_name}' already exists with a different harness config. "
                "Please use a new --run-name."
            )
        if not config.resume:
            raise ValueError(
                f"Run '{run_name}' already exists. Reuse --run-name only with resume enabled."
            )

    reports_dir.mkdir(parents=True, exist_ok=True)
    case_results_dir.mkdir(parents=True, exist_ok=True)
    _write_json(manifest_path, manifest)

    results: list[HarnessCaseResult] = []
    executed_cases = 0
    resumed_cases = 0

    start_ts = datetime.now(timezone.utc)
    summary_payload: HarnessSummary | None = None
    try:
        for index, case in enumerate(config.cases, start=1):
            result_path = case_results_dir / f"{_sanitize_token(case.case_id)}.json"
            if config.resume and result_path.exists():
                cached = _load_case_result(result_path)
                cached.cached = True
                results.append(cached)
                resumed_cases += 1
                log(f"[harness] 跳过已完成样本 {index}/{len(config.cases)}: {case.case_id}")
                continue

            log(f"[harness] 运行样本 {index}/{len(config.cases)}: {case.case_id}")
            executed_cases += 1
            case_start = datetime.now(timezone.utc)
            try:
                solve_result = run_pipeline(
                    input_value=case.input_value,
                    analysis_mode=case.analysis_mode or config.analysis_mode,
                    model_type=config.model_type,
                    copilot_command=config.copilot_command,
                    local_base_url=config.local_base_url,
                    local_model=config.local_model,
                    local_api_key=config.local_api_key,
                    tool_config=config.tool_config,
                    runtime_validation_enabled=(
                        config.runtime_validation_enabled
                        if case.runtime_validation_enabled is None
                        else case.runtime_validation_enabled
                    ),
                    reports_dir=reports_dir,
                    log=lambda message, cid=case.case_id: log(f"[{cid}] {message}"),
                    copilot_timeout_seconds=config.copilot_timeout_seconds,
                    ctf_skill_enabled=config.ctf_skill_enabled,
                    ctf_skill_profile=config.ctf_skill_profile,
                )
                case_result = _case_result_from_solve_result(
                    case=case,
                    solve_result=solve_result,
                    elapsed_seconds=(datetime.now(timezone.utc) - case_start).total_seconds(),
                )
            except Exception as exc:
                case_result = HarnessCaseResult(
                    case_id=case.case_id,
                    input_value=case.input_value,
                    expected_flag=case.expected_flag,
                    selected_flag="",
                    matched_expected=False if case.expected_flag else None,
                    status="error",
                    elapsed_seconds=(datetime.now(timezone.utc) - case_start).total_seconds(),
                    analysis_mode=case.analysis_mode or config.analysis_mode,
                    report_path="",
                    resolved_path="",
                    model_name=config.model_type,
                    candidate_count=0,
                    extracted_strings_count=0,
                    tool_artifact_count=0,
                    structured_evidence_count=0,
                    validation_count=0,
                    category=case.category,
                    tags=case.tags[:],
                    notes=case.notes,
                    error=str(exc),
                    traceback_text=traceback.format_exc(),
                )
                log(f"[harness] 样本失败 {case.case_id}: {exc}")
                _write_json(result_path, asdict(case_result))
                results.append(case_result)
                if config.fail_fast:
                    manifest["status"] = "failed"
                    manifest["failure_case_id"] = case.case_id
                    raise
            else:
                _write_json(result_path, asdict(case_result))
                results.append(case_result)
    except Exception:
        manifest["status"] = "failed"
        manifest["failure_traceback"] = traceback.format_exc()
        raise
    finally:
        elapsed_seconds = (datetime.now(timezone.utc) - start_ts).total_seconds()
        summary_payload = _build_summary(
            run_name=run_name,
            run_dir=run_dir,
            elapsed_seconds=elapsed_seconds,
            executed_cases=executed_cases,
            resumed_cases=resumed_cases,
            manifest_path=manifest_path,
            summary_path=summary_path,
            results=results,
        )
        _write_json(summary_path, asdict(summary_payload))
        _write_summary_markdown(run_dir / "summary.md", summary_payload, results)
        manifest["completed_at"] = _now_iso()
        manifest["summary_path"] = str(summary_path)
        manifest["summary_digest"] = _sha256_json(asdict(summary_payload))
        if manifest.get("status") == "running":
            manifest["status"] = "completed"
        _write_json(manifest_path, manifest)

    return summary_payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run reverse-agent as a reproducible harness.")
    parser.add_argument("--dataset", required=True, help="Path to a JSON dataset file.")
    parser.add_argument("--run-name", default="", help="Stable run name. Reuse it to resume.")
    parser.add_argument("--reports-dir", default="solve_reports", help="Reports root directory.")
    parser.add_argument(
        "--analysis-mode",
        default="Auto",
        choices=["Auto", "Static Analysis", "Dynamic Debug"],
        help="Default analysis mode for cases.",
    )
    parser.add_argument(
        "--model-type",
        default="Copilot CLI",
        choices=["Copilot CLI", "Local Model"],
        help="Pipeline model backend.",
    )
    parser.add_argument("--copilot-command", default='gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s')
    parser.add_argument("--copilot-timeout-seconds", type=int, default=300)
    parser.add_argument("--local-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--local-model", default="qwen2.5-coder:7b")
    parser.add_argument("--local-api-key", default="")
    parser.add_argument("--runtime-validation-enabled", action="store_true")
    parser.add_argument("--tool-enabled", action="store_true")
    parser.add_argument("--ida-enabled", action="store_true")
    parser.add_argument("--ida-executable", default="")
    parser.add_argument("--ida-script-path", default="")
    parser.add_argument("--ida-timeout-seconds", type=int, default=180)
    parser.add_argument("--olly-enabled", action="store_true")
    parser.add_argument("--olly-executable", default="")
    parser.add_argument("--olly-script-path", default="")
    parser.add_argument("--olly-timeout-seconds", type=int, default=120)
    parser.add_argument("--ctf-skill-profile", default="compact", choices=["compact", "full"])
    parser.add_argument("--disable-ctf-skill", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--case-id", action="append", default=[], help="Run only selected case ids.")
    parser.add_argument("--tag", action="append", default=[], help="Run only cases matching at least one tag.")
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N selected cases.")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    cases = load_harness_cases(dataset_path)
    cases = filter_harness_cases(cases, case_ids=args.case_id, tags=args.tag, limit=args.limit)
    if not cases:
        raise SystemExit("No cases selected for the harness run.")

    config = HarnessConfig(
        cases=cases,
        reports_dir=Path(args.reports_dir),
        run_name=args.run_name,
        dataset_path=str(dataset_path),
        analysis_mode=args.analysis_mode,
        model_type=args.model_type,
        copilot_command=args.copilot_command,
        local_base_url=args.local_base_url,
        local_model=args.local_model,
        local_api_key=args.local_api_key,
        tool_config=ToolAutomationConfig(
            enabled=args.tool_enabled,
            ida_enabled=args.ida_enabled,
            ida_executable=args.ida_executable,
            ida_script_path=args.ida_script_path,
            ida_timeout_seconds=args.ida_timeout_seconds,
            ollydbg_enabled=args.olly_enabled,
            ollydbg_executable=args.olly_executable,
            ollydbg_script_path=args.olly_script_path,
            ollydbg_timeout_seconds=args.olly_timeout_seconds,
        ),
        runtime_validation_enabled=args.runtime_validation_enabled,
        copilot_timeout_seconds=args.copilot_timeout_seconds,
        ctf_skill_enabled=not args.disable_ctf_skill,
        ctf_skill_profile=args.ctf_skill_profile,
        resume=not args.no_resume,
        fail_fast=args.fail_fast,
    )

    summary = run_harness(config, log=_safe_console_log)
    _safe_console_log(
        "[harness] completed "
        f"total={summary.total_cases} executed={summary.executed_cases} resumed={summary.resumed_cases} "
        f"passed={summary.passed_cases} failed={summary.failed_cases} errors={summary.error_cases} "
        f"accuracy={summary.accuracy_when_labeled}"
    )
    _safe_console_log(f"[harness] summary: {summary.summary_path}")
    return 0


def _safe_console_log(message: object) -> None:
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        sys.stdout.write(safe_text + "\n")


def _case_result_from_solve_result(
    case: HarnessCase,
    solve_result: SolveResult,
    elapsed_seconds: float,
) -> HarnessCaseResult:
    selected_flag = solve_result.selected_flag or ""
    expected_flag = case.expected_flag.strip()
    matched_expected: bool | None
    if expected_flag:
        matched_expected = selected_flag == expected_flag
        status = "passed" if matched_expected else "failed_expected"
    else:
        matched_expected = None
        status = "completed_no_expected"

    if selected_flag == "NOT_FOUND":
        status = "not_found" if not expected_flag else "failed_expected"

    return HarnessCaseResult(
        case_id=case.case_id,
        input_value=case.input_value,
        expected_flag=expected_flag,
        selected_flag=selected_flag,
        matched_expected=matched_expected,
        status=status,
        elapsed_seconds=elapsed_seconds,
        analysis_mode=solve_result.analysis_mode,
        report_path=solve_result.report_path,
        resolved_path=solve_result.resolved_path,
        model_name=solve_result.model_name,
        candidate_count=len(solve_result.candidates),
        extracted_strings_count=solve_result.extracted_strings_count,
        tool_artifact_count=len(solve_result.tool_artifacts),
        structured_evidence_count=len(solve_result.structured_evidence),
        validation_count=len(solve_result.candidate_validations),
        profile_name=solve_result.active_profile,
        matched_profiles=solve_result.matched_profiles[:],
        applied_strategies=solve_result.applied_strategies[:],
        category=case.category,
        tags=case.tags[:],
        notes=case.notes,
    )


def _build_manifest(
    config: HarnessConfig,
    run_name: str,
    run_dir: Path,
    started_at: str,
) -> dict[str, object]:
    config_payload = {
        "dataset_path": config.dataset_path,
        "analysis_mode": config.analysis_mode,
        "model_type": config.model_type,
        "copilot_command": config.copilot_command,
        "local_base_url": config.local_base_url,
        "local_model": config.local_model,
        "runtime_validation_enabled": config.runtime_validation_enabled,
        "copilot_timeout_seconds": config.copilot_timeout_seconds,
        "ctf_skill_enabled": config.ctf_skill_enabled,
        "ctf_skill_profile": config.ctf_skill_profile,
        "resume": config.resume,
        "fail_fast": config.fail_fast,
        "tool_config": asdict(config.tool_config),
        "cases": [asdict(case) for case in config.cases],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "run_name": run_name,
        "run_dir": str(run_dir),
        "started_at": started_at,
        "dataset_digest": _sha256_json(config_payload["cases"]),
        "config_digest": _sha256_json(config_payload),
        "git_commit": _git_commit(),
        "pipeline_defaults": config_payload,
        "case_ids": [case.case_id for case in config.cases],
    }


def _build_summary(
    run_name: str,
    run_dir: Path,
    elapsed_seconds: float,
    executed_cases: int,
    resumed_cases: int,
    manifest_path: Path,
    summary_path: Path,
    results: list[HarnessCaseResult],
) -> HarnessSummary:
    passed_cases = sum(1 for item in results if item.status == "passed")
    failed_cases = sum(1 for item in results if item.status == "failed_expected")
    completed_without_expected = sum(1 for item in results if item.status == "completed_no_expected")
    error_cases = sum(1 for item in results if item.status == "error")
    not_found_cases = sum(1 for item in results if item.selected_flag == "NOT_FOUND")
    labeled_cases = sum(1 for item in results if item.expected_flag)
    accuracy = (passed_cases / labeled_cases) if labeled_cases else None
    evidence_coverage = (
        sum(1 for item in results if item.structured_evidence_count > 0) / len(results)
        if results
        else None
    )
    candidate_quality = (
        sum(1 for item in results if item.candidate_count > 0 and item.selected_flag != "NOT_FOUND") / len(results)
        if results
        else None
    )
    category_counts: dict[str, dict[str, int]] = {}
    for item in results:
        category = item.category or "uncategorized"
        bucket = category_counts.setdefault(category, {"total": 0, "solved": 0})
        bucket["total"] += 1
        if item.selected_flag and item.selected_flag != "NOT_FOUND":
            bucket["solved"] += 1
    return HarnessSummary(
        run_name=run_name,
        run_dir=str(run_dir),
        total_cases=len(results),
        executed_cases=executed_cases,
        resumed_cases=resumed_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        completed_without_expected=completed_without_expected,
        error_cases=error_cases,
        not_found_cases=not_found_cases,
        labeled_cases=labeled_cases,
        accuracy_when_labeled=accuracy,
        evidence_coverage=evidence_coverage,
        candidate_quality=candidate_quality,
        solve_rate_by_category={
            key: (value["solved"] / value["total"]) if value["total"] else 0.0
            for key, value in sorted(category_counts.items())
        },
        elapsed_seconds=elapsed_seconds,
        manifest_path=str(manifest_path),
        summary_path=str(summary_path),
        case_result_paths=[str(Path(run_dir) / "case_results" / f"{_sanitize_token(item.case_id)}.json") for item in results],
    )


def _write_summary_markdown(
    path: Path,
    summary: HarnessSummary,
    results: list[HarnessCaseResult],
) -> None:
    category_lines = [
        f"| `{category}` | {rate:.2f} |"
        for category, rate in summary.solve_rate_by_category.items()
    ] or ["| `uncategorized` | 0.00 |"]
    lines = [
        "# Reverse Agent Harness Summary",
        "",
        f"- Run: `{summary.run_name}`",
        f"- Total cases: `{summary.total_cases}`",
        f"- Executed now: `{summary.executed_cases}`",
        f"- Resumed from cache: `{summary.resumed_cases}`",
        f"- Passed: `{summary.passed_cases}`",
        f"- Failed: `{summary.failed_cases}`",
        f"- Errors: `{summary.error_cases}`",
        f"- Not found: `{summary.not_found_cases}`",
        f"- Accuracy (labeled only): `{summary.accuracy_when_labeled}`",
        f"- Evidence coverage: `{summary.evidence_coverage}`",
        f"- Candidate quality: `{summary.candidate_quality}`",
        "",
        "## Solve Rate By Category",
        "",
        "| category | solve_rate |",
        "|---|---:|",
        *category_lines,
        "",
        "| case_id | category | status | profile | selected | expected | elapsed_s | cached | report |",
        "|---|---|---|---|---|---|---:|---|---|",
    ]
    for item in results:
        report = Path(item.report_path).name if item.report_path else "-"
        lines.append(
            f"| `{item.case_id}` | `{item.category or '-'}` | {item.status} | `{item.profile_name or '-'}` | `{item.selected_flag or '-'}` | "
            f"`{item.expected_flag or '-'}` | {item.elapsed_seconds:.2f} | "
            f"{'yes' if item.cached else 'no'} | `{report}` |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_case_result(path: Path) -> HarnessCaseResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    return HarnessCaseResult(**data)


def _resolve_run_name(config: HarnessConfig) -> str:
    if config.run_name.strip():
        return _sanitize_token(config.run_name)
    stem = Path(config.dataset_path).stem if config.dataset_path else "manual"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return _sanitize_token(f"{stem}_{ts}")


def _sanitize_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return token.strip("._") or "run"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _sha256_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _git_commit() -> str:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
