from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..tool_runners import ToolRunArtifact

LogFn = Callable[[str], None]


@dataclass
class StrategyResult:
    strategy_name: str
    summary: str = ""
    candidates: list[str] = field(default_factory=list)
    artifacts: list[ToolRunArtifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class SolverStrategy(ABC):
    name: str = "SolverStrategy"

    @abstractmethod
    def preconditions(self, **kwargs: Any) -> bool:
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, **kwargs: Any) -> float:
        raise NotImplementedError

    @abstractmethod
    def run(self, **kwargs: Any) -> StrategyResult:
        raise NotImplementedError

    def resume(self, **kwargs: Any) -> StrategyResult:
        return self.run(**kwargs)

    def emit_artifacts(
        self,
        file_path: Path,
        artifacts_dir: Path,
        log: LogFn,
        **kwargs: Any,
    ) -> list[ToolRunArtifact]:
        _ = file_path, artifacts_dir, log, kwargs
        return []
