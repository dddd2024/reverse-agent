from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..tool_runners import ToolRunArtifact
from ..transforms.base import TransformModel

LogFn = Callable[[str], None]


@dataclass
class ProfileSolveResult:
    enabled: bool
    summary: str
    candidates: list[str] = field(default_factory=list)
    artifacts: list[ToolRunArtifact] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    handled: bool
    selected_flag: str = ""
    records: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""


class ChallengeProfile(ABC):
    profile_id: str = "generic"
    display_name: str = "Generic Profile"
    category: str = "uncategorized"

    @abstractmethod
    def detect(
        self,
        file_path: Path,
        strings: list[str],
        static_evidence: list[str],
    ) -> int:
        raise NotImplementedError

    def transforms(self) -> list[TransformModel]:
        return []

    def supported_strategies(self) -> list[str]:
        return []

    def collect_runtime_probes(
        self,
        file_path: Path,
        strings: list[str],
        artifacts_dir: Path,
        seed_candidates: list[str],
        analysis_mode: str,
        log: LogFn,
    ) -> list[ToolRunArtifact]:
        _ = file_path, strings, artifacts_dir, seed_candidates, analysis_mode, log
        return []

    def build_seed_candidates(
        self,
        strings: list[str],
        pre_candidates: list[str],
        tool_evidence: list[str],
    ) -> list[str]:
        _ = strings, tool_evidence
        return pre_candidates[:]

    def run_specialized_solver(
        self,
        file_path: Path,
        strings: list[str],
        seed_candidates: list[str],
        artifacts_dir: Path,
        log: LogFn,
        prior_artifacts: list[ToolRunArtifact],
    ) -> ProfileSolveResult | None:
        _ = file_path, strings, seed_candidates, artifacts_dir, log, prior_artifacts
        return None

    def validate_candidate(
        self,
        file_path: Path,
        candidates: list[str],
        success_markers: list[str],
        fail_markers: list[str],
        runtime_validation_enabled: bool,
        log: LogFn,
    ) -> ValidationResult | None:
        _ = file_path, candidates, success_markers, fail_markers, runtime_validation_enabled, log
        return None
