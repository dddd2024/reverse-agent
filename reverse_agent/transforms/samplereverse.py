from __future__ import annotations

import base64

from .base import TransformModel

TARGET_PREFIX = "flag{".encode("utf-16le")
TARGET_WCHARS = "flag{"
TARGET_COMPARE_BYTES = 10
TARGET_COMPARE_WCHARS = 5
LONG_PREFIX_BYTES = 64
STRUCTURE_PREFIX_BYTES = 16
TAIL_FLAGLIKE_BYTES = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-")


def _samplereverse_enc_const() -> bytes:
    from ..sample_solver import SAMPLEREVERSE_ENC_CONST

    return SAMPLEREVERSE_ENC_CONST


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


def _expand_candidate_bytes(candidate: bytes) -> bytes:
    expanded = bytearray()
    for value in candidate:
        expanded.append(((value >> 4) & 0x0F) + 0x78)
        expanded.append((value & 0x0F) + 0x7A)
    return bytes(expanded)


def _utf16_interleaved_bytes(expanded: bytes) -> bytes:
    raw = bytearray()
    for value in expanded:
        raw.extend((value, 0))
    return bytes(raw)


def _rc4_decrypt_prefix(key: bytes, prefix_len: int) -> bytes:
    enc_const = _samplereverse_enc_const()
    s = list(range(256))
    j = 0
    for idx in range(256):
        j = (j + s[idx] + key[idx % len(key)]) & 0xFF
        s[idx], s[j] = s[j], s[idx]
    i = 0
    j = 0
    out = bytearray()
    for idx in range(min(prefix_len, len(enc_const))):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        ks = s[(s[i] + s[j]) & 0xFF]
        out.append(enc_const[idx] ^ ks)
    return bytes(out)


def _wchar_compare_deltas(raw_prefix: bytes) -> list[dict[str, object]]:
    raw = bytes(raw_prefix[:TARGET_COMPARE_BYTES])
    deltas: list[dict[str, object]] = []
    for idx in range(TARGET_COMPARE_WCHARS):
        raw_low = raw[idx * 2] if idx * 2 < len(raw) else 0x100
        raw_high = raw[idx * 2 + 1] if idx * 2 + 1 < len(raw) else 0x100
        target_low = TARGET_PREFIX[idx * 2]
        target_high = TARGET_PREFIX[idx * 2 + 1]
        low_distance = abs(_lower_ascii(int(raw_low)) - _lower_ascii(int(target_low)))
        high_distance = abs(int(raw_high) - int(target_high))
        exact_ci = (
            raw_high <= 0xFF
            and int(raw_high) == int(target_high)
            and _lower_ascii(int(raw_low)) == _lower_ascii(int(target_low))
        )
        deltas.append(
            {
                "index": idx,
                "raw_pair_hex": (
                    f"{int(raw_low) & 0xFF:02x}{int(raw_high) & 0xFF:02x}"
                    if raw_low <= 0xFF and raw_high <= 0xFF
                    else ""
                ),
                "target_pair_hex": f"{target_low:02x}{target_high:02x}",
                "raw_low": int(raw_low),
                "raw_high": int(raw_high),
                "target_low": int(target_low),
                "target_high": int(target_high),
                "low_distance_ci": int(low_distance),
                "high_distance": int(high_distance),
                "distance": int(low_distance + high_distance),
                "exact_ci": bool(exact_ci),
            }
        )
    return deltas


def _prefix_length_trace_rows(candidate: bytes, max_prefix_bytes: int = 10) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    max_len = min(max_prefix_bytes, len(candidate))
    for prefix_len in range(1, max_len + 1):
        prefix = candidate[:prefix_len]
        expanded = _expand_candidate_bytes(prefix)
        utf16_raw = _utf16_interleaved_bytes(expanded)
        base64_text = base64.b64encode(utf16_raw).decode("ascii")
        key = base64_text.encode("utf-16le")[: len(base64_text)]
        rows.append(
            {
                "candidate_prefix_len_bytes": prefix_len,
                "candidate_prefix_hex": prefix.hex(),
                "wchar_len": len(expanded),
                "utf16le_hex": utf16_raw.hex(),
                "utf16le_len_bytes": len(utf16_raw),
                "utf16le_len_mod3": len(utf16_raw) % 3,
                "base64_text": base64_text,
                "base64_len": len(base64_text),
                "base64_remainder_mod4": len(base64_text) % 4,
                "base64_padding_count": len(base64_text) - len(base64_text.rstrip("=")),
                "rc4_input_len": len(key),
                "rc4_key_length_bytes": len(key),
            }
        )
    return rows


