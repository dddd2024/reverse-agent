from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StructuredEvidence:
    kind: str
    source_tool: str
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    derived_candidates: list[str] = field(default_factory=list)


def collect_derived_candidates(items: list[StructuredEvidence]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in items:
        for candidate in item.derived_candidates:
            text = str(candidate).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            values.append(text)
    return values
