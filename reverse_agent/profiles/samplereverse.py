from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

from ..evidence import StructuredEvidence
from ..probes.compare import artifact_has_compare_truth
from ..probes.gui import collect_gui_runtime_outputs, is_windows_gui_exe, validate_candidates_with_gui_session
from ..sample_solver import CHECKPOINT_FILE_NAME, run_samplereverse_resumable_search
from ..tool_runners import ToolRunArtifact, run_compare_probe
from ..transforms.samplereverse import SamplereverseTransformModel
from .base import ChallengeProfile, ProfileSolveResult, ValidationResult

LogFn = Callable[[str], None]


def _looks_like_samplereverse(file_path: Path, strings: list[str]) -> bool:
    if "samplereverse" in file_path.name.lower():
        return True
    return any("输入的密钥是" in item for item in strings[:3000]) and any(
        "密钥不正确" in item for item in strings[:3000]
    )


def _extract_compare_probe_inputs(tool_evidence: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for line in tool_evidence:
        if not line.startswith("runtime_compare:input="):
            continue
        token = line.split("=", 1)[1].strip().strip("`").strip("'").strip('"')
        if not token or token in seen:
            continue
        seen.add(token)
        candidates.append(token)
    return candidates


def _env_int(name: str, default: int, min_value: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= min_value else default


def _env_float(name: str, default: float, min_value: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= min_value else default


def _pipeline_module():
    return sys.modules.get("reverse_agent.pipeline")


class SamplereverseProfile(ChallengeProfile):
    profile_id = "samplereverse"
    display_name = "Samplereverse"
    category = "gui_compare"

    def transforms(self) -> list[SamplereverseTransformModel]:
        return [SamplereverseTransformModel()]

    def supported_strategies(self) -> list[str]:
        return [
            "CompareAwareSearchStrategy",
            "TransformConstraintStrategy",
            "SMTPartitionStrategy",
            "GuiFeedbackStrategy",
        ]

    def detect(
        self,
        file_path: Path,
        strings: list[str],
        static_evidence: list[str],
    ) -> int:
        score = 0
        if "samplereverse" in file_path.name.lower():
            score += 80
        if _looks_like_samplereverse(file_path, strings):
            score += 80
        if any("runtime_probe:samplereverse_signature=1" in item for item in static_evidence):
            score += 20
        if any("flag{" in item.lower() and "输入的密钥是" in "".join(strings[:3000]) for item in strings[:3000]):
            score += 10
        return score

    def build_seed_candidates(
        self,
        strings: list[str],
        pre_candidates: list[str],
        tool_evidence: list[str],
    ) -> list[str]:
        _ = strings
        ordered = [*_extract_compare_probe_inputs(tool_evidence), *pre_candidates]
        seeds: list[str] = []
        seen: set[str] = set()
        for value in ordered:
            token = str(value).strip()
            if not token or token in seen:
                continue
            seen.add(token)
            seeds.append(token)
        return seeds

    def collect_runtime_probes(
        self,
        file_path: Path,
        strings: list[str],
        artifacts_dir: Path,
        seed_candidates: list[str],
        analysis_mode: str,
        log: LogFn,
    ) -> list[ToolRunArtifact]:
        _ = analysis_mode
        if not _looks_like_samplereverse(file_path, strings):
            return []

        artifacts: list[ToolRunArtifact] = []
        pipeline_mod = _pipeline_module()
        compare_artifact = None
        legacy_compare_probe = getattr(pipeline_mod, "_run_compare_probe_if_needed", None) if pipeline_mod else None
        if callable(legacy_compare_probe):
            compare_artifact = legacy_compare_probe(
                file_path=file_path,
                strings=strings,
                artifacts_dir=artifacts_dir,
                log=log,
            )
        if compare_artifact is None:
            compare_artifact = run_compare_probe(file_path=file_path, artifacts_dir=artifacts_dir, log=log)
        compare_artifact.owner_profile = self.profile_id
        compare_artifact.strategy_name = "CompareAwareSearchStrategy"
        artifacts.append(compare_artifact)

        if artifact_has_compare_truth(compare_artifact):
            if any(item.kind == "RuntimeCompareEvidence" for item in compare_artifact.structured_evidence):
                log("Samplereverse profile: compare 前真值已捕获，本轮跳过额外 GUI runtime sampling。")
            return artifacts

        probe_inputs = ["AAAAAAA", "flag{"]
        for candidate in seed_candidates[:4]:
            token = str(candidate).strip()
            if token and token not in probe_inputs:
                probe_inputs.append(token)
        legacy_gui_probe = getattr(pipeline_mod, "_probe_gui_runtime_outputs", None) if pipeline_mod else None
        if callable(legacy_gui_probe):
            gui_artifact = legacy_gui_probe(
                file_path=file_path,
                strings=strings,
                seed_candidates=probe_inputs[:4],
            )
        else:
            gui_artifact = collect_gui_runtime_outputs(file_path=file_path, probe_inputs=probe_inputs[:4])
        if gui_artifact:
            gui_artifact.owner_profile = self.profile_id
            gui_artifact.strategy_name = "GuiFeedbackStrategy"
            artifacts.append(gui_artifact)
        return artifacts

    def run_specialized_solver(
        self,
        file_path: Path,
        strings: list[str],
        seed_candidates: list[str],
        artifacts_dir: Path,
        log: LogFn,
        prior_artifacts: list[ToolRunArtifact],
    ) -> ProfileSolveResult | None:
        if not _looks_like_samplereverse(file_path, strings):
            return None

        compare_has_truth = any(artifact_has_compare_truth(item) for item in prior_artifacts)
        has_recovered_candidate = any(
            ev.kind == "CandidateEvidence" and ev.derived_candidates
            for item in prior_artifacts
            for ev in item.structured_evidence
        ) or any(
            any(line.startswith("runtime_candidate:") for line in item.evidence)
            for item in prior_artifacts
        )
        if compare_has_truth and has_recovered_candidate:
            return ProfileSolveResult(
                enabled=False,
                summary="Samplereverse profile skipped specialized solver because compare probe already recovered candidates.",
                strategies=["CompareAwareSearchStrategy"],
            )

        runner = run_samplereverse_resumable_search
        pipeline_mod = _pipeline_module()
        legacy_runner = getattr(pipeline_mod, "run_samplereverse_resumable_search", None) if pipeline_mod else None
        if callable(legacy_runner):
            runner = legacy_runner
        result = runner(
            file_path=file_path,
            strings=strings,
            seed_candidates=seed_candidates,
            artifacts_dir=artifacts_dir,
            log=log,
            max_attempts=_env_int("REVERSE_AGENT_SAMPLE_MAX_ATTEMPTS", 250_000, 10_000),
            max_seconds=_env_float("REVERSE_AGENT_SAMPLE_MAX_SECONDS", 6 * 60 * 60, 30.0),
            random_seed=_env_int("REVERSE_AGENT_SAMPLE_RANDOM_SEED", 1337, 1),
        )
        if not result.enabled:
            return ProfileSolveResult(enabled=False, summary=result.summary)

        artifact = ToolRunArtifact(
            tool_name="SampleProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary=result.summary,
            output_path=str(artifacts_dir / CHECKPOINT_FILE_NAME),
            evidence=result.evidence,
            owner_profile=self.profile_id,
            strategy_name="CompareAwareSearchStrategy",
        )
        if result.candidates:
            artifact.structured_evidence.append(
                StructuredEvidence(
                    kind="CandidateEvidence",
                    source_tool="SampleProbe",
                    summary=result.summary,
                    derived_candidates=result.candidates[:16],
                    confidence=0.75,
                    payload={"candidate_count": len(result.candidates)},
                )
            )
        artifact.structured_evidence.append(
            StructuredEvidence(
                kind="TransformEvidence",
                source_tool="SampleProbe",
                summary="samplereverse specialized transform search",
                payload={
                    "transform": self.transforms()[0].describe(),
                    "checkpoint": str(artifacts_dir / CHECKPOINT_FILE_NAME),
                },
                confidence=0.9,
            )
        )
        return ProfileSolveResult(
            enabled=True,
            summary=result.summary,
            candidates=result.candidates,
            artifacts=[artifact],
            evidence=result.evidence,
            strategies=["CompareAwareSearchStrategy", "TransformConstraintStrategy", "SMTPartitionStrategy"],
        )

    def validate_candidate(
        self,
        file_path: Path,
        candidates: list[str],
        success_markers: list[str],
        fail_markers: list[str],
        runtime_validation_enabled: bool,
        log: LogFn,
    ) -> ValidationResult | None:
        if not runtime_validation_enabled or not candidates or not is_windows_gui_exe(file_path):
            return ValidationResult(handled=False)
        log("Samplereverse profile: 使用 profile 绑定的 GUI 会话校验候选。")
        pipeline_mod = _pipeline_module()
        legacy_validator = getattr(pipeline_mod, "_validate_candidates_with_gui_session", None) if pipeline_mod else None
        validator = legacy_validator if callable(legacy_validator) else validate_candidates_with_gui_session
        try:
            chosen, records = validator(
                file_path=file_path,
                candidates=candidates[:60],
                success_markers=success_markers,
                fail_markers=fail_markers,
            )
        except RuntimeError as exc:
            return ValidationResult(
                handled=True,
                selected_flag="",
                records=[
                    {"candidate": cand, "validated": "skipped_gui", "evidence": ""}
                    for cand in candidates[:10]
                ],
                summary=str(exc),
            )
        return ValidationResult(
            handled=True,
            selected_flag=chosen,
            records=records,
            summary="profile_gui_validation",
        )
