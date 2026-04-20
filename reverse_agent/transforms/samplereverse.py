from __future__ import annotations

from .base import TransformModel

TARGET_PREFIX = "flag{".encode("utf-16le")


def _lower_ascii(value: int) -> int:
    if 0x41 <= value <= 0x5A:
        return value + 0x20
    return value


class SamplereverseTransformModel(TransformModel):
    name = "SamplereverseTransform"

    def describe(self) -> str:
        return "nibble expand -> UTF-16LE -> Base64 -> RC4 prefix decrypt -> __wcsnicmp(L\"flag{\", 5)"

    def score_prefix(self, raw_prefix: bytes) -> dict[str, int]:
        ci_exact_wchars = 0
        ci_distance5 = 0
        raw_distance10 = 0
        compare_len = min(len(raw_prefix), len(TARGET_PREFIX))
        for idx in range(compare_len):
            raw_value = raw_prefix[idx]
            target_value = TARGET_PREFIX[idx]
            if _lower_ascii(raw_value) == _lower_ascii(target_value):
                if idx == ci_exact_wchars:
                    ci_exact_wchars += 1
            else:
                if idx == ci_exact_wchars:
                    ci_exact_wchars = idx
            raw_distance10 += abs(raw_value - target_value)
            if idx < 10:
                ci_distance5 += abs(_lower_ascii(raw_value) - _lower_ascii(target_value))
        return {
            "ci_exact_wchars": ci_exact_wchars // 2,
            "ci_distance5": ci_distance5,
            "raw_distance10": raw_distance10,
        }