def trace_candidate_transform(
    candidate_hex: str,
    *,
    prefix_bytes: int = 8,
    decrypt_prefix_len: int = LONG_PREFIX_BYTES,
) -> dict[str, object]:
    normalized = str(candidate_hex).strip().lower()
    try:
        candidate = bytes.fromhex(normalized)
    except ValueError:
        return {
            "candidate_hex": normalized,
            "valid": False,
            "reason": "invalid_candidate_hex",
        }
    if not candidate:
        return {
            "candidate_hex": normalized,
            "valid": False,
            "reason": "empty_candidate",
        }

    prefix_len = min(max(0, int(prefix_bytes)), len(candidate))
    expanded = _expand_candidate_bytes(candidate)
    utf16_raw = _utf16_interleaved_bytes(expanded)
    base64_text = base64.b64encode(utf16_raw).decode("ascii")
    key = base64_text.encode("utf-16le")[: len(base64_text)]
    decrypt_prefix = _rc4_decrypt_prefix(key, decrypt_prefix_len)
    metrics = {
        **score_compare_prefix(decrypt_prefix),
        **score_prefix_oracle_metrics(decrypt_prefix),
    }

    prefix_raw_bytes = prefix_len * 4
    prefix_base64_cover_chars = ((prefix_raw_bytes + 2) // 3) * 4 if prefix_raw_bytes else 0
    suffix_first_raw_byte_index = prefix_raw_bytes if prefix_len < len(candidate) else None
    suffix_first_base64_char_index = (
        (int(suffix_first_raw_byte_index) // 3) * 4
        if suffix_first_raw_byte_index is not None
        else None
    )

    return {
        "candidate_hex": normalized,
        "valid": True,
        "candidate_raw_bytes": {
            "hex": candidate.hex(),
            "length_bytes": len(candidate),
        },
        "candidate_layout": {
            "candidate_length_bytes": len(candidate),
            "prefix_bytes": prefix_len,
            "prefix_hex": candidate[:prefix_len].hex(),
            "suffix_hex": candidate[prefix_len:].hex(),
            "suffix_length_bytes": len(candidate[prefix_len:]),
            "suffix_is_all_A": candidate[prefix_len:] == (b"A" * len(candidate[prefix_len:])),
        },
        "nibble_expansion": {
            "expanded_length_bytes": len(expanded),
            "expanded_hex": expanded.hex(),
            "prefix_expanded_length_bytes": prefix_len * 2,
            "prefix_expanded_hex": expanded[: prefix_len * 2].hex(),
        },
        "utf16_payload": {
            "raw_length_bytes": len(utf16_raw),
            "raw_hex": utf16_raw.hex(),
            "prefix_raw_length_bytes": prefix_raw_bytes,
            "prefix_raw_hex": utf16_raw[:prefix_raw_bytes].hex(),
        },
        "base64_boundary": {
            "base64_length_chars": len(base64_text),
            "base64_prefix": base64_text[:96],
            "padding_count": len(base64_text) - len(base64_text.rstrip("=")),
            "prefix_raw_bytes": prefix_raw_bytes,
            "prefix_base64_cover_chars": prefix_base64_cover_chars,
            "prefix_ends_on_base64_chunk_boundary": prefix_raw_bytes % 3 == 0,
            "prefix_last_chunk_raw_remainder": prefix_raw_bytes % 3,
            "suffix_first_raw_byte_index": suffix_first_raw_byte_index,
            "suffix_first_base64_char_index": suffix_first_base64_char_index,
            "base64_remainder_mod4": len(base64_text) % 4,
            "raw_length_mod3": len(utf16_raw) % 3,
        },
        "rc4": {
            "key_length_bytes": len(key),
            "key_source_base64_chars": len(base64_text),
            "key_hex_prefix": key[:64].hex(),
            "input_type": "base64_ascii_text_encoded_utf16le_truncated_to_base64_char_count",
            "state_reset_per_candidate": True,
            "prga_first_byte_discarded": False,
            "decrypt_prefix_len": len(decrypt_prefix),
            "decrypt_prefix_hex": decrypt_prefix.hex(),
        },
        "compare_boundary": {
            "target_wchars": TARGET_WCHARS,
            "target_prefix_hex": TARGET_PREFIX.hex(),
            "raw_prefix_hex_10": decrypt_prefix[:TARGET_COMPARE_BYTES].hex(),
            "compare_window_hex": decrypt_prefix[:TARGET_COMPARE_BYTES].hex(),
            "compare_window_bytes": TARGET_COMPARE_BYTES,
            "compare_unit": "wchar",
            "case_sensitive": False,
            "wchar_deltas": _wchar_compare_deltas(decrypt_prefix),
            **metrics,
        },
        "prefix_length_table": _prefix_length_trace_rows(candidate, max_prefix_bytes=10),
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

    def trace_candidate(self, candidate_hex: str) -> dict[str, object]:
        return trace_candidate_transform(candidate_hex)
