from __future__ import annotations

from pathlib import Path

from .base import ChallengeProfile
from .samplereverse import SamplereverseProfile


def registered_profiles() -> list[ChallengeProfile]:
    return [SamplereverseProfile()]


def match_profiles(
    file_path: Path,
    strings: list[str],
    static_evidence: list[str],
) -> list[tuple[ChallengeProfile, int]]:
    matches: list[tuple[ChallengeProfile, int]] = []
    for profile in registered_profiles():
        score = int(profile.detect(file_path=file_path, strings=strings, static_evidence=static_evidence))
        if score > 0:
            matches.append((profile, score))
    matches.sort(key=lambda item: (-item[1], item[0].profile_id))
    return matches
