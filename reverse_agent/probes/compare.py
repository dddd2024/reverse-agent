from __future__ import annotations

from ..tool_runners import ToolRunArtifact


def artifact_has_compare_truth(artifact: ToolRunArtifact) -> bool:
    if any(item.kind == "RuntimeCompareEvidence" for item in artifact.structured_evidence):
        return True
    return any(
        line.startswith("runtime_compare:lhs=") or line.startswith("runtime_compare:lhs_ptr=")
        for line in artifact.evidence
    )
