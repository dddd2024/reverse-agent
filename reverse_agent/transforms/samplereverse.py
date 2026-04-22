from __future__ import annotations

from .base import TransformModel

TARGET_PREFIX = "flag{".encode("utf-16le")
TARGET_WCHARS = "flag{"
TARGET_COMPARE_BYTES = 10
TARGET_COMPARE_WCHARS = 5
LONG_PREFIX_BYTES = 64
STRUCTURE_PREFIX_BYTES = 16
TAIL_FLAGLIKE_BYTES = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-")


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


def _is_wide_ascii_pair(raw_low: int, raw_high: int) -> bool:
    return raw_high == 0x00 and 0x20 <= raw_low <= 0x7E


def score_prefix_oracle_metrics(raw_prefix: bytes) -> dict[str, int | str]:
    raw = bytes(raw_prefix[:LONG_PREFIX_BYTES])
    structure = raw[:STRUCTURE_PREFIX_BYTES]
    wide_ascii_contiguous_16 = 0
    wide_ascii_total_16 = 0
    wide_zero_high_pairs_16 = 0
    flaglike_tail_pairs_16 = 0
    pair_count = min(len(structure) // 2, STRUCTURE_PREFIX_BYTES // 2)

    for idx in range(pair_count):
        raw_low = structure[idx * 2]
        raw_high = structure[idx * 2 + 1]
        is_ascii_pair = _is_wide_ascii_pair(raw_low, raw_high)
        if raw_high == 0x00:
            wide_zero_high_pairs_16 += 1
        if is_ascii_pair:
            wide_ascii_total_16 += 1
            if idx == wide_ascii_contiguous_16:
                wide_ascii_contiguous_16 += 1
        if 5 <= idx <= 7 and raw_high == 0x00 and raw_low in TAIL_FLAGLIKE_BYTES:
            flaglike_tail_pairs_16 += 1

    return {
        "raw_prefix_hex_64": raw.hex(),
        "wide_ascii_contiguous_16": wide_ascii_contiguous_16,
        "wide_ascii_total_16": wide_ascii_total_16,
        "wide_zero_high_pairs_16": wide_zero_high_pairs_16,
        "flaglike_tail_pairs_16": flaglike_tail_pairs_16,
    }


class SamplereverseTransformModel(TransformModel):
    name = "SamplereverseTransform"

    def describe(self) -> str:
        return "nibble expand -> UTF-16LE -> Base64 -> RC4 prefix decrypt -> __wcsnicmp(L\"flag{\", 5)"

    def score_prefix(self, raw_prefix: bytes) -> dict[str, int | str]:
        return {
            **score_compare_prefix(raw_prefix),
            **score_prefix_oracle_metrics(raw_prefix),
        }
