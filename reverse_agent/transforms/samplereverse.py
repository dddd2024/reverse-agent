from __future__ import annotations

from .base import TransformModel

TARGET_PREFIX = "flag{".encode("utf-16le")
TARGET_WCHARS = "flag{"
TARGET_COMPARE_BYTES = 10
TARGET_COMPARE_WCHARS = 5


def _lower_ascii(value: int) -> int:
    if 0x41 <= value <= 0x5A:
        return value + 0x20
    return value


def _score_wchar_pair(raw_low: int, raw_high: int, target_low: int, target_high: int) -> tuple[bool, int]:
    matches = raw_high == target_high and _lower_ascii(raw_low) == _lower_ascii(target_low)
    distance = abs(raw_high - target_high) + abs(_lower_ascii(raw_low) - _lower_ascii(target_low))
    return matches, distance


def score_compare_prefix(raw_prefix: bytes) -> dict[str, int | str]:
    raw = bytes(raw_prefix[:TARGET_COMPARE_BYTES])
    compare_bytes = min(len(raw), TARGET_COMPARE_BYTES)
    compare_wchars = min(compare_bytes // 2, TARGET_COMPARE_WCHARS)
    ci_exact_wchars = 0
    ci_distance5 = 0
    raw_distance10 = 0

    for idx in range(compare_bytes):
        raw_distance10 += abs(raw[idx] - TARGET_PREFIX[idx])

    if compare_bytes < TARGET_COMPARE_BYTES:
        raw_distance10 += 0x100 * (TARGET_COMPARE_BYTES - compare_bytes)

    for idx in range(compare_wchars):
        raw_low = raw[idx * 2]
        raw_high = raw[idx * 2 + 1]
        target_low = TARGET_PREFIX[idx * 2]
        target_high = TARGET_PREFIX[idx * 2 + 1]
        matches, distance = _score_wchar_pair(raw_low, raw_high, target_low, target_high)
        ci_distance5 += distance
        if matches and idx == ci_exact_wchars:
            ci_exact_wchars += 1

    if compare_wchars < TARGET_COMPARE_WCHARS:
        for idx in range(compare_wchars, TARGET_COMPARE_WCHARS):
            target_low = TARGET_PREFIX[idx * 2]
            target_high = TARGET_PREFIX[idx * 2 + 1]
            ci_distance5 += abs(target_high) + abs(_lower_ascii(target_low))

    return {
        "ci_exact_wchars": ci_exact_wchars,
        "ci_distance5": ci_distance5,
        "raw_distance10": raw_distance10,
        "raw_prefix_hex": raw.hex(),
    }


class SamplereverseTransformModel(TransformModel):
    name = "SamplereverseTransform"

    def describe(self) -> str:
        return "nibble expand -> UTF-16LE -> Base64 -> RC4 prefix decrypt -> __wcsnicmp(L\"flag{\", 5)"

    def score_prefix(self, raw_prefix: bytes) -> dict[str, int | str]:
        return score_compare_prefix(raw_prefix)
