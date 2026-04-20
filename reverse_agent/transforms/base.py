from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TransformModel(ABC):
    name: str = "TransformModel"

    @abstractmethod
    def describe(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def score_prefix(self, raw_prefix: bytes) -> dict[str, Any]:
        raise NotImplementedError
