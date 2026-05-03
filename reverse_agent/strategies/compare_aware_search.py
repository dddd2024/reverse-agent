from __future__ import annotations

import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..evidence import StructuredEvidence
from ..sample_solver import _decrypt_prefix
from ..samplereverse_z3 import solve_targeted_prefix8
from ..tool_runners import ToolRunArtifact
from ..transforms.samplereverse import (
    SamplereverseTransformModel,
    score_compare_prefix,
    trace_candidate_transform,
)
from .base import SolverStrategy, StrategyResult

RESULT_FILE_NAME = "samplereverse_compare_aware_result.json"
RESULT_LOG_FILE_NAME = "samplereverse_compare_aware.log"
VALIDATION_FILE_NAME = "samplereverse_compare_aware_validation.json"
BASELINE_SUMMARY_FILE_NAME = "samplereverse_compare_aware_baseline_summary.json"
PAIRSCAN_FILE_NAME = "pairscan_summary.json"
BRIDGE_RESULT_FILE_NAME = "bridge_search_result.json"
BRIDGE_VALIDATION_FILE_NAME = "bridge_validation.json"
GUIDED_POOL_RESULT_FILE_NAME = "samplereverse_compare_aware_guided_pool_result.json"
GUIDED_POOL_VALIDATION_FILE_NAME = "samplereverse_compare_aware_guided_pool_validation.json"
STRATA_SUMMARY_FILE_NAME = "samplereverse_compare_aware_strata_summary.json"
SMT_RESULT_FILE_NAME = "samplereverse_compare_aware_smt_result.json"
SMT_VALIDATION_FILE_NAME = "samplereverse_compare_aware_smt_validation.json"
EXACT2_BASIN_VALUE_POOL_RESULT_FILE_NAME = "samplereverse_exact2_basin_value_pool_result.json"
EXACT2_BASIN_VALUE_POOL_VALIDATION_FILE_NAME = "samplereverse_exact2_basin_value_pool_validation.json"
FRONTIER_SUMMARY_FILE_NAME = "samplereverse_compare_aware_frontier_summary.json"
PROFILE_TRANSFORM_HYPOTHESIS_MATRIX_FILE_NAME = "profile_transform_hypothesis_matrix.json"
PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT = 8

DEFAULT_ANCHORS = (
    "78d540b49c590770",
    "4a78f0eaeb4f13b0",
    "95a3f65dcedb6290",
)
DEFAULT_FRONTIER_ANCHOR = "95a3f65dcedb6290"
DEFAULT_FIXED_SUFFIX_HEX = "41414141414141"
DEFAULT_BRIDGE_BASELINE_DISTANCE5 = 246
INPUT_LENGTH = 15
BRIDGE_VALIDATE_TOP = 8
HOT_POSITION_LIMIT = 5
HOT_NIBBLE_LIMIT = 5
TRIAD_SEED_LIMIT = 4
REFINE_MAX_ANCHORS = 8
TARGET_PREFIX = "flag{".encode("utf-16le")
LONG_PREFIX_BYTES = 64
RUNTIME_PREFIX_BYTES = 64
PAIR_METRIC_BYTES = 16
PAIR_METRIC_WCHARS = 8
GUIDED_POOL_POSITION_LIMIT = 5
GUIDED_POOL_TOP_VALUES = 10
GUIDED_POOL_BEAM_LIMIT = 16
GUIDED_POOL_VALIDATE_TOP = 8
GUIDED_POOL_EXPLORATION_SLOTS = 4
FRONTIER_MAX_ANCHORS = 4
FRONTIER_PAIR_VALUE_LIMIT = 4
FRONTIER_PAIR_TOP_PER_PAIR = 6
FRONTIER_TOP_PAIR_LIMIT = 8
FRONTIER_PAIR_SEED_LIMIT = 8
FRONTIER_TRIAD_VALUE_LIMIT = 3
FRONTIER_TRIAD_POOL_LIMIT = 8
FRONTIER_MAX_ITERATIONS = 2
EXACT2_BASIN_VALUE_POOL_EVAL_MAX_COMBINATIONS = 128
EXACT1_PAIR_LOCK_LIMIT = 3
EXACT1_PAIR_DISTANCE_ESCAPE = 24
EXACT1_PAIR_PRESERVE_VALUE_LIMIT = 6
EXACT1_PAIR_ESCAPE_VALUE_LIMIT = 6
EXACT1_PAIR_PROFILE_PRESERVE_TOP = 4
EXACT1_PAIR_PROFILE_ESCAPE_TOP = 2
EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX = 5
EXACT1_PAIR_ESCAPE_BORDERLINE_SCORE_MAX = 7
EXACT1_PAIR_NEAR_LOCAL_RADIUS = 2
EXACT1_PAIR_NEAR_DISTANCE_SLACK = 128
EXACT1_PAIR_NEAR_RAW_SLACK = 128
EXACT1_PAIR_SINGLE_BYTE_GUARD_SLACK = 96
EXACT1_PAIR_SINGLE_BYTE_SOFT_RADIUS = 4
EXACT1_PAIR_SINGLE_BYTE_SOFT_PROMOTE_LIMIT = 1
EXACT1_PROJECTED_STEP_LIMIT = 2
EXACT1_PROJECTED_KEEP_PER_DIRECTION = 1
EXACT1_PROJECTED_DISTANCE_SLACK = 192
EXACT1_PROJECTED_RAW_SLACK = 192
EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR = 1
EXACT1_PAIR_HARD_ESCAPE_DIAG_SAMPLES = 1
EXACT1_LINEAGE_SOURCE_LIMIT = 4
EXACT1_PRESERVE_NEIGHBOR_RADIUS = 2
EXACT1_ESCAPE_NEIGHBOR_RADIUS = 4
EXACT1_LOCAL_SOURCE_RADIUS = 6
EXACT2_ANCHOR_MODE = "exact2"
FRONTIER_ANCHOR_MODE = "frontier"
FRONTIER_EXACT1_SUBMODE = "frontier_exact1"
FRONTIER_EXACT0_SUBMODE = "frontier_exact0"
PROJECTED_PRESERVE_SECOND_HOP_ROLE = "validated_projected_preserve_second_hop"
DO_NOT_PROMOTE_PROJECTED_ANCHORS = (
    "5a3f7f46ddd474d0",
    "5a3f7fc2ddd474d0",
    "343f7f46ddd474d0",
)
PAIR_TAIL_FLAGLIKE_BYTES = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-")

PAIRSCAN_TOOL = "pairscan"
TRIAD_TOOL = "triad"
QUAD_TOOL = "quartet"
QUINT_FIXED_TOOL = "quint_fixed"

FINAL_LINE_RE = re.compile(
    r"^FINAL exact=(?P<exact>\d+) dist4=(?P<dist4>\d+) dist6=(?P<dist6>\d+) dist10=(?P<dist10>\d+) "
    r"cand(?:8)?=(?P<cand>[0-9a-f]{16,30}) raw=(?P<raw>[0-9a-f]{20})(?: combo=\[(?P<combo>[0-9,\- ]+)\])?$",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _compare_probe_script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "olly_scripts" / "compare_probe.py"


def _tool_source_path(tool_name: str) -> Path:
    mapping = {
        PAIRSCAN_TOOL: "samplereverse_pair_search_prefix8_len15.c",
        TRIAD_TOOL: "samplereverse_triad_search_prefix8_len15.c",
        QUAD_TOOL: "samplereverse_nibble_quad_search_prefix8.c",
        QUINT_FIXED_TOOL: "samplereverse_nibble_quint_fixed.c",
    }
    return _repo_root() / "tools" / mapping[tool_name]


def _tool_binary_path(tool_name: str) -> Path:
    mapping = {
        PAIRSCAN_TOOL: "samplereverse_pair_search_prefix8_len15.exe",
        TRIAD_TOOL: "samplereverse_triad_search_prefix8_len15.exe",
        QUAD_TOOL: "samplereverse_nibble_quad_search_prefix8.exe",
        QUINT_FIXED_TOOL: "samplereverse_nibble_quint_fixed.exe",
    }
    return _repo_root() / "tools" / mapping[tool_name]


def _candidate_text_from_hex(candidate_hex: str) -> str:
    return bytes.fromhex(candidate_hex).decode("latin1")


def _candidate_hex_from_entry(entry: dict[str, object]) -> str:
    candidate_hex = str(entry.get("candidate_hex", "")).strip().lower()
    cand8_hex = str(entry.get("cand8_hex", "")).strip().lower()
    if candidate_hex:
        return candidate_hex
    if len(cand8_hex) == 16:
        return f"{cand8_hex}{DEFAULT_FIXED_SUFFIX_HEX}"
    return ""


def _lower_ascii(value: int) -> int:
    if 0x41 <= value <= 0x5A:
        return value + 0x20
    return value


def _bridge_metrics_from_raw_prefix(raw_prefix: bytes) -> dict[str, int | str]:
    raw = bytes(raw_prefix[: len(TARGET_PREFIX)])
    exact = 0
    dist4 = 0
    dist6 = 0
    dist10 = 0
    for idx, target in enumerate(TARGET_PREFIX):
        value = raw[idx] if idx < len(raw) else 0x100
        if idx < 4:
            dist4 += abs(int(value) - int(target))
        if idx < 6:
            dist6 += abs(int(value) - int(target))
        if idx < 10:
            dist10 += abs(int(value) - int(target))
        if idx < len(raw) and _lower_ascii(raw[idx]) == _lower_ascii(target) and idx == exact:
            exact += 1
    return {
        "raw_prefix_hex": raw.hex(),
        "exact": exact,
        "dist4": dist4,
        "dist6": dist6,
        "dist10": dist10,
    }


def _prefix_boundary_breakdown_from_prefix(
    raw_prefix: bytes,
    *,
    candidate_hex: str = "",
    label: str = "",
    source: str = "",
    transform_model: SamplereverseTransformModel | None = None,
) -> dict[str, object]:
    model = transform_model or SamplereverseTransformModel()
    raw = bytes(raw_prefix[: len(TARGET_PREFIX)])
    metrics = model.score_prefix(raw_prefix)
    wchar_deltas: list[dict[str, object]] = []
    for idx in range(len(TARGET_PREFIX) // 2):
        raw_low = raw[idx * 2] if idx * 2 < len(raw) else 0x100
        raw_high = raw[idx * 2 + 1] if idx * 2 + 1 < len(raw) else 0x100
        target_low = TARGET_PREFIX[idx * 2]
        target_high = TARGET_PREFIX[idx * 2 + 1]
        low_distance = abs(_lower_ascii(int(raw_low)) - _lower_ascii(int(target_low)))
        high_distance = abs(int(raw_high) - int(target_high))
        exact_ci = (
            idx * 2 + 1 < len(raw)
            and int(raw_high) == int(target_high)
            and _lower_ascii(int(raw_low)) == _lower_ascii(int(target_low))
        )
        wchar_deltas.append(
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
                "contiguous_exact": idx < int(metrics.get("ci_exact_wchars", 0) or 0),
            }
        )
    candidate = str(candidate_hex).strip().lower()
    return {
        "label": str(label),
        "source": str(source),
        "candidate_hex": candidate,
        "cand8_hex": candidate[:16],
        "raw_prefix_hex_10": raw.hex(),
        "ci_exact_wchars": int(metrics.get("ci_exact_wchars", 0) or 0),
        "ci_distance5": int(metrics.get("ci_distance5", 1 << 30) or (1 << 30)),
        "raw_distance10": int(metrics.get("raw_distance10", 1 << 30) or (1 << 30)),
        "wide_ascii_contiguous_16": int(metrics.get("wide_ascii_contiguous_16", 0) or 0),
        "wide_ascii_total_16": int(metrics.get("wide_ascii_total_16", 0) or 0),
        "wide_zero_high_pairs_16": int(metrics.get("wide_zero_high_pairs_16", 0) or 0),
        "flaglike_tail_pairs_16": int(metrics.get("flaglike_tail_pairs_16", 0) or 0),
        "wchar_deltas": wchar_deltas,
    }


def _prefix_boundary_breakdown_from_entry(
    entry: dict[str, object],
    *,
    transform_model: SamplereverseTransformModel,
    label: str = "",
    source: str = "",
) -> dict[str, object]:
    prefix_hex = (
        str(entry.get("runtime_lhs_prefix_hex_10", "")).strip().lower()
        or str(entry.get("offline_raw_prefix_hex", "")).strip().lower()
        or str(entry.get("raw_prefix_hex", "")).strip().lower()
    )
    if not prefix_hex:
        prefix_hex = _entry_long_prefix_bytes(entry)[: len(TARGET_PREFIX)].hex()
    try:
        raw_prefix = bytes.fromhex(prefix_hex[: len(TARGET_PREFIX) * 2])
    except ValueError:
        raw_prefix = b""
    candidate_hex = _candidate_hex_from_entry(entry)
    diagnostic = _prefix_boundary_breakdown_from_prefix(
        raw_prefix,
        candidate_hex=candidate_hex,
        label=label or str(entry.get("label", "")),
        source=source,
        transform_model=transform_model,
    )
    diagnostic.update(
        {
            "frontier_role": str(entry.get("frontier_role", "")),
            "source_anchor": str(entry.get("source_anchor", "")).strip().lower(),
            "anchor_mode": str(entry.get("anchor_mode", "")),
            "compare_semantics_agree": bool(entry.get("compare_semantics_agree"))
            if "compare_semantics_agree" in entry
            else None,
            "runtime_ci_exact_wchars": int(
                entry.get("runtime_ci_exact_wchars", diagnostic["ci_exact_wchars"]) or 0
            ),
            "runtime_ci_distance5": int(
                entry.get("runtime_ci_distance5", diagnostic["ci_distance5"]) or 0
            ),
        }
    )
    return diagnostic


def _prefix_boundary_diagnostics(
    entries: Sequence[dict[str, object]],
    *,
    transform_model: SamplereverseTransformModel,
    limit: int = 16,
) -> list[dict[str, object]]:
    ranked = sorted(
        (entry for entry in entries if _candidate_hex_from_entry(entry)),
        key=lambda entry: (
            0
            if str(entry.get("frontier_role", ""))
            in {"projected_preserve_handoff", PROJECTED_PRESERVE_SECOND_HOP_ROLE}
            else 1,
            _runtime_validation_sort_key(entry)
            if "runtime_ci_exact_wchars" in entry
            else (
                -int(entry.get("ci_exact_wchars", 0) or 0),
                int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                0,
                _candidate_hex_from_entry(entry),
            ),
        ),
    )
    diagnostics: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in ranked:
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen:
            continue
        diagnostics.append(
            _prefix_boundary_breakdown_from_entry(
                entry,
                transform_model=transform_model,
                source="runtime_validation" if "runtime_ci_exact_wchars" in entry else "offline_entry",
            )
        )
        seen.add(candidate_hex)
        if len(diagnostics) >= limit:
            break
    return diagnostics


def _offline_raw_prefix(candidate_hex: str, prefix_bytes: int = LONG_PREFIX_BYTES) -> bytes:
    return _decrypt_prefix(_candidate_text_from_hex(candidate_hex), prefix_bytes)


def _entry_long_prefix_bytes(entry: dict[str, object]) -> bytes:
    raw_prefix_hex_64 = str(entry.get("raw_prefix_hex_64", "")).strip().lower()
    if raw_prefix_hex_64:
        try:
            return bytes.fromhex(raw_prefix_hex_64[: LONG_PREFIX_BYTES * 2])
        except ValueError:
            pass
    candidate_hex = _candidate_hex_from_entry(entry)
    if candidate_hex:
        return _offline_raw_prefix(candidate_hex, LONG_PREFIX_BYTES)
    raw_prefix_hex = str(entry.get("raw_prefix_hex") or entry.get("raw", "")).strip().lower()
    if raw_prefix_hex:
        try:
            return bytes.fromhex(raw_prefix_hex)
        except ValueError:
            pass
    return b""


def _evaluate_candidate_hex(candidate_hex: str, transform_model: SamplereverseTransformModel) -> dict[str, object]:
    raw_prefix = _offline_raw_prefix(candidate_hex, LONG_PREFIX_BYTES)
    bridge_metrics = _bridge_metrics_from_raw_prefix(raw_prefix)
    compare_metrics = transform_model.score_prefix(raw_prefix)
    pair_metrics = _pair_structure_metrics_from_raw_prefix(raw_prefix)
    return {
        "cand8_hex": candidate_hex[:16],
        "candidate_hex": candidate_hex,
        **bridge_metrics,
        "raw_prefix_hex_64": str(compare_metrics.get("raw_prefix_hex_64", raw_prefix[:LONG_PREFIX_BYTES].hex())),
        "ci_exact_wchars": int(compare_metrics.get("ci_exact_wchars", 0) or 0),
        "ci_distance5": int(compare_metrics.get("ci_distance5", 1 << 30) or (1 << 30)),
        "raw_distance10": int(compare_metrics.get("raw_distance10", 1 << 30) or (1 << 30)),
        "wide_ascii_contiguous_16": int(compare_metrics.get("wide_ascii_contiguous_16", 0) or 0),
        "wide_ascii_total_16": int(compare_metrics.get("wide_ascii_total_16", 0) or 0),
        "wide_zero_high_pairs_16": int(compare_metrics.get("wide_zero_high_pairs_16", 0) or 0),
        "flaglike_tail_pairs_16": int(compare_metrics.get("flaglike_tail_pairs_16", 0) or 0),
        "pair_raw_prefix_hex_16": str(pair_metrics.get("pair_raw_prefix_hex_16", raw_prefix[:PAIR_METRIC_BYTES].hex())),
        "pair_wide_ascii_contiguous_8": int(pair_metrics.get("pair_wide_ascii_contiguous_8", 0) or 0),
        "pair_wide_zero_high_pairs_8": int(pair_metrics.get("pair_wide_zero_high_pairs_8", 0) or 0),
        "pair_flaglike_tail_pairs_8": int(pair_metrics.get("pair_flaglike_tail_pairs_8", 0) or 0),
    }


def _entry_metrics(entry: dict[str, object], transform_model: SamplereverseTransformModel) -> dict[str, int | str]:
    raw_prefix = _entry_long_prefix_bytes(entry)
    if raw_prefix:
        try:
            metrics = transform_model.score_prefix(raw_prefix)
            pair_metrics = _pair_structure_metrics_from_raw_prefix(raw_prefix)
        except Exception:
            metrics = {}
            pair_metrics = {}
    else:
        metrics = {}
        pair_metrics = {}
    raw_prefix_hex = str(metrics.get("raw_prefix_hex", entry.get("raw_prefix_hex") or entry.get("raw", ""))).strip().lower()
    raw_prefix_hex_64 = str(metrics.get("raw_prefix_hex_64", entry.get("raw_prefix_hex_64", ""))).strip().lower()
    return {
        "ci_exact_wchars": int(metrics.get("ci_exact_wchars", entry.get("ci_exact_wchars", 0)) or 0),
        "ci_distance5": int(metrics.get("ci_distance5", entry.get("ci_distance5", 1 << 30)) or (1 << 30)),
        "raw_distance10": int(metrics.get("raw_distance10", entry.get("raw_distance10", entry.get("dist10", 1 << 30))) or (1 << 30)),
        "raw_prefix_hex": raw_prefix_hex,
        "raw_prefix_hex_64": raw_prefix_hex_64,
        "wide_ascii_contiguous_16": int(
            metrics.get("wide_ascii_contiguous_16", entry.get("wide_ascii_contiguous_16", 0)) or 0
        ),
        "wide_ascii_total_16": int(metrics.get("wide_ascii_total_16", entry.get("wide_ascii_total_16", 0)) or 0),
        "wide_zero_high_pairs_16": int(
            metrics.get("wide_zero_high_pairs_16", entry.get("wide_zero_high_pairs_16", 0)) or 0
        ),
        "flaglike_tail_pairs_16": int(metrics.get("flaglike_tail_pairs_16", entry.get("flaglike_tail_pairs_16", 0)) or 0),
        "pair_raw_prefix_hex_16": str(
            pair_metrics.get("pair_raw_prefix_hex_16", entry.get("pair_raw_prefix_hex_16", ""))
        ).strip().lower(),
        "pair_wide_ascii_contiguous_8": int(
            pair_metrics.get("pair_wide_ascii_contiguous_8", entry.get("pair_wide_ascii_contiguous_8", 0)) or 0
        ),
        "pair_wide_zero_high_pairs_8": int(
            pair_metrics.get("pair_wide_zero_high_pairs_8", entry.get("pair_wide_zero_high_pairs_8", 0)) or 0
        ),
        "pair_flaglike_tail_pairs_8": int(
            pair_metrics.get("pair_flaglike_tail_pairs_8", entry.get("pair_flaglike_tail_pairs_8", 0)) or 0
        ),
    }


def _pair_structure_metrics_from_raw_prefix(raw_prefix: bytes) -> dict[str, int | str]:
    raw = bytes(raw_prefix[:PAIR_METRIC_BYTES])
    wide_ascii_contiguous_8 = 0
    wide_zero_high_pairs_8 = 0
    flaglike_tail_pairs_8 = 0
    pair_count = min(len(raw) // 2, PAIR_METRIC_WCHARS)
    for idx in range(pair_count):
        raw_low = raw[idx * 2]
        raw_high = raw[idx * 2 + 1]
        is_ascii_pair = raw_high == 0x00 and 0x20 <= raw_low <= 0x7E
        if raw_high == 0x00:
            wide_zero_high_pairs_8 += 1
        if is_ascii_pair and idx == wide_ascii_contiguous_8:
            wide_ascii_contiguous_8 += 1
        if 5 <= idx <= 7 and raw_high == 0x00 and raw_low in PAIR_TAIL_FLAGLIKE_BYTES:
            flaglike_tail_pairs_8 += 1
    return {
        "pair_raw_prefix_hex_16": raw.hex(),
        "pair_wide_ascii_contiguous_8": wide_ascii_contiguous_8,
        "pair_wide_zero_high_pairs_8": wide_zero_high_pairs_8,
        "pair_flaglike_tail_pairs_8": flaglike_tail_pairs_8,
    }


def _pair_structure_rank(entry: dict[str, object], transform_model: SamplereverseTransformModel) -> tuple[int, int, int, int, int, int]:
    if any(
        key in entry
        for key in (
            "pair_wide_ascii_contiguous_8",
            "pair_wide_zero_high_pairs_8",
            "pair_flaglike_tail_pairs_8",
            "wide_ascii_contiguous_16",
            "wide_zero_high_pairs_16",
            "flaglike_tail_pairs_16",
        )
    ):
        metrics = entry
    else:
        metrics = _entry_metrics(entry, transform_model)
    return (
        int(metrics.get("pair_wide_ascii_contiguous_8", 0) or 0),
        int(metrics.get("pair_wide_zero_high_pairs_8", 0) or 0),
        int(metrics.get("pair_flaglike_tail_pairs_8", 0) or 0),
        int(metrics.get("wide_ascii_contiguous_16", 0) or 0),
        int(metrics.get("wide_zero_high_pairs_16", 0) or 0),
        int(metrics.get("flaglike_tail_pairs_16", 0) or 0),
    )


def _exact1_single_byte_origin_priority(origins: Sequence[str]) -> int:
    labels = [str(item).strip() for item in origins if str(item).strip()]
    if any(label.startswith("lineage_") for label in labels):
        return 0
    if any(label.startswith("incoming_") for label in labels):
        return 1
    if any(label.startswith("profile_") for label in labels):
        return 2
    if any(label.startswith("preserve_") or label in {"anchor", "single_byte_guard_base"} for label in labels):
        return 3
    if any("escape_neighbor" in label for label in labels):
        return 4
    return 5


def _exact1_single_byte_soft_quality(
    *,
    radius: int,
    distance_delta: int,
    raw_delta: int,
    structure_rank: Sequence[int],
    baseline_rank: Sequence[int],
    origins: Sequence[str],
) -> dict[str, object]:
    structure_delta = tuple(int(a) - int(b) for a, b in zip(structure_rank, baseline_rank))
    structure_penalty = sum(max(0, -int(value)) for value in structure_delta[:3])
    structure_gain = sum(max(0, int(value)) for value in structure_delta[:3])
    origin_priority = _exact1_single_byte_origin_priority(origins)
    local_compatible = (
        distance_delta <= max(EXACT1_PAIR_SINGLE_BYTE_GUARD_SLACK * 2, EXACT1_PAIR_DISTANCE_ESCAPE * 6)
        and raw_delta <= max(EXACT1_PAIR_SINGLE_BYTE_GUARD_SLACK * 2, EXACT1_PAIR_DISTANCE_ESCAPE * 6)
        and (structure_penalty == 0 or structure_gain > 0 or origin_priority <= 2)
    )
    quality_band = "local_compatible_soft" if local_compatible else "distance_explosive_soft"
    sort_key = (
        0 if local_compatible else 1,
        int(distance_delta),
        int(raw_delta),
        int(structure_penalty),
        int(radius),
        int(origin_priority),
        -int(structure_gain),
    )
    return {
        "quality_band": quality_band,
        "structure_delta": structure_delta,
        "structure_penalty": structure_penalty,
        "structure_gain": structure_gain,
        "origin_priority": origin_priority,
        "sort_key": sort_key,
    }


def _exact1_soft_family_competition_key(candidate: dict[str, object]) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(candidate.get("ci_distance_delta", 1 << 30) or (1 << 30)),
        int(candidate.get("raw_distance_delta", 1 << 30) or (1 << 30)),
        int(candidate.get("structure_penalty", 1 << 30) or (1 << 30)),
        int(candidate.get("projection_step", candidate.get("radius", 1 << 30)) or (1 << 30)),
        int(candidate.get("origin_priority", 1 << 30) or (1 << 30)),
        -int(candidate.get("structure_gain", 0) or 0),
        int(candidate.get("value", 0) or 0),
    )


def _candidate_sort_key(
    entry: dict[str, object],
    transform_model: SamplereverseTransformModel,
) -> tuple[int, int, int, int, int, int, int, str]:
    metrics = _entry_metrics(entry, transform_model)
    return (
        -int(metrics["ci_exact_wchars"]),
        int(metrics["ci_distance5"]),
        int(metrics["raw_distance10"]),
        -int(metrics["wide_ascii_contiguous_16"]),
        -int(metrics["wide_ascii_total_16"]),
        -int(metrics["wide_zero_high_pairs_16"]),
        -int(metrics["flaglike_tail_pairs_16"]),
        _candidate_hex_from_entry(entry),
    )


def _guided_sort_key(
    entry: dict[str, object],
    transform_model: SamplereverseTransformModel,
    *,
    anchor_mode: str,
    frontier_submode: str = "",
) -> tuple[int, int, int, int, int, int, int, int, int, int, str]:
    metrics = _entry_metrics(entry, transform_model)
    candidate_hex = _candidate_hex_from_entry(entry)
    if anchor_mode == FRONTIER_ANCHOR_MODE:
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            return (
                int(metrics["ci_distance5"]),
                int(metrics["raw_distance10"]),
                -max(0, int(metrics["ci_exact_wchars"])),
                -int(metrics["pair_wide_ascii_contiguous_8"]),
                -int(metrics["pair_wide_zero_high_pairs_8"]),
                -int(metrics["pair_flaglike_tail_pairs_8"]),
                -int(metrics["wide_ascii_contiguous_16"]),
                -int(metrics["wide_ascii_total_16"]),
                -int(metrics["wide_zero_high_pairs_16"]),
                -int(metrics["flaglike_tail_pairs_16"]),
                candidate_hex,
            )
        return (
            int(metrics["ci_distance5"]),
            int(metrics["raw_distance10"]),
            -int(metrics["wide_ascii_contiguous_16"]),
            -int(metrics["wide_ascii_total_16"]),
            -int(metrics["wide_zero_high_pairs_16"]),
            -int(metrics["flaglike_tail_pairs_16"]),
            -int(metrics["ci_exact_wchars"]),
            0,
            0,
            0,
            candidate_hex,
        )
    return _candidate_sort_key(entry, transform_model)


def _exact1_escape_profile_sort_key(
    entry: dict[str, object],
    transform_model: SamplereverseTransformModel,
    *,
    baseline_entry: dict[str, object] | None,
) -> tuple[int, int, int, int, int, int, int, str]:
    metrics = _entry_metrics(entry, transform_model)
    signal = (
        _exact1_pair_escape_signal(entry, baseline_entry, transform_model=transform_model)
        if baseline_entry
        else {"score": EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX + 1, "lane": "hard_escape"}
    )
    return (
        int(signal.get("score", EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX + 1)),
        0 if str(signal.get("lane", "")) == "local_escape" else 1,
        int(metrics.get("ci_distance5", 1 << 30) or (1 << 30)),
        int(metrics.get("raw_distance10", 1 << 30) or (1 << 30)),
        -int(metrics.get("pair_wide_ascii_contiguous_8", 0) or 0),
        -int(metrics.get("pair_wide_zero_high_pairs_8", 0) or 0),
        -int(metrics.get("pair_flaglike_tail_pairs_8", 0) or 0),
        -max(0, int(metrics.get("ci_exact_wchars", 0) or 0)),
        _candidate_hex_from_entry(entry),
    )


def _runtime_validation_sort_key(
    entry: dict[str, object],
) -> tuple[int, int, int, int, str]:
    return (
        -int(entry.get("runtime_ci_exact_wchars", 0) or 0),
        int(entry.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
        int(entry.get("offline_ci_distance5", 1 << 30) or (1 << 30)),
        int(entry.get("raw_distance10", 1 << 30) or entry.get("offline_raw_distance10", 1 << 30) or (1 << 30)),
        str(entry.get("candidate_hex", "")),
    )


def _lineage_root(*, source_anchor: str, frontier_role: str, anchor_mode: str) -> str:
    normalized_anchor = str(source_anchor).strip().lower()
    normalized_role = str(frontier_role).strip() or (
        "exact2_seed" if anchor_mode == EXACT2_ANCHOR_MODE else "frontier_anchor"
    )
    if normalized_anchor:
        return f"{normalized_role}({normalized_anchor})"
    return normalized_role


def _frontier_submode_for_role(frontier_role: str) -> str:
    normalized_role = str(frontier_role).strip()
    if normalized_role in {"exact1_frontier", PROJECTED_PRESERVE_SECOND_HOP_ROLE}:
        return FRONTIER_EXACT1_SUBMODE
    if normalized_role in {"exact0_frontier", "distance_probe", "raw_distance_probe", "frontier_anchor"}:
        return FRONTIER_EXACT0_SUBMODE
    return ""


def _frontier_submode_for_exact(exact_wchars: int) -> str:
    if int(exact_wchars) == 1:
        return FRONTIER_EXACT1_SUBMODE
    return FRONTIER_EXACT0_SUBMODE


def _frontier_submode_from_entry(entry: dict[str, object], *, default_anchor_mode: str = "") -> str:
    explicit = str(entry.get("frontier_submode", "")).strip()
    if explicit:
        return explicit
    role_submode = _frontier_submode_for_role(str(entry.get("frontier_role", "")))
    if role_submode:
        return role_submode
    anchor_mode = str(entry.get("anchor_mode", "")).strip() or str(default_anchor_mode).strip()
    if anchor_mode != FRONTIER_ANCHOR_MODE:
        return ""
    runtime_exact = entry.get("runtime_ci_exact_wchars")
    if runtime_exact is not None and str(runtime_exact).strip():
        return _frontier_submode_for_exact(int(runtime_exact or 0))
    return _frontier_submode_for_exact(int(entry.get("ci_exact_wchars", 0) or 0))


def _append_lineage(anchor_lineage: str, stage_label: str) -> str:
    prefix = str(anchor_lineage).strip()
    if not prefix:
        return stage_label
    return f"{prefix} -> {stage_label}"


def _annotate_entry_context(
    entry: dict[str, object],
    *,
    source_anchor: str,
    frontier_role: str,
    anchor_mode: str,
    anchor_lineage: str,
    frontier_submode: str = "",
) -> dict[str, object]:
    normalized = dict(entry)
    normalized["source_anchor"] = str(source_anchor).strip().lower()
    normalized["frontier_role"] = str(frontier_role).strip()
    normalized["anchor_mode"] = str(anchor_mode).strip()
    normalized["anchor_lineage"] = str(anchor_lineage).strip()
    normalized["frontier_submode"] = (
        str(frontier_submode).strip()
        or _frontier_submode_for_role(frontier_role)
        or _frontier_submode_from_entry(entry, default_anchor_mode=anchor_mode)
    )
    return normalized


def _annotate_entries_context(
    entries: Sequence[dict[str, object]],
    *,
    source_anchor: str,
    frontier_role: str,
    anchor_mode: str,
    stage_label: str,
    anchor_lineage: str = "",
    frontier_submode: str = "",
) -> list[dict[str, object]]:
    base_lineage = str(anchor_lineage).strip() or _lineage_root(
        source_anchor=source_anchor,
        frontier_role=frontier_role,
        anchor_mode=anchor_mode,
    )
    stage_lineage = _append_lineage(base_lineage, stage_label)
    return [
        _annotate_entry_context(
            entry,
            source_anchor=source_anchor,
            frontier_role=frontier_role,
            anchor_mode=anchor_mode,
            anchor_lineage=stage_lineage,
            frontier_submode=frontier_submode,
        )
        for entry in entries
    ]


def _context_by_anchor(*entry_groups: Sequence[dict[str, object]]) -> dict[str, dict[str, object]]:
    context: dict[str, dict[str, object]] = {}
    for entries in entry_groups:
        for entry in entries:
            anchor = _candidate_anchor_from_payload_entry(entry)
            if len(anchor) != 16 or anchor in context:
                continue
            context[anchor] = dict(entry)
    return context


def _annotate_entries_from_context_map(
    entries: Sequence[dict[str, object]],
    *,
    context_map: dict[str, dict[str, object]],
    default_source_anchor: str,
    default_frontier_role: str,
    default_anchor_mode: str,
    stage_label: str,
) -> list[dict[str, object]]:
    annotated: list[dict[str, object]] = []
    for entry in entries:
        anchor = _candidate_anchor_from_payload_entry(entry)
        context = context_map.get(anchor, {})
        source_anchor = str(context.get("source_anchor", "")).strip().lower() or default_source_anchor
        frontier_role = str(context.get("frontier_role", "")).strip() or default_frontier_role
        anchor_mode = str(context.get("anchor_mode", "")).strip() or default_anchor_mode
        frontier_submode = _frontier_submode_from_entry(context, default_anchor_mode=anchor_mode)
        anchor_lineage = str(context.get("anchor_lineage", "")).strip() or _lineage_root(
            source_anchor=source_anchor,
            frontier_role=frontier_role,
            anchor_mode=anchor_mode,
        )
        annotated.append(
            _annotate_entry_context(
                entry,
                source_anchor=source_anchor,
                frontier_role=frontier_role,
                anchor_mode=anchor_mode,
                anchor_lineage=_append_lineage(anchor_lineage, stage_label),
                frontier_submode=frontier_submode,
            )
        )
    return annotated


def _best_compare_agree_frontier_entry(
    validations: Sequence[dict[str, object]],
) -> dict[str, object] | None:
    return min(
        (
            item
            for item in validations
            if bool(item.get("compare_semantics_agree"))
            and int(item.get("runtime_ci_exact_wchars", 0) or 0) <= 1
        ),
        key=lambda item: (
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            int(item.get("offline_raw_distance10", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
        default=None,
    )


def _best_compare_agree_frontier_entry_for_exact(
    validations: Sequence[dict[str, object]],
    exact_wchars: int,
) -> dict[str, object] | None:
    return min(
        (
            item
            for item in validations
            if bool(item.get("compare_semantics_agree"))
            and int(item.get("runtime_ci_exact_wchars", 0) or 0) == int(exact_wchars)
        ),
        key=lambda item: (
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            int(item.get("offline_raw_distance10", item.get("raw_distance10", 1 << 30)) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
        default=None,
    )


def _frontier_runtime_distance(entry: dict[str, object] | None) -> int:
    if not entry:
        return 1 << 30
    return int(entry.get("runtime_ci_distance5", 1 << 30) or (1 << 30))


def _frontier_iteration_converged_reason(
    *,
    validations: Sequence[dict[str, object]],
    previous_best_frontier: dict[str, object] | None,
    current_best_frontier: dict[str, object] | None,
    iteration_index: int,
) -> str:
    if any(
        bool(item.get("compare_semantics_agree"))
        and int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 3
        for item in validations
    ):
        return "runtime_exact3"
    if iteration_index >= FRONTIER_MAX_ITERATIONS:
        return "iteration_limit"
    if _frontier_runtime_distance(current_best_frontier) >= _frontier_runtime_distance(previous_best_frontier):
        return "distance_not_improved"
    return "continue"


def _frontier_offline_improved(
    candidate: dict[str, object],
    baseline: dict[str, object] | None,
    *,
    frontier_submode: str = "",
) -> bool:
    if not baseline:
        return False
    candidate_distance = int(candidate.get("ci_distance5", 1 << 30) or (1 << 30))
    baseline_distance = int(baseline.get("ci_distance5", 1 << 30) or (1 << 30))
    if candidate_distance < baseline_distance:
        return True
    candidate_raw = int(candidate.get("raw_distance10", 1 << 30) or (1 << 30))
    baseline_raw = int(baseline.get("raw_distance10", 1 << 30) or (1 << 30))
    if candidate_distance != baseline_distance or candidate_raw >= baseline_raw:
        return False
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        return int(candidate.get("ci_exact_wchars", 0) or 0) >= int(baseline.get("ci_exact_wchars", 0) or 0)
    return True


def _frontier_runtime_improved(
    candidate: dict[str, object],
    baseline: dict[str, object] | None,
    *,
    frontier_submode: str = "",
) -> bool:
    if not baseline:
        return False
    candidate_distance = int(candidate.get("runtime_ci_distance5", 1 << 30) or (1 << 30))
    baseline_distance = int(baseline.get("runtime_ci_distance5", 1 << 30) or (1 << 30))
    if candidate_distance < baseline_distance:
        return True
    candidate_raw = int(candidate.get("offline_raw_distance10", candidate.get("raw_distance10", 1 << 30)) or (1 << 30))
    baseline_raw = int(baseline.get("offline_raw_distance10", baseline.get("raw_distance10", 1 << 30)) or (1 << 30))
    if candidate_distance != baseline_distance or candidate_raw >= baseline_raw:
        return False
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        return int(candidate.get("runtime_ci_exact_wchars", candidate.get("ci_exact_wchars", 0)) or 0) >= int(
            baseline.get("runtime_ci_exact_wchars", baseline.get("ci_exact_wchars", 0)) or 0
        )
    return True


def _annotate_frontier_improvement_gate(
    entries: Sequence[dict[str, object]],
    *,
    baseline_entry: dict[str, object] | None,
    runtime_baseline_entry: dict[str, object] | None = None,
    frontier_submode: str = "",
) -> list[dict[str, object]]:
    annotated: list[dict[str, object]] = []
    for entry in entries:
        normalized = dict(entry)
        resolved_frontier_submode = str(frontier_submode).strip() or _frontier_submode_from_entry(normalized)
        offline_passed = _frontier_offline_improved(
            normalized,
            baseline_entry,
            frontier_submode=resolved_frontier_submode,
        )
        runtime_passed = _frontier_runtime_improved(
            normalized,
            runtime_baseline_entry,
            frontier_submode=resolved_frontier_submode,
        )
        normalized["improvement_gate_passed"] = bool(offline_passed or runtime_passed)
        normalized["frontier_submode"] = resolved_frontier_submode
        annotated.append(normalized)
    return annotated


def _exact1_pair_escape_signal(
    candidate: dict[str, object],
    baseline_entry: dict[str, object],
    *,
    transform_model: SamplereverseTransformModel,
) -> dict[str, object]:
    candidate_metrics = candidate if "ci_distance5" in candidate and "raw_distance10" in candidate else _entry_metrics(candidate, transform_model)
    baseline_metrics = (
        baseline_entry
        if "ci_distance5" in baseline_entry and "raw_distance10" in baseline_entry
        else _entry_metrics(baseline_entry, transform_model)
    )
    candidate_distance = int(candidate_metrics.get("ci_distance5", 1 << 30) or (1 << 30))
    baseline_distance = int(baseline_metrics.get("ci_distance5", 1 << 30) or (1 << 30))
    candidate_raw = int(candidate_metrics.get("raw_distance10", 1 << 30) or (1 << 30))
    baseline_raw = int(baseline_metrics.get("raw_distance10", 1 << 30) or (1 << 30))
    candidate_exact = int(candidate_metrics.get("ci_exact_wchars", 0) or 0)
    baseline_exact = int(baseline_metrics.get("ci_exact_wchars", 0) or 0)
    candidate_pair_rank = _pair_structure_rank(candidate, transform_model)
    baseline_pair_rank = _pair_structure_rank(baseline_entry, transform_model)
    candidate_bytes = _entry_anchor_bytes(candidate)
    baseline_bytes = _entry_anchor_bytes(baseline_entry)
    locality_score = 0
    local_step_count = 0
    large_step_count = 0
    max_mutation_radius = int(candidate.get("pair_mutation_radius", 0) or 0)
    pair_positions = [int(item) for item in candidate.get("pair_positions", []) if isinstance(item, int) or str(item).isdigit()]
    for position in pair_positions:
        if 0 <= position < len(candidate_bytes) and 0 <= position < len(baseline_bytes):
            delta = abs(candidate_bytes[position] - baseline_bytes[position])
            max_mutation_radius = max(max_mutation_radius, delta)
            if delta <= 4:
                locality_score += 1
                local_step_count += 1
            elif delta >= 16:
                large_step_count += 1
    if any(value > 0 for value in candidate_pair_rank[:3]):
        locality_score += 1
    distance_delta = candidate_distance - baseline_distance
    raw_delta = candidate_raw - baseline_raw
    pair_structure_delta = tuple(int(a) - int(b) for a, b in zip(candidate_pair_rank, baseline_pair_rank))
    pair_structure_gain = sum(max(0, value) for value in pair_structure_delta[:3])
    local_move_score = local_step_count + pair_structure_gain
    hard_escape = (
        candidate_exact < baseline_exact
        and sum(candidate_pair_rank[:3]) == 0
        and locality_score <= 0
        and distance_delta > max(EXACT1_PAIR_DISTANCE_ESCAPE * 4, 96)
        and raw_delta >= 0
    )
    if candidate_distance < baseline_distance:
        score = 0
        reason = "distance_improved"
    elif (
        candidate_distance <= baseline_distance + 8
        and candidate_raw <= baseline_raw + 8
        and local_move_score >= 2
    ):
        score = 2
        reason = "local_escape_within_tight_tolerance"
    elif candidate_distance == baseline_distance and candidate_raw < baseline_raw and candidate_pair_rank > baseline_pair_rank:
        score = 1
        reason = "raw_improved_with_structure"
    elif candidate_distance == baseline_distance and candidate_raw < baseline_raw and locality_score > 0:
        score = 3
        reason = "raw_improved_local_escape"
    elif (
        candidate_distance <= baseline_distance + EXACT1_PAIR_DISTANCE_ESCAPE
        and candidate_raw <= baseline_raw + max(16, EXACT1_PAIR_DISTANCE_ESCAPE)
        and local_move_score >= 2
    ):
        score = 4
        reason = "local_escape_near_anchor"
    elif (
        locality_score > 0
        and local_step_count > 0
        and large_step_count < len(pair_positions)
        and candidate_distance <= baseline_distance + max(EXACT1_PAIR_DISTANCE_ESCAPE * 2, 56)
        and candidate_raw <= baseline_raw + max(EXACT1_PAIR_DISTANCE_ESCAPE * 2, 48)
    ):
        score = 5
        reason = "local_escape_weak_but_acceptable"
    elif hard_escape:
        score = 10
        reason = "hard_escape_far_from_anchor"
    else:
        score = 7
        reason = "local_escape_but_signal_weak" if locality_score > 0 else "hard_escape_signal_weak"
    lane = "hard_escape" if hard_escape else "local_escape"
    status = "reject"
    quality_band = "hard_escape" if lane == "hard_escape" else "rejected_local_escape"
    quality_reason = reason
    if lane == "local_escape":
        if score <= EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX:
            status = "keep"
            quality_band = "kept_local_escape"
        elif score <= EXACT1_PAIR_ESCAPE_BORDERLINE_SCORE_MAX and locality_score > 0:
            status = "borderline"
            if (
                max_mutation_radius <= EXACT1_PAIR_NEAR_LOCAL_RADIUS
                or (
                    distance_delta <= EXACT1_PAIR_NEAR_DISTANCE_SLACK
                    and raw_delta <= EXACT1_PAIR_NEAR_RAW_SLACK
                )
            ):
                quality_band = "near_local_escape"
                quality_reason = "near_radius_or_distance_slack"
            else:
                quality_band = "wide_local_escape"
                quality_reason = "distance_or_radius_too_wide"
    return {
        "passed": status == "keep",
        "status": status,
        "quality_band": quality_band,
        "quality_reason": quality_reason,
        "score": score,
        "reason": reason,
        "lane": lane,
        "distance_delta": distance_delta,
        "raw_delta": raw_delta,
        "locality_score": locality_score,
        "pair_structure_delta": pair_structure_delta,
        "local_step_count": local_step_count,
        "large_step_count": large_step_count,
        "local_move_score": local_move_score,
        "max_mutation_radius": max_mutation_radius,
    }


def _best_runtime_validation_by_anchor(
    validations: Sequence[dict[str, object]],
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in validations:
        anchor = str(item.get("cand8_hex", "")).strip().lower() or str(item.get("candidate_hex", ""))[:16].lower()
        if len(anchor) != 16:
            continue
        grouped.setdefault(anchor, []).append(item)
    return {
        anchor: sorted(entries, key=_runtime_validation_sort_key)[0]
        for anchor, entries in grouped.items()
        if entries
    }


def _bounded_value_pool(
    *,
    base_value: int,
    profile_values: Sequence[int],
    feedback_values: Sequence[int] = (),
    limit: int = GUIDED_POOL_TOP_VALUES,
) -> list[int]:
    ordered = [int(base_value) & 0xFF]
    ordered.extend(int(value) & 0xFF for value in profile_values)
    ordered.extend(int(value) & 0xFF for value in feedback_values)
    deduped: list[int] = []
    seen: set[int] = set()
    for value in ordered:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
        if len(deduped) >= limit:
            break
    return deduped


def _feedback_counts_from_frontier_entries(
    entries: Sequence[dict[str, object]],
) -> dict[int, dict[int, int]]:
    counts: dict[int, dict[int, int]] = {}
    for entry in entries:
        for position, value in zip(
            [int(item) for item in entry.get("pair_positions", []) if isinstance(item, int) or str(item).isdigit()],
            [int(item) & 0xFF for item in entry.get("pair_values", []) if isinstance(item, int) or str(item).isdigit()],
        ):
            counts.setdefault(position, {})
            counts[position][value] = counts[position].get(value, 0) + 1
        triad_positions = [int(item) for item in entry.get("triad_positions", []) if isinstance(item, int) or str(item).isdigit()]
        if len(triad_positions) >= 3:
            triad_value = int(entry.get("triad_value", -1))
            if 0 <= triad_value <= 0xFF:
                position = triad_positions[-1]
                counts.setdefault(position, {})
                counts[position][triad_value] = counts[position].get(triad_value, 0) + 1
    return counts


def _small_perturbation_values(base_value: int, *, radius: int = 2) -> list[int]:
    ordered = [int(base_value) & 0xFF]
    for delta in range(1, radius + 1):
        ordered.append((int(base_value) - delta) & 0xFF)
        ordered.append((int(base_value) + delta) & 0xFF)
    deduped: list[int] = []
    seen: set[int] = set()
    for value in ordered:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _append_exact1_value_origin(
    target: dict[int, list[str]],
    *,
    value: int,
    origin: str,
) -> None:
    normalized_value = int(value) & 0xFF
    bucket = target.setdefault(normalized_value, [])
    if origin not in bucket:
        bucket.append(origin)


def _exact1_projected_local_values(
    *,
    base_value: int,
    raw_value: int,
    radius: int = EXACT1_PROJECTED_STEP_LIMIT,
) -> list[int]:
    base = int(base_value) & 0xFF
    value = int(raw_value) & 0xFF
    signed_delta = value - base
    if signed_delta == 0:
        return []
    direction = 1 if signed_delta > 0 else -1
    projected: list[int] = []
    seen: set[int] = set()
    for step in range(1, max(1, radius) + 1):
        candidate = (base + direction * step) & 0xFF
        if candidate == base or candidate in seen:
            continue
        seen.add(candidate)
        projected.append(candidate)
    return projected


def _exact1_projected_value_quality(
    *,
    distance_delta: int,
    raw_delta: int,
    structure_rank: Sequence[int],
    baseline_rank: Sequence[int],
    step: int,
    origins: Sequence[str],
) -> dict[str, object]:
    structure_delta = tuple(int(a) - int(b) for a, b in zip(structure_rank, baseline_rank))
    structure_penalty = sum(max(0, -int(value)) for value in structure_delta[:3])
    structure_gain = sum(max(0, int(value)) for value in structure_delta[:3])
    origin_priority = _exact1_single_byte_origin_priority(origins)
    local_compatible = (
        distance_delta <= EXACT1_PROJECTED_DISTANCE_SLACK
        and raw_delta <= EXACT1_PROJECTED_RAW_SLACK
        and structure_penalty <= 1
    )
    quality_band = "projected_local_compatible" if local_compatible else "projected_distance_explosive"
    sort_key = (
        0 if local_compatible else 1,
        int(distance_delta),
        int(raw_delta),
        int(structure_penalty),
        int(step),
        int(origin_priority),
        -int(structure_gain),
    )
    return {
        "quality_band": quality_band,
        "structure_delta": structure_delta,
        "structure_penalty": structure_penalty,
        "structure_gain": structure_gain,
        "origin_priority": origin_priority,
        "sort_key": sort_key,
    }


def _exact1_neighbor_value_maps(
    *,
    base_value: int,
    profile_values: Sequence[int],
    incoming_values: Sequence[int],
    lineage_values: Sequence[int],
    projection_details: dict[str, object] | None = None,
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    base = int(base_value) & 0xFF
    preserve_sources: dict[int, list[str]] = {}
    escape_sources: dict[int, list[str]] = {}
    if projection_details is None:
        projection_details = {}
    projection_details.setdefault("raw_source_present_but_too_far", [])
    projection_details.setdefault("projected_local_value_generated", [])
    projection_details.setdefault("projected_values", [])
    projection_details.setdefault("projected_origins", {})
    projection_details.setdefault("projected_direction", {})
    projection_details.setdefault("projected_step", {})

    _append_exact1_value_origin(preserve_sources, value=base, origin="anchor")
    for value in _small_perturbation_values(base, radius=EXACT1_PRESERVE_NEIGHBOR_RADIUS):
        if value == base:
            continue
        _append_exact1_value_origin(preserve_sources, value=value, origin="preserve_neighbor")
    for value in _small_perturbation_values(base, radius=EXACT1_ESCAPE_NEIGHBOR_RADIUS):
        if value == base:
            continue
        if abs(int(value) - base) <= EXACT1_PRESERVE_NEIGHBOR_RADIUS:
            continue
        _append_exact1_value_origin(escape_sources, value=value, origin="escape_neighbor")

    for origin_prefix, values in (
        ("profile", profile_values),
        ("incoming", incoming_values),
        ("lineage", lineage_values),
    ):
        supported_directions: set[str] = set()
        for raw_value in values:
            value = int(raw_value) & 0xFF
            delta = abs(value - base)
            if value == base:
                _append_exact1_value_origin(preserve_sources, value=value, origin=f"{origin_prefix}_anchor")
                continue
            if delta <= EXACT1_PRESERVE_NEIGHBOR_RADIUS:
                _append_exact1_value_origin(preserve_sources, value=value, origin=f"{origin_prefix}_local")
            elif delta <= EXACT1_LOCAL_SOURCE_RADIUS:
                _append_exact1_value_origin(escape_sources, value=value, origin=f"{origin_prefix}_near")
            else:
                supported_directions.add("positive_projection" if value > base else "negative_projection")
                raw_too_far = projection_details.setdefault("raw_source_present_but_too_far", [])
                if value not in raw_too_far:
                    raw_too_far.append(value)
        for direction_label in sorted(supported_directions):
            direction_raw = (base + EXACT1_LOCAL_SOURCE_RADIUS + 1) if direction_label == "positive_projection" else (base - EXACT1_LOCAL_SOURCE_RADIUS - 1)
            projected_values = _exact1_projected_local_values(
                base_value=base,
                raw_value=direction_raw,
                radius=EXACT1_PROJECTED_STEP_LIMIT,
            )
            for step, projected_value in enumerate(projected_values, start=1):
                if step > EXACT1_PROJECTED_STEP_LIMIT:
                    break
                projected_origin = f"{origin_prefix}_projected"
                projected_direction = projection_details.setdefault("projected_direction", {})
                projected_direction[str(projected_value)] = direction_label
                projected_step = projection_details.setdefault("projected_step", {})
                projected_step[str(projected_value)] = int(step)
                projected_generated = projection_details.setdefault("projected_local_value_generated", [])
                if projected_value not in projected_generated:
                    projected_generated.append(projected_value)
                projected_values_bucket = projection_details.setdefault("projected_values", [])
                if projected_value not in projected_values_bucket:
                    projected_values_bucket.append(projected_value)
                projected_origins = projection_details.setdefault("projected_origins", {})
                origin_bucket = projected_origins.setdefault(str(projected_value), [])
                if projected_origin not in origin_bucket:
                    origin_bucket.append(projected_origin)
                _append_exact1_value_origin(
                    escape_sources,
                    value=projected_value,
                    origin=projected_origin,
                )

    for value in list(preserve_sources):
        escape_origins = list(escape_sources.get(value, []))
        if any(str(origin).endswith("_projected") for origin in escape_origins):
            continue
        escape_sources.pop(value, None)

    return preserve_sources, escape_sources


def _exact1_neighbor_value_maps_with_optional_details(
    *,
    base_value: int,
    profile_values: Sequence[int],
    incoming_values: Sequence[int],
    lineage_values: Sequence[int],
    projection_details: dict[str, object],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    try:
        return _exact1_neighbor_value_maps(
            base_value=base_value,
            profile_values=profile_values,
            incoming_values=incoming_values,
            lineage_values=lineage_values,
            projection_details=projection_details,
        )
    except TypeError:
        return _exact1_neighbor_value_maps(
            base_value=base_value,
            profile_values=profile_values,
            incoming_values=incoming_values,
            lineage_values=lineage_values,
        )


def _bounded_exact1_value_map(
    value_origins: dict[int, list[str]],
    *,
    limit: int,
) -> dict[int, list[str]]:
    def _origin_priority(origins: Sequence[str]) -> tuple[int, int, int]:
        normalized = [str(origin) for origin in origins]
        if any(origin.endswith("_projected") for origin in normalized):
            return (0, 0 if any(origin.startswith("lineage_") for origin in normalized) else 1, len(normalized))
        if any(origin.endswith("_near") for origin in normalized):
            return (1, 0 if any(origin.startswith("lineage_") for origin in normalized) else 1, len(normalized))
        if any(origin.startswith("escape_neighbor") for origin in normalized):
            return (2, 0, len(normalized))
        return (3, 0, len(normalized))

    bounded: dict[int, list[str]] = {}
    ordered_items = sorted(
        value_origins.items(),
        key=lambda item: (
            _origin_priority(item[1]),
            int(item[0]) & 0xFF,
        ),
    )
    for value, origins in ordered_items:
        bounded[int(value) & 0xFF] = list(origins)
        if len(bounded) >= limit:
            break
    return bounded


def _diff_positions_for_anchor(candidate_anchor: str, reference_anchor: str) -> list[int]:
    candidate = str(candidate_anchor).strip().lower()
    reference = str(reference_anchor).strip().lower()
    if len(candidate) != 16 or len(reference) != 16:
        return []
    return [
        idx
        for idx in range(8)
        if candidate[idx * 2 : idx * 2 + 2] != reference[idx * 2 : idx * 2 + 2]
    ]


def _locked_pair_positions_for_exact1(
    *,
    base_anchor: str,
    source_anchor: str,
    bridge_entries: Sequence[dict[str, object]],
    pair_profiles: dict[tuple[int, int], list[dict[str, object]]],
) -> tuple[list[tuple[int, int]], dict[str, object]]:
    ranked_pairs = [
        list(pair_positions)
        for pair_positions, entries in sorted(
            pair_profiles.items(),
            key=lambda item: _candidate_hex_from_entry(item[1][0]) if item[1] else "",
        )
        if entries
    ]
    source_diff_positions = _diff_positions_for_anchor(base_anchor, source_anchor)
    bridge_diff_positions: list[int] = []
    for entry in bridge_entries[:BRIDGE_VALIDATE_TOP]:
        bridge_diff_positions.extend(_diff_positions_for_anchor(_candidate_hex_from_entry(entry)[:16], base_anchor))
    ordered_bridge_positions = list(dict.fromkeys(bridge_diff_positions))
    candidate_pairs: list[tuple[int, int]] = []
    if len(source_diff_positions) >= 2:
        for combo in itertools.combinations(source_diff_positions[:4], 2):
            candidate_pairs.append(tuple(sorted(combo)))
    if source_diff_positions and ordered_bridge_positions:
        for source_pos in source_diff_positions[:2]:
            for bridge_pos in ordered_bridge_positions[:3]:
                if source_pos == bridge_pos:
                    continue
                candidate_pairs.append(tuple(sorted((source_pos, bridge_pos))))
    for pair_positions in ranked_pairs[:FRONTIER_TOP_PAIR_LIMIT]:
        if len(pair_positions) == 2:
            candidate_pairs.append(tuple(sorted((int(pair_positions[0]), int(pair_positions[1])))))
    deduped_pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in candidate_pairs:
        if pair in seen:
            continue
        seen.add(pair)
        deduped_pairs.append(pair)
    return deduped_pairs[:EXACT1_PAIR_LOCK_LIMIT], {
        "source_anchor_diff_positions": source_diff_positions,
        "bridge_diff_positions": ordered_bridge_positions,
        "top_ranked_pairs": ranked_pairs[:FRONTIER_TOP_PAIR_LIMIT],
        "candidate_pairs": [list(pair) for pair in deduped_pairs],
    }


def _compact_pair_candidate(entry: dict[str, object]) -> dict[str, object]:
    compact = {
        "candidate_hex": str(entry.get("candidate_hex", "")),
        "cand8_hex": str(entry.get("cand8_hex", "")),
        "ci_exact_wchars": int(entry.get("ci_exact_wchars", 0) or 0),
        "ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
        "raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
        "pair_positions": list(entry.get("pair_positions", [])),
        "pair_values": list(entry.get("pair_values", [])),
        "pair_escape_mode": str(entry.get("pair_escape_mode", "")),
        "pair_drop_reason": str(entry.get("pair_drop_reason", "")),
        "pair_raw_prefix_hex_16": str(entry.get("pair_raw_prefix_hex_16", "")),
        "pair_wide_ascii_contiguous_8": int(entry.get("pair_wide_ascii_contiguous_8", 0) or 0),
        "pair_wide_zero_high_pairs_8": int(entry.get("pair_wide_zero_high_pairs_8", 0) or 0),
        "pair_flaglike_tail_pairs_8": int(entry.get("pair_flaglike_tail_pairs_8", 0) or 0),
        "pair_escape_signal_score": int(entry.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
        "pair_escape_signal_reason": str(entry.get("pair_escape_signal_reason", "")),
        "pair_escape_lane": str(entry.get("pair_escape_lane", "")),
        "pair_escape_status": str(entry.get("pair_escape_status", "")),
        "pair_escape_quality_band": str(entry.get("pair_escape_quality_band", "")),
        "pair_borderline_quality_reason": str(entry.get("pair_borderline_quality_reason", "")),
        "pair_candidate_origin": str(entry.get("pair_candidate_origin", "")),
        "pair_mutation_radius": int(entry.get("pair_mutation_radius", 0) or 0),
        "pair_neighbor_mode": str(entry.get("pair_neighbor_mode", "")),
        "pair_value_origin_by_pos": {
            str(key): list(value)
            for key, value in dict(entry.get("pair_value_origin_by_pos", {})).items()
        },
    }
    if entry.get("pair_projected_winner_available"):
        compact["pair_projected_winner_available"] = [
            dict(item)
            for item in entry.get("pair_projected_winner_available", [])
            if isinstance(item, dict)
        ]
    if entry.get("pair_projected_winner_contributions"):
        compact["pair_projected_winner_contributions"] = [
            dict(item)
            for item in entry.get("pair_projected_winner_contributions", [])
            if isinstance(item, dict)
        ]
    if entry.get("pair_projected_boundary_mix"):
        compact["pair_projected_boundary_mix"] = list(entry.get("pair_projected_boundary_mix", []))
    if entry.get("pair_projected_boundary_role"):
        compact["pair_projected_boundary_role"] = str(entry.get("pair_projected_boundary_role", ""))
    if entry.get("pair_projected_winner_gate_status"):
        compact["pair_projected_winner_gate_status"] = str(entry.get("pair_projected_winner_gate_status", ""))
    return compact


def _projected_winner_gate_status(entry: dict[str, object]) -> str:
    available = [
        item
        for item in entry.get("pair_projected_winner_available", [])
        if isinstance(item, dict)
    ]
    if not available:
        return ""
    contributions = [
        item
        for item in entry.get("pair_projected_winner_contributions", [])
        if isinstance(item, dict)
    ]
    if not contributions:
        pair_positions = [int(item) for item in entry.get("pair_positions", []) if isinstance(item, int) or str(item).isdigit()]
        pair_values = [int(item) & 0xFF for item in entry.get("pair_values", []) if isinstance(item, int) or str(item).isdigit()]
        value_by_position = dict(zip(pair_positions, pair_values))
        base_only = all(
            int(value_by_position.get(int(item.get("position", -1)), -1)) == int(item.get("base_value", -2))
            for item in available
        )
        return "projected_winner_kept_as_base_only" if base_only else "projected_winner_lost_after_pair_metrics"
    quality_band = str(entry.get("pair_escape_quality_band", ""))
    status = str(entry.get("pair_escape_status", ""))
    mix_sources = {str(item.get("paired_source", "")) for item in contributions}
    if quality_band == "near_local_escape":
        return "projected_winner_promoted_to_near_local"
    if quality_band == "wide_local_escape" and (
        "neighbor" in mix_sources or "projected_runner_up" in mix_sources
    ):
        return "projected_winner_mixed_with_neighbor_wide"
    if status == "keep":
        return "projected_winner_reached_pair_gate"
    return "projected_winner_lost_after_pair_metrics"


def _alternate_locked_pair_positions_for_exact1(
    *,
    primary_locked_pairs: Sequence[tuple[int, int]],
    source_details: dict[str, object],
    pair_gate_input_summary: dict[str, list[dict[str, object]]],
) -> tuple[list[tuple[int, int]], dict[str, object]]:
    primary = [tuple(sorted((int(left), int(right)))) for left, right in primary_locked_pairs]
    candidate_pairs = [
        tuple(sorted((int(pair[0]), int(pair[1]))))
        for pair in source_details.get("candidate_pairs", [])
        if isinstance(pair, list) and len(pair) == 2
    ]
    local_escape_counts: dict[tuple[int, int], int] = {}
    for pair_key, rows in pair_gate_input_summary.items():
        try:
            left_s, right_s = pair_key.split(",", 1)
            pair = tuple(sorted((int(left_s), int(right_s))))
        except ValueError:
            continue
        count = sum(1 for row in rows if str(row.get("pair_escape_lane", "")) == "local_escape")
        if count:
            local_escape_counts[pair] = count
    ranked_local_pairs = [
        pair
        for pair, _ in sorted(
            local_escape_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    ordered: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in [*ranked_local_pairs, *candidate_pairs]:
        if pair in seen or pair in primary:
            continue
        seen.add(pair)
        ordered.append(pair)
        if len(ordered) >= EXACT1_PAIR_LOCK_LIMIT:
            break
    if not ordered:
        ordered = list(primary)
    return ordered[:EXACT1_PAIR_LOCK_LIMIT], {
        "primary_locked_pair_positions": [list(pair) for pair in primary],
        "alternate_candidate_pairs": [list(pair) for pair in ordered],
        "local_escape_counts": {f"{left},{right}": count for (left, right), count in local_escape_counts.items()},
    }


def _exact1_pair_set_selection_key(result: dict[str, object]) -> tuple[int, int, int, int, int, int, int, int]:
    diagnostics = dict(result.get("pair_frontier_diagnostics", {}))
    generation_details = dict(result.get("pair_generation_details", {}))
    pair_stage_stats = dict(result.get("pair_stage_stats", {}))
    near_local_candidates = [entry for entry in diagnostics.get("pair_near_local_escape_candidates", []) if isinstance(entry, dict)]
    wide_local_candidates = [entry for entry in diagnostics.get("pair_wide_local_escape_candidates", []) if isinstance(entry, dict)]
    projected_beats_neighbor_count = sum(
        1
        for pair_map in generation_details.get("pair_projected_competitive_status", {}).values()
        if isinstance(pair_map, dict)
        for status in pair_map.values()
        if str(status) == "projected_beats_neighbor"
    )
    best_near_distance = min(
        (int(entry.get("ci_distance5", 1 << 30) or (1 << 30)) for entry in near_local_candidates),
        default=1 << 30,
    )
    best_wide_distance = min(
        (int(entry.get("ci_distance5", 1 << 30) or (1 << 30)) for entry in wide_local_candidates),
        default=1 << 30,
    )
    best_wide_raw = min(
        (int(entry.get("raw_distance10", 1 << 30) or (1 << 30)) for entry in wide_local_candidates),
        default=1 << 30,
    )
    return (
        -projected_beats_neighbor_count,
        -len(near_local_candidates),
        best_near_distance,
        best_wide_distance,
        best_wide_raw,
        int(diagnostics.get("pair_wide_local_escape_count", 0) or 0),
        -int(pair_stage_stats.get("projected_local_compatible_count", 0) or 0),
        -len(diagnostics.get("pair_gate_kept_escape", [])),
    )


def _exact1_projected_competition_summary(
    *,
    pair_stage_stats: dict[str, object],
    pair_set_comparison_summary: dict[str, object] | None = None,
) -> dict[str, object]:
    projected_beats_neighbor_count = int(pair_stage_stats.get("projected_beats_neighbor_count", 0) or 0)
    kept_escape_count = int(pair_stage_stats.get("pair_gate_kept_escape", 0) or 0)
    near_local_escape_count = int(pair_stage_stats.get("pair_near_local_escape_count", 0) or 0)
    wide_local_escape_count = int(pair_stage_stats.get("pair_wide_local_escape_count", 0) or 0)
    pair_set_diagnosis = "pair_set_not_evaluated"
    if isinstance(pair_set_comparison_summary, dict) and pair_set_comparison_summary:
        projected_pair_set_wins = [
            int(details.get("projected_beats_neighbor_count", 0) or 0)
            for details in pair_set_comparison_summary.values()
            if isinstance(details, dict)
        ]
        if projected_pair_set_wins and max(projected_pair_set_wins) <= 0:
            pair_set_diagnosis = "pair_set_not_limiting_single_byte_competition"
        elif projected_pair_set_wins:
            pair_set_diagnosis = "pair_set_rotation_secondary_to_projected_winner"
    stall_reason = "single_byte_projected_competition"
    if projected_beats_neighbor_count > 0:
        if near_local_escape_count > 0 or kept_escape_count > 0:
            stall_reason = "projected_winner_reached_pair_gate"
        elif wide_local_escape_count > 0:
            stall_reason = "pair_refine_after_projected_winner"
        else:
            stall_reason = "pair_gate_after_projected_winner"
    return {
        "stall_reason": stall_reason,
        "pair_set_diagnosis": pair_set_diagnosis,
        "projected_beats_neighbor_count": projected_beats_neighbor_count,
        "pair_gate_kept_escape_count": kept_escape_count,
        "near_local_escape_count": near_local_escape_count,
        "wide_local_escape_count": wide_local_escape_count,
    }


def _exact1_projected_competition_reason_from_runs(runs: Sequence[dict[str, object]]) -> str:
    priority = {
        "pair_refine_after_projected_winner": 0,
        "projected_winner_reached_pair_gate": 1,
        "pair_gate_after_projected_winner": 2,
        "single_byte_projected_competition": 3,
    }
    reasons = [
        str(
            dict(run.get("pair_stage_stats", {}).get("exact1_projected_competition_summary", {})).get("stall_reason", "")
        )
        for run in runs
    ]
    reasons = [reason for reason in reasons if reason]
    if not reasons:
        return ""
    return min(reasons, key=lambda reason: (priority.get(reason, 1 << 30), reason))


def _entry_anchor_bytes(entry: dict[str, object]) -> bytes:
    candidate_hex = _candidate_hex_from_entry(entry)
    if len(candidate_hex) >= 16:
        try:
            return bytes.fromhex(candidate_hex[:16])
        except ValueError:
            return b""
    return b""


def _entry_is_exact1_lineage(entry: dict[str, object], *, source_anchor: str = "") -> bool:
    normalized_source_anchor = str(source_anchor).strip().lower()
    entry_source_anchor = str(entry.get("source_anchor", "")).strip().lower()
    if normalized_source_anchor and entry_source_anchor and entry_source_anchor != normalized_source_anchor:
        return False
    if str(entry.get("frontier_submode", "")).strip() == FRONTIER_EXACT1_SUBMODE:
        return True
    if str(entry.get("frontier_role", "")).strip() == "exact1_frontier":
        return True
    if bool(entry.get("compare_semantics_agree")) and int(entry.get("runtime_ci_exact_wchars", 0) or 0) == 1:
        return True
    return int(entry.get("ci_exact_wchars", 0) or 0) == 1


def _collect_payload_lineage_entries(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
    *,
    source_anchor: str,
    limit: int = 16,
) -> list[dict[str, object]]:
    entries = [
        *_collect_top_entries(payload, transform_model, limit=limit),
        *[
            item
            for item in _collect_validation_entries(payload, transform_model, validate_top=limit)
            if isinstance(item, dict)
        ],
    ]
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        normalized = _normalize_compare_entry(entry, transform_model=transform_model)
        if not normalized or not _entry_is_exact1_lineage(normalized, source_anchor=source_anchor):
            continue
        candidate_hex = _candidate_hex_from_entry(normalized)
        if not candidate_hex or candidate_hex in seen:
            continue
        seen.add(candidate_hex)
        out.append(normalized)
        if len(out) >= limit:
            break
    return out


def _mine_exact1_lineage_value_sources(
    *,
    base_anchor: str,
    source_anchor: str,
    positions: Sequence[int],
    transform_model: SamplereverseTransformModel,
    lineage_entries: Sequence[dict[str, object]] = (),
    pair_frontier_pool: Sequence[dict[str, object]] = (),
    triad_frontier_pool: Sequence[dict[str, object]] = (),
) -> tuple[dict[int, list[int]], dict[int, dict[int, int]], dict[int, list[str]], dict[str, object]]:
    base_bytes = bytes.fromhex(base_anchor)
    source_anchor_bytes = bytes.fromhex(source_anchor) if len(source_anchor) == 16 else b""
    ordered_entries: list[tuple[str, dict[str, object]]] = []
    for origin, entries in (
        ("lineage_context", lineage_entries),
        ("pair_frontier_pool", pair_frontier_pool),
        ("triad_frontier_pool", triad_frontier_pool),
    ):
        for entry in entries:
            if _entry_is_exact1_lineage(entry, source_anchor=source_anchor):
                ordered_entries.append((origin, entry))
    for payload in _recent_compare_aware_payloads():
        for entry in _collect_payload_lineage_entries(
            payload,
            transform_model,
            source_anchor=source_anchor,
            limit=EXACT1_LINEAGE_SOURCE_LIMIT,
        ):
            ordered_entries.append(("recent_payload", entry))

    values_by_position: dict[int, list[int]] = {}
    counts_by_position: dict[int, dict[int, int]] = {}
    origins_by_position: dict[int, dict[int, list[str]]] = {}
    summary: dict[str, object] = {"positions": {}}
    for position in positions:
        pos = int(position)
        if not (0 <= pos < len(base_bytes)):
            continue
        candidates: list[tuple[int, str]] = []
        if len(source_anchor_bytes) == len(base_bytes) and source_anchor_bytes[pos] != base_bytes[pos]:
            candidates.append((source_anchor_bytes[pos], "source_anchor_diff"))
        for origin, entry in ordered_entries:
            anchor_bytes = _entry_anchor_bytes(entry)
            if len(anchor_bytes) != len(base_bytes):
                continue
            if anchor_bytes[pos] == base_bytes[pos]:
                continue
            candidates.append((anchor_bytes[pos], origin))

        ordered_values: list[int] = []
        counts: dict[int, int] = {}
        origins: dict[int, list[str]] = {}
        seen_values: set[int] = set()
        for value, origin in candidates:
            counts[value] = counts.get(value, 0) + 1
            origin_list = origins.setdefault(value, [])
            if origin not in origin_list:
                origin_list.append(origin)
            if value in seen_values:
                continue
            seen_values.add(value)
            ordered_values.append(value)
            if len(ordered_values) >= EXACT1_LINEAGE_SOURCE_LIMIT:
                break
        values_by_position[pos] = ordered_values
        counts_by_position[pos] = counts
        origins_by_position[pos] = origins
        summary["positions"][str(pos)] = {
            "values": ordered_values,
            "counts": {str(key): value for key, value in counts.items()},
            "origins": {str(key): origin_list for key, origin_list in origins.items()},
        }
    return (
        values_by_position,
        counts_by_position,
        {pos: [origin for _, origin_list in origins_by_position[pos].items() for origin in origin_list] for pos in origins_by_position},
        summary,
    )


def _feedback_value_pools_from_frontier_entries(
    *,
    base_anchor: str,
    positions: Sequence[int],
    position_profiles: dict[int, list[dict[str, object]]],
    pair_frontier_pool: Sequence[dict[str, object]],
    triad_frontier_pool: Sequence[dict[str, object]],
    incoming_feedback_value_pools: dict[int, Sequence[int]] | None = None,
    frontier_submode: str = "",
) -> tuple[dict[int, list[int]], dict[str, dict[str, list[int]]]]:
    base_bytes = bytes.fromhex(base_anchor)
    incoming = incoming_feedback_value_pools or {}
    improved_pair_counts = _feedback_counts_from_frontier_entries(
        [entry for entry in pair_frontier_pool if bool(entry.get("improvement_gate_passed"))]
    )
    improved_triad_counts = _feedback_counts_from_frontier_entries(
        [entry for entry in triad_frontier_pool if bool(entry.get("improvement_gate_passed"))]
    )
    pools: dict[int, list[int]] = {}
    sources: dict[str, dict[str, list[int]]] = {}
    for position in positions:
        profile_values = [
            int(entry.get("mutated_byte_value", base_bytes[position])) & 0xFF
            for entry in position_profiles.get(position, [])[:GUIDED_POOL_TOP_VALUES]
        ]
        incoming_values = [int(value) & 0xFF for value in incoming.get(int(position), [])]
        pair_values = [
            value
            for value, _ in sorted(
                improved_pair_counts.get(int(position), {}).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        triad_values = [
            value
            for value, _ in sorted(
                improved_triad_counts.get(int(position), {}).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        exact1_perturbation_values: list[int] = []
        effective_incoming_values = incoming_values
        effective_profile_values = profile_values
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            effective_incoming_values = []
            exact1_perturbation_values = _small_perturbation_values(base_bytes[position], radius=2)
            effective_profile_values = profile_values[:2]
        pools[int(position)] = _bounded_value_pool(
            base_value=base_bytes[position],
            profile_values=[*exact1_perturbation_values, *effective_profile_values],
            feedback_values=[*effective_incoming_values, *pair_values, *triad_values],
        )
        sources[str(position)] = {
            "incoming_feedback": effective_incoming_values[:GUIDED_POOL_TOP_VALUES],
            "improved_pair_values": pair_values[:GUIDED_POOL_TOP_VALUES],
            "improved_triad_values": triad_values[:GUIDED_POOL_TOP_VALUES],
            "small_perturbation_values": exact1_perturbation_values[:GUIDED_POOL_TOP_VALUES],
        }
    return pools, sources


def _improved_frontier_candidates(
    validations: Sequence[dict[str, object]],
    *,
    context_entries: Sequence[dict[str, object]],
    baseline_validations: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    candidates = _frontier_anchor_candidates(validations, context_entries=context_entries)
    if not candidates:
        return []
    baseline_by_anchor = _best_runtime_validation_by_anchor(baseline_validations)
    improved: list[dict[str, object]] = []
    for candidate in candidates:
        if str(candidate.get("frontier_role", "")) == "exact2_seed":
            continue
        source_anchor = str(candidate.get("source_anchor", "")).strip().lower() or str(candidate.get("anchor", "")).strip().lower()
        baseline = baseline_by_anchor.get(source_anchor)
        candidate = dict(candidate)
        candidate["improvement_gate_passed"] = _frontier_runtime_improved(candidate, baseline)
        candidate["improvement_gate_source_anchor"] = source_anchor
        if bool(candidate["improvement_gate_passed"]):
            improved.append(candidate)
    role_order = {"exact1_frontier": 0, "exact0_frontier": 1, "distance_probe": 2}
    improved.sort(
        key=lambda item: (
            role_order.get(str(item.get("frontier_role", "")), 9),
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        )
    )
    return improved[: max(1, FRONTIER_MAX_ANCHORS - 1)]


def _metadata_value(entry: dict[str, object], context: dict[str, object], key: str) -> str:
    value = str(entry.get(key, "")).strip()
    if value:
        return value
    return str(context.get(key, "")).strip()


def _validated_projected_preserve_second_hop_candidates(
    validations: Sequence[dict[str, object]],
    *,
    context_entries: Sequence[dict[str, object]],
    limit: int = max(1, FRONTIER_MAX_ANCHORS - 1),
) -> list[dict[str, object]]:
    context_by_anchor = _context_by_anchor(context_entries)
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in sorted(validations, key=_runtime_validation_sort_key):
        if not bool(entry.get("compare_semantics_agree")):
            continue
        anchor = str(entry.get("cand8_hex", "")).strip().lower() or str(entry.get("candidate_hex", ""))[:16].lower()
        if len(anchor) != 16 or anchor in seen:
            continue
        if int(entry.get("runtime_ci_exact_wchars", 0) or 0) >= 2:
            continue
        context = context_by_anchor.get(anchor, {})
        frontier_role = _metadata_value(entry, context, "frontier_role")
        pair_origin = _metadata_value(entry, context, "pair_candidate_origin")
        boundary_role = _metadata_value(entry, context, "pair_projected_boundary_role")
        gate_status = _metadata_value(entry, context, "pair_projected_winner_gate_status")
        if frontier_role != "projected_preserve_handoff" and pair_origin != "exact1_projected_preserve_lane":
            continue
        if boundary_role != "projected_winner_with_base":
            continue
        if gate_status != "projected_winner_promoted_to_near_local":
            continue
        source_anchor = (
            str(entry.get("source_anchor", "")).strip().lower()
            or str(context.get("source_anchor", "")).strip().lower()
            or anchor
        )
        base_lineage = (
            str(entry.get("anchor_lineage", "")).strip()
            or str(context.get("anchor_lineage", "")).strip()
            or _lineage_root(
                source_anchor=source_anchor,
                frontier_role=frontier_role or "projected_preserve_handoff",
                anchor_mode=FRONTIER_ANCHOR_MODE,
            )
        )
        candidate = dict(context)
        candidate.update({key: value for key, value in entry.items() if value not in ("", None, [])})
        candidate.update(
            {
                "anchor": anchor,
                "cand8_hex": anchor,
                "frontier_role": PROJECTED_PRESERVE_SECOND_HOP_ROLE,
                "anchor_mode": FRONTIER_ANCHOR_MODE,
                "frontier_submode": FRONTIER_EXACT1_SUBMODE,
                "source_anchor": source_anchor,
                "anchor_lineage": _append_lineage(base_lineage, "second-hop(projected-preserve)"),
                "second_hop_source_role": frontier_role or pair_origin,
                "second_hop_reason": "validated_projected_preserve_handoff_no_runtime_gain",
                "improvement_gate_passed": False,
            }
        )
        selected.append(candidate)
        seen.add(anchor)
        if len(selected) >= limit:
            break
    return selected


def _frontier_continuation_candidates(
    *,
    improved_frontier_candidates: Sequence[dict[str, object]],
    second_hop_frontier_candidates: Sequence[dict[str, object]],
    frontier_converged_reason: str,
    iteration_index: int,
) -> tuple[list[dict[str, object]], str, bool]:
    if improved_frontier_candidates:
        return list(improved_frontier_candidates), frontier_converged_reason, False
    if (
        frontier_converged_reason == "distance_not_improved"
        and iteration_index < FRONTIER_MAX_ITERATIONS
        and second_hop_frontier_candidates
    ):
        return list(second_hop_frontier_candidates), "continue", True
    return [], frontier_converged_reason, False


def _runtime_prefix_hex(compare_payload: dict[str, object], prefix_bytes: int) -> str:
    explicit_key = f"runtime_lhs_prefix_hex_{prefix_bytes}"
    explicit_value = str(compare_payload.get(explicit_key, "")).strip().lower()
    if explicit_value:
        return explicit_value[: prefix_bytes * 2]
    lhs_wide_hex = str(compare_payload.get("lhs_wide_hex", "")).strip().lower()
    return lhs_wide_hex[: prefix_bytes * 2]


def _bridge_sort_key(entry: dict[str, object]) -> tuple[int, int, int, int, str]:
    candidate_hex = _candidate_hex_from_entry(entry)
    return (
        -int(entry.get("exact", 0) or 0),
        int(entry.get("dist4", 1 << 30) or (1 << 30)),
        int(entry.get("dist6", 1 << 30) or (1 << 30)),
        int(entry.get("dist10", 1 << 30) or (1 << 30)),
        candidate_hex,
    )


def _bridge_entry_is_better(candidate: dict[str, object], current_best: dict[str, object]) -> bool:
    return _bridge_sort_key(candidate) < _bridge_sort_key(current_best)


def _normalize_compare_entry(
    entry: dict[str, object],
    *,
    transform_model: SamplereverseTransformModel,
) -> dict[str, object]:
    candidate_hex = _candidate_hex_from_entry(entry)
    if not candidate_hex:
        return {}
    metrics = _entry_metrics(entry, transform_model)
    payload = {
        **entry,
        **metrics,
        "candidate_hex": candidate_hex,
        "cand8_hex": candidate_hex[:16],
        "raw_prefix_hex": str(metrics["raw_prefix_hex"]),
        "raw_prefix_hex_64": str(metrics["raw_prefix_hex_64"]),
    }
    return payload


def _collect_top_entries(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
    limit: int = 32,
) -> list[dict[str, object]]:
    entries = payload.get("top_entries", [])
    if not isinstance(entries, list):
        best = payload.get("best", {})
        entries = [best] if isinstance(best, dict) and best else []
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_compare_entry(entry, transform_model=transform_model)
        if not normalized:
            continue
        candidate_hex = str(normalized["candidate_hex"])
        if candidate_hex in seen:
            continue
        seen.add(candidate_hex)
        out.append(normalized)
    out.sort(key=lambda item: _candidate_sort_key(item, transform_model))
    return out[:limit]


def _collect_validation_entries(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
    validate_top: int,
) -> list[dict[str, object]]:
    explicit_candidates = payload.get("validation_candidates", [])
    if isinstance(explicit_candidates, list) and explicit_candidates:
        ranked = []
        seen: set[str] = set()
        for idx, entry in enumerate(explicit_candidates, 1):
            if not isinstance(entry, dict):
                continue
            normalized = _normalize_compare_entry(entry, transform_model=transform_model)
            if not normalized:
                continue
            candidate_hex = str(normalized["candidate_hex"])
            if candidate_hex in seen:
                continue
            seen.add(candidate_hex)
            ranked.append({"label": f"top{idx}", **normalized})
            if len(ranked) >= validate_top:
                break
        return ranked

    ranked = _collect_top_entries(payload, transform_model, limit=64)
    if not ranked:
        return []
    qualified = [item for item in ranked if int(item.get("ci_exact_wchars", 0)) >= 2]
    if qualified:
        best_distance = min(int(item.get("ci_distance5", 1 << 30)) for item in qualified)
    else:
        best_distance = min(int(item.get("ci_distance5", 1 << 30)) for item in ranked)
    out: list[dict[str, object]] = []
    for idx, entry in enumerate(ranked, 1):
        if int(entry.get("ci_exact_wchars", 0)) < 2:
            continue
        if int(entry.get("ci_distance5", 1 << 30)) > best_distance:
            continue
        out.append({"label": f"top{idx}", **entry})
        if len(out) >= validate_top:
            break
    return out


def _diverse_validation_candidates(
    entries: Sequence[dict[str, object]],
    *,
    transform_model: SamplereverseTransformModel,
    validate_top: int,
) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    seen_ranked: set[str] = set()
    for entry in _collect_top_entries({"top_entries": list(entries)}, transform_model, limit=64):
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen_ranked:
            continue
        seen_ranked.add(candidate_hex)
        ranked.append(entry)
    if not ranked:
        return []

    selected: list[dict[str, object]] = []
    seen: set[str] = set()

    def _push(entry: dict[str, object], frontier_role: str = "") -> None:
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen or len(selected) >= validate_top:
            return
        seen.add(candidate_hex)
        normalized = _normalize_compare_entry(entry, transform_model=transform_model)
        normalized["frontier_role"] = frontier_role
        selected.append(normalized)

    ranked_by_distance = sorted(
        ranked,
        key=lambda item: (
            int(item.get("ci_distance5", 1 << 30) or (1 << 30)),
            int(item.get("raw_distance10", 1 << 30) or (1 << 30)),
            -int(item.get("ci_exact_wchars", 0) or 0),
            _candidate_hex_from_entry(item),
        ),
    )
    ranked_by_raw = sorted(
        ranked,
        key=lambda item: (
            int(item.get("raw_distance10", 1 << 30) or (1 << 30)),
            int(item.get("ci_distance5", 1 << 30) or (1 << 30)),
            -int(item.get("ci_exact_wchars", 0) or 0),
            _candidate_hex_from_entry(item),
        ),
    )

    _push(ranked[0], "best_overall")
    for exact_target in (2, 1, 0):
        for entry in ranked:
            if int(entry.get("ci_exact_wchars", 0) or 0) == exact_target:
                role = {2: "exact2_seed", 1: "exact1_frontier", 0: "exact0_frontier"}[exact_target]
                _push(entry, role)
                break
    _push(ranked_by_distance[0], "distance_probe")
    _push(ranked_by_raw[0], "raw_distance_probe")
    for entry in ranked_by_distance:
        _push(entry, str(entry.get("frontier_role", "")))
        if len(selected) >= validate_top:
            break
    return selected[:validate_top]


def _frontier_guided_validation_candidates(
    guided_entries: Sequence[dict[str, object]],
    pair_frontier_pool: Sequence[dict[str, object]],
    *,
    validate_top: int,
) -> list[dict[str, object]]:
    selected = [dict(entry) for entry in guided_entries[: max(0, validate_top)]]
    if validate_top <= 0:
        return selected
    handoff = next(
        (
            item
            for item in pair_frontier_pool
            if str(item.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane"
            and str(item.get("pair_projected_boundary_role", "")) == "projected_winner_with_base"
            and str(item.get("pair_projected_winner_gate_status", "")) == "projected_winner_promoted_to_near_local"
        ),
        None,
    )
    if not handoff:
        return selected
    handoff_hex = _candidate_hex_from_entry(handoff)
    if not handoff_hex:
        return selected
    selected_hexes = {_candidate_hex_from_entry(item) for item in selected}
    if handoff_hex in selected_hexes:
        return selected
    handoff_entry = dict(handoff)
    handoff_entry.setdefault("frontier_role", "projected_preserve_handoff")
    if len(selected) < validate_top:
        selected.append(handoff_entry)
    else:
        selected[-1] = handoff_entry
    return selected


def _frontier_role_for_runtime_validation(entry: dict[str, object]) -> str:
    runtime_exact = int(entry.get("runtime_ci_exact_wchars", 0) or 0)
    if runtime_exact >= 2:
        return "exact2_seed"
    if runtime_exact == 1:
        return "exact1_frontier"
    return "exact0_frontier"


def _frontier_anchor_candidates(
    validations: Sequence[dict[str, object]],
    *,
    context_entries: Sequence[dict[str, object]] = (),
) -> list[dict[str, object]]:
    compare_agree = [item for item in validations if bool(item.get("compare_semantics_agree"))]
    if not compare_agree:
        return []

    context_by_anchor: dict[str, dict[str, object]] = {}
    for item in context_entries:
        anchor = str(item.get("cand8_hex", "")).strip().lower() or str(item.get("candidate_hex", ""))[:16].lower()
        if len(anchor) == 16 and anchor not in context_by_anchor:
            context_by_anchor[anchor] = item

    buckets: list[tuple[str, dict[str, object] | None]] = []
    ranked = sorted(compare_agree, key=_runtime_validation_sort_key)
    exact2 = next((item for item in ranked if int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 2), None)
    exact1 = next((item for item in ranked if int(item.get("runtime_ci_exact_wchars", 0) or 0) == 1), None)
    exact0 = min(
        (item for item in ranked if int(item.get("runtime_ci_exact_wchars", 0) or 0) == 0),
        key=lambda item: (
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
        default=None,
    )
    buckets.extend(
        [
            ("exact2_seed", exact2),
            ("exact1_frontier", exact1),
            ("exact0_frontier", exact0),
            ("distance_probe", ranked[0] if ranked else None),
        ]
    )

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for role, item in buckets:
        if not item:
            continue
        anchor = str(item.get("cand8_hex", "")).strip().lower() or str(item.get("candidate_hex", ""))[:16].lower()
        if len(anchor) != 16 or anchor in seen:
            continue
        seen.add(anchor)
        context = context_by_anchor.get(anchor, {})
        source_anchor = str(item.get("source_anchor", "")).strip().lower() or str(context.get("source_anchor", "")).strip().lower() or anchor
        anchor_mode = str(item.get("anchor_mode", "")).strip() or str(context.get("anchor_mode", "")).strip()
        if not anchor_mode:
            anchor_mode = EXACT2_ANCHOR_MODE if role == "exact2_seed" else FRONTIER_ANCHOR_MODE
        frontier_submode = (
            _frontier_submode_from_entry(item, default_anchor_mode=anchor_mode)
            or _frontier_submode_from_entry(context, default_anchor_mode=anchor_mode)
            or _frontier_submode_for_role(role)
        )
        anchor_lineage = str(item.get("anchor_lineage", "")).strip() or str(context.get("anchor_lineage", "")).strip()
        if not anchor_lineage:
            anchor_lineage = _lineage_root(
                source_anchor=source_anchor,
                frontier_role=role,
                anchor_mode=anchor_mode,
            )
        out.append(
            {
                "anchor": anchor,
                "frontier_role": role,
                "candidate_hex": str(item.get("candidate_hex", "")).strip().lower(),
                "runtime_ci_exact_wchars": int(item.get("runtime_ci_exact_wchars", 0) or 0),
                "runtime_ci_distance5": int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
                "compare_semantics_agree": bool(item.get("compare_semantics_agree")),
                "source_anchor": source_anchor,
                "anchor_mode": anchor_mode,
                "frontier_submode": frontier_submode,
                "anchor_lineage": anchor_lineage,
            }
        )
        if len(out) >= FRONTIER_MAX_ANCHORS:
            break
    return out


def _compile_c_tool(source_path: Path, binary_path: Path, log) -> Path:
    gcc_path = shutil.which("gcc")
    if not source_path.exists():
        raise RuntimeError(f"compare-aware tool source missing: {source_path}")
    if not gcc_path:
        raise RuntimeError("gcc not found in PATH")
    if binary_path.exists() and binary_path.stat().st_mtime >= source_path.stat().st_mtime:
        return binary_path
    log(f"编译 compare-aware 工具: {binary_path.name}")
    proc = subprocess.run(
        [gcc_path, "-O3", str(source_path), "-o", str(binary_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "gcc failed").strip())
    return binary_path


def compile_compare_aware_refine(log) -> Path:
    source_path = _repo_root() / "tools" / "samplereverse_exact4_refine_prefix8_len15.c"
    binary_path = _repo_root() / "tools" / "samplereverse_exact4_refine_prefix8_len15.exe"
    return _compile_c_tool(source_path, binary_path, log)


def _compile_bridge_tool(tool_name: str, log) -> Path:
    return _compile_c_tool(_tool_source_path(tool_name), _tool_binary_path(tool_name), log)


def _parse_final_result_line(stdout: str) -> dict[str, object]:
    final_line = ""
    for line in stdout.splitlines():
        if line.startswith("FINAL "):
            final_line = line.strip()
    if not final_line:
        raise RuntimeError("bridge tool did not emit FINAL line")
    match = FINAL_LINE_RE.match(final_line)
    if not match:
        raise RuntimeError(f"unable to parse bridge FINAL line: {final_line}")
    combo = [
        int(token.strip())
        for token in str(match.group("combo") or "").split(",")
        if token.strip()
    ]
    candidate_hex = str(match.group("cand")).lower()
    if len(candidate_hex) == 16:
        candidate_hex = f"{candidate_hex}{DEFAULT_FIXED_SUFFIX_HEX}"
    return {
        "candidate_hex": candidate_hex,
        "cand8_hex": candidate_hex[:16],
        "raw_prefix_hex": str(match.group("raw")).lower(),
        "exact": int(match.group("exact")),
        "dist4": int(match.group("dist4")),
        "dist6": int(match.group("dist6")),
        "dist10": int(match.group("dist10")),
        "combo": combo,
    }


def _run_command(command: list[str], log_path: Path, error_prefix: str) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.write_text(
        f"[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or error_prefix).strip())
    return proc.stdout or ""


def _run_pairscan_tool(
    *,
    artifacts_dir: Path,
    base_anchor: str,
    positions: tuple[int, int],
    log,
) -> dict[str, object]:
    binary_path = _compile_bridge_tool(PAIRSCAN_TOOL, log)
    log_path = artifacts_dir / f"pairscan_{positions[0]}_{positions[1]}.log"
    stdout = _run_command(
        [str(binary_path), str(positions[0]), str(positions[1]), base_anchor],
        log_path,
        "pairscan failed",
    )
    entry = _parse_final_result_line(stdout)
    entry.update(
        {
            "stage": "pairscan",
            "base_anchor": base_anchor,
            "positions_or_nibbles": [positions[0], positions[1]],
        }
    )
    return entry


def _run_triad_tool(
    *,
    artifacts_dir: Path,
    base_anchor: str,
    positions: tuple[int, int, int],
    log,
) -> dict[str, object]:
    binary_path = _compile_bridge_tool(TRIAD_TOOL, log)
    log_path = artifacts_dir / f"triad_{positions[0]}_{positions[1]}_{positions[2]}.log"
    stdout = _run_command(
        [str(binary_path), str(positions[0]), str(positions[1]), str(positions[2]), base_anchor],
        log_path,
        "triad search failed",
    )
    entry = _parse_final_result_line(stdout)
    entry.update(
        {
            "stage": "triad",
            "base_anchor": base_anchor,
            "positions_or_nibbles": [positions[0], positions[1], positions[2]],
        }
    )
    return entry


def _run_quartet_tool(
    *,
    artifacts_dir: Path,
    base_anchor: str,
    log,
) -> dict[str, object]:
    binary_path = _compile_bridge_tool(QUAD_TOOL, log)
    out_path = artifacts_dir / f"quartet_{base_anchor}.json"
    log_path = artifacts_dir / f"quartet_{base_anchor}.log"
    _run_command(
        [
            str(binary_path),
            "--base",
            base_anchor,
            "--input-len",
            str(INPUT_LENGTH),
            "--out-json",
            str(out_path),
        ],
        log_path,
        "quartet search failed",
    )
    return json.loads(out_path.read_text(encoding="utf-8"))


def _run_quint_fixed_tool(
    *,
    artifacts_dir: Path,
    base_anchor: str,
    nibbles: Sequence[int],
    log,
) -> dict[str, object]:
    binary_path = _compile_bridge_tool(QUINT_FIXED_TOOL, log)
    log_path = artifacts_dir / f"quint_{base_anchor}_{'_'.join(str(item) for item in nibbles)}.log"
    stdout = _run_command(
        [
            str(binary_path),
            "--base",
            base_anchor,
            "--input-len",
            str(INPUT_LENGTH),
            *[str(item) for item in nibbles],
        ],
        log_path,
        "quint search failed",
    )
    entry = _parse_final_result_line(stdout)
    entry.update(
        {
            "stage": "quint",
            "base_anchor": base_anchor,
            "positions_or_nibbles": list(nibbles),
        }
    )
    return entry


def run_compare_aware_refine(
    *,
    artifacts_dir: Path,
    search_budget: int,
    seed: int,
    anchors: list[str],
    snapshot_interval: int,
    log,
) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    binary_path = compile_compare_aware_refine(log)
    out_path = artifacts_dir / RESULT_FILE_NAME
    log_path = artifacts_dir / RESULT_LOG_FILE_NAME
    command = [
        str(binary_path),
        "--out-json",
        str(out_path),
        "--max-evals",
        str(search_budget),
        "--seed",
        str(seed),
        "--snapshot-interval",
        str(snapshot_interval),
    ]
    for anchor in anchors:
        command.extend(["--anchor", anchor])
    log(f"运行 compare-aware refine: budget={search_budget} seed={seed}")
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path.write_text(
        f"[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}",
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "compare-aware refine failed").strip())
    if not out_path.exists():
        raise RuntimeError(f"compare-aware refine did not produce result json: {out_path}")
    return out_path


def validate_compare_aware_results(
    *,
    target: Path,
    artifacts_dir: Path,
    result_path: Path,
    transform_model: SamplereverseTransformModel,
    validate_top: int,
    per_probe_timeout: float,
    log,
    output_file_name: str = VALIDATION_FILE_NAME,
    compare_output_prefix: str = "samplereverse_compare_aware_compare",
    capture_prefix_bytes: int = RUNTIME_PREFIX_BYTES,
) -> tuple[Path, list[dict[str, object]]]:
    compare_probe_script = _compare_probe_script_path()
    if not compare_probe_script.exists():
        raise RuntimeError(f"compare probe script missing: {compare_probe_script}")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    validation_entries = _collect_validation_entries(payload, transform_model, validate_top)
    summary: dict[str, object] = {
        "target": str(target),
        "result_path": str(result_path),
        "validation_gate": {
            "min_ci_exact_wchars": min(
                (int(item.get("ci_exact_wchars", 0)) for item in validation_entries),
                default=0,
            ),
            "max_ci_distance5": min(
                (int(item.get("ci_distance5", 1 << 30)) for item in validation_entries),
                default=1 << 30,
            ),
        },
        "validations": [],
    }
    for idx, entry in enumerate(validation_entries, 1):
        candidate_hex = str(entry["candidate_hex"])
        compare_out = artifacts_dir / f"{compare_output_prefix}_{idx}.json"
        compare_log = artifacts_dir / f"{compare_output_prefix}_{idx}.log"
        offline_ci_exact_wchars = int(entry.get("ci_exact_wchars", 0))
        offline_ci_distance5 = int(entry.get("ci_distance5", 1 << 30))
        raw_prefix_hex = str(entry.get("raw_prefix_hex", "")).strip().lower()
        command = [
            sys.executable,
            str(compare_probe_script),
            "--target",
            str(target),
            "--out",
            str(compare_out),
            "--probe-hex",
            candidate_hex,
            "--offline-ci-exact-wchars",
            str(offline_ci_exact_wchars),
            "--offline-ci-distance5",
            str(offline_ci_distance5),
            "--offline-raw-prefix-hex",
            raw_prefix_hex,
            "--per-probe-timeout",
            str(per_probe_timeout),
        ]
        normalized_capture_prefix_bytes = max(10, int(capture_prefix_bytes or 10))
        if normalized_capture_prefix_bytes != 10:
            command.extend(["--capture-prefix-bytes", str(normalized_capture_prefix_bytes)])
        log(f"CompareProbe 回归 compare-aware 候选 {idx}: {candidate_hex}")
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        compare_log.write_text(
            f"[stdout]\n{proc.stdout or ''}\n\n[stderr]\n{proc.stderr or ''}",
            encoding="utf-8",
        )
        record: dict[str, object] = {
            "label": entry.get("label", f"top{idx}"),
            "candidate_hex": candidate_hex,
            "cand8_hex": str(entry.get("cand8_hex", "")),
            "offline_ci_exact_wchars": offline_ci_exact_wchars,
            "offline_ci_distance5": offline_ci_distance5,
            "offline_raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
            "offline_raw_prefix_hex": raw_prefix_hex,
            "offline_raw_prefix_hex_64": str(entry.get("raw_prefix_hex_64", "")),
            "wide_ascii_contiguous_16": int(entry.get("wide_ascii_contiguous_16", 0) or 0),
            "wide_ascii_total_16": int(entry.get("wide_ascii_total_16", 0) or 0),
            "wide_zero_high_pairs_16": int(entry.get("wide_zero_high_pairs_16", 0) or 0),
            "flaglike_tail_pairs_16": int(entry.get("flaglike_tail_pairs_16", 0) or 0),
            "frontier_role": str(entry.get("frontier_role", "")),
            "compare_result_path": str(compare_out),
            "compare_log_path": str(compare_log),
            "stage": entry.get("stage", ""),
            "positions_or_nibbles": entry.get("positions_or_nibbles", []),
            "base_anchor": entry.get("base_anchor", ""),
            "source_anchor": str(entry.get("source_anchor", "")),
            "anchor_mode": str(entry.get("anchor_mode", "")),
            "anchor_lineage": str(entry.get("anchor_lineage", "")),
            "pair_positions": list(entry.get("pair_positions", [])),
            "triad_positions": list(entry.get("triad_positions", [])),
        }
        if compare_out.exists():
            compare_payload = json.loads(compare_out.read_text(encoding="utf-8"))
            runtime_lhs_prefix_hex_10 = _runtime_prefix_hex(compare_payload, 10)
            runtime_lhs_prefix_hex_16 = _runtime_prefix_hex(compare_payload, 16)
            runtime_lhs_prefix_hex = str(
                compare_payload.get("runtime_lhs_prefix_hex")
                or _runtime_prefix_hex(compare_payload, RUNTIME_PREFIX_BYTES)
                or runtime_lhs_prefix_hex_16
                or runtime_lhs_prefix_hex_10
            ).strip().lower()
            runtime_ci_exact_wchars = int(
                compare_payload.get("runtime_ci_exact_wchars")
                or score_compare_prefix(bytes.fromhex(runtime_lhs_prefix_hex_10)).get("ci_exact_wchars", 0)
            )
            runtime_ci_distance5 = int(
                compare_payload.get("runtime_ci_distance5")
                or score_compare_prefix(bytes.fromhex(runtime_lhs_prefix_hex_10)).get("ci_distance5", 1 << 30)
            )
            compare_semantics_agree = bool(
                compare_payload.get("compare_semantics_agree")
                if compare_payload.get("compare_semantics_agree") is not None
                else runtime_lhs_prefix_hex_10 == raw_prefix_hex
            )
            record.update(
                {
                    "compare_summary": compare_payload.get("summary", ""),
                    "runtime_lhs_prefix_hex": runtime_lhs_prefix_hex,
                    "runtime_lhs_prefix_hex_10": runtime_lhs_prefix_hex_10,
                    "runtime_lhs_prefix_hex_16": runtime_lhs_prefix_hex_16,
                    "runtime_lhs_prefix_bytes_captured": compare_payload.get("runtime_lhs_prefix_bytes_captured"),
                    "runtime_ci_exact_wchars": runtime_ci_exact_wchars,
                    "runtime_ci_distance5": runtime_ci_distance5,
                    "compare_semantics_agree": compare_semantics_agree,
                    "matched_target_prefix": runtime_ci_exact_wchars >= 5,
                }
            )
            record["prefix_boundary"] = _prefix_boundary_breakdown_from_prefix(
                bytes.fromhex(runtime_lhs_prefix_hex_10[: len(TARGET_PREFIX) * 2]),
                candidate_hex=candidate_hex,
                label=str(record.get("label", f"top{idx}")),
                source="runtime_compare",
                transform_model=transform_model,
            )
        else:
            record.update(
                {
                    "compare_summary": f"compare probe failed with exit code {proc.returncode}",
                    "runtime_lhs_prefix_hex": "",
                    "runtime_lhs_prefix_hex_10": "",
                    "runtime_lhs_prefix_hex_16": "",
                    "runtime_lhs_prefix_bytes_captured": 0,
                    "runtime_ci_exact_wchars": 0,
                    "runtime_ci_distance5": 1 << 30,
                    "compare_semantics_agree": False,
                    "matched_target_prefix": False,
                }
            )
            record["prefix_boundary"] = _prefix_boundary_breakdown_from_entry(
                record,
                transform_model=transform_model,
                source="offline_entry",
            )
        summary["validations"].append(record)

    validation_path = artifacts_dir / output_file_name
    validation_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return validation_path, list(summary["validations"])


def _unique_candidate_entries(entries: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        candidate_hex = _candidate_hex_from_entry(entry)
        if not candidate_hex or candidate_hex in seen:
            continue
        seen.add(candidate_hex)
        out.append({**entry, "candidate_hex": candidate_hex, "cand8_hex": candidate_hex[:16]})
    out.sort(key=_bridge_sort_key)
    return out


def _extract_hot_positions(pair_entries: Sequence[dict[str, object]], max_positions: int = HOT_POSITION_LIMIT) -> list[int]:
    scores: dict[int, list[int]] = {}
    for rank, entry in enumerate(pair_entries[:BRIDGE_VALIDATE_TOP], 1):
        positions = [int(item) for item in entry.get("positions_or_nibbles", [])][:2]
        for position in positions:
            scores.setdefault(position, []).append(rank)
    ordered = sorted(
        scores.items(),
        key=lambda item: (-len(item[1]), tuple(item[1]), item[0]),
    )
    return [position for position, _ in ordered[:max_positions]]


def _diff_nibbles(base_anchor: str, candidate_hex: str) -> list[int]:
    base = bytes.fromhex(base_anchor[:16])
    candidate = bytes.fromhex(candidate_hex[:16])
    out: list[int] = []
    for idx in range(7):
        base_hi, base_lo = (base[idx] >> 4) & 0x0F, base[idx] & 0x0F
        cand_hi, cand_lo = (candidate[idx] >> 4) & 0x0F, candidate[idx] & 0x0F
        if base_hi != cand_hi:
            out.append(idx * 2)
        if base_lo != cand_lo:
            out.append(idx * 2 + 1)
    if ((base[7] >> 4) & 0x0F) != ((candidate[7] >> 4) & 0x0F):
        out.append(14)
    return out


def _extract_hot_nibbles(
    triad_entries: Sequence[dict[str, object]],
    *,
    base_anchor: str,
    max_nibbles: int = HOT_NIBBLE_LIMIT,
) -> list[int]:
    scores: dict[int, list[int]] = {}
    for rank, entry in enumerate(triad_entries[:TRIAD_SEED_LIMIT], 1):
        for nibble_idx in _diff_nibbles(base_anchor, str(entry.get("candidate_hex", ""))):
            scores.setdefault(nibble_idx, []).append(rank)
    ordered = sorted(
        scores.items(),
        key=lambda item: (-len(item[1]), tuple(item[1]), item[0] % 2, item[0]),
    )
    return [nibble for nibble, _ in ordered[:max_nibbles]]


def _coerce_bridge_entry(
    entry: dict[str, object],
    *,
    stage: str,
    base_anchor: str,
    positions_or_nibbles: Sequence[int],
    transform_model: SamplereverseTransformModel,
) -> dict[str, object]:
    candidate_hex = _candidate_hex_from_entry(entry)
    if not candidate_hex:
        return {}
    payload = {
        "stage": stage,
        "base_anchor": base_anchor,
        "positions_or_nibbles": list(positions_or_nibbles),
        "candidate_hex": candidate_hex,
        "cand8_hex": candidate_hex[:16],
        "raw_prefix_hex": str(entry.get("raw_prefix_hex") or entry.get("raw", "")).strip().lower(),
        "exact": int(entry.get("exact", 0) or 0),
        "dist4": int(entry.get("dist4", 1 << 30) or (1 << 30)),
        "dist6": int(entry.get("dist6", 1 << 30) or (1 << 30)),
        "dist10": int(entry.get("dist10", 1 << 30) or (1 << 30)),
    }
    payload.update(_entry_metrics(payload, transform_model))
    return payload


def _select_rows_for_hot_nibbles(
    rows: Sequence[dict[str, object]],
    *,
    selected_nibbles: Sequence[int],
    stage: str,
    base_anchor: str,
    transform_model: SamplereverseTransformModel,
) -> list[dict[str, object]]:
    selected = set(int(item) for item in selected_nibbles)
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        combo = [int(item) for item in row.get("combo", [])]
        if combo and selected and not set(combo).issubset(selected):
            continue
        normalized_row = _coerce_bridge_entry(
            row,
            stage=stage,
            base_anchor=base_anchor,
            positions_or_nibbles=combo,
            transform_model=transform_model,
        )
        if normalized_row:
            normalized.append(normalized_row)
    if normalized:
        normalized.sort(key=_bridge_sort_key)
        return normalized

    fallback: list[dict[str, object]] = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        combo = [int(item) for item in row.get("combo", [])]
        normalized_row = _coerce_bridge_entry(
            row,
            stage=stage,
            base_anchor=base_anchor,
            positions_or_nibbles=combo,
            transform_model=transform_model,
        )
        if normalized_row:
            fallback.append(normalized_row)
    fallback.sort(key=_bridge_sort_key)
    return fallback


def _stage_improvements(
    entries: Iterable[dict[str, object]],
    *,
    current_best: dict[str, object],
) -> tuple[list[dict[str, object]], dict[str, object], bool]:
    kept: list[dict[str, object]] = []
    best = dict(current_best)
    reached_exact3 = False
    for entry in sorted(entries, key=_bridge_sort_key):
        if not _bridge_entry_is_better(entry, best):
            continue
        kept.append(entry)
        best = entry
        if int(entry.get("exact", 0)) >= 3:
            reached_exact3 = True
            break
    return kept, best, reached_exact3


def _bridge_entries_to_payload_entries(
    entries: Sequence[dict[str, object]],
    transform_model: SamplereverseTransformModel,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for entry in entries:
        payload = dict(entry)
        payload.update(_entry_metrics(entry, transform_model))
        payload["raw_prefix_hex"] = str(payload.get("raw_prefix_hex") or payload.get("raw", "")).strip().lower()
        out.append(payload)
    return out


def _bridge_progress(validations: Sequence[dict[str, object]]) -> bool:
    return any(
        bool(item.get("compare_semantics_agree"))
        and (
            int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 3
            or int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)) < DEFAULT_BRIDGE_BASELINE_DISTANCE5
        )
        for item in validations
    )


def _collect_promoted_bridge_anchors(validations: Sequence[dict[str, object]]) -> list[str]:
    ranked = sorted(
        (
            item
            for item in validations
            if bool(item.get("compare_semantics_agree"))
        ),
        key=lambda item: (
            -int(item.get("runtime_ci_exact_wchars", 0) or 0),
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
    )
    out: list[str] = []
    seen: set[str] = set()
    for item in ranked:
        anchor = str(item.get("cand8_hex", "")).strip().lower() or str(item.get("candidate_hex", ""))[:16].lower()
        if len(anchor) != 16 or anchor in seen:
            continue
        seen.add(anchor)
        out.append(anchor)
    return out


def _collect_frontier_promoted_anchors(
    validations: Sequence[dict[str, object]],
    *,
    context_entries: Sequence[dict[str, object]] = (),
) -> list[dict[str, object]]:
    return _frontier_anchor_candidates(validations, context_entries=context_entries)


def _frontier_lane(frontier_submode: str) -> str:
    normalized = str(frontier_submode).strip()
    if normalized == FRONTIER_EXACT1_SUBMODE:
        return FRONTIER_EXACT1_SUBMODE
    return FRONTIER_EXACT0_SUBMODE


def _active_frontier_lane(frontier_candidates: Sequence[dict[str, object]]) -> str:
    if any(_frontier_lane(item.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE for item in frontier_candidates):
        return FRONTIER_EXACT1_SUBMODE
    return FRONTIER_EXACT0_SUBMODE


def _refine_anchor_plan(main_anchor: str, promoted_bridge_anchors: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    anchors: list[str] = []
    anchor_sources: dict[str, str] = {}

    def _push(anchor: str, source: str) -> None:
        normalized = str(anchor).strip().lower()
        if len(normalized) != 16 or normalized in anchor_sources or len(anchors) >= REFINE_MAX_ANCHORS:
            return
        anchors.append(normalized)
        anchor_sources[normalized] = source

    _push(main_anchor, "seed_anchor")
    for anchor in promoted_bridge_anchors:
        _push(anchor, "bridge_promoted")
    _push(DEFAULT_FRONTIER_ANCHOR, "frontier_anchor")
    return anchors, anchor_sources


def _frontier_refine_anchor_plan(
    discovered_anchors: Sequence[str],
    frontier_candidates: Sequence[dict[str, object]],
    *,
    active_lane: str,
) -> tuple[list[str], dict[str, str]]:
    anchors: list[str] = []
    anchor_sources: dict[str, str] = {}

    def _push(anchor: str, source: str) -> None:
        normalized = str(anchor).strip().lower()
        if len(normalized) != 16 or normalized in anchor_sources or len(anchors) >= REFINE_MAX_ANCHORS:
            return
        anchors.append(normalized)
        anchor_sources[normalized] = source

    lane_candidates = [
        item
        for item in frontier_candidates
        if _frontier_lane(item.get("frontier_submode", "")) == active_lane
    ]
    for item in lane_candidates:
        _push(str(item.get("anchor", "")), str(item.get("frontier_role", "frontier_anchor")))
    _push(DEFAULT_FRONTIER_ANCHOR, "frontier_anchor")
    for anchor in discovered_anchors:
        source = "recent_compare_aware_anchor"
        if anchor == discovered_anchors[0]:
            source = "seed_anchor"
        _push(anchor, source)
    return anchors, anchor_sources


def _validated_candidates_from_runs(*validation_groups: Sequence[dict[str, object]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for validations in validation_groups:
        for item in validations:
            if int(item.get("runtime_ci_exact_wchars", 0) or 0) < 5:
                continue
            if not bool(item.get("compare_semantics_agree")):
                continue
            candidate_hex = str(item.get("candidate_hex", "")).strip().lower()
            if not candidate_hex or candidate_hex in seen:
                continue
            seen.add(candidate_hex)
            out.append(_candidate_text_from_hex(candidate_hex))
    return out


def _make_search_artifact(
    *,
    tool_name: str,
    output_path: Path,
    summary: str,
    strategy_name: str,
    evidence_kind: str,
    payload: dict[str, object],
    derived_entries: Sequence[dict[str, object]] = (),
) -> ToolRunArtifact:
    artifact = ToolRunArtifact(
        tool_name=tool_name,
        enabled=True,
        attempted=True,
        success=True,
        summary=summary,
        output_path=str(output_path),
        strategy_name=strategy_name,
    )
    derived_candidates = [
        _candidate_text_from_hex(str(entry.get("candidate_hex", "")))
        for entry in derived_entries[:8]
        if str(entry.get("candidate_hex", "")).strip()
    ]
    artifact.structured_evidence.append(
        StructuredEvidence(
            kind=evidence_kind,
            source_tool=tool_name,
            summary=summary,
            confidence=0.92 if evidence_kind == "TransformEvidence" else 0.9,
            payload=payload,
            derived_candidates=derived_candidates,
        )
    )
    return artifact


def _make_validation_artifact(
    *,
    tool_name: str,
    output_path: Path,
    validations: Sequence[dict[str, object]],
    strategy_name: str,
) -> ToolRunArtifact:
    artifact = ToolRunArtifact(
        tool_name=tool_name,
        enabled=True,
        attempted=True,
        success=True,
        summary=f"{tool_name} complete",
        output_path=str(output_path),
        strategy_name=strategy_name,
        evidence=[
            f"runtime_compare:validation_candidate={item.get('candidate_hex', '')}"
            for item in validations[:BRIDGE_VALIDATE_TOP]
        ],
    )
    for item in validations[:BRIDGE_VALIDATE_TOP]:
        derived_candidates = []
        if item.get("compare_semantics_agree") and int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 5:
            derived_candidates = [_candidate_text_from_hex(str(item["candidate_hex"]))]
        artifact.structured_evidence.append(
            StructuredEvidence(
                kind="RuntimeCompareEvidence",
                source_tool=tool_name,
                summary=str(item.get("compare_summary", "")).strip() or tool_name,
                confidence=0.96 if item.get("compare_semantics_agree") else 0.65,
                payload=dict(item),
                derived_candidates=derived_candidates,
            )
        )
    return artifact


def _candidate_anchor_from_payload_entry(entry: dict[str, object]) -> str:
    candidate_hex = _candidate_hex_from_entry(entry)
    return candidate_hex[:16].lower() if len(candidate_hex) >= 16 else ""


def _payload_anchor_candidates(
    payload: dict[str, object],
    transform_model: SamplereverseTransformModel,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _push(anchor: str) -> None:
        normalized = str(anchor).strip().lower()
        if len(normalized) != 16 or normalized in seen:
            return
        seen.add(normalized)
        out.append(normalized)

    for entry in _collect_top_entries(payload, transform_model, limit=8):
        _push(_candidate_anchor_from_payload_entry(entry))

    best_global = payload.get("best_global")
    if isinstance(best_global, list) and len(best_global) >= 8:
        _push(str(best_global[7]))

    for raw_pair in payload.get("pairs", []) if isinstance(payload.get("pairs", []), list) else []:
        if not isinstance(raw_pair, list) or len(raw_pair) < 8:
            continue
        _push(str(raw_pair[7]))

    validation_candidates = payload.get("validation_candidates", [])
    if isinstance(validation_candidates, list):
        for entry in validation_candidates:
            if not isinstance(entry, dict):
                continue
            _push(_candidate_anchor_from_payload_entry(entry))

    for entry in payload.get("rows", []) if isinstance(payload.get("rows", []), list) else []:
        if not isinstance(entry, dict):
            continue
        _push(_candidate_anchor_from_payload_entry(entry))

    return out


def _recent_compare_aware_payloads(limit: int = 16) -> list[dict[str, object]]:
    repo_root = _repo_root()
    roots = [repo_root / "solve_reports" / "tool_artifacts"]
    roots.extend(
        path / "reports" / "tool_artifacts" / "samplereverse"
        for path in (repo_root / "solve_reports" / "harness_runs").glob("*")
        if path.is_dir()
    )
    directories = sorted(
        (
            path
            for root in roots
            if root.exists()
            for path in (
                [root]
                if root.name == "samplereverse"
                else [
                    candidate
                    for candidate in root.iterdir()
                    if candidate.is_dir() and candidate.name.startswith("samplereverse_compare_aware")
                ]
            )
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    payloads: list[dict[str, object]] = []
    for directory in directories[:limit]:
        json_paths = directory.rglob("*.json") if directory.name == "samplereverse" else directory.glob("*.json")
        for json_path in sorted(json_paths):
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def resolve_compare_aware_anchors(
    transform_model: SamplereverseTransformModel,
    explicit_anchors: Sequence[str] | None = None,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _push(anchor: str) -> None:
        normalized = str(anchor).strip().lower()
        if len(normalized) != 16 or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    for anchor in DEFAULT_ANCHORS:
        _push(anchor)
    for anchor in explicit_anchors or []:
        _push(anchor)
    for payload in _recent_compare_aware_payloads():
        for anchor in _payload_anchor_candidates(payload, transform_model):
            _push(anchor)
            if len(ordered) >= REFINE_MAX_ANCHORS:
                return ordered
    return ordered


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _project_state_json(name: str) -> dict[str, object]:
    return _read_json_object(_repo_root() / "project_state" / name)


def _indexed_artifact_payload(kind: str) -> tuple[dict[str, object], str]:
    index = _project_state_json("artifact_index.json")
    latest = index.get("latest_artifacts", {})
    if not isinstance(latest, dict):
        return {}, ""
    rel_path = str(latest.get(kind, "") or "").strip()
    if not rel_path:
        return {}, ""
    path = Path(rel_path)
    if not path.is_absolute():
        path = _repo_root() / path
    return _read_json_object(path), rel_path


def _candidate_from_project_state(label: str) -> dict[str, object]:
    current_state = _project_state_json("current_state.json")
    best_candidates = current_state.get("best_candidates", current_state.get("current_best", {}))
    if not isinstance(best_candidates, dict):
        return {}
    entry = best_candidates.get(label, {})
    return dict(entry) if isinstance(entry, dict) else {}


def _negative_exact2_value_pool_recorded() -> bool:
    negative_results_path = _repo_root() / "project_state" / "negative_results.json"
    try:
        payload = json.loads(negative_results_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(payload, list):
        return False
    for item in payload:
        if not isinstance(item, dict):
            continue
        direction = str(item.get("direction", "")).lower()
        if "exact2 basin value-pool" in direction and bool(item.get("do_not_repeat")):
            return True
    return False


def _profile_audit_candidate_record(
    *,
    label: str,
    source: str,
    candidate_hex: str,
    runtime_entry: dict[str, object] | None = None,
    promotion_allowed: bool = True,
) -> dict[str, object]:
    runtime_entry = runtime_entry or {}
    normalized = str(candidate_hex).strip().lower()
    trace = trace_candidate_transform(normalized)
    runtime_prefix = str(
        runtime_entry.get("runtime_lhs_prefix_hex_10")
        or str(runtime_entry.get("runtime_lhs_prefix_hex") or "")[: len(TARGET_PREFIX) * 2]
        or ""
    ).strip().lower()
    if not runtime_prefix and normalized.startswith("78d540b49c590770"):
        runtime_prefix = "46006c004464830d311c"
    if not runtime_prefix and normalized.startswith("4a78f0eaeb4f13b0"):
        runtime_prefix = "46004c007e40b92886f5"
    compare_boundary = dict(trace.get("compare_boundary", {})) if isinstance(trace.get("compare_boundary"), dict) else {}
    decrypt_prefix = str(dict(trace.get("rc4", {})).get("decrypt_prefix_hex", "")).strip().lower()
    offline_prefix_10 = decrypt_prefix[: len(TARGET_PREFIX) * 2]
    return {
        "label": label,
        "source": source,
        "candidate_hex": normalized,
        "cand8_hex": normalized[:16],
        "promotion_allowed": bool(promotion_allowed),
        "runtime_prefix_hex_10": runtime_prefix,
        "offline_prefix_hex_10": offline_prefix_10,
        "offline_runtime_prefix_agree_10": bool(runtime_prefix) and runtime_prefix == offline_prefix_10,
        "runtime_ci_exact_wchars": runtime_entry.get("runtime_ci_exact_wchars"),
        "runtime_ci_distance5": runtime_entry.get("runtime_ci_distance5"),
        "compare_semantics_agree": runtime_entry.get("compare_semantics_agree"),
        "trace": trace,
        "summary": {
            "candidate_length_bytes": dict(trace.get("candidate_layout", {})).get("candidate_length_bytes"),
            "prefix_bytes": dict(trace.get("candidate_layout", {})).get("prefix_bytes"),
            "base64_length_chars": dict(trace.get("base64_boundary", {})).get("base64_length_chars"),
            "prefix_ends_on_base64_chunk_boundary": dict(trace.get("base64_boundary", {})).get(
                "prefix_ends_on_base64_chunk_boundary"
            ),
            "prefix_last_chunk_raw_remainder": dict(trace.get("base64_boundary", {})).get(
                "prefix_last_chunk_raw_remainder"
            ),
            "rc4_key_length_bytes": dict(trace.get("rc4", {})).get("key_length_bytes"),
            "ci_exact_wchars": compare_boundary.get("ci_exact_wchars"),
            "ci_distance5": compare_boundary.get("ci_distance5"),
        },
    }


def _profile_transform_audit_candidates(
    *,
    runtime_validations: Sequence[dict[str, object]],
    top_entries: Sequence[dict[str, object]],
    exact2_basin_value_pool_payload: dict[str, object],
) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()

    def _push(
        label: str,
        source: str,
        candidate_hex: str,
        runtime_entry: dict[str, object] | None = None,
        *,
        promotion_allowed: bool = True,
    ) -> None:
        normalized = str(candidate_hex).strip().lower()
        if not normalized or len(normalized) != INPUT_LENGTH * 2 or normalized in seen:
            return
        if len(candidates) >= PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT:
            return
        seen.add(normalized)
        candidates.append(
            _profile_audit_candidate_record(
                label=label,
                source=source,
                candidate_hex=normalized,
                runtime_entry=runtime_entry,
                promotion_allowed=promotion_allowed,
            )
        )

    all_validations = [
        dict(item)
        for item in runtime_validations
        if isinstance(item, dict) and bool(item.get("compare_semantics_agree"))
    ]
    exact2_runtime = next(
        (
            item
            for item in sorted(all_validations, key=_runtime_validation_sort_key)
            if int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 2
        ),
        None,
    )
    if exact2_runtime:
        _push(
            "current_exact2_best",
            "current_run_runtime_validation",
            _candidate_hex_from_entry(exact2_runtime),
            exact2_runtime,
        )
    else:
        state_exact2 = _candidate_from_project_state("exact2")
        _push(
            "current_exact2_best",
            "project_state.current_state.best_candidates.exact2",
            _candidate_hex_from_entry(state_exact2) or "78d540b49c59077041414141414141",
            state_exact2,
        )

    exact1_runtime = _best_compare_agree_frontier_entry_for_exact(all_validations, 1)
    if exact1_runtime:
        _push(
            "exact1_frontier_best",
            "current_run_runtime_validation",
            _candidate_hex_from_entry(exact1_runtime),
            exact1_runtime,
        )
    else:
        state_exact1 = _candidate_from_project_state("exact1")
        _push(
            "exact1_frontier_best",
            "project_state.current_state.best_candidates.exact1",
            _candidate_hex_from_entry(state_exact1),
            state_exact1,
        )

    _push(
        "secondary_exact2_reference",
        "project_handoff_reference",
        "4a78f0eaeb4f13b041414141414141",
    )
    for anchor in DO_NOT_PROMOTE_PROJECTED_ANCHORS:
        _push(
            f"projected_do_not_promote_{anchor}",
            "project_state.do_not_do_projected_example",
            f"{anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
            promotion_allowed=False,
        )

    for entry in top_entries:
        if len(candidates) >= PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT - 1:
            break
        _push("frontier_top_entry", "current_run_top_entries", _candidate_hex_from_entry(dict(entry)), dict(entry))

    value_pool_best = exact2_basin_value_pool_payload.get("best_runtime_candidate", {})
    if isinstance(value_pool_best, dict):
        _push(
            "exact2_value_pool_best_runtime",
            "latest_indexed_exact2_basin_value_pool_result",
            _candidate_hex_from_entry(value_pool_best),
            value_pool_best,
        )

    _push("probe_all_A_baseline", "probe_baseline", "414141414141414141414141414141")
    return candidates[:PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT]


def run_profile_transform_hypothesis_audit(
    *,
    artifacts_dir: Path,
    transform_model: SamplereverseTransformModel,
    runtime_validations: Sequence[dict[str, object]],
    top_entries: Sequence[dict[str, object]],
    exact2_basin_value_pool_run: dict[str, object] | None = None,
    smt_run: dict[str, object] | None = None,
    exact2_basin_smt_run: dict[str, object] | None = None,
    frontier_summary_path: Path | None = None,
    strata_summary_path: Path | None = None,
    search_budget: int | None = None,
    snapshot_interval: int | None = None,
    validate_top: int | None = None,
    per_probe_timeout: float | None = None,
    log: Any | None = None,
) -> dict[str, object]:
    _ = transform_model
    latest_value_pool_payload, latest_value_pool_path = _indexed_artifact_payload("exact2_basin_value_pool_result")
    if exact2_basin_value_pool_run and isinstance(exact2_basin_value_pool_run.get("payload"), dict):
        value_pool_payload = dict(exact2_basin_value_pool_run["payload"])
        value_pool_path = str(exact2_basin_value_pool_run.get("result_path", "")) or latest_value_pool_path
    else:
        value_pool_payload = latest_value_pool_payload
        value_pool_path = latest_value_pool_path

    candidates = _profile_transform_audit_candidates(
        runtime_validations=runtime_validations,
        top_entries=top_entries,
        exact2_basin_value_pool_payload=value_pool_payload,
    )
    exact2_record = next((item for item in candidates if item["label"] == "current_exact2_best"), {})
    helper_runtime_consistent = bool(exact2_record.get("offline_runtime_prefix_agree_10"))
    any_runtime_disagreement = any(
        item.get("runtime_prefix_hex_10") and not item.get("offline_runtime_prefix_agree_10")
        for item in candidates
    )
    negative_recorded = _negative_exact2_value_pool_recorded()
    exhausted_branch = {
        "branch": "exact2_basin_value_pool",
        "attempted": bool(value_pool_payload.get("attempted")),
        "classification": str(value_pool_payload.get("classification", "")),
        "generated_count": int(value_pool_payload.get("generated_count", 0) or 0),
        "unique_count": int(value_pool_payload.get("unique_count", 0) or 0),
        "validated_count": int(value_pool_payload.get("validated_count", 0) or 0),
        "best_candidate": str(
            dict(value_pool_payload.get("best_runtime_candidate", {})).get(
                "candidate_hex",
                value_pool_payload.get("best_candidate", ""),
            )
        ),
        "best_runtime_exact_wchars": int(
            dict(value_pool_payload.get("best_runtime_candidate", {})).get(
                "runtime_ci_exact_wchars",
                value_pool_payload.get("best_runtime_exact_wchars", 0),
            )
            or 0
        ),
        "best_runtime_distance5": int(
            dict(value_pool_payload.get("best_runtime_candidate", {})).get(
                "runtime_ci_distance5",
                value_pool_payload.get("best_runtime_distance5", 1 << 30),
            )
            or (1 << 30)
        ),
        "improved_over_exact2": bool(value_pool_payload.get("improved_over_exact2")),
        "negative_result_recorded": negative_recorded,
        "do_not_repeat": bool(negative_recorded),
        "artifact": value_pool_path,
    }

    trace_artifacts = [
        item
        for item in [
            "project_state/artifact_index.json",
            "project_state/current_state.json",
            "project_state/negative_results.json",
            str(frontier_summary_path) if frontier_summary_path else "",
            str(strata_summary_path) if strata_summary_path else "",
            value_pool_path,
        ]
        if item
    ]
    hypothesis_matrix = [
        {
            "id": "H1",
            "hypothesis": "candidate byte layout / prefix length is wrong or too narrow",
            "evidence_for": [
                "The active line is fixed at L15(prefix8), while compare evidence is judged over the first five UTF-16LE wchars.",
                "The exact2 best stabilizes on f/l but current local mutations do not move the third wchar to an exact match.",
            ],
            "evidence_against": [
                "The exact2 baseline trace is runtime-consistent at the 5-wchar prefix.",
                "No candidate generation, ranking, or final selection change was made in this audit.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Trace at most 8 existing candidates and compare prefix8/suffix layout against the 5-wchar boundary; do not generate new search candidates.",
            "success_signal": "A contrast candidate shows the same early score but a different prefix/suffix or suffix-influenced Base64 boundary that explains the exact2 stall.",
            "stop_condition": "If traces show the fixed prefix8 boundary is internally consistent for all audit candidates, do not widen this branch without a separate bounded contrast design.",
            "code_change_allowed": "metadata_only",
            "recommendation": "primary_next_target" if helper_runtime_consistent else "blocked_until_helper_runtime_consistency",
        },
        {
            "id": "H2",
            "hypothesis": "UTF-16LE wchar boundary and byte mutation positions are offset",
            "evidence_for": [
                "Candidate bytes are nibble-expanded before UTF-16LE interleaving, so one candidate byte maps to four raw bytes before Base64.",
                "The third wchar failure appears immediately after two exact UTF-16LE pairs.",
            ],
            "evidence_against": [
                "The trace helper reproduces the known exact2 runtime prefix when helper/runtime consistency is true.",
                "Current wchar deltas align target pairs as f, l, a, g, {.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Compare nibble expansion, UTF-16 raw bytes, and wchar deltas for the audit candidates only.",
            "success_signal": "A trace shows a systematic one-byte or one-wchar shift between mutation positions and compare pairs.",
            "stop_condition": "If exact2 offline/runtime prefix and wchar deltas align, demote H2 and avoid transform changes.",
            "code_change_allowed": "metadata_only",
            "recommendation": "secondary",
        },
        {
            "id": "H3",
            "hypothesis": "Base64 boundary / padding / chunk alignment has an off-by-one",
            "evidence_for": [
                "The prefix8 raw payload is 32 bytes, which leaves remainder 2 inside a Base64 3-byte chunk.",
                "The first suffix byte therefore begins inside the Base64 chunk covering the prefix boundary.",
            ],
            "evidence_against": [
                "The current helper/runtime exact2 prefix consistency argues against a simple Base64 implementation mismatch.",
                "The audit has not generated contrast candidates, only trace metadata.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Use no more than 8 hand-picked contrast candidates to vary only the boundary-adjacent byte layout in a later run.",
            "success_signal": "A minimal contrast changes third-to-fifth wchar deltas through Base64 chunk alignment without losing runtime consistency.",
            "stop_condition": "If boundary traces are identical across best/frontier/projected examples, stop and move to candidate-quality evidence.",
            "code_change_allowed": "metadata_only_now; later_contrast_candidates_only",
            "recommendation": "primary_next_target" if helper_runtime_consistent else "blocked_until_helper_runtime_consistency",
        },
        {
            "id": "H4",
            "hypothesis": "RC4 helper and runtime are inconsistent",
            "evidence_for": [
                "Any offline/runtime prefix mismatch in the audit candidate table would immediately support this.",
            ]
            if any_runtime_disagreement
            else [
                "No direct evidence in this audit unless a runtime prefix mismatch appears.",
            ],
            "evidence_against": [
                "The exact2 baseline offline trace matches the known runtime prefix."
            ]
            if helper_runtime_consistent
            else [
                "The exact2 baseline did not prove helper/runtime consistency in this matrix.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Stop search and compare RC4 key prefix/key length/decrypt prefix for existing runtime-validated candidates.",
            "success_signal": "Any compare_semantics_agree candidate has runtime prefix different from trace decrypt_prefix.",
            "stop_condition": "If exact2 and secondary exact2 prefixes match runtime, demote H4 for the next iteration.",
            "code_change_allowed": "trace_or_helper_fix_only_if_mismatch",
            "recommendation": "stop_and_fix_first" if any_runtime_disagreement else "demote",
        },
        {
            "id": "H5",
            "hypothesis": "offline compare-aware semantics skew from runtime in this basin",
            "evidence_for": [
                "Would be supported by compare_semantics_agree=false or metric deltas among audit candidates.",
            ],
            "evidence_against": [
                "Current exact2 value-pool validations were runtime checked and compare_semantics_agree=true.",
                "The negative branch was recorded after validating all 18 bounded combinations.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Contrast offline ci_exact/distance5 with runtime ci_exact/distance5 for existing validation rows only.",
            "success_signal": "A repeated metric divergence appears among compare_semantics_agree=true candidates.",
            "stop_condition": "If offline/runtime metrics agree for exact2 and value-pool rows, demote H5.",
            "code_change_allowed": "metadata_only",
            "recommendation": "demote" if helper_runtime_consistent else "inspect_with_H4",
        },
        {
            "id": "H6",
            "hypothesis": "current exact2 candidate is locally optimal under available evidence; candidate quality is insufficient",
            "evidence_for": [
                "The exact2 basin value-pool branch generated and runtime-validated all 18 diagnostic combinations with no exact3+ or distance5 improvement.",
                "negative_results.json records this pool as do_not_repeat.",
            ],
            "evidence_against": [
                "This is not an unsat proof for the full input space; it only closes the current diagnostic pool.",
            ],
            "files_or_artifacts_needed": trace_artifacts,
            "bounded_validation": "Do not repeat the exhausted pool; only choose a new bounded contrast if H1/H3 trace evidence justifies it.",
            "success_signal": "A new evidence source identifies a smaller, different candidate family than the exhausted value pool.",
            "stop_condition": "If no H1/H3/H4/H5 evidence appears, stop local mutation and request a different evidence source.",
            "code_change_allowed": "metadata_only",
            "recommendation": "fallback_after_H1_H3_audit",
        },
    ]
    next_target = (
        {
            "selected_hypotheses": ["H4", "H5"],
            "reason": "offline/runtime prefix consistency failed or could not be established",
            "bounded_validation": "Stop candidate search and repair/verify transform or RC4 trace assumptions first.",
        }
        if not helper_runtime_consistent or any_runtime_disagreement
        else {
            "selected_hypotheses": ["H1", "H3"],
            "reason": "helper/runtime prefix is consistent, while prefix8 raw bytes end inside a Base64 chunk boundary.",
            "bounded_validation": "Next run should use a tiny hand-picked contrast set around prefix8 + Base64 chunk boundary; this run only emits trace metadata.",
        }
    )
    payload = {
        "artifact_kind": "profile_transform_hypothesis_matrix",
        "profile": "samplereverse",
        "audit_only": True,
        "candidate_limit": PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT,
        "candidate_count": len(candidates),
        "candidate_generation_changed": False,
        "ranking_changed": False,
        "final_selection_changed": False,
        "search_budget_changed": False,
        "beam_budget_topn_timeout_frontier_limit_expanded": False,
        "settings_snapshot": {
            "search_budget": search_budget,
            "snapshot_interval": snapshot_interval,
            "validate_top": validate_top,
            "per_probe_timeout": per_probe_timeout,
            "frontier_max_iterations": FRONTIER_MAX_ITERATIONS,
            "guided_pool_beam_limit": GUIDED_POOL_BEAM_LIMIT,
        },
        "read_scope": {
            "uses_latest_indexed_artifacts_only": True,
            "scans_full_solve_reports": False,
            "read_artifacts": trace_artifacts,
        },
        "exhausted_branch_confirmation": exhausted_branch,
        "candidates": candidates,
        "hypotheses": hypothesis_matrix,
        "next_bounded_validation_target": {
            **next_target,
            "candidate_count_allowed": PROFILE_TRANSFORM_AUDIT_CANDIDATE_LIMIT,
            "runtime_validation_required": True,
            "expected_improvement_signal": "runtime_ci_exact_wchars > 2 or runtime_ci_distance5 < 246 without compare_semantics disagreement",
            "stop_condition": "Do not expand beam/budget/topN/timeout/frontier iterations; stop if trace shows helper/runtime mismatch.",
        },
        "smt_context": {
            "primary_smt_summary": str(dict((smt_run or {}).get("payload", {})).get("summary", "")),
            "exact2_basin_smt_summary": str(
                dict((exact2_basin_smt_run or {}).get("payload", {})).get("summary", "")
            ),
        },
    }
    output_path = artifacts_dir / PROFILE_TRANSFORM_HYPOTHESIS_MATRIX_FILE_NAME
    _write_json(output_path, payload)
    if log:
        log(f"profile transform hypothesis audit wrote {output_path}")
    return {"result_path": str(output_path), "payload": payload}


def _mutated_candidate_hex(base_anchor: str, position: int, value: int) -> str:
    work = bytearray(bytes.fromhex(base_anchor))
    work[position] = value & 0xFF
    return bytes(work).hex() + DEFAULT_FIXED_SUFFIX_HEX


def _top_compare_aware_single_byte_entries(
    *,
    base_anchor: str,
    positions: Sequence[int],
    transform_model: SamplereverseTransformModel,
    top_k: int = GUIDED_POOL_TOP_VALUES,
) -> dict[int, list[dict[str, object]]]:
    base_bytes = bytes.fromhex(base_anchor)
    base_candidate_hex = f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}"
    cache: dict[str, dict[str, object]] = {}

    def _entry_for(candidate_hex: str) -> dict[str, object]:
        cached = cache.get(candidate_hex)
        if cached is not None:
            return cached
        entry = _evaluate_candidate_hex(candidate_hex, transform_model)
        cache[candidate_hex] = entry
        return entry

    out: dict[int, list[dict[str, object]]] = {}
    for position in positions:
        if not (0 <= int(position) < len(base_bytes)):
            continue
        original = base_bytes[position]
        scored: list[dict[str, object]] = []
        for value in [original, *[candidate for candidate in range(1, 256) if candidate != original]]:
            candidate_hex = base_candidate_hex if value == original else _mutated_candidate_hex(base_anchor, position, value)
            entry = dict(_entry_for(candidate_hex))
            entry.update(
                {
                    "mutated_position": int(position),
                    "mutated_byte_value": int(value),
                }
            )
            scored.append(entry)
        scored.sort(key=lambda item: _candidate_sort_key(item, transform_model))
        out[int(position)] = scored[: max(1, top_k)]
    return out


def _top_compare_aware_pair_entries(
    *,
    base_anchor: str,
    positions: Sequence[int],
    position_profiles: dict[int, list[dict[str, object]]],
    transform_model: SamplereverseTransformModel,
    anchor_mode: str,
    frontier_submode: str = "",
    locked_pair_positions: Sequence[tuple[int, int]] | None = None,
    incoming_feedback_value_pools: dict[int, Sequence[int]] | None = None,
    lineage_value_pools: dict[int, Sequence[int]] | None = None,
    lineage_value_counts: dict[int, dict[int, int]] | None = None,
    lineage_value_origins: dict[int, Sequence[str]] | None = None,
    baseline_entry: dict[str, object] | None = None,
    top_per_pair: int = FRONTIER_PAIR_TOP_PER_PAIR,
) -> tuple[dict[tuple[int, int], list[dict[str, object]]], dict[str, object]]:
    base_bytes = bytes.fromhex(base_anchor)
    cache: dict[str, dict[str, object]] = {}
    incoming = incoming_feedback_value_pools or {}
    lineage_sources = lineage_value_pools or {}
    lineage_counts = lineage_value_counts or {}
    lineage_origins = lineage_value_origins or {}

    def _entry_for(candidate_hex: str) -> dict[str, object]:
        cached = cache.get(candidate_hex)
        if cached is not None:
            return cached
        entry = _evaluate_candidate_hex(candidate_hex, transform_model)
        cache[candidate_hex] = entry
        return entry

    pair_profiles: dict[tuple[int, int], list[dict[str, object]]] = {}
    pair_generation_details: dict[str, object] = {
        "pair_escape_mode": "single_pool",
        "pair_preserve_pool": {},
        "pair_escape_pool": {},
        "pair_escape_pool_strategy": "generic_profile",
        "pair_neighbor_generation_summary": {},
        "pair_mutation_radius_summary": {},
        "pair_profile_preserve_entries": {},
        "pair_profile_escape_entries": {},
        "pair_profile_kept_preserve": {},
        "pair_profile_kept_escape": {},
        "pair_profile_drop_reasons": {},
        "pair_profile_truncation_summary": {},
        "pair_escape_source_values": {},
        "pair_escape_source_counts": {},
        "pair_escape_source_origins": {},
        "pair_escape_source_projected_values": {},
        "pair_escape_source_projected_origins": {},
        "pair_escape_source_projected_quality_band": {},
        "pair_escape_source_projected_rank_summary": {},
        "pair_escape_source_projected_direction": {},
        "pair_escape_source_projected_step": {},
        "pair_escape_source_projected_kept_values": {},
        "pair_escape_source_projected_dropped_values": {},
        "lineage_projection_summary": {},
        "pair_escape_source_reject_reasons": {},
        "pair_single_byte_guard_summary": {},
        "pair_single_byte_guard_candidates": {},
        "pair_single_byte_guard_status_counts": {},
        "pair_guard_soft_promoted_values": {},
        "pair_guard_nonbase_starved": {},
        "pair_guard_soft_quality_band": {},
        "pair_guard_soft_rank_summary": {},
        "pair_guard_soft_distance_delta": {},
        "pair_guard_soft_raw_delta": {},
        "pair_guard_soft_structure_delta": {},
        "pair_projected_vs_neighbor_summary": {},
        "pair_projected_competitive_status": {},
        "pair_projected_competitive_winner": {},
        "pair_projected_blocked_by_neighbor": {},
        "pair_projected_best_delta_gap": {},
        "pair_projected_boundary_candidates": {},
        "pair_projected_preserve_candidates": {},
    }
    allowed_pairs = {tuple(sorted((int(left), int(right)))) for left, right in (locked_pair_positions or [])}
    for left, right in itertools.combinations(positions, 2):
        normalized_pair = tuple(sorted((int(left), int(right))))
        if allowed_pairs and normalized_pair not in allowed_pairs:
            continue
        left_values = [
            int(entry.get("mutated_byte_value", base_bytes[left])) & 0xFF
            for entry in position_profiles.get(left, [])[:FRONTIER_PAIR_VALUE_LIMIT]
        ] or [base_bytes[left]]
        right_values = [
            int(entry.get("mutated_byte_value", base_bytes[right])) & 0xFF
            for entry in position_profiles.get(right, [])[:FRONTIER_PAIR_VALUE_LIMIT]
        ] or [base_bytes[right]]
        preserve_left_values: list[int] = []
        preserve_right_values: list[int] = []
        escape_left_values: list[int] = []
        escape_right_values: list[int] = []
        preserve_left_origins: dict[int, list[str]] = {}
        preserve_right_origins: dict[int, list[str]] = {}
        escape_left_origins: dict[int, list[str]] = {}
        escape_right_origins: dict[int, list[str]] = {}
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            left_projection_details: dict[str, object] = {}
            right_projection_details: dict[str, object] = {}
            preserve_left_origins, escape_left_origins = _exact1_neighbor_value_maps_with_optional_details(
                base_value=base_bytes[left],
                profile_values=left_values,
                incoming_values=[int(value) & 0xFF for value in incoming.get(int(left), [])],
                lineage_values=[int(value) & 0xFF for value in lineage_sources.get(int(left), [])],
                projection_details=left_projection_details,
            )
            preserve_right_origins, escape_right_origins = _exact1_neighbor_value_maps_with_optional_details(
                base_value=base_bytes[right],
                profile_values=right_values,
                incoming_values=[int(value) & 0xFF for value in incoming.get(int(right), [])],
                lineage_values=[int(value) & 0xFF for value in lineage_sources.get(int(right), [])],
                projection_details=right_projection_details,
            )
            preserve_left_origins = _bounded_exact1_value_map(
                preserve_left_origins,
                limit=EXACT1_PAIR_PRESERVE_VALUE_LIMIT,
            )
            preserve_right_origins = _bounded_exact1_value_map(
                preserve_right_origins,
                limit=EXACT1_PAIR_PRESERVE_VALUE_LIMIT,
            )
            escape_left_origins = _bounded_exact1_value_map(
                escape_left_origins,
                limit=EXACT1_PAIR_ESCAPE_VALUE_LIMIT,
            )
            escape_right_origins = _bounded_exact1_value_map(
                escape_right_origins,
                limit=EXACT1_PAIR_ESCAPE_VALUE_LIMIT,
            )
            preserve_left_values = list(preserve_left_origins)
            preserve_right_values = list(preserve_right_origins)
            escape_left_values = list(escape_left_origins)
            escape_right_values = list(escape_right_origins)
            pair_key = f"{left},{right}"
            pair_generation_details["pair_escape_mode"] = "exact1_dual_lane"
            pair_generation_details["pair_escape_pool_strategy"] = "exact1_local_neighbors"
            pair_generation_details["pair_preserve_pool"][pair_key] = {
                str(left): preserve_left_values[:EXACT1_PAIR_PRESERVE_VALUE_LIMIT],
                str(right): preserve_right_values[:EXACT1_PAIR_PRESERVE_VALUE_LIMIT],
            }
            pair_generation_details["pair_escape_pool"][pair_key] = {
                str(left): escape_left_values[:EXACT1_PAIR_ESCAPE_VALUE_LIMIT],
                str(right): escape_right_values[:EXACT1_PAIR_ESCAPE_VALUE_LIMIT],
            }
            pair_generation_details["pair_neighbor_generation_summary"][pair_key] = {
                "preserve_generated": len(preserve_left_values) * len(preserve_right_values),
                "escape_generated": len(escape_left_values) * len(escape_right_values),
                "preserve_neighbor_mode": "preserve_neighbors",
                "escape_neighbor_mode": "escape_neighbors",
            }
            pair_generation_details["pair_mutation_radius_summary"][pair_key] = {
                "preserve_left": [abs(value - base_bytes[left]) for value in preserve_left_values],
                "preserve_right": [abs(value - base_bytes[right]) for value in preserve_right_values],
                "escape_left": [abs(value - base_bytes[left]) for value in escape_left_values],
                "escape_right": [abs(value - base_bytes[right]) for value in escape_right_values],
            }
            pair_generation_details["pair_escape_source_values"][pair_key] = {
                str(left): [int(value) & 0xFF for value in lineage_sources.get(int(left), [])[:EXACT1_LINEAGE_SOURCE_LIMIT]],
                str(right): [int(value) & 0xFF for value in lineage_sources.get(int(right), [])[:EXACT1_LINEAGE_SOURCE_LIMIT]],
            }
            pair_generation_details["pair_escape_source_counts"][pair_key] = {
                str(left): {str(key): value for key, value in dict(lineage_counts.get(int(left), {})).items()},
                str(right): {str(key): value for key, value in dict(lineage_counts.get(int(right), {})).items()},
            }
            pair_generation_details["pair_escape_source_origins"][pair_key] = {
                str(left): list(dict.fromkeys(str(item) for item in lineage_origins.get(int(left), []))),
                str(right): list(dict.fromkeys(str(item) for item in lineage_origins.get(int(right), []))),
            }
            left_projected_values = [int(value) & 0xFF for value in left_projection_details.get("projected_values", [])]
            right_projected_values = [int(value) & 0xFF for value in right_projection_details.get("projected_values", [])]
            pair_generation_details["pair_escape_source_projected_values"][pair_key] = {
                str(left): left_projected_values,
                str(right): right_projected_values,
            }
            pair_generation_details["pair_escape_source_projected_origins"][pair_key] = {
                str(left): {
                    str(key): list(value)
                    for key, value in dict(left_projection_details.get("projected_origins", {})).items()
                },
                str(right): {
                    str(key): list(value)
                    for key, value in dict(right_projection_details.get("projected_origins", {})).items()
                },
            }
            pair_generation_details["lineage_projection_summary"][pair_key] = {
                str(left): {
                    "raw_source_present_but_too_far": [
                        int(value) & 0xFF for value in left_projection_details.get("raw_source_present_but_too_far", [])
                    ],
                    "projected_local_value_generated": left_projected_values,
                },
                str(right): {
                    "raw_source_present_but_too_far": [
                        int(value) & 0xFF for value in right_projection_details.get("raw_source_present_but_too_far", [])
                    ],
                    "projected_local_value_generated": right_projected_values,
                },
            }
            guard_summary: dict[str, dict[str, int]] = {}
            guard_candidates_by_pos: dict[str, list[dict[str, object]]] = {}
            guard_status_counts_by_pos: dict[str, dict[str, int]] = {}
            guard_soft_promoted_values: dict[str, list[int]] = {}
            guard_nonbase_starved: dict[str, bool] = {}
            guard_soft_quality_band: dict[str, dict[str, str]] = {}
            guard_soft_rank_summary: dict[str, list[dict[str, object]]] = {}
            guard_soft_distance_delta: dict[str, dict[str, int]] = {}
            guard_soft_raw_delta: dict[str, dict[str, int]] = {}
            guard_soft_structure_delta: dict[str, dict[str, list[int]]] = {}
            projected_quality_band: dict[str, dict[str, str]] = {}
            projected_rank_summary: dict[str, list[dict[str, object]]] = {}
            projected_direction_summary: dict[str, dict[str, str]] = {}
            projected_step_summary: dict[str, dict[str, int]] = {}
            projected_kept_values: dict[str, list[int]] = {}
            projected_dropped_values: dict[str, list[int]] = {}
            source_reject_reasons: dict[str, dict[str, list[int]]] = {}
            projected_vs_neighbor_summary: dict[str, dict[str, object]] = {}
            projected_competitive_status: dict[str, str] = {}
            projected_competitive_winner: dict[str, dict[str, object]] = {}
            projected_blocked_by_neighbor: dict[str, dict[str, object]] = {}
            projected_best_delta_gap: dict[str, dict[str, int]] = {}
            projected_summary_by_pos: dict[int, dict[str, object]] = {}
            projected_winner_by_pos: dict[int, dict[str, object]] = {}
            neighbor_summary_by_pos: dict[int, dict[str, object]] = {}
            if baseline_entry:
                for position, values, origin_map in (
                    (int(left), escape_left_values, escape_left_origins),
                    (int(right), escape_right_values, escape_right_origins),
                ):
                    projection_details = left_projection_details if position == int(left) else right_projection_details
                    projected_value_set = {
                        int(value) & 0xFF for value in projection_details.get("projected_values", [])
                    }
                    projected_candidates: list[dict[str, object]] = []
                    for value in values:
                        normalized_value = int(value) & 0xFF
                        origins = list(dict.fromkeys(origin_map.get(normalized_value, [])))
                        if normalized_value not in projected_value_set or not any(origin.endswith("_projected") for origin in origins):
                            continue
                        work = bytearray(base_bytes)
                        work[position] = normalized_value
                        single_entry = _entry_for(bytes(work).hex() + DEFAULT_FIXED_SUFFIX_HEX)
                        single_rank = _pair_structure_rank(single_entry, transform_model)
                        baseline_rank = _pair_structure_rank(baseline_entry, transform_model)
                        single_distance_delta = int(single_entry.get("ci_distance5", 1 << 30) or (1 << 30)) - int(
                            baseline_entry.get("ci_distance5", 1 << 30) or (1 << 30)
                        )
                        single_raw_delta = int(single_entry.get("raw_distance10", 1 << 30) or (1 << 30)) - int(
                            baseline_entry.get("raw_distance10", 1 << 30) or (1 << 30)
                        )
                        direction = str(projection_details.get("projected_direction", {}).get(str(normalized_value), ""))
                        step = int(projection_details.get("projected_step", {}).get(str(normalized_value), 0) or 0)
                        quality = _exact1_projected_value_quality(
                            distance_delta=single_distance_delta,
                            raw_delta=single_raw_delta,
                            structure_rank=single_rank,
                            baseline_rank=baseline_rank,
                            step=step,
                            origins=origins,
                        )
                        projected_candidates.append(
                            {
                                "value": normalized_value,
                                "ci_distance_delta": single_distance_delta,
                                "raw_distance_delta": single_raw_delta,
                                "structure_delta": list(quality.get("structure_delta", ())),
                                "structure_penalty": int(quality.get("structure_penalty", 0) or 0),
                                "structure_gain": int(quality.get("structure_gain", 0) or 0),
                                "origin_priority": int(quality.get("origin_priority", 0) or 0),
                                "quality_band": str(quality.get("quality_band", "")),
                                "projection_direction": direction,
                                "projection_step": step,
                                "origins": origins,
                            }
                        )
                    projected_quality_band[str(position)] = {
                        str(int(item.get("value", 0) or 0)): str(item.get("quality_band", ""))
                        for item in projected_candidates
                    }
                    projected_direction_summary[str(position)] = {
                        str(int(item.get("value", 0) or 0)): str(item.get("projection_direction", ""))
                        for item in projected_candidates
                    }
                    projected_step_summary[str(position)] = {
                        str(int(item.get("value", 0) or 0)): int(item.get("projection_step", 0) or 0)
                        for item in projected_candidates
                    }
                    projected_candidates.sort(
                        key=lambda item: (
                            0 if str(item.get("quality_band", "")) == "projected_local_compatible" else 1,
                            int(item.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            int(item.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            int(item.get("structure_penalty", 1 << 30) or (1 << 30)),
                            int(item.get("projection_step", 1 << 30) or (1 << 30)),
                            int(item.get("origin_priority", 1 << 30) or (1 << 30)),
                            -int(item.get("structure_gain", 0) or 0),
                            int(item.get("value", 0) or 0),
                        )
                    )
                    projected_rank_summary[str(position)] = [
                        {
                            "value": int(item.get("value", 0) or 0),
                            "quality_band": str(item.get("quality_band", "")),
                            "ci_distance_delta": int(item.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            "raw_distance_delta": int(item.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            "structure_delta": list(item.get("structure_delta", [])),
                            "projection_direction": str(item.get("projection_direction", "")),
                            "projection_step": int(item.get("projection_step", 0) or 0),
                            "origins": list(item.get("origins", [])),
                        }
                        for item in projected_candidates
                    ]
                    best_projected = projected_candidates[0] if projected_candidates else None
                    projected_kept_values[str(position)] = []
                    projected_dropped_values[str(position)] = [
                        int(item.get("value", 0) or 0)
                        for item in projected_candidates
                    ]
                    filtered_values = [
                        int(value) & 0xFF
                        for value in values
                        if (int(value) & 0xFF) not in projected_value_set
                    ]
                    guard_candidates: list[dict[str, object]] = []
                    guard_status_counts = {
                        "guard_passed": 0,
                        "guard_soft_rejected": 0,
                        "guard_hard_rejected": 0,
                    }
                    passed_values: list[int] = []
                    soft_candidates: list[dict[str, object]] = []
                    hard_dropped_count = 0
                    for value in filtered_values:
                        work = bytearray(base_bytes)
                        work[position] = int(value) & 0xFF
                        single_entry = _entry_for(bytes(work).hex() + DEFAULT_FIXED_SUFFIX_HEX)
                        radius = abs((int(value) & 0xFF) - base_bytes[position])
                        single_distance_delta = int(single_entry.get("ci_distance5", 1 << 30) or (1 << 30)) - int(
                            baseline_entry.get("ci_distance5", 1 << 30) or (1 << 30)
                        )
                        single_raw_delta = int(single_entry.get("raw_distance10", 1 << 30) or (1 << 30)) - int(
                            baseline_entry.get("raw_distance10", 1 << 30) or (1 << 30)
                        )
                        single_rank = _pair_structure_rank(single_entry, transform_model)
                        baseline_rank = _pair_structure_rank(baseline_entry, transform_model)
                        origins = list(dict.fromkeys(origin_map.get(int(value) & 0xFF, [])))
                        if (
                            value == base_bytes[position]
                            or single_distance_delta <= EXACT1_PAIR_SINGLE_BYTE_GUARD_SLACK
                            or single_raw_delta <= EXACT1_PAIR_SINGLE_BYTE_GUARD_SLACK
                            or single_rank > baseline_rank
                        ):
                            status = "guard_passed"
                            passed_values.append(int(value) & 0xFF)
                        elif radius <= EXACT1_PAIR_SINGLE_BYTE_SOFT_RADIUS:
                            status = "guard_soft_rejected"
                            soft_quality = _exact1_single_byte_soft_quality(
                                radius=radius,
                                distance_delta=single_distance_delta,
                                raw_delta=single_raw_delta,
                                structure_rank=single_rank,
                                baseline_rank=baseline_rank,
                                origins=origins,
                            )
                            soft_candidates.append(
                                {
                                    "value": int(value) & 0xFF,
                                    "radius": radius,
                                    "ci_distance_delta": single_distance_delta,
                                    "raw_distance_delta": single_raw_delta,
                                    "pair_structure_rank": list(single_rank),
                                    "quality_band": str(soft_quality.get("quality_band", "")),
                                    "structure_delta": list(soft_quality.get("structure_delta", ())),
                                    "structure_penalty": int(soft_quality.get("structure_penalty", 0) or 0),
                                    "structure_gain": int(soft_quality.get("structure_gain", 0) or 0),
                                    "origin_priority": int(soft_quality.get("origin_priority", 0) or 0),
                                    "sort_key": list(soft_quality.get("sort_key", ())),
                                    "origins": origins,
                                    "family": "escape_neighbor_soft_family",
                                }
                            )
                        else:
                            status = "guard_hard_rejected"
                            hard_dropped_count += 1
                        guard_status_counts[status] = guard_status_counts.get(status, 0) + 1
                        guard_candidates.append(
                            {
                                "value": int(value) & 0xFF,
                                "status": status,
                                "radius": radius,
                                "ci_distance_delta": single_distance_delta,
                                "raw_distance_delta": single_raw_delta,
                                "pair_structure_rank": list(single_rank),
                                "origins": origins,
                            }
                        )
                    soft_candidates.sort(
                        key=lambda item: (
                            int(item.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            int(item.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            sum(max(0, -int(value)) for value in item.get("structure_delta", [])[:3]),
                            int(item.get("radius", 1 << 30) or (1 << 30)),
                            (
                                1 << 30
                                if item.get("origin_priority", None) is None
                                else int(item.get("origin_priority", 1 << 30))
                            ),
                            -int(item.get("structure_gain", 0) or 0),
                            int(item.get("value", 0) or 0),
                        )
                    )
                    best_neighbor = soft_candidates[0] if soft_candidates else None
                    competitive_candidates: list[dict[str, object]] = []
                    projected_summary_item: dict[str, object] | None = None
                    if best_projected is not None:
                        projected_summary_item = {
                            "family": "projected_soft_family",
                            "value": int(best_projected.get("value", 0) or 0),
                            "quality_band": str(best_projected.get("quality_band", "")),
                            "ci_distance_delta": int(best_projected.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            "raw_distance_delta": int(best_projected.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            "structure_delta": list(best_projected.get("structure_delta", [])),
                            "structure_penalty": int(best_projected.get("structure_penalty", 0) or 0),
                            "structure_gain": int(best_projected.get("structure_gain", 0) or 0),
                            "projection_step": int(best_projected.get("projection_step", 0) or 0),
                            "origin_priority": int(best_projected.get("origin_priority", 0) or 0),
                            "origins": list(best_projected.get("origins", [])),
                        }
                        competitive_candidates.append(projected_summary_item)
                    neighbor_summary_item: dict[str, object] | None = None
                    if best_neighbor is not None:
                        neighbor_summary_item = {
                            "family": "escape_neighbor_soft_family",
                            "value": int(best_neighbor.get("value", 0) or 0),
                            "quality_band": str(best_neighbor.get("quality_band", "")),
                            "ci_distance_delta": int(best_neighbor.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            "raw_distance_delta": int(best_neighbor.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            "structure_delta": list(best_neighbor.get("structure_delta", [])),
                            "structure_penalty": int(best_neighbor.get("structure_penalty", 0) or 0),
                            "structure_gain": int(best_neighbor.get("structure_gain", 0) or 0),
                            "radius": int(best_neighbor.get("radius", 0) or 0),
                            "origin_priority": int(best_neighbor.get("origin_priority", 0) or 0),
                            "origins": list(best_neighbor.get("origins", [])),
                        }
                        competitive_candidates.append(neighbor_summary_item)
                        neighbor_summary_by_pos[int(position)] = dict(neighbor_summary_item)
                    competitive_candidates.sort(key=_exact1_soft_family_competition_key)
                    winner_item = competitive_candidates[0] if competitive_candidates else None
                    loser_item = competitive_candidates[1] if len(competitive_candidates) > 1 else None
                    competitive_status = "projected_family_empty"
                    delta_gap = {"ci_distance_delta_gap": 0, "raw_distance_delta_gap": 0, "structure_penalty_gap": 0}
                    if projected_summary_item is not None and neighbor_summary_item is None:
                        competitive_status = "projected_beats_neighbor"
                    elif projected_summary_item is not None and neighbor_summary_item is not None:
                        delta_gap = {
                            "ci_distance_delta_gap": int(projected_summary_item.get("ci_distance_delta", 0) or 0)
                            - int(neighbor_summary_item.get("ci_distance_delta", 0) or 0),
                            "raw_distance_delta_gap": int(projected_summary_item.get("raw_distance_delta", 0) or 0)
                            - int(neighbor_summary_item.get("raw_distance_delta", 0) or 0),
                            "structure_penalty_gap": int(projected_summary_item.get("structure_penalty", 0) or 0)
                            - int(neighbor_summary_item.get("structure_penalty", 0) or 0),
                        }
                        if winner_item is projected_summary_item:
                            competitive_status = "projected_beats_neighbor"
                        elif int(delta_gap["ci_distance_delta_gap"]) > 0:
                            competitive_status = "projected_loses_on_distance"
                        elif int(delta_gap["raw_distance_delta_gap"]) > 0:
                            competitive_status = "projected_loses_on_raw"
                        elif int(delta_gap["structure_penalty_gap"]) > 0:
                            competitive_status = "projected_loses_on_structure"
                        else:
                            competitive_status = "projected_runner_up_kept_for_diagnostic"
                    elif neighbor_summary_item is not None:
                        competitive_status = "projected_family_empty"
                    soft_promoted = []
                    if winner_item is not None:
                        promoted_value = int(winner_item.get("value", 0) or 0) & 0xFF
                        if promoted_value != base_bytes[position]:
                            soft_promoted = [promoted_value]
                    if projected_summary_item is not None:
                        projected_summary_by_pos[int(position)] = dict(projected_summary_item)
                        if winner_item is projected_summary_item:
                            projected_kept_values[str(position)] = [int(projected_summary_item.get("value", 0) or 0)]
                            projected_dropped_values[str(position)] = [
                                int(item.get("value", 0) or 0)
                                for item in projected_candidates
                                if int(item.get("value", 0) or 0) not in projected_kept_values[str(position)]
                            ]
                            projected_winner_by_pos[int(position)] = dict(projected_summary_item)
                        projected_competitive_status[str(position)] = competitive_status
                        projected_competitive_winner[str(position)] = dict(winner_item or projected_summary_item)
                        if loser_item is not None and winner_item is not projected_summary_item:
                            projected_blocked_by_neighbor[str(position)] = dict(loser_item if loser_item is neighbor_summary_item else neighbor_summary_item or {})
                        elif competitive_status != "projected_beats_neighbor" and neighbor_summary_item is not None:
                            projected_blocked_by_neighbor[str(position)] = dict(neighbor_summary_item)
                        projected_best_delta_gap[str(position)] = dict(delta_gap)
                        projected_vs_neighbor_summary[str(position)] = {
                            "projected": dict(projected_summary_item),
                            "neighbor": dict(neighbor_summary_item) if neighbor_summary_item is not None else {},
                            "winner_family": str((winner_item or {}).get("family", "")),
                            "winner_value": int((winner_item or {}).get("value", 0) or 0),
                            "status": competitive_status,
                        }
                    kept_values = list(dict.fromkeys([*passed_values, *soft_promoted]))
                    if base_bytes[position] not in kept_values:
                        kept_values.insert(0, base_bytes[position])
                        origin_map.setdefault(base_bytes[position], []).append("single_byte_guard_base")
                    guard_summary[str(position)] = {
                        "input": len(values),
                        "kept": len(kept_values),
                        "passed": len(passed_values),
                        "soft_promoted": len(soft_promoted),
                        "hard_dropped": hard_dropped_count,
                        "dropped": hard_dropped_count + max(0, len(soft_candidates) - len(soft_promoted)),
                    }
                    guard_candidates_by_pos[str(position)] = guard_candidates
                    guard_status_counts_by_pos[str(position)] = guard_status_counts
                    guard_soft_promoted_values[str(position)] = soft_promoted
                    guard_soft_quality_band[str(position)] = {
                        str(int(item.get("value", 0) or 0)): str(item.get("quality_band", ""))
                        for item in soft_candidates
                    }
                    guard_soft_rank_summary[str(position)] = [
                        {
                            "value": int(item.get("value", 0) or 0),
                            "quality_band": str(item.get("quality_band", "")),
                            "ci_distance_delta": int(item.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                            "raw_distance_delta": int(item.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                            "structure_delta": list(item.get("structure_delta", [])),
                            "radius": int(item.get("radius", 0) or 0),
                            "origin_priority": int(item.get("origin_priority", 0) or 0),
                            "origins": list(item.get("origins", [])),
                        }
                        for item in soft_candidates
                    ]
                    guard_soft_distance_delta[str(position)] = {
                        str(int(item.get("value", 0) or 0)): int(item.get("ci_distance_delta", 1 << 30) or (1 << 30))
                        for item in soft_candidates
                    }
                    guard_soft_raw_delta[str(position)] = {
                        str(int(item.get("value", 0) or 0)): int(item.get("raw_distance_delta", 1 << 30) or (1 << 30))
                        for item in soft_candidates
                    }
                    guard_soft_structure_delta[str(position)] = {
                        str(int(item.get("value", 0) or 0)): list(item.get("structure_delta", []))
                        for item in soft_candidates
                    }
                    guard_nonbase_starved[str(position)] = not any(value != base_bytes[position] for value in kept_values)
                    reached_pair_pool = [
                        int(value) & 0xFF
                        for value in kept_values
                        if (int(value) & 0xFF) in projected_value_set
                    ]
                    ranked_out = sorted(projected_value_set.difference(set(reached_pair_pool)))
                    projected_generated_but_distance_explosive = [
                        int(item.get("value", 0) or 0)
                        for item in projected_candidates
                        if str(item.get("quality_band", "")) == "projected_distance_explosive"
                    ]
                    projected_local_compatible_values = [
                        int(item.get("value", 0) or 0)
                        for item in projected_candidates
                        if str(item.get("quality_band", "")) == "projected_local_compatible"
                    ]
                    source_reject_reasons[str(position)] = {
                        "raw_source_present_but_too_far": [
                            int(value) & 0xFF
                            for value in projection_details.get("raw_source_present_but_too_far", [])
                        ],
                        "projected_local_value_generated": sorted(projected_value_set),
                        "projected_generated_but_distance_explosive": projected_generated_but_distance_explosive,
                        "projected_local_compatible_but_ranked_out": [
                            value for value in projected_local_compatible_values if value in ranked_out
                        ],
                        "projected_local_compatible_reached_pair_pool": [
                            value for value in projected_local_compatible_values if value in reached_pair_pool
                        ],
                        "projected_value_ranked_out": ranked_out,
                        "projected_value_reached_pair_pool": reached_pair_pool,
                    }
                    if position == int(left):
                        escape_left_values = list(dict.fromkeys(kept_values))
                    else:
                        escape_right_values = list(dict.fromkeys(kept_values))
            pair_generation_details["pair_single_byte_guard_summary"][pair_key] = guard_summary
            pair_generation_details["pair_single_byte_guard_candidates"][pair_key] = guard_candidates_by_pos
            pair_generation_details["pair_single_byte_guard_status_counts"][pair_key] = guard_status_counts_by_pos
            pair_generation_details["pair_guard_soft_promoted_values"][pair_key] = guard_soft_promoted_values
            pair_generation_details["pair_guard_nonbase_starved"][pair_key] = guard_nonbase_starved
            pair_generation_details["pair_guard_soft_quality_band"][pair_key] = guard_soft_quality_band
            pair_generation_details["pair_guard_soft_rank_summary"][pair_key] = guard_soft_rank_summary
            pair_generation_details["pair_guard_soft_distance_delta"][pair_key] = guard_soft_distance_delta
            pair_generation_details["pair_guard_soft_raw_delta"][pair_key] = guard_soft_raw_delta
            pair_generation_details["pair_guard_soft_structure_delta"][pair_key] = guard_soft_structure_delta
            pair_generation_details["pair_projected_vs_neighbor_summary"][pair_key] = projected_vs_neighbor_summary
            pair_generation_details["pair_projected_competitive_status"][pair_key] = projected_competitive_status
            pair_generation_details["pair_projected_competitive_winner"][pair_key] = projected_competitive_winner
            pair_generation_details["pair_projected_blocked_by_neighbor"][pair_key] = projected_blocked_by_neighbor
            pair_generation_details["pair_projected_best_delta_gap"][pair_key] = projected_best_delta_gap
            pair_generation_details["pair_escape_source_projected_quality_band"][pair_key] = projected_quality_band
            pair_generation_details["pair_escape_source_projected_rank_summary"][pair_key] = projected_rank_summary
            pair_generation_details["pair_escape_source_projected_direction"][pair_key] = projected_direction_summary
            pair_generation_details["pair_escape_source_projected_step"][pair_key] = projected_step_summary
            pair_generation_details["pair_escape_source_projected_kept_values"][pair_key] = projected_kept_values
            pair_generation_details["pair_escape_source_projected_dropped_values"][pair_key] = projected_dropped_values
            pair_generation_details["pair_escape_source_reject_reasons"][pair_key] = source_reject_reasons
            pair_generation_details["pair_escape_pool"][pair_key] = {
                str(left): escape_left_values[:EXACT1_PAIR_ESCAPE_VALUE_LIMIT],
                str(right): escape_right_values[:EXACT1_PAIR_ESCAPE_VALUE_LIMIT],
            }
            pair_generation_details["pair_neighbor_generation_summary"][pair_key]["escape_generated"] = (
                len(escape_left_values) * len(escape_right_values)
            )
            pair_generation_details["pair_mutation_radius_summary"][pair_key]["escape_left"] = [
                abs(value - base_bytes[left]) for value in escape_left_values
            ]
            pair_generation_details["pair_mutation_radius_summary"][pair_key]["escape_right"] = [
                abs(value - base_bytes[right]) for value in escape_right_values
            ]

        def _pair_value_source(position: int, value: int) -> str:
            normalized = int(value) & 0xFF
            if normalized == int(base_bytes[position]):
                return "base"
            neighbor = neighbor_summary_by_pos.get(int(position), {})
            if neighbor and normalized == int(neighbor.get("value", -1) or -1):
                return "neighbor"
            projected_winner = projected_winner_by_pos.get(int(position), {})
            if projected_winner and normalized == int(projected_winner.get("value", -1) or -1):
                return "projected_winner"
            projected = projected_summary_by_pos.get(int(position), {})
            if projected and normalized == int(projected.get("value", -1) or -1):
                return "projected_runner_up"
            return "other"

        def _projected_winner_available_items() -> list[dict[str, object]]:
            return [
                {
                    "position": int(position),
                    "value": int(winner.get("value", 0) or 0) & 0xFF,
                    "base_value": int(base_bytes[int(position)]),
                    "quality_band": str(winner.get("quality_band", "")),
                    "ci_distance_delta": int(winner.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                    "raw_distance_delta": int(winner.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                }
                for position, winner in sorted(projected_winner_by_pos.items())
            ]

        def _pair_entries_for_values(
            left_candidates: Sequence[int],
            right_candidates: Sequence[int],
            *,
            escape_mode: str,
            left_origin_map: dict[int, list[str]] | None = None,
            right_origin_map: dict[int, list[str]] | None = None,
        ) -> list[dict[str, object]]:
            entries: list[dict[str, object]] = []
            for left_value in dict.fromkeys(left_candidates):
                for right_value in dict.fromkeys(right_candidates):
                    work = bytearray(base_bytes)
                    work[left] = left_value
                    work[right] = right_value
                    candidate_hex = bytes(work).hex() + DEFAULT_FIXED_SUFFIX_HEX
                    entry = dict(_entry_for(candidate_hex))
                    mutation_radius = max(
                        abs(int(left_value) - int(base_bytes[left])),
                        abs(int(right_value) - int(base_bytes[right])),
                    )
                    projected_available = _projected_winner_available_items()
                    projected_contributions: list[dict[str, object]] = []
                    values_by_pos = {int(left): int(left_value) & 0xFF, int(right): int(right_value) & 0xFF}
                    for winner_position, winner in projected_winner_by_pos.items():
                        winner_value = int(winner.get("value", 0) or 0) & 0xFF
                        if values_by_pos.get(int(winner_position)) != winner_value:
                            continue
                        paired_position = int(right) if int(winner_position) == int(left) else int(left)
                        paired_value = values_by_pos.get(paired_position, int(base_bytes[paired_position]))
                        projected_contributions.append(
                            {
                                "position": int(winner_position),
                                "value": winner_value,
                                "quality_band": str(winner.get("quality_band", "")),
                                "ci_distance_delta": int(winner.get("ci_distance_delta", 1 << 30) or (1 << 30)),
                                "raw_distance_delta": int(winner.get("raw_distance_delta", 1 << 30) or (1 << 30)),
                                "paired_position": paired_position,
                                "paired_value": int(paired_value) & 0xFF,
                                "paired_source": _pair_value_source(paired_position, int(paired_value) & 0xFF),
                            }
                        )
                    entry.update(
                        {
                            "pair_positions": [int(left), int(right)],
                            "pair_values": [int(left_value), int(right_value)],
                            "pair_escape_mode": escape_mode,
                            "pair_candidate_origin": (
                                "exact1_escape_neighbors"
                                if frontier_submode == FRONTIER_EXACT1_SUBMODE and escape_mode == "escape"
                                else "exact1_preserve_neighbors"
                                if frontier_submode == FRONTIER_EXACT1_SUBMODE
                                else "profile_pairs"
                            ),
                            "pair_neighbor_mode": (
                                "escape_neighbors"
                                if frontier_submode == FRONTIER_EXACT1_SUBMODE and escape_mode == "escape"
                                else "preserve_neighbors"
                                if frontier_submode == FRONTIER_EXACT1_SUBMODE
                                else "profile_pairs"
                            ),
                            "pair_mutation_radius": int(mutation_radius),
                            "pair_value_origin_by_pos": {
                                str(left): list(dict.fromkeys((left_origin_map or {}).get(int(left_value), []))),
                                str(right): list(dict.fromkeys((right_origin_map or {}).get(int(right_value), []))),
                            },
                            "pair_projected_winner_available": projected_available,
                            "pair_projected_winner_contributions": projected_contributions,
                            "pair_projected_boundary_mix": [
                                str(item.get("paired_source", ""))
                                for item in projected_contributions
                                if str(item.get("paired_source", ""))
                            ],
                        }
                    )
                    entries.append(entry)
            entries.sort(
                key=lambda item: _guided_sort_key(
                    item,
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                )
            )
            return entries

        def _projected_boundary_entries() -> list[dict[str, object]]:
            if not projected_winner_by_pos:
                return []
            boundary_entries: list[dict[str, object]] = []
            seen_boundary: set[str] = set()
            for winner_position, winner in sorted(projected_winner_by_pos.items()):
                if int(winner_position) not in {int(left), int(right)}:
                    continue
                other_position = int(right) if int(winner_position) == int(left) else int(left)
                other_options: list[tuple[int, str]] = [(int(base_bytes[other_position]), "base")]
                neighbor = neighbor_summary_by_pos.get(other_position, {})
                if neighbor:
                    other_options.append((int(neighbor.get("value", 0) or 0) & 0xFF, "neighbor"))
                projected_other = projected_summary_by_pos.get(other_position, {})
                projected_other_value = int(projected_other.get("value", -1) or -1)
                if projected_other and not (
                    other_position in projected_winner_by_pos
                    and projected_other_value == int(projected_winner_by_pos[other_position].get("value", -2) or -2)
                ):
                    other_options.append((projected_other_value & 0xFF, "projected_runner_up"))
                for other_value, other_source in dict.fromkeys(other_options):
                    if int(winner_position) == int(left):
                        left_value = int(winner.get("value", 0) or 0) & 0xFF
                        right_value = int(other_value) & 0xFF
                    else:
                        left_value = int(other_value) & 0xFF
                        right_value = int(winner.get("value", 0) or 0) & 0xFF
                    generated = _pair_entries_for_values(
                        [left_value],
                        [right_value],
                        escape_mode="escape",
                        left_origin_map=escape_left_origins,
                        right_origin_map=escape_right_origins,
                    )
                    if not generated:
                        continue
                    entry = generated[0]
                    candidate_hex = _candidate_hex_from_entry(entry)
                    if not candidate_hex or candidate_hex in seen_boundary:
                        continue
                    seen_boundary.add(candidate_hex)
                    entry["pair_candidate_origin"] = (
                        "exact1_projected_preserve_lane"
                        if other_source == "base"
                        else "exact1_projected_boundary"
                    )
                    entry["pair_neighbor_mode"] = (
                        "projected_preserve_lane"
                        if other_source == "base"
                        else "projected_boundary"
                    )
                    entry["pair_projected_boundary_role"] = f"projected_winner_with_{other_source}"
                    boundary_entries.append(entry)
            boundary_entries.sort(
                key=lambda item: _exact1_escape_profile_sort_key(
                    item,
                    transform_model,
                    baseline_entry=baseline_entry,
                )
            )
            return boundary_entries

        pair_entries: list[dict[str, object]]
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            preserve_entries = _pair_entries_for_values(
                preserve_left_values,
                preserve_right_values,
                escape_mode="preserve",
                left_origin_map=preserve_left_origins,
                right_origin_map=preserve_right_origins,
            )
            escape_entries: list[dict[str, object]] = []
            if baseline_entry and not any(
                _frontier_offline_improved(entry, baseline_entry, frontier_submode=frontier_submode)
                for entry in preserve_entries
            ):
                escape_entries = _pair_entries_for_values(
                    escape_left_values,
                    escape_right_values,
                    escape_mode="escape",
                    left_origin_map=escape_left_origins,
                    right_origin_map=escape_right_origins,
                )
                boundary_entries = _projected_boundary_entries()
                if boundary_entries:
                    preserve_lane_entries = [
                        entry
                        for entry in boundary_entries
                        if str(entry.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane"
                    ]
                    boundary_escape_entries = [
                        entry
                        for entry in boundary_entries
                        if str(entry.get("pair_candidate_origin", "")) != "exact1_projected_preserve_lane"
                    ]
                    merged_escape_entries: list[dict[str, object]] = []
                    seen_escape_hex: set[str] = set()
                    for entry in [*preserve_lane_entries, *boundary_escape_entries, *escape_entries]:
                        candidate_hex = _candidate_hex_from_entry(entry)
                        if not candidate_hex or candidate_hex in seen_escape_hex:
                            continue
                        seen_escape_hex.add(candidate_hex)
                        merged_escape_entries.append(entry)
                    escape_entries = merged_escape_entries
                    pair_generation_details["pair_projected_preserve_candidates"][pair_key] = [
                        _compact_pair_candidate(item) for item in preserve_lane_entries
                    ]
                    pair_generation_details["pair_projected_boundary_candidates"][pair_key] = [
                        _compact_pair_candidate(item) for item in boundary_escape_entries
                    ]
                else:
                    pair_generation_details["pair_projected_preserve_candidates"][pair_key] = []
                    pair_generation_details["pair_projected_boundary_candidates"][pair_key] = []
                escape_entries.sort(
                    key=lambda item: _exact1_escape_profile_sort_key(
                        item,
                        transform_model,
                        baseline_entry=baseline_entry,
                    )
                )
            pair_key = f"{left},{right}"
            projected_preserve_entries = [
                entry
                for entry in escape_entries
                if str(entry.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane"
            ][:1]
            regular_escape_entries = [
                entry
                for entry in escape_entries
                if str(entry.get("pair_candidate_origin", "")) != "exact1_projected_preserve_lane"
            ]
            kept_escape = regular_escape_entries[: min(max(0, top_per_pair), EXACT1_PAIR_PROFILE_ESCAPE_TOP)]
            seen_kept_escape = {_candidate_hex_from_entry(entry) for entry in kept_escape}
            for entry in projected_preserve_entries:
                candidate_hex = _candidate_hex_from_entry(entry)
                if candidate_hex and candidate_hex not in seen_kept_escape:
                    kept_escape.append(entry)
                    seen_kept_escape.add(candidate_hex)
            remaining_slots = max(0, top_per_pair - len(kept_escape))
            kept_preserve = preserve_entries[: min(remaining_slots, EXACT1_PAIR_PROFILE_PRESERVE_TOP)]
            pair_entries = [*kept_escape, *kept_preserve]
            pair_generation_details["pair_profile_preserve_entries"][pair_key] = [
                _compact_pair_candidate(item) for item in preserve_entries
            ]
            pair_generation_details["pair_profile_escape_entries"][pair_key] = [
                _compact_pair_candidate(item) for item in escape_entries
            ]
            pair_generation_details["pair_profile_kept_preserve"][pair_key] = [
                _compact_pair_candidate(item) for item in kept_preserve
            ]
            pair_generation_details["pair_profile_kept_escape"][pair_key] = [
                _compact_pair_candidate(item) for item in kept_escape
            ]
            drop_reason = "profile_kept"
            if escape_entries and not kept_escape:
                drop_reason = "profile_ranked_out"
            elif not escape_entries:
                drop_reason = "profile_source_empty"
            pair_generation_details["pair_profile_drop_reasons"][pair_key] = {
                "preserve": "profile_kept" if kept_preserve else "profile_ranked_out",
                "escape": drop_reason,
            }
            pair_generation_details["pair_profile_truncation_summary"][pair_key] = {
                "preserve_total": len(preserve_entries),
                "escape_total": len(escape_entries),
                "preserve_kept": len(kept_preserve),
                "escape_kept": len(kept_escape),
                "top_per_pair": max(1, top_per_pair),
            }
        else:
            pair_entries = _pair_entries_for_values(
                left_values,
                right_values,
                escape_mode="profile",
            )
        pair_profiles[(int(left), int(right))] = pair_entries[: max(1, top_per_pair)]
    return pair_profiles, pair_generation_details


def _hot_positions_from_pair_profiles(
    pair_profiles: dict[tuple[int, int], list[dict[str, object]]],
    *,
    transform_model: SamplereverseTransformModel,
    anchor_mode: str,
    frontier_submode: str = "",
    max_positions: int = HOT_POSITION_LIMIT,
) -> list[int]:
    ranked_pairs = sorted(
        (
            ((left, right), entries[0])
            for (left, right), entries in pair_profiles.items()
            if entries
        ),
        key=lambda item: _guided_sort_key(
            item[1],
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        ),
    )[:FRONTIER_TOP_PAIR_LIMIT]
    scores: dict[int, list[int]] = {}
    for rank, ((left, right), _) in enumerate(ranked_pairs, 1):
        scores.setdefault(left, []).append(rank)
        scores.setdefault(right, []).append(rank)
    ordered = sorted(scores.items(), key=lambda item: (-len(item[1]), tuple(item[1]), item[0]))
    return [position for position, _ in ordered[:max_positions]]


def _diverse_pair_frontier_pool(
    pair_profiles: dict[tuple[int, int], list[dict[str, object]]],
    *,
    transform_model: SamplereverseTransformModel,
    anchor_mode: str,
    frontier_submode: str = "",
    pair_profile_details: dict[str, object] | None = None,
    baseline_entry: dict[str, object] | None = None,
    keep_limit: int = FRONTIER_PAIR_SEED_LIMIT,
) -> tuple[list[dict[str, object]], dict[str, int], dict[str, object]]:
    if frontier_submode == FRONTIER_EXACT1_SUBMODE and baseline_entry:
        profile_details_provided = pair_profile_details is not None
        ranked_pairs = [
            (pair_positions, entries)
            for pair_positions, entries in sorted(
                pair_profiles.items(),
                key=lambda item: _guided_sort_key(
                    item[1][0],
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                )
                if item[1]
                else (1 << 30,),
            )
            if entries
        ]
        drop_reasons: dict[str, int] = {}
        diagnostics: dict[str, object] = {
            "pair_escape_mode": "exact1_dual_lane",
            "pair_preserve_pool": [],
            "pair_escape_pool": [],
            "pair_neighbor_generation_summary": dict((pair_profile_details or {}).get("pair_neighbor_generation_summary", {})),
            "pair_mutation_radius_summary": dict((pair_profile_details or {}).get("pair_mutation_radius_summary", {})),
            "pair_escape_candidates_kept": [],
            "pair_escape_candidates_dropped": [],
            "pair_gate_failed_escape": [],
            "pair_gate_kept_escape": [],
            "pair_borderline_escape_candidates": [],
            "pair_near_local_escape_candidates": [],
            "pair_wide_local_escape_candidates": [],
            "pair_best_preserve_candidate": None,
            "pair_best_escape_candidate": None,
            "pair_best_local_escape": {},
            "pair_best_hard_escape": {},
            "pair_local_escape_candidate_count": 0,
            "pair_hard_escape_candidate_count": 0,
            "pair_local_escape_borderline_count": 0,
            "pair_near_local_escape_count": 0,
            "pair_wide_local_escape_count": 0,
            "pair_local_escape_reject_count": 0,
            "pair_escape_source_statuses": {},
            "pair_escape_status_by_lane": {},
            "pair_escape_lane_counts": {},
            "pair_radius_band_counts": {},
            "pair_gate_input_summary": {},
            "pair_gate_borderline_summary": {},
            "pair_single_byte_guard_summary": dict((pair_profile_details or {}).get("pair_single_byte_guard_summary", {})),
            "pair_profile_preserve_entries": dict((pair_profile_details or {}).get("pair_profile_preserve_entries", {})),
            "pair_profile_escape_entries": dict((pair_profile_details or {}).get("pair_profile_escape_entries", {})),
            "pair_profile_kept_preserve": dict((pair_profile_details or {}).get("pair_profile_kept_preserve", {})),
            "pair_profile_kept_escape": dict((pair_profile_details or {}).get("pair_profile_kept_escape", {})),
            "pair_profile_drop_reasons": dict((pair_profile_details or {}).get("pair_profile_drop_reasons", {})),
            "pair_profile_truncation_summary": dict((pair_profile_details or {}).get("pair_profile_truncation_summary", {})),
            "pair_projected_winner_gate_summary": {},
            "pair_projected_winner_gate_status_counts": {},
            "pair_projected_preserve_entries": [],
            "pair_projected_boundary_entries": [],
            "pair_projected_boundary_wide_candidates": [],
        }
        accepted_local: list[dict[str, object]] = []
        accepted_preserve: list[dict[str, object]] = []
        hard_diag_samples: list[dict[str, object]] = []
        seen_local: set[str] = set()
        seen_preserve: set[str] = set()

        for pair_positions, entries in ranked_pairs[:FRONTIER_TOP_PAIR_LIMIT]:
            pair_key = f"{pair_positions[0]},{pair_positions[1]}"
            profile_escape_entries = diagnostics["pair_profile_escape_entries"].get(pair_key, [])
            kept_escape_entries = diagnostics["pair_profile_kept_escape"].get(pair_key, [])
            pair_lane_counts = {"local_escape": 0, "hard_escape": 0}
            pair_status_by_lane = {"local_escape": "profile_source_empty", "hard_escape": "profile_source_empty"}
            preserve_candidates: list[dict[str, object]] = []
            local_kept_candidates: list[dict[str, object]] = []
            local_near_borderline_candidates: list[dict[str, object]] = []
            local_wide_borderline_candidates: list[dict[str, object]] = []
            local_filtered_candidates: list[dict[str, object]] = []
            hard_filtered_candidates: list[dict[str, object]] = []

            for raw_entry in entries:
                candidate = dict(raw_entry)
                candidate.setdefault("pair_positions", list(pair_positions))
                candidate_hex = _candidate_hex_from_entry(candidate)
                if not candidate_hex:
                    continue
                candidate_exact = int(candidate.get("ci_exact_wchars", 0) or 0)
                pair_escape_mode = str(candidate.get("pair_escape_mode", "")).strip()
                if not pair_escape_mode:
                    pair_escape_mode = "escape" if candidate_exact < int(baseline_entry.get("ci_exact_wchars", 0) or 0) else "preserve"
                candidate["pair_escape_mode"] = pair_escape_mode
                if pair_escape_mode != "escape":
                    preserve_candidates.append(candidate)
                    continue

                signal = _exact1_pair_escape_signal(candidate, baseline_entry, transform_model=transform_model)
                candidate["pair_escape_signal_score"] = int(signal.get("score", 1 << 30))
                candidate["pair_escape_signal_reason"] = str(signal.get("reason", ""))
                candidate["pair_escape_lane"] = str(signal.get("lane", ""))
                candidate["pair_escape_status"] = str(signal.get("status", "reject"))
                candidate["pair_escape_quality_band"] = str(signal.get("quality_band", ""))
                candidate["pair_borderline_quality_reason"] = str(signal.get("quality_reason", ""))
                candidate["pair_projected_winner_gate_status"] = _projected_winner_gate_status(candidate)
                compact = _compact_pair_candidate(candidate)
                diagnostics["pair_gate_input_summary"].setdefault(pair_key, []).append(compact)
                gate_status = str(candidate.get("pair_projected_winner_gate_status", ""))
                if gate_status:
                    diagnostics["pair_projected_winner_gate_summary"].setdefault(pair_key, []).append(compact)
                    status_counts = diagnostics["pair_projected_winner_gate_status_counts"].setdefault(pair_key, {})
                    status_counts[gate_status] = int(status_counts.get(gate_status, 0) or 0) + 1
                lane = str(signal.get("lane", ""))
                pair_lane_counts[lane] = pair_lane_counts.get(lane, 0) + 1
                quality_band = str(signal.get("quality_band", ""))
                if quality_band:
                    diagnostics["pair_radius_band_counts"].setdefault(pair_key, {})
                    diagnostics["pair_radius_band_counts"][pair_key][quality_band] = (
                        diagnostics["pair_radius_band_counts"][pair_key].get(quality_band, 0) + 1
                    )
                if lane == "local_escape":
                    diagnostics["pair_local_escape_candidate_count"] = int(
                        diagnostics.get("pair_local_escape_candidate_count", 0)
                    ) + 1
                elif lane == "hard_escape":
                    diagnostics["pair_hard_escape_candidate_count"] = int(
                        diagnostics.get("pair_hard_escape_candidate_count", 0)
                    ) + 1
                if lane == "local_escape":
                    if not diagnostics["pair_best_local_escape"].get(pair_key) or int(compact.get("pair_escape_signal_score", 1 << 30)) < int(
                        diagnostics["pair_best_local_escape"][pair_key].get("pair_escape_signal_score", 1 << 30)
                    ):
                        diagnostics["pair_best_local_escape"][pair_key] = compact
                    status = str(signal.get("status", "reject"))
                    if status == "keep":
                        local_kept_candidates.append(candidate)
                    elif status == "borderline":
                        diagnostics["pair_local_escape_borderline_count"] = int(
                            diagnostics.get("pair_local_escape_borderline_count", 0)
                        ) + 1
                        if quality_band == "near_local_escape":
                            local_near_borderline_candidates.append(candidate)
                            diagnostics["pair_near_local_escape_count"] = int(
                                diagnostics.get("pair_near_local_escape_count", 0)
                            ) + 1
                        else:
                            candidate["pair_drop_reason"] = "wide_local_escape_diagnostic"
                            local_wide_borderline_candidates.append(candidate)
                            diagnostics["pair_wide_local_escape_count"] = int(
                                diagnostics.get("pair_wide_local_escape_count", 0)
                            ) + 1
                    else:
                        candidate["pair_drop_reason"] = "gate_filtered_local_escape"
                        local_filtered_candidates.append(candidate)
                        diagnostics["pair_local_escape_reject_count"] = int(
                            diagnostics.get("pair_local_escape_reject_count", 0)
                        ) + 1
                else:
                    if not diagnostics["pair_best_hard_escape"].get(pair_key) or int(compact.get("pair_escape_signal_score", 1 << 30)) < int(
                        diagnostics["pair_best_hard_escape"][pair_key].get("pair_escape_signal_score", 1 << 30)
                    ):
                        diagnostics["pair_best_hard_escape"][pair_key] = compact
                    candidate["pair_drop_reason"] = "gate_filtered_hard_escape"
                    hard_filtered_candidates.append(candidate)

            preserve_candidates.sort(
                key=lambda item: _guided_sort_key(
                    item,
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                )
            )
            local_kept_candidates.sort(
                key=lambda item: (
                    int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    _guided_sort_key(
                        item,
                        transform_model,
                        anchor_mode=anchor_mode,
                        frontier_submode=frontier_submode,
                    ),
                )
            )
            local_near_borderline_candidates.sort(
                key=lambda item: (
                    int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    int(item.get("ci_distance5", 1 << 30) or (1 << 30)),
                    int(item.get("raw_distance10", 1 << 30) or (1 << 30)),
                    int(item.get("pair_mutation_radius", 1 << 30) or (1 << 30)),
                    -int(item.get("pair_wide_ascii_contiguous_8", 0) or 0),
                    -int(item.get("pair_wide_zero_high_pairs_8", 0) or 0),
                    -int(item.get("pair_flaglike_tail_pairs_8", 0) or 0),
                    str(item.get("candidate_hex", "")),
                )
            )
            local_wide_borderline_candidates.sort(
                key=lambda item: (
                    int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    int(item.get("ci_distance5", 1 << 30) or (1 << 30)),
                    int(item.get("raw_distance10", 1 << 30) or (1 << 30)),
                    int(item.get("pair_mutation_radius", 1 << 30) or (1 << 30)),
                    str(item.get("candidate_hex", "")),
                )
            )
            local_filtered_candidates.sort(
                key=lambda item: (
                    int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    _guided_sort_key(
                        item,
                        transform_model,
                        anchor_mode=anchor_mode,
                        frontier_submode=frontier_submode,
                    ),
                )
            )
            hard_filtered_candidates.sort(
                key=lambda item: (
                    int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    _guided_sort_key(
                        item,
                        transform_model,
                        anchor_mode=anchor_mode,
                        frontier_submode=frontier_submode,
                    ),
                )
            )

            if profile_details_provided and not profile_escape_entries:
                pair_status_by_lane["local_escape"] = "profile_source_empty"
                pair_status_by_lane["hard_escape"] = "profile_source_empty"
            elif profile_details_provided and profile_escape_entries and not kept_escape_entries:
                pair_status_by_lane["local_escape"] = "profile_ranked_out"
                pair_status_by_lane["hard_escape"] = "profile_ranked_out"
            else:
                if local_kept_candidates:
                    pair_status_by_lane["local_escape"] = "gate_kept_escape"
                elif local_near_borderline_candidates:
                    pair_status_by_lane["local_escape"] = "gate_borderline_escape"
                elif local_wide_borderline_candidates:
                    pair_status_by_lane["local_escape"] = "gate_filtered_wide_local_escape"
                elif local_filtered_candidates:
                    pair_status_by_lane["local_escape"] = "gate_filtered_local_escape"
                elif profile_escape_entries:
                    pair_status_by_lane["local_escape"] = "profile_ranked_out"
                if hard_filtered_candidates:
                    pair_status_by_lane["hard_escape"] = "gate_filtered_hard_escape"
                elif profile_escape_entries:
                    pair_status_by_lane["hard_escape"] = "gate_filtered_hard_escape"

            diagnostics["pair_escape_lane_counts"][pair_key] = pair_lane_counts
            diagnostics["pair_escape_status_by_lane"][pair_key] = pair_status_by_lane
            if pair_status_by_lane["local_escape"] == "gate_kept_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_kept_escape"
            elif pair_status_by_lane["local_escape"] == "gate_borderline_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_borderline_escape"
            elif pair_status_by_lane["local_escape"] == "gate_filtered_wide_local_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_wide_local_escape"
            elif pair_status_by_lane["local_escape"] == "gate_filtered_local_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_local_escape"
            elif pair_status_by_lane["hard_escape"] == "gate_filtered_hard_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_hard_escape"
            else:
                diagnostics["pair_escape_source_statuses"][pair_key] = pair_status_by_lane["local_escape"]
            diagnostics["pair_gate_borderline_summary"][pair_key] = {
                "kept_local": len(local_kept_candidates),
                "near_local": len(local_near_borderline_candidates),
                "wide_local": len(local_wide_borderline_candidates),
                "borderline_local": len(local_near_borderline_candidates) + len(local_wide_borderline_candidates),
                "rejected_local": len(local_filtered_candidates),
                "hard_escape": len(hard_filtered_candidates),
            }

            for candidate in local_kept_candidates[:EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR]:
                candidate_hex = _candidate_hex_from_entry(candidate)
                if candidate_hex in seen_local:
                    continue
                seen_local.add(candidate_hex)
                candidate["pair_drop_reason"] = ""
                accepted_local.append(candidate)
                compact_candidate = _compact_pair_candidate(candidate)
                diagnostics["pair_gate_kept_escape"].append(compact_candidate)
                if str(candidate.get("pair_candidate_origin", "")) == "exact1_projected_boundary":
                    diagnostics["pair_projected_boundary_entries"].append(compact_candidate)
                elif str(candidate.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane":
                    diagnostics["pair_projected_preserve_entries"].append(compact_candidate)
            for candidate in local_near_borderline_candidates[:EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR]:
                candidate_hex = _candidate_hex_from_entry(candidate)
                if candidate_hex in seen_local:
                    continue
                seen_local.add(candidate_hex)
                candidate["pair_drop_reason"] = "gate_borderline_escape"
                accepted_local.append(candidate)
                compact_candidate = _compact_pair_candidate(candidate)
                diagnostics["pair_borderline_escape_candidates"].append(compact_candidate)
                diagnostics["pair_near_local_escape_candidates"].append(compact_candidate)
                if str(candidate.get("pair_candidate_origin", "")) == "exact1_projected_boundary":
                    diagnostics["pair_projected_boundary_entries"].append(compact_candidate)
                elif str(candidate.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane":
                    diagnostics["pair_projected_preserve_entries"].append(compact_candidate)
            for candidate in local_wide_borderline_candidates[:EXACT1_PAIR_HARD_ESCAPE_DIAG_SAMPLES]:
                compact_candidate = _compact_pair_candidate(candidate)
                diagnostics["pair_wide_local_escape_candidates"].append(compact_candidate)
                diagnostics["pair_escape_candidates_dropped"].append(compact_candidate)
                if str(candidate.get("pair_candidate_origin", "")) == "exact1_projected_boundary":
                    diagnostics["pair_projected_boundary_wide_candidates"].append(compact_candidate)
            for candidate in local_kept_candidates[EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR:]:
                candidate["pair_drop_reason"] = "escape_signal_but_ranked_out"
                drop_reasons["escape_signal_but_ranked_out"] = drop_reasons.get("escape_signal_but_ranked_out", 0) + 1
                diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
            for candidate in local_near_borderline_candidates[EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR:]:
                candidate["pair_drop_reason"] = "escape_signal_but_ranked_out"
                drop_reasons["escape_signal_but_ranked_out"] = drop_reasons.get("escape_signal_but_ranked_out", 0) + 1
                diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
            for candidate in local_wide_borderline_candidates:
                drop_reasons["gate_filtered_wide_local_escape"] = drop_reasons.get("gate_filtered_wide_local_escape", 0) + 1
            for candidate in local_filtered_candidates:
                drop_reasons["gate_filtered_local_escape"] = drop_reasons.get("gate_filtered_local_escape", 0) + 1
                diagnostics["pair_gate_failed_escape"].append(_compact_pair_candidate(candidate))
                diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
            for candidate in hard_filtered_candidates:
                drop_reasons["gate_filtered_hard_escape"] = drop_reasons.get("gate_filtered_hard_escape", 0) + 1
                diagnostics["pair_gate_failed_escape"].append(_compact_pair_candidate(candidate))
                diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
            for candidate in hard_filtered_candidates[:EXACT1_PAIR_HARD_ESCAPE_DIAG_SAMPLES]:
                hard_diag_samples.append(candidate)
            for candidate in preserve_candidates:
                candidate_hex = _candidate_hex_from_entry(candidate)
                if candidate_hex in seen_preserve or candidate_hex in seen_local:
                    continue
                seen_preserve.add(candidate_hex)
                accepted_preserve.append(candidate)

        accepted = [*accepted_local, *accepted_preserve]
        selected = accepted[:keep_limit]
        projected_preserve_handoff = next(
            (
                item
                for item in accepted_local
                if str(item.get("pair_candidate_origin", "")) == "exact1_projected_preserve_lane"
                and str(item.get("pair_projected_boundary_role", "")) == "projected_winner_with_base"
                and str(item.get("pair_escape_quality_band", "")) in {"near_local_escape", "kept_local_escape"}
            ),
            None,
        )
        if projected_preserve_handoff and keep_limit > 0:
            projected_hex = _candidate_hex_from_entry(projected_preserve_handoff)
            selected_hexes = {_candidate_hex_from_entry(item) for item in selected}
            if projected_hex and projected_hex not in selected_hexes:
                if len(selected) < keep_limit:
                    selected.append(projected_preserve_handoff)
                else:
                    selected[-1] = projected_preserve_handoff
        preserve_candidates_all = [item for item in accepted_preserve]
        escape_candidates_all = [item for item in accepted_local]
        diagnostics["pair_preserve_pool"] = [_compact_pair_candidate(item) for item in preserve_candidates_all[:keep_limit]]
        diagnostics["pair_escape_pool"] = [_compact_pair_candidate(item) for item in escape_candidates_all[:keep_limit]]
        diagnostics["pair_escape_candidates_kept"] = [_compact_pair_candidate(item) for item in selected if str(item.get("pair_escape_mode", "")) == "escape"]
        if preserve_candidates_all:
            diagnostics["pair_best_preserve_candidate"] = _compact_pair_candidate(
                min(
                    preserve_candidates_all,
                    key=lambda item: _guided_sort_key(
                        item,
                        transform_model,
                        anchor_mode=anchor_mode,
                        frontier_submode=frontier_submode,
                    ),
                )
            )
        all_escape_candidates = [
            *(item for item in accepted_local),
            *(item for item in hard_diag_samples),
        ]
        if all_escape_candidates:
            diagnostics["pair_best_escape_candidate"] = _compact_pair_candidate(
                min(
                    all_escape_candidates,
                    key=lambda item: (
                        int(item.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                        _guided_sort_key(
                            item,
                            transform_model,
                            anchor_mode=anchor_mode,
                            frontier_submode=frontier_submode,
                        ),
                    ),
                )
            )
        selected.sort(
            key=lambda item: (
                0 if str(item.get("pair_escape_mode", "")) == "escape" else 1,
                _guided_sort_key(
                    item,
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                ),
            )
        )
        return selected[:keep_limit], drop_reasons, diagnostics

    profile_details_provided = pair_profile_details is not None
    ranked_pairs = [
        (pair_positions, entries)
        for pair_positions, entries in sorted(
            pair_profiles.items(),
            key=lambda item: _guided_sort_key(
                item[1][0],
                transform_model,
                anchor_mode=anchor_mode,
                frontier_submode=frontier_submode,
            )
            if item[1]
            else (1 << 30,),
        )
        if entries
    ]
    accepted: list[dict[str, object]] = []
    seen: set[str] = set()
    rank = 0
    drop_reasons: dict[str, int] = {}
    diagnostics: dict[str, object] = {
        "pair_escape_mode": "single_pool" if frontier_submode != FRONTIER_EXACT1_SUBMODE else "exact1_dual_lane",
        "pair_preserve_pool": [],
        "pair_escape_pool": [],
        "pair_escape_candidates_kept": [],
        "pair_escape_candidates_dropped": [],
        "pair_gate_failed_escape": [],
        "pair_gate_kept_escape": [],
        "pair_best_preserve_candidate": None,
        "pair_best_escape_candidate": None,
        "pair_escape_source_statuses": {},
        "pair_gate_input_summary": {},
        "pair_profile_preserve_entries": dict((pair_profile_details or {}).get("pair_profile_preserve_entries", {})),
        "pair_profile_escape_entries": dict((pair_profile_details or {}).get("pair_profile_escape_entries", {})),
        "pair_profile_kept_preserve": dict((pair_profile_details or {}).get("pair_profile_kept_preserve", {})),
        "pair_profile_kept_escape": dict((pair_profile_details or {}).get("pair_profile_kept_escape", {})),
        "pair_profile_drop_reasons": dict((pair_profile_details or {}).get("pair_profile_drop_reasons", {})),
        "pair_profile_truncation_summary": dict((pair_profile_details or {}).get("pair_profile_truncation_summary", {})),
    }
    pair_escape_status_counts: dict[str, dict[str, int]] = {}
    baseline_distance = int(baseline_entry.get("ci_distance5", 1 << 30) or (1 << 30)) if baseline_entry else (1 << 30)
    baseline_exact = int(baseline_entry.get("ci_exact_wchars", 0) or 0) if baseline_entry else 0
    baseline_raw = int(baseline_entry.get("raw_distance10", 1 << 30) or (1 << 30)) if baseline_entry else (1 << 30)
    max_rank = max((len(entries) for _, entries in ranked_pairs), default=0)
    while rank < max_rank:
        for pair_positions, entries in ranked_pairs[:FRONTIER_TOP_PAIR_LIMIT]:
            if rank >= len(entries):
                continue
            candidate = dict(entries[rank])
            candidate.setdefault("pair_positions", list(pair_positions))
            candidate_hex = _candidate_hex_from_entry(candidate)
            if not candidate_hex or candidate_hex in seen:
                continue
            drop_reason = ""
            if frontier_submode == FRONTIER_EXACT1_SUBMODE and baseline_entry:
                candidate_exact = int(candidate.get("ci_exact_wchars", 0) or 0)
                candidate_distance = int(candidate.get("ci_distance5", 1 << 30) or (1 << 30))
                candidate_raw = int(candidate.get("raw_distance10", 1 << 30) or (1 << 30))
                pair_escape_mode = "preserve" if candidate_exact >= baseline_exact else "escape"
                candidate["pair_escape_mode"] = pair_escape_mode
                if pair_escape_mode == "escape":
                    signal = _exact1_pair_escape_signal(candidate, baseline_entry, transform_model=transform_model)
                    candidate["pair_escape_signal_score"] = int(signal.get("score", 1 << 30))
                    candidate["pair_escape_signal_reason"] = str(signal.get("reason", ""))
                    candidate["pair_escape_lane"] = str(signal.get("lane", ""))
                    pair_key = ",".join(str(item) for item in candidate.get("pair_positions", []))
                    diagnostics["pair_gate_input_summary"].setdefault(pair_key, []).append(
                        {
                            "cand8_hex": str(candidate.get("cand8_hex", "")),
                            "pair_escape_signal_score": int(signal.get("score", 1 << 30)),
                            "pair_escape_signal_reason": str(signal.get("reason", "")),
                            "pair_escape_lane": str(signal.get("lane", "")),
                        }
                    )
                    if str(signal.get("lane", "")) == "hard_escape":
                        drop_reason = "gate_filtered_hard_escape"
                    elif not bool(signal.get("passed")):
                        drop_reason = "gate_filtered_local_escape"
                elif (
                    candidate_hex != _candidate_hex_from_entry(baseline_entry)
                    and candidate_distance > baseline_distance
                    and candidate_raw >= baseline_raw
                    and candidate_exact <= baseline_exact
                ):
                    drop_reason = "distance_not_improved"
            if drop_reason:
                candidate["pair_drop_reason"] = drop_reason
                drop_reasons[drop_reason] = drop_reasons.get(drop_reason, 0) + 1
                if candidate.get("pair_escape_mode") == "escape":
                    pair_key = ",".join(str(item) for item in candidate.get("pair_positions", []))
                    pair_escape_status_counts.setdefault(pair_key, {})
                    pair_escape_status_counts[pair_key][drop_reason] = pair_escape_status_counts[pair_key].get(drop_reason, 0) + 1
                    diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
                    diagnostics["pair_gate_failed_escape"].append(_compact_pair_candidate(candidate))
                continue
            seen.add(candidate_hex)
            candidate["pair_drop_reason"] = ""
            accepted.append(candidate)
            if candidate.get("pair_escape_mode") == "escape":
                pair_key = ",".join(str(item) for item in candidate.get("pair_positions", []))
                pair_escape_status_counts.setdefault(pair_key, {})
                pair_escape_status_counts[pair_key]["kept"] = pair_escape_status_counts[pair_key].get("kept", 0) + 1
                diagnostics["pair_gate_kept_escape"].append(_compact_pair_candidate(candidate))
        rank += 1
    selected = accepted[:keep_limit]
    for candidate in accepted[keep_limit:]:
        if str(candidate.get("pair_escape_mode", "")) == "escape":
            drop_reasons["escape_signal_but_ranked_out"] = drop_reasons.get("escape_signal_but_ranked_out", 0) + 1
            candidate_with_reason = dict(candidate)
            candidate_with_reason["pair_drop_reason"] = "escape_signal_but_ranked_out"
            diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate_with_reason))
    selected.sort(
        key=lambda item: _guided_sort_key(
            item,
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        )
    )
    preserve_candidates = [item for item in accepted if str(item.get("pair_escape_mode", "preserve")) != "escape"]
    escape_candidates = [item for item in accepted if str(item.get("pair_escape_mode", "")) == "escape"]
    diagnostics["pair_preserve_pool"] = [_compact_pair_candidate(item) for item in preserve_candidates[:keep_limit]]
    diagnostics["pair_escape_pool"] = [_compact_pair_candidate(item) for item in escape_candidates[:keep_limit]]
    diagnostics["pair_escape_candidates_kept"] = [_compact_pair_candidate(item) for item in selected if str(item.get("pair_escape_mode", "")) == "escape"]
    if preserve_candidates:
        diagnostics["pair_best_preserve_candidate"] = _compact_pair_candidate(
            min(
                preserve_candidates,
                key=lambda item: _guided_sort_key(
                    item,
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                ),
            )
        )
    if escape_candidates:
        diagnostics["pair_best_escape_candidate"] = _compact_pair_candidate(
            min(
                escape_candidates,
                key=lambda item: _guided_sort_key(
                    item,
                    transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                ),
            )
        )
    for pair_positions, entries in ranked_pairs:
        pair_key = f"{pair_positions[0]},{pair_positions[1]}"
        escape_entries = diagnostics["pair_profile_escape_entries"].get(pair_key, [])
        kept_escape_entries = diagnostics["pair_profile_kept_escape"].get(pair_key, [])
        status_counts = pair_escape_status_counts.get(pair_key, {})
        if not profile_details_provided:
            escape_seen = any(str(entry.get("pair_escape_mode", "")) == "escape" for entry in entries)
            if not escape_seen:
                diagnostics["pair_escape_source_statuses"][pair_key] = (
                    "profile_source_empty" if frontier_submode == FRONTIER_EXACT1_SUBMODE else "source_empty"
                )
            elif status_counts.get("kept", 0):
                diagnostics["pair_escape_source_statuses"][pair_key] = (
                    "gate_kept_escape" if frontier_submode == FRONTIER_EXACT1_SUBMODE else "source_used"
                )
            elif frontier_submode == FRONTIER_EXACT1_SUBMODE and status_counts.get("gate_filtered_hard_escape", 0):
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_hard_escape"
            elif frontier_submode == FRONTIER_EXACT1_SUBMODE and (
                status_counts.get("gate_filtered_local_escape", 0) or status_counts.get("escape_signal_but_ranked_out", 0)
            ):
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_local_escape"
            elif status_counts.get("escape_signal_but_ranked_out", 0):
                diagnostics["pair_escape_source_statuses"][pair_key] = "source_used_but_ranked_out"
            elif status_counts.get("exact_regressed_without_escape_signal", 0) or status_counts.get("distance_not_improved", 0):
                diagnostics["pair_escape_source_statuses"][pair_key] = (
                    "gate_filtered_local_escape" if frontier_submode == FRONTIER_EXACT1_SUBMODE else "frontier_gate_failed"
                )
            else:
                diagnostics["pair_escape_source_statuses"][pair_key] = (
                    "gate_filtered_local_escape" if frontier_submode == FRONTIER_EXACT1_SUBMODE else "source_used"
                )
            continue
        if not escape_entries:
            diagnostics["pair_escape_source_statuses"][pair_key] = "profile_source_empty"
        elif not kept_escape_entries:
            diagnostics["pair_escape_source_statuses"][pair_key] = "profile_ranked_out"
        elif status_counts.get("kept", 0):
            diagnostics["pair_escape_source_statuses"][pair_key] = "gate_kept_escape"
        elif status_counts.get("gate_filtered_hard_escape", 0):
            diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_hard_escape"
        elif status_counts.get("gate_filtered_local_escape", 0):
            diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_local_escape"
        else:
            diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_local_escape"
    return selected[:keep_limit], drop_reasons, diagnostics


def _triad_frontier_pool(
    *,
    base_anchor: str,
    pair_pool: Sequence[dict[str, object]],
    hot_positions: Sequence[int],
    position_profiles: dict[int, list[dict[str, object]]],
    transform_model: SamplereverseTransformModel,
    anchor_mode: str,
    frontier_submode: str = "",
    keep_limit: int = FRONTIER_TRIAD_POOL_LIMIT,
) -> list[dict[str, object]]:
    base_bytes = bytes.fromhex(base_anchor)
    triad_entries: list[dict[str, object]] = []
    for pair_entry in pair_pool[:FRONTIER_TOP_PAIR_LIMIT]:
        pair_positions = [int(item) for item in pair_entry.get("pair_positions", []) if str(item).strip()]
        if len(pair_positions) != 2:
            continue
        pair_values = [int(item) & 0xFF for item in pair_entry.get("pair_values", []) if str(item).strip()]
        if len(pair_values) != 2:
            continue
        for extra_position in hot_positions:
            if extra_position in pair_positions:
                continue
            value_entries = position_profiles.get(extra_position, [])[:FRONTIER_TRIAD_VALUE_LIMIT]
            for value_entry in value_entries:
                mutated_value = int(value_entry.get("mutated_byte_value", base_bytes[extra_position])) & 0xFF
                work = bytearray(base_bytes)
                work[pair_positions[0]] = pair_values[0]
                work[pair_positions[1]] = pair_values[1]
                work[int(extra_position)] = mutated_value
                candidate_hex = bytes(work).hex() + DEFAULT_FIXED_SUFFIX_HEX
                entry = _evaluate_candidate_hex(candidate_hex, transform_model)
                entry.update(
                    {
                        "pair_positions": list(pair_positions),
                        "pair_values": list(pair_values),
                        "triad_positions": [*pair_positions, int(extra_position)],
                        "triad_value": int(mutated_value),
                    }
                )
                triad_entries.append(entry)
    triad_entries = _unique_candidate_entries(triad_entries)
    triad_entries.sort(
        key=lambda item: _guided_sort_key(
            item,
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        )
    )
    return triad_entries[:keep_limit]


def _guided_anchor_mode(base_entry: dict[str, object]) -> str:
    return EXACT2_ANCHOR_MODE if int(base_entry.get("ci_exact_wchars", 0) or 0) >= 2 else FRONTIER_ANCHOR_MODE


def _guided_frontier_submode(
    base_entry: dict[str, object],
    *,
    frontier_role: str = "",
) -> str:
    if _guided_anchor_mode(base_entry) != FRONTIER_ANCHOR_MODE:
        return ""
    return (
        _frontier_submode_for_role(frontier_role)
        or _frontier_submode_from_entry(base_entry, default_anchor_mode=FRONTIER_ANCHOR_MODE)
        or _frontier_submode_for_exact(int(base_entry.get("ci_exact_wchars", 0) or 0))
    )


def _profiled_guided_pool_positions(
    *,
    base_anchor: str,
    bridge_entries: Sequence[dict[str, object]],
    position_profiles: dict[int, list[dict[str, object]]],
    transform_model: SamplereverseTransformModel,
    anchor_mode: str,
    pair_profiles: dict[tuple[int, int], list[dict[str, object]]] | None = None,
    frontier_submode: str = "",
) -> list[int]:
    if anchor_mode == FRONTIER_ANCHOR_MODE and pair_profiles:
        hot_positions = _hot_positions_from_pair_profiles(
            pair_profiles,
            transform_model=transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
            max_positions=4 if frontier_submode == FRONTIER_EXACT1_SUBMODE else GUIDED_POOL_POSITION_LIMIT,
        )
        if hot_positions:
            return hot_positions
    preferred: list[int] = []
    seen: set[int] = set()
    base_anchor = str(base_anchor).strip().lower()
    for entry in bridge_entries[:BRIDGE_VALIDATE_TOP]:
        candidate_hex = _candidate_hex_from_entry(entry)
        if candidate_hex[:16].lower() == base_anchor:
            continue
        for position in range(min(8, GUIDED_POOL_POSITION_LIMIT)):
            if candidate_hex[position * 2 : position * 2 + 2] != base_anchor[position * 2 : position * 2 + 2]:
                if position not in seen:
                    seen.add(position)
                    preferred.append(position)
                if len(preferred) >= GUIDED_POOL_POSITION_LIMIT:
                    return preferred
    ranked_profiles = sorted(
        (
            (position, entries[0])
            for position, entries in position_profiles.items()
            if entries and any(
                int(item.get("mutated_byte_value", -1)) != bytes.fromhex(base_anchor)[position]
                for item in entries
            )
        ),
        key=lambda item: _guided_sort_key(
            item[1],
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        ),
    )
    for position, _ in ranked_profiles:
        if position in seen:
            continue
        seen.add(position)
        preferred.append(position)
        if len(preferred) >= GUIDED_POOL_POSITION_LIMIT:
            break
    if preferred:
        return preferred
    return list(range(4 if frontier_submode == FRONTIER_EXACT1_SUBMODE else GUIDED_POOL_POSITION_LIMIT))


def run_compare_aware_bridge(
    *,
    target: Path,
    artifacts_dir: Path,
    base_anchor: str,
    transform_model: SamplereverseTransformModel,
    validate_top: int,
    per_probe_timeout: float,
    log,
) -> dict[str, object]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    pairscan_dir = artifacts_dir / "pairscan_runs"
    pair_entries: list[dict[str, object]] = []
    for positions in itertools.combinations(range(8), 2):
        pair_entries.append(
            _run_pairscan_tool(
                artifacts_dir=pairscan_dir,
                base_anchor=base_anchor,
                positions=positions,
                log=log,
            )
        )
    pair_entries = _unique_candidate_entries(pair_entries)
    pair_top = pair_entries[:BRIDGE_VALIDATE_TOP]
    hot_positions = _extract_hot_positions(pair_top)
    base_entry = _evaluate_candidate_hex(f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}", transform_model)
    current_best = pair_top[0] if pair_top and _bridge_entry_is_better(pair_top[0], base_entry) else base_entry

    triad_candidates: list[dict[str, object]] = []
    triad_stop = False
    if len(hot_positions) >= 3:
        triad_entries = []
        for combo in itertools.combinations(hot_positions, 3):
            triad_entries.append(
                _run_triad_tool(
                    artifacts_dir=artifacts_dir / "triad_runs",
                    base_anchor=base_anchor,
                    positions=combo,
                    log=log,
                )
            )
        triad_entries = _unique_candidate_entries(triad_entries)
        triad_candidates, current_best, triad_stop = _stage_improvements(triad_entries, current_best=current_best)
    hot_nibbles = _extract_hot_nibbles(triad_candidates, base_anchor=base_anchor)

    quartet_candidates: list[dict[str, object]] = []
    quartet_stop = False
    triad_seeds = triad_candidates[:TRIAD_SEED_LIMIT] or pair_top[:1]
    if hot_nibbles and not triad_stop:
        quartet_rows: list[dict[str, object]] = []
        for entry in triad_seeds:
            payload = _run_quartet_tool(
                artifacts_dir=artifacts_dir / "quartet_runs",
                base_anchor=str(entry["cand8_hex"]),
                log=log,
            )
            quartet_rows.extend(
                _select_rows_for_hot_nibbles(
                    payload.get("rows", []),
                    selected_nibbles=hot_nibbles,
                    stage="quartet",
                    base_anchor=str(entry["cand8_hex"]),
                    transform_model=transform_model,
                )
            )
        quartet_rows = _unique_candidate_entries(quartet_rows)
        quartet_candidates, current_best, quartet_stop = _stage_improvements(quartet_rows, current_best=current_best)

    quint_candidates: list[dict[str, object]] = []
    if len(hot_nibbles) >= HOT_NIBBLE_LIMIT and not triad_stop and not quartet_stop:
        quint_rows = []
        selected_nibbles = hot_nibbles[:HOT_NIBBLE_LIMIT]
        for entry in triad_seeds:
            quint_rows.append(
                _run_quint_fixed_tool(
                    artifacts_dir=artifacts_dir / "quint_runs",
                    base_anchor=str(entry["cand8_hex"]),
                    nibbles=selected_nibbles,
                    log=log,
                )
            )
        quint_rows = _unique_candidate_entries(quint_rows)
        quint_candidates, current_best, _ = _stage_improvements(quint_rows, current_best=current_best)

    pairscan_summary = {
        "base_anchor": base_anchor,
        "best_global": pair_top[0] if pair_top else base_entry,
        "pairs": pair_entries,
        "hot_positions": hot_positions,
    }
    pairscan_path = artifacts_dir / PAIRSCAN_FILE_NAME
    _write_json(pairscan_path, pairscan_summary)

    bridge_entries = _unique_candidate_entries([*pair_top, *triad_candidates, *quartet_candidates, *quint_candidates])
    bridge_payload = {
        "stage_order": ["pairscan", "triad", "quartet", "quint"],
        "base_anchor": base_anchor,
        "base_candidate_hex": f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
        "hot_positions": hot_positions,
        "hot_nibbles": hot_nibbles,
        "best": bridge_entries[0] if bridge_entries else base_entry,
        "top_entries": _bridge_entries_to_payload_entries(bridge_entries[:32], transform_model),
        "validation_candidates": _bridge_entries_to_payload_entries(bridge_entries[:BRIDGE_VALIDATE_TOP], transform_model),
        "pairscan_summary_path": str(pairscan_path),
        "stages": {
            "pairscan": _bridge_entries_to_payload_entries(pair_top, transform_model),
            "triad": _bridge_entries_to_payload_entries(triad_candidates, transform_model),
            "quartet": _bridge_entries_to_payload_entries(quartet_candidates, transform_model),
            "quint": _bridge_entries_to_payload_entries(quint_candidates, transform_model),
        },
    }
    bridge_result_path = artifacts_dir / BRIDGE_RESULT_FILE_NAME
    _write_json(bridge_result_path, bridge_payload)

    bridge_validation_path, bridge_validations = validate_compare_aware_results(
        target=target,
        artifacts_dir=artifacts_dir / "bridge_validation",
        result_path=bridge_result_path,
        transform_model=transform_model,
        validate_top=max(validate_top, BRIDGE_VALIDATE_TOP),
        per_probe_timeout=per_probe_timeout,
        log=log,
        output_file_name=BRIDGE_VALIDATION_FILE_NAME,
        compare_output_prefix="bridge_compare_aware",
    )

    return {
        "pairscan_path": str(pairscan_path),
        "bridge_result_path": str(bridge_result_path),
        "bridge_validation_path": str(bridge_validation_path),
        "bridge_entries": bridge_entries,
        "bridge_validations": bridge_validations,
        "hot_positions": hot_positions,
        "hot_nibbles": hot_nibbles,
    }


def _guided_pool_entry(
    *,
    candidate_hex: str,
    base_anchor: str,
    positions: Sequence[int],
    transform_model: SamplereverseTransformModel,
) -> dict[str, object]:
    entry = _evaluate_candidate_hex(candidate_hex, transform_model)
    entry.update(
        {
            "stage": "guided_pool",
            "base_anchor": base_anchor,
            "positions_or_nibbles": list(positions),
        }
    )
    return entry


def _guided_pool_beam_entries(
    *,
    candidates: Sequence[dict[str, object]],
    transform_model: SamplereverseTransformModel,
    exact_floor: int,
    anchor_mode: str,
    frontier_submode: str = "",
    exploration_slots: int = GUIDED_POOL_EXPLORATION_SLOTS,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    ranked = sorted(
        candidates,
        key=lambda item: _guided_sort_key(
            item,
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        ),
    )
    filtered = [entry for entry in ranked if int(entry.get("ci_exact_wchars", 0) or 0) >= exact_floor]
    deduped: list[dict[str, object]] = []
    seen: set[str] = set()
    primary_kept = 0
    exploratory_kept = 0
    for entry in filtered:
        candidate_hex = str(entry.get("candidate_hex", ""))
        if candidate_hex in seen:
            continue
        seen.add(candidate_hex)
        deduped.append(entry)
        primary_kept += 1
        if len(deduped) >= GUIDED_POOL_BEAM_LIMIT:
            break
    if len(deduped) < GUIDED_POOL_BEAM_LIMIT and exploration_slots > 0:
        for entry in ranked:
            candidate_hex = str(entry.get("candidate_hex", ""))
            if candidate_hex in seen:
                continue
            seen.add(candidate_hex)
            deduped.append(entry)
            exploratory_kept += 1
            if exploratory_kept >= exploration_slots or len(deduped) >= GUIDED_POOL_BEAM_LIMIT:
                break
    if not deduped:
        for entry in ranked[:GUIDED_POOL_BEAM_LIMIT]:
            candidate_hex = str(entry.get("candidate_hex", ""))
            if candidate_hex in seen:
                continue
            seen.add(candidate_hex)
            deduped.append(entry)
            exploratory_kept += 1
    return deduped, {
        "primary_kept": primary_kept,
        "exploratory_kept": exploratory_kept,
        "ranked_total": len(ranked),
        "floor_matched": len(filtered),
    }


def run_compare_aware_guided_pool(
    *,
    target: Path,
    artifacts_dir: Path,
    base_anchor: str,
    bridge_entries: Sequence[dict[str, object]],
    transform_model: SamplereverseTransformModel,
    validate_top: int,
    per_probe_timeout: float,
    log,
    source_anchor: str | None = None,
    frontier_role: str = "",
    anchor_lineage: str = "",
    feedback_value_pools: dict[int, Sequence[int]] | None = None,
    lineage_entries: Sequence[dict[str, object]] = (),
) -> dict[str, object]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    base_entry = _evaluate_candidate_hex(f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}", transform_model)
    base_exact = int(base_entry.get("ci_exact_wchars", 0) or 0)
    anchor_mode = _guided_anchor_mode(base_entry)
    frontier_submode = _guided_frontier_submode(base_entry, frontier_role=frontier_role)
    normalized_feedback_value_pools = {
        int(key): [int(value) & 0xFF for value in values]
        for key, values in (feedback_value_pools or {}).items()
        if str(key).strip().isdigit()
    }
    normalized_source_anchor = str(source_anchor or base_anchor).strip().lower()
    normalized_frontier_role = str(frontier_role).strip() or (
        "exact2_seed" if anchor_mode == EXACT2_ANCHOR_MODE else "frontier_anchor"
    )
    base_lineage = str(anchor_lineage).strip() or _lineage_root(
        source_anchor=normalized_source_anchor,
        frontier_role=normalized_frontier_role,
        anchor_mode=anchor_mode,
    )
    position_profiles = _top_compare_aware_single_byte_entries(
        base_anchor=base_anchor,
        positions=list(range(8)),
        transform_model=transform_model,
        top_k=GUIDED_POOL_TOP_VALUES,
    )
    pair_profiles: dict[tuple[int, int], list[dict[str, object]]] = {}
    pair_generation_details: dict[str, object] = {}
    lineage_value_pools: dict[int, list[int]] = {}
    lineage_value_counts: dict[int, dict[int, int]] = {}
    lineage_value_origins: dict[int, list[str]] = {}
    lineage_value_mining_summary: dict[str, object] = {}
    locked_pair_positions: list[tuple[int, int]] = []
    alternate_locked_pair_positions: list[tuple[int, int]] = []
    pair_generation_sources: dict[str, object] = {}
    pair_set_mode = "single_pair_set"
    pair_set_comparison_summary: dict[str, object] = {}
    if anchor_mode == FRONTIER_ANCHOR_MODE:
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            lineage_value_pools, lineage_value_counts, lineage_value_origins, lineage_value_mining_summary = (
                _mine_exact1_lineage_value_sources(
                    base_anchor=base_anchor,
                    source_anchor=normalized_source_anchor,
                    positions=list(range(8)),
                    transform_model=transform_model,
                    lineage_entries=lineage_entries,
                )
            )
        if frontier_submode == FRONTIER_EXACT1_SUBMODE:
            primary_locked_pair_positions, pair_generation_sources = _locked_pair_positions_for_exact1(
                base_anchor=base_anchor,
                source_anchor=normalized_source_anchor,
                bridge_entries=bridge_entries,
                pair_profiles={},
            )
            pair_set_mode = "primary_pair_set"

            def _evaluate_exact1_pair_set(
                pair_set_name: str,
                candidate_locked_pairs: Sequence[tuple[int, int]],
            ) -> dict[str, object]:
                candidate_set = [tuple(sorted((int(left), int(right)))) for left, right in candidate_locked_pairs][:EXACT1_PAIR_LOCK_LIMIT]
                candidate_pair_profiles, candidate_generation_details = _top_compare_aware_pair_entries(
                    base_anchor=base_anchor,
                    positions=list(range(8)),
                    position_profiles=position_profiles,
                    transform_model=transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                    locked_pair_positions=candidate_set,
                    incoming_feedback_value_pools=normalized_feedback_value_pools,
                    lineage_value_pools=lineage_value_pools,
                    lineage_value_counts=lineage_value_counts,
                    lineage_value_origins=lineage_value_origins,
                    baseline_entry=base_entry,
                )
                candidate_pair_frontier_pool, candidate_pair_drop_reasons, candidate_pair_diagnostics = _diverse_pair_frontier_pool(
                    candidate_pair_profiles,
                    transform_model=transform_model,
                    anchor_mode=anchor_mode,
                    frontier_submode=frontier_submode,
                    pair_profile_details=candidate_generation_details,
                    baseline_entry=base_entry,
                    keep_limit=FRONTIER_PAIR_SEED_LIMIT,
                )
                candidate_pair_frontier_pool = _annotate_frontier_improvement_gate(
                    candidate_pair_frontier_pool,
                    baseline_entry=base_entry,
                    frontier_submode=frontier_submode,
                )
                return {
                    "pair_set_mode": pair_set_name,
                    "locked_pair_positions": candidate_set,
                    "pair_profiles": candidate_pair_profiles,
                    "pair_generation_details": candidate_generation_details,
                    "pair_frontier_pool": candidate_pair_frontier_pool,
                    "pair_drop_reasons": candidate_pair_drop_reasons,
                    "pair_frontier_diagnostics": candidate_pair_diagnostics,
                }

            primary_pair_run = _evaluate_exact1_pair_set("primary_pair_set", primary_locked_pair_positions)
            locked_pair_positions = list(primary_pair_run["locked_pair_positions"])

            alternate_locked_pair_positions, alternate_pair_details = _alternate_locked_pair_positions_for_exact1(
                primary_locked_pairs=locked_pair_positions,
                source_details=pair_generation_sources,
                pair_gate_input_summary=primary_pair_run["pair_frontier_diagnostics"].get("pair_gate_input_summary", {}),
            )
            exact1_pair_runs = [primary_pair_run]
            if alternate_locked_pair_positions and alternate_locked_pair_positions != locked_pair_positions:
                exact1_pair_runs.append(_evaluate_exact1_pair_set("alternate_pair_set", alternate_locked_pair_positions))

            selected_pair_run = min(exact1_pair_runs, key=_exact1_pair_set_selection_key)
            pair_set_mode = str(selected_pair_run.get("pair_set_mode", "primary_pair_set"))
            locked_pair_positions = list(selected_pair_run["locked_pair_positions"])
            pair_profiles = dict(selected_pair_run["pair_profiles"])
            pair_generation_details = dict(selected_pair_run["pair_generation_details"])
            pair_generation_sources = {
                **pair_generation_sources,
                **pair_generation_details,
                "lineage_value_mining_summary": lineage_value_mining_summary,
                "primary_pair_set_details": {
                    **pair_generation_sources,
                    **primary_pair_run["pair_generation_details"],
                },
                "alternate_pair_set_details": alternate_pair_details,
            }
            pair_set_comparison_summary = {
                "primary_pair_set": {
                    "locked_pair_positions": [list(item) for item in primary_pair_run["locked_pair_positions"]],
                    "selection_key": list(_exact1_pair_set_selection_key(primary_pair_run)),
                    "projected_beats_neighbor_count": sum(
                        1
                        for pair_map in primary_pair_run["pair_generation_details"].get("pair_projected_competitive_status", {}).values()
                        if isinstance(pair_map, dict)
                        for status in pair_map.values()
                        if str(status) == "projected_beats_neighbor"
                    ),
                    "projected_local_compatible_count": sum(
                        1
                        for pair_map in primary_pair_run["pair_generation_details"].get("pair_escape_source_projected_quality_band", {}).values()
                        if isinstance(pair_map, dict)
                        for pos_map in pair_map.values()
                        if isinstance(pos_map, dict)
                        for band in pos_map.values()
                        if str(band) == "projected_local_compatible"
                    ),
                    "pair_gate_kept_escape": len(primary_pair_run["pair_frontier_diagnostics"].get("pair_gate_kept_escape", [])),
                    "pair_borderline_escape_candidates": len(primary_pair_run["pair_frontier_diagnostics"].get("pair_borderline_escape_candidates", [])),
                    "pair_near_local_escape_candidates": len(primary_pair_run["pair_frontier_diagnostics"].get("pair_near_local_escape_candidates", [])),
                    "pair_wide_local_escape_candidates": len(primary_pair_run["pair_frontier_diagnostics"].get("pair_wide_local_escape_candidates", [])),
                    "gate_filtered_local_escape": int(primary_pair_run["pair_drop_reasons"].get("gate_filtered_local_escape", 0) or 0),
                    "gate_filtered_hard_escape": int(primary_pair_run["pair_drop_reasons"].get("gate_filtered_hard_escape", 0) or 0),
                },
                "alternate_pair_set": {
                    "locked_pair_positions": [list(item) for item in alternate_locked_pair_positions],
                    "selection_key": list(_exact1_pair_set_selection_key(exact1_pair_runs[-1])) if len(exact1_pair_runs) > 1 else [],
                    "projected_beats_neighbor_count": sum(
                        1
                        for pair_map in exact1_pair_runs[-1]["pair_generation_details"].get("pair_projected_competitive_status", {}).values()
                        if len(exact1_pair_runs) > 1 and isinstance(pair_map, dict)
                        for status in pair_map.values()
                        if str(status) == "projected_beats_neighbor"
                    ) if len(exact1_pair_runs) > 1 else 0,
                    "projected_local_compatible_count": sum(
                        1
                        for pair_map in exact1_pair_runs[-1]["pair_generation_details"].get("pair_escape_source_projected_quality_band", {}).values()
                        if len(exact1_pair_runs) > 1 and isinstance(pair_map, dict)
                        for pos_map in pair_map.values()
                        if isinstance(pos_map, dict)
                        for band in pos_map.values()
                        if str(band) == "projected_local_compatible"
                    ) if len(exact1_pair_runs) > 1 else 0,
                    "pair_gate_kept_escape": len(exact1_pair_runs[-1]["pair_frontier_diagnostics"].get("pair_gate_kept_escape", [])) if len(exact1_pair_runs) > 1 else 0,
                    "pair_borderline_escape_candidates": len(exact1_pair_runs[-1]["pair_frontier_diagnostics"].get("pair_borderline_escape_candidates", [])) if len(exact1_pair_runs) > 1 else 0,
                    "pair_near_local_escape_candidates": len(exact1_pair_runs[-1]["pair_frontier_diagnostics"].get("pair_near_local_escape_candidates", [])) if len(exact1_pair_runs) > 1 else 0,
                    "pair_wide_local_escape_candidates": len(exact1_pair_runs[-1]["pair_frontier_diagnostics"].get("pair_wide_local_escape_candidates", [])) if len(exact1_pair_runs) > 1 else 0,
                    "gate_filtered_local_escape": int(exact1_pair_runs[-1]["pair_drop_reasons"].get("gate_filtered_local_escape", 0) or 0) if len(exact1_pair_runs) > 1 else 0,
                    "gate_filtered_hard_escape": int(exact1_pair_runs[-1]["pair_drop_reasons"].get("gate_filtered_hard_escape", 0) or 0) if len(exact1_pair_runs) > 1 else 0,
                },
                "winner": pair_set_mode,
            }
        else:
            pair_profiles, pair_generation_details = _top_compare_aware_pair_entries(
                base_anchor=base_anchor,
                positions=list(range(8)),
                position_profiles=position_profiles,
                transform_model=transform_model,
                anchor_mode=anchor_mode,
                frontier_submode=frontier_submode,
                locked_pair_positions=locked_pair_positions,
                incoming_feedback_value_pools={},
                lineage_value_pools={},
                lineage_value_counts={},
                lineage_value_origins={},
                baseline_entry=None,
            )
    positions = _profiled_guided_pool_positions(
        base_anchor=base_anchor,
        bridge_entries=bridge_entries,
        position_profiles=position_profiles,
        transform_model=transform_model,
        anchor_mode=anchor_mode,
        pair_profiles=pair_profiles,
        frontier_submode=frontier_submode,
    )
    base_bytes = bytes.fromhex(base_anchor)
    value_pools: dict[int, list[int]] = {}
    exact_floor = max(0, base_exact - 1)
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        exact_floor = max(1, base_exact)
    beam: list[bytes] = [base_bytes]
    stage_order: list[str] = []
    pair_stage_stats: dict[str, object] = {}
    pair_frontier_pool: list[dict[str, object]] = []
    triad_frontier_pool: list[dict[str, object]] = []
    feedback_sources: dict[str, dict[str, list[int]]] = {}
    feedback_value_pool_payload: dict[int, list[int]] = {}
    pair_pool_source_counts: dict[str, int] = {}
    pair_drop_reasons: dict[str, int] = {}
    pair_frontier_diagnostics: dict[str, object] = {}
    if anchor_mode == FRONTIER_ANCHOR_MODE and pair_profiles:
        pair_profiles_for_positions = {
            pair_positions: entries
            for pair_positions, entries in pair_profiles.items()
            if set(pair_positions).issubset(set(positions))
        }
        if frontier_submode == FRONTIER_EXACT1_SUBMODE and pair_set_mode in {"primary_pair_set", "alternate_pair_set"}:
            selected_pair_frontier_pool, selected_pair_drop_reasons, selected_pair_frontier_diagnostics = _diverse_pair_frontier_pool(
                pair_profiles_for_positions,
                transform_model=transform_model,
                anchor_mode=anchor_mode,
                frontier_submode=frontier_submode,
                pair_profile_details=pair_generation_details,
                baseline_entry=base_entry,
                keep_limit=FRONTIER_PAIR_SEED_LIMIT,
            )
            pair_frontier_pool = _annotate_frontier_improvement_gate(
                selected_pair_frontier_pool,
                baseline_entry=base_entry,
                frontier_submode=frontier_submode,
            )
            pair_drop_reasons = selected_pair_drop_reasons
            pair_frontier_diagnostics = selected_pair_frontier_diagnostics
        else:
            pair_frontier_pool, pair_drop_reasons, pair_frontier_diagnostics = _diverse_pair_frontier_pool(
                pair_profiles_for_positions,
                transform_model=transform_model,
                anchor_mode=anchor_mode,
                frontier_submode=frontier_submode,
                pair_profile_details=pair_generation_details,
                baseline_entry=base_entry,
                keep_limit=FRONTIER_PAIR_SEED_LIMIT,
            )
            pair_frontier_pool = _annotate_frontier_improvement_gate(
                pair_frontier_pool,
                baseline_entry=base_entry,
                frontier_submode=frontier_submode,
            )
        triad_frontier_pool = _triad_frontier_pool(
            base_anchor=base_anchor,
            pair_pool=pair_frontier_pool,
            hot_positions=positions,
            position_profiles=position_profiles,
            transform_model=transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
            keep_limit=FRONTIER_TRIAD_POOL_LIMIT,
        )
        triad_frontier_pool = _annotate_frontier_improvement_gate(
            triad_frontier_pool,
            baseline_entry=base_entry,
            frontier_submode=frontier_submode,
        )
        feedback_value_pool_payload, feedback_sources = _feedback_value_pools_from_frontier_entries(
            base_anchor=base_anchor,
            positions=positions,
            position_profiles=position_profiles,
            pair_frontier_pool=pair_frontier_pool,
            triad_frontier_pool=triad_frontier_pool,
            incoming_feedback_value_pools=normalized_feedback_value_pools,
            frontier_submode=frontier_submode,
        )
        for entry in [*pair_frontier_pool, *triad_frontier_pool]:
            pair_positions = [int(item) for item in entry.get("pair_positions", []) if str(item).strip()]
            if len(pair_positions) != 2:
                continue
            key = f"{pair_positions[0]},{pair_positions[1]}"
            pair_pool_source_counts[key] = pair_pool_source_counts.get(key, 0) + 1
        pair_seed_entries = _unique_candidate_entries([*pair_frontier_pool, *triad_frontier_pool])
        pair_seed_entries.sort(
            key=lambda item: _guided_sort_key(
                item,
                transform_model,
                anchor_mode=anchor_mode,
                frontier_submode=frontier_submode,
            )
        )
        pair_seed_entries = pair_seed_entries[:GUIDED_POOL_BEAM_LIMIT]
        if pair_seed_entries:
            beam = [bytes.fromhex(str(entry["cand8_hex"])) for entry in pair_seed_entries]
            stage_order.extend(["pair_frontier_pool", "triad_frontier_pool"])
            pair_stage_stats = {
                "seed_count": len(pair_seed_entries),
                "profiled_pairs": len(pair_profiles),
                "top_pair_positions": [list(item.get("pair_positions", [])) for item in pair_seed_entries[:FRONTIER_TOP_PAIR_LIMIT]],
                "pair_frontier_pool_size": len(pair_frontier_pool),
                "triad_frontier_pool_size": len(triad_frontier_pool),
                "improved_pair_frontier_pool_count": sum(
                    1 for item in pair_frontier_pool if bool(item.get("improvement_gate_passed"))
                ),
                "improved_triad_frontier_pool_count": sum(
                    1 for item in triad_frontier_pool if bool(item.get("improvement_gate_passed"))
                ),
                "pair_pool_source_counts": pair_pool_source_counts,
                "pair_drop_reasons": pair_drop_reasons,
            }
            pair_stage_stats.update(
                {
                    "pair_set_mode": pair_set_mode,
                    "alternate_locked_pair_positions": [list(item) for item in alternate_locked_pair_positions],
                    "pair_set_comparison_summary": pair_set_comparison_summary,
                    "pair_escape_mode": pair_frontier_diagnostics.get("pair_escape_mode", ""),
                    "pair_escape_candidates_kept": len(pair_frontier_diagnostics.get("pair_escape_candidates_kept", [])),
                    "pair_escape_candidates_dropped": len(pair_frontier_diagnostics.get("pair_escape_candidates_dropped", [])),
                    "pair_escape_source_statuses": pair_frontier_diagnostics.get("pair_escape_source_statuses", {}),
                    "pair_escape_lane_counts": pair_frontier_diagnostics.get("pair_escape_lane_counts", {}),
                    "pair_escape_status_by_lane": pair_frontier_diagnostics.get("pair_escape_status_by_lane", {}),
                    "pair_gate_kept_escape": len(pair_frontier_diagnostics.get("pair_gate_kept_escape", [])),
                    "pair_borderline_escape_candidates": len(
                        pair_frontier_diagnostics.get("pair_borderline_escape_candidates", [])
                    ),
                    "pair_near_local_escape_candidates": len(
                        pair_frontier_diagnostics.get("pair_near_local_escape_candidates", [])
                    ),
                    "pair_wide_local_escape_candidates": len(
                        pair_frontier_diagnostics.get("pair_wide_local_escape_candidates", [])
                    ),
                    "pair_gate_failed_escape": len(pair_frontier_diagnostics.get("pair_gate_failed_escape", [])),
                    "pair_gate_input_summary": pair_frontier_diagnostics.get("pair_gate_input_summary", {}),
                    "pair_gate_borderline_summary": pair_frontier_diagnostics.get("pair_gate_borderline_summary", {}),
                    "pair_best_local_escape": pair_frontier_diagnostics.get("pair_best_local_escape", {}),
                    "pair_best_hard_escape": pair_frontier_diagnostics.get("pair_best_hard_escape", {}),
                    "pair_neighbor_generation_summary": pair_generation_details.get("pair_neighbor_generation_summary", {}),
                    "pair_single_byte_guard_summary": pair_generation_details.get("pair_single_byte_guard_summary", {}),
                    "pair_single_byte_guard_candidates": pair_generation_details.get("pair_single_byte_guard_candidates", {}),
                    "pair_single_byte_guard_status_counts": pair_generation_details.get("pair_single_byte_guard_status_counts", {}),
                    "pair_guard_soft_promoted_values": pair_generation_details.get("pair_guard_soft_promoted_values", {}),
                    "pair_guard_nonbase_starved": pair_generation_details.get("pair_guard_nonbase_starved", {}),
                    "pair_guard_soft_quality_band": pair_generation_details.get("pair_guard_soft_quality_band", {}),
                    "pair_guard_soft_rank_summary": pair_generation_details.get("pair_guard_soft_rank_summary", {}),
                    "pair_guard_soft_distance_delta": pair_generation_details.get("pair_guard_soft_distance_delta", {}),
                    "pair_guard_soft_raw_delta": pair_generation_details.get("pair_guard_soft_raw_delta", {}),
                    "pair_guard_soft_structure_delta": pair_generation_details.get("pair_guard_soft_structure_delta", {}),
                    "pair_projected_vs_neighbor_summary": pair_generation_details.get(
                        "pair_projected_vs_neighbor_summary",
                        {},
                    ),
                    "pair_projected_competitive_status": pair_generation_details.get(
                        "pair_projected_competitive_status",
                        {},
                    ),
                    "pair_projected_competitive_winner": pair_generation_details.get(
                        "pair_projected_competitive_winner",
                        {},
                    ),
                    "pair_projected_blocked_by_neighbor": pair_generation_details.get(
                        "pair_projected_blocked_by_neighbor",
                        {},
                    ),
                    "pair_projected_best_delta_gap": pair_generation_details.get(
                        "pair_projected_best_delta_gap",
                        {},
                    ),
                    "pair_projected_boundary_candidates": pair_generation_details.get(
                        "pair_projected_boundary_candidates",
                        {},
                    ),
                    "pair_projected_preserve_candidates": pair_generation_details.get(
                        "pair_projected_preserve_candidates",
                        {},
                    ),
                    "pair_projected_winner_gate_summary": pair_frontier_diagnostics.get(
                        "pair_projected_winner_gate_summary",
                        {},
                    ),
                    "pair_projected_winner_gate_status_counts": pair_frontier_diagnostics.get(
                        "pair_projected_winner_gate_status_counts",
                        {},
                    ),
                    "pair_projected_boundary_entries": len(
                        pair_frontier_diagnostics.get("pair_projected_boundary_entries", [])
                    ),
                    "pair_projected_preserve_entries": len(
                        pair_frontier_diagnostics.get("pair_projected_preserve_entries", [])
                    ),
                    "pair_projected_boundary_wide_candidates": len(
                        pair_frontier_diagnostics.get("pair_projected_boundary_wide_candidates", [])
                    ),
                    "projected_beats_neighbor_count": sum(
                        1
                        for pair_map in pair_generation_details.get("pair_projected_competitive_status", {}).values()
                        if isinstance(pair_map, dict)
                        for status in pair_map.values()
                        if str(status) == "projected_beats_neighbor"
                    ),
                    "projected_local_compatible_count": sum(
                        1
                        for pair_map in pair_generation_details.get("pair_escape_source_projected_quality_band", {}).values()
                        if isinstance(pair_map, dict)
                        for pos_map in pair_map.values()
                        if isinstance(pos_map, dict)
                        for band in pos_map.values()
                        if str(band) == "projected_local_compatible"
                    ),
                    "projected_distance_explosive_count": sum(
                        1
                        for pair_map in pair_generation_details.get("pair_escape_source_projected_quality_band", {}).values()
                        if isinstance(pair_map, dict)
                        for pos_map in pair_map.values()
                        if isinstance(pos_map, dict)
                        for band in pos_map.values()
                        if str(band) == "projected_distance_explosive"
                    ),
                    "pair_escape_source_projected_quality_band": pair_generation_details.get(
                        "pair_escape_source_projected_quality_band",
                        {},
                    ),
                    "pair_escape_source_projected_rank_summary": pair_generation_details.get(
                        "pair_escape_source_projected_rank_summary",
                        {},
                    ),
                    "pair_escape_source_projected_direction": pair_generation_details.get(
                        "pair_escape_source_projected_direction",
                        {},
                    ),
                    "pair_escape_source_projected_step": pair_generation_details.get(
                        "pair_escape_source_projected_step",
                        {},
                    ),
                    "pair_escape_source_projected_kept_values": pair_generation_details.get(
                        "pair_escape_source_projected_kept_values",
                        {},
                    ),
                    "pair_escape_source_projected_dropped_values": pair_generation_details.get(
                        "pair_escape_source_projected_dropped_values",
                        {},
                    ),
                    "pair_escape_source_projected_values": pair_generation_details.get(
                        "pair_escape_source_projected_values",
                        {},
                    ),
                    "pair_escape_source_projected_origins": pair_generation_details.get(
                        "pair_escape_source_projected_origins",
                        {},
                    ),
                    "lineage_projection_summary": pair_generation_details.get("lineage_projection_summary", {}),
                    "pair_escape_source_reject_reasons": pair_generation_details.get(
                        "pair_escape_source_reject_reasons",
                        {},
                    ),
                    "pair_mutation_radius_summary": pair_frontier_diagnostics.get(
                        "pair_mutation_radius_summary",
                        pair_generation_details.get("pair_mutation_radius_summary", {}),
                    ),
                    "pair_local_escape_candidate_count": int(
                        pair_frontier_diagnostics.get("pair_local_escape_candidate_count", 0) or 0
                    ),
                    "pair_local_escape_borderline_count": int(
                        pair_frontier_diagnostics.get("pair_local_escape_borderline_count", 0) or 0
                    ),
                    "pair_near_local_escape_count": int(
                        pair_frontier_diagnostics.get("pair_near_local_escape_count", 0) or 0
                    ),
                    "pair_wide_local_escape_count": int(
                        pair_frontier_diagnostics.get("pair_wide_local_escape_count", 0) or 0
                    ),
                    "pair_local_escape_reject_count": int(
                        pair_frontier_diagnostics.get("pair_local_escape_reject_count", 0) or 0
                    ),
                    "pair_hard_escape_candidate_count": int(
                        pair_frontier_diagnostics.get("pair_hard_escape_candidate_count", 0) or 0
                    ),
                    "pair_profile_kept_preserve": sum(
                        len(items) for items in pair_frontier_diagnostics.get("pair_profile_kept_preserve", {}).values()
                    ),
                    "pair_profile_kept_escape": sum(
                        len(items) for items in pair_frontier_diagnostics.get("pair_profile_kept_escape", {}).values()
                    ),
                    "pair_radius_band_counts": pair_frontier_diagnostics.get("pair_radius_band_counts", {}),
                    "pair_profile_truncation_summary": pair_frontier_diagnostics.get("pair_profile_truncation_summary", {}),
                }
            )
            pair_stage_stats["exact1_projected_competition_summary"] = _exact1_projected_competition_summary(
                pair_stage_stats=pair_stage_stats,
                pair_set_comparison_summary=pair_set_comparison_summary,
            )
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        value_pools = {
            position: _bounded_value_pool(
                base_value=base_bytes[position],
                profile_values=[
                    *_small_perturbation_values(base_bytes[position], radius=2),
                    *[
                        int(entry.get("mutated_byte_value", base_bytes[position])) & 0xFF
                        for entry in position_profiles.get(position, [])[:2]
                    ],
                ],
                feedback_values=[
                    *lineage_value_pools.get(int(position), []),
                    *feedback_sources.get(str(position), {}).get("improved_pair_values", []),
                    *feedback_sources.get(str(position), {}).get("improved_triad_values", []),
                ],
            )
            for position in positions
        }
    else:
        value_pools = {
            position: _bounded_value_pool(
                base_value=base_bytes[position],
                profile_values=[
                    int(entry.get("mutated_byte_value", base_bytes[position]))
                    for entry in position_profiles.get(position, [])
                ],
                feedback_values=[
                    int(value)
                    for value in (
                        feedback_value_pool_payload.get(int(position))
                        or normalized_feedback_value_pools.get(int(position), [])
                    )
                ],
            )
            for position in positions
        }
    stage_stats: list[dict[str, int]] = []
    for upto, position in enumerate(positions, 1):
        candidates: list[dict[str, object]] = []
        values = list(dict.fromkeys(int(value) & 0xFF for value in value_pools.get(position, [base_bytes[position]])))
        for prefix_bytes in beam:
            for value in values:
                mutated = bytearray(prefix_bytes)
                mutated[position] = value
                candidate_hex = bytes(mutated).hex() + DEFAULT_FIXED_SUFFIX_HEX
                candidates.append(
                    _guided_pool_entry(
                        candidate_hex=candidate_hex,
                        base_anchor=base_anchor,
                        positions=positions[:upto],
                        transform_model=transform_model,
                    )
                )
        deduped, beam_stats = _guided_pool_beam_entries(
            candidates=candidates,
            transform_model=transform_model,
            exact_floor=exact_floor,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        )
        beam = [bytes.fromhex(str(entry["cand8_hex"])) for entry in deduped]
        stage_stats.append(
            {
                "position": int(position),
                "pool_size": len(values),
                "candidate_count": len(candidates),
                **beam_stats,
                "beam_size": len(beam),
            }
        )
        stage_order.append(f"byte_{position}")
        log(
            "guided pool stage: "
            f"pos={position} pool={len(values)} beam={len(beam)} "
            f"primary={beam_stats['primary_kept']} explore={beam_stats['exploratory_kept']}"
        )

    guided_entries = _unique_candidate_entries(
        [
            *pair_frontier_pool,
            *triad_frontier_pool,
            *[
                _guided_pool_entry(
                    candidate_hex=prefix_bytes.hex() + DEFAULT_FIXED_SUFFIX_HEX,
                    base_anchor=base_anchor,
                    positions=positions,
                    transform_model=transform_model,
                )
                for prefix_bytes in beam
            ],
        ]
    )
    guided_entries = _annotate_entries_context(
        guided_entries,
        source_anchor=normalized_source_anchor,
        frontier_role=normalized_frontier_role,
        anchor_mode=anchor_mode,
        stage_label="guided(frontier)" if anchor_mode == FRONTIER_ANCHOR_MODE else "guided(seed)",
        anchor_lineage=base_lineage,
        frontier_submode=frontier_submode,
    )
    guided_entries.sort(
        key=lambda item: _guided_sort_key(
            item,
            transform_model,
            anchor_mode=anchor_mode,
            frontier_submode=frontier_submode,
        )
    )
    guided_payload = {
        "stage_order": stage_order or ["guided_pool"],
        "base_anchor": base_anchor,
        "anchor_mode": anchor_mode,
        "frontier_submode": frontier_submode,
        "source_anchor": normalized_source_anchor,
        "frontier_role": normalized_frontier_role,
        "anchor_lineage": _append_lineage(
            base_lineage,
            "guided(frontier)" if anchor_mode == FRONTIER_ANCHOR_MODE else "guided(seed)",
        ),
        "pair_generation_mode": (
            "exact1_locked_pairs" if frontier_submode == FRONTIER_EXACT1_SUBMODE else "frontier_profile_pairs"
        ),
        "pair_set_mode": pair_set_mode,
        "locked_pair_positions": [list(item) for item in locked_pair_positions],
        "alternate_locked_pair_positions": [list(item) for item in alternate_locked_pair_positions],
        "pair_set_comparison_summary": pair_set_comparison_summary,
        "pair_generation_sources": pair_generation_sources,
        "pair_escape_mode": pair_frontier_diagnostics.get("pair_escape_mode", ""),
        "pair_escape_pool_strategy": pair_generation_details.get("pair_escape_pool_strategy", ""),
        "pair_neighbor_generation_summary": pair_generation_details.get("pair_neighbor_generation_summary", {}),
        "pair_single_byte_guard_summary": pair_generation_details.get("pair_single_byte_guard_summary", {}),
        "pair_single_byte_guard_candidates": pair_generation_details.get("pair_single_byte_guard_candidates", {}),
        "pair_single_byte_guard_status_counts": pair_generation_details.get("pair_single_byte_guard_status_counts", {}),
        "pair_guard_soft_promoted_values": pair_generation_details.get("pair_guard_soft_promoted_values", {}),
        "pair_guard_nonbase_starved": pair_generation_details.get("pair_guard_nonbase_starved", {}),
        "pair_guard_soft_quality_band": pair_generation_details.get("pair_guard_soft_quality_band", {}),
        "pair_guard_soft_rank_summary": pair_generation_details.get("pair_guard_soft_rank_summary", {}),
        "pair_guard_soft_distance_delta": pair_generation_details.get("pair_guard_soft_distance_delta", {}),
        "pair_guard_soft_raw_delta": pair_generation_details.get("pair_guard_soft_raw_delta", {}),
        "pair_guard_soft_structure_delta": pair_generation_details.get("pair_guard_soft_structure_delta", {}),
        "pair_projected_vs_neighbor_summary": pair_generation_details.get("pair_projected_vs_neighbor_summary", {}),
        "pair_projected_competitive_status": pair_generation_details.get("pair_projected_competitive_status", {}),
        "pair_projected_competitive_winner": pair_generation_details.get("pair_projected_competitive_winner", {}),
        "pair_projected_blocked_by_neighbor": pair_generation_details.get("pair_projected_blocked_by_neighbor", {}),
        "pair_projected_best_delta_gap": pair_generation_details.get("pair_projected_best_delta_gap", {}),
        "pair_projected_boundary_candidates": pair_generation_details.get("pair_projected_boundary_candidates", {}),
        "pair_projected_preserve_candidates": pair_generation_details.get("pair_projected_preserve_candidates", {}),
        "pair_projected_winner_gate_summary": pair_frontier_diagnostics.get("pair_projected_winner_gate_summary", {}),
        "pair_projected_winner_gate_status_counts": pair_frontier_diagnostics.get(
            "pair_projected_winner_gate_status_counts",
            {},
        ),
        "pair_escape_source_projected_quality_band": pair_generation_details.get(
            "pair_escape_source_projected_quality_band",
            {},
        ),
        "pair_escape_source_projected_rank_summary": pair_generation_details.get(
            "pair_escape_source_projected_rank_summary",
            {},
        ),
        "pair_escape_source_projected_direction": pair_generation_details.get(
            "pair_escape_source_projected_direction",
            {},
        ),
        "pair_escape_source_projected_step": pair_generation_details.get(
            "pair_escape_source_projected_step",
            {},
        ),
        "pair_escape_source_projected_kept_values": pair_generation_details.get(
            "pair_escape_source_projected_kept_values",
            {},
        ),
        "pair_escape_source_projected_dropped_values": pair_generation_details.get(
            "pair_escape_source_projected_dropped_values",
            {},
        ),
        "pair_escape_source_projected_values": pair_generation_details.get("pair_escape_source_projected_values", {}),
        "pair_escape_source_projected_origins": pair_generation_details.get("pair_escape_source_projected_origins", {}),
        "lineage_projection_summary": pair_generation_details.get("lineage_projection_summary", {}),
        "pair_escape_source_reject_reasons": pair_generation_details.get("pair_escape_source_reject_reasons", {}),
        "pair_mutation_radius_summary": pair_frontier_diagnostics.get(
            "pair_mutation_radius_summary",
            pair_generation_details.get("pair_mutation_radius_summary", {}),
        ),
        "pair_escape_source_values": pair_generation_details.get("pair_escape_source_values", {}),
        "pair_escape_source_counts": pair_generation_details.get("pair_escape_source_counts", {}),
        "pair_escape_source_origins": pair_generation_details.get("pair_escape_source_origins", {}),
        "lineage_value_mining_summary": lineage_value_mining_summary,
        "pair_preserve_pool": pair_generation_details.get("pair_preserve_pool", {})
        if frontier_submode == FRONTIER_EXACT1_SUBMODE
        else {},
        "pair_escape_pool": pair_generation_details.get("pair_escape_pool", {})
        if frontier_submode == FRONTIER_EXACT1_SUBMODE
        else {},
        "pair_escape_candidates_kept": pair_frontier_diagnostics.get("pair_escape_candidates_kept", []),
        "pair_escape_candidates_dropped": pair_frontier_diagnostics.get("pair_escape_candidates_dropped", []),
        "pair_gate_failed_escape": pair_frontier_diagnostics.get("pair_gate_failed_escape", []),
        "pair_gate_kept_escape": pair_frontier_diagnostics.get("pair_gate_kept_escape", []),
        "pair_borderline_escape_candidates": pair_frontier_diagnostics.get("pair_borderline_escape_candidates", []),
        "pair_near_local_escape_candidates": pair_frontier_diagnostics.get("pair_near_local_escape_candidates", []),
        "pair_wide_local_escape_candidates": pair_frontier_diagnostics.get("pair_wide_local_escape_candidates", []),
        "pair_projected_preserve_entries": pair_frontier_diagnostics.get("pair_projected_preserve_entries", []),
        "pair_projected_boundary_entries": pair_frontier_diagnostics.get("pair_projected_boundary_entries", []),
        "pair_projected_boundary_wide_candidates": pair_frontier_diagnostics.get(
            "pair_projected_boundary_wide_candidates",
            [],
        ),
        "pair_gate_input_summary": pair_frontier_diagnostics.get("pair_gate_input_summary", {}),
        "pair_gate_borderline_summary": pair_frontier_diagnostics.get("pair_gate_borderline_summary", {}),
        "pair_escape_source_statuses": pair_frontier_diagnostics.get("pair_escape_source_statuses", {}),
        "pair_escape_lane_counts": pair_frontier_diagnostics.get("pair_escape_lane_counts", {}),
        "pair_escape_status_by_lane": pair_frontier_diagnostics.get("pair_escape_status_by_lane", {}),
        "pair_radius_band_counts": pair_frontier_diagnostics.get("pair_radius_band_counts", {}),
        "pair_best_preserve_candidate": pair_frontier_diagnostics.get("pair_best_preserve_candidate"),
        "pair_best_escape_candidate": pair_frontier_diagnostics.get("pair_best_escape_candidate"),
        "pair_best_local_escape": pair_frontier_diagnostics.get("pair_best_local_escape", {}),
        "pair_best_hard_escape": pair_frontier_diagnostics.get("pair_best_hard_escape", {}),
        "pair_local_escape_candidate_count": int(
            pair_frontier_diagnostics.get("pair_local_escape_candidate_count", 0) or 0
        ),
        "pair_local_escape_borderline_count": int(
            pair_frontier_diagnostics.get("pair_local_escape_borderline_count", 0) or 0
        ),
        "pair_near_local_escape_count": int(
            pair_frontier_diagnostics.get("pair_near_local_escape_count", 0) or 0
        ),
        "pair_wide_local_escape_count": int(
            pair_frontier_diagnostics.get("pair_wide_local_escape_count", 0) or 0
        ),
        "pair_local_escape_reject_count": int(
            pair_frontier_diagnostics.get("pair_local_escape_reject_count", 0) or 0
        ),
        "pair_hard_escape_candidate_count": int(
            pair_frontier_diagnostics.get("pair_hard_escape_candidate_count", 0) or 0
        ),
        "pair_profile_preserve_entries": pair_frontier_diagnostics.get("pair_profile_preserve_entries", {}),
        "pair_profile_escape_entries": pair_frontier_diagnostics.get("pair_profile_escape_entries", {}),
        "pair_profile_kept_preserve": pair_frontier_diagnostics.get("pair_profile_kept_preserve", {}),
        "pair_profile_kept_escape": pair_frontier_diagnostics.get("pair_profile_kept_escape", {}),
        "pair_profile_drop_reasons": pair_frontier_diagnostics.get("pair_profile_drop_reasons", {}),
        "pair_profile_truncation_summary": pair_frontier_diagnostics.get("pair_profile_truncation_summary", {}),
        "exact1_projected_competition_summary": pair_stage_stats.get("exact1_projected_competition_summary", {}),
        "pair_drop_reasons": pair_drop_reasons,
        "positions": positions,
        "exact_floor": exact_floor,
        "beam_limit": GUIDED_POOL_BEAM_LIMIT,
        "exploration_slots": GUIDED_POOL_EXPLORATION_SLOTS,
        "value_pool_limit": GUIDED_POOL_TOP_VALUES,
        "best": guided_entries[0] if guided_entries else base_entry,
        "top_entries": guided_entries[:GUIDED_POOL_BEAM_LIMIT],
        "validation_candidates": _frontier_guided_validation_candidates(
            guided_entries,
            pair_frontier_pool,
            validate_top=GUIDED_POOL_VALIDATE_TOP,
        ),
        "stage_stats": stage_stats,
        "pair_stage_stats": pair_stage_stats,
        "pair_frontier_pool": pair_frontier_pool[:FRONTIER_PAIR_SEED_LIMIT],
        "triad_frontier_pool": triad_frontier_pool[:FRONTIER_TRIAD_POOL_LIMIT],
        "feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES]) for key, values in feedback_value_pool_payload.items()
        },
        "exact1_feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES])
            for key, values in feedback_value_pool_payload.items()
            if frontier_submode == FRONTIER_EXACT1_SUBMODE
        },
        "exact0_feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES])
            for key, values in feedback_value_pool_payload.items()
            if frontier_submode != FRONTIER_EXACT1_SUBMODE
        },
        "feedback_sources": feedback_sources,
        "pair_pool_source_counts": pair_pool_source_counts,
        "pair_profiles": {
            f"{left},{right}": [
                {
                    "candidate_hex": str(entry.get("candidate_hex", "")),
                    "cand8_hex": str(entry.get("cand8_hex", "")),
                    "pair_values": list(entry.get("pair_values", [])),
                    "ci_exact_wchars": int(entry.get("ci_exact_wchars", 0) or 0),
                    "ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                    "raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                    "pair_escape_mode": str(entry.get("pair_escape_mode", "")),
                    "pair_raw_prefix_hex_16": str(entry.get("pair_raw_prefix_hex_16", "")),
                    "pair_wide_ascii_contiguous_8": int(entry.get("pair_wide_ascii_contiguous_8", 0) or 0),
                    "pair_wide_zero_high_pairs_8": int(entry.get("pair_wide_zero_high_pairs_8", 0) or 0),
                    "pair_flaglike_tail_pairs_8": int(entry.get("pair_flaglike_tail_pairs_8", 0) or 0),
                    "pair_escape_signal_score": int(entry.get("pair_escape_signal_score", 1 << 30) or (1 << 30)),
                    "pair_escape_signal_reason": str(entry.get("pair_escape_signal_reason", "")),
                    "pair_escape_lane": str(entry.get("pair_escape_lane", "")),
                    "pair_escape_status": str(entry.get("pair_escape_status", "")),
                    "pair_candidate_origin": str(entry.get("pair_candidate_origin", "")),
                    "pair_mutation_radius": int(entry.get("pair_mutation_radius", 0) or 0),
                    "pair_neighbor_mode": str(entry.get("pair_neighbor_mode", "")),
                    "pair_value_origin_by_pos": {
                        str(key): list(value)
                        for key, value in dict(entry.get("pair_value_origin_by_pos", {})).items()
                    },
                }
                for entry in entries[:FRONTIER_PAIR_TOP_PER_PAIR]
            ]
            for (left, right), entries in pair_profiles.items()
        },
        "position_profiles": {
            str(position): [
                {
                    "candidate_hex": str(entry.get("candidate_hex", "")),
                    "cand8_hex": str(entry.get("cand8_hex", "")),
                    "mutated_byte_value": int(entry.get("mutated_byte_value", 0) or 0),
                    "ci_exact_wchars": int(entry.get("ci_exact_wchars", 0) or 0),
                    "ci_distance5": int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
                    "raw_distance10": int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
                    "wide_ascii_contiguous_16": int(entry.get("wide_ascii_contiguous_16", 0) or 0),
                    "wide_ascii_total_16": int(entry.get("wide_ascii_total_16", 0) or 0),
                    "wide_zero_high_pairs_16": int(entry.get("wide_zero_high_pairs_16", 0) or 0),
                    "flaglike_tail_pairs_16": int(entry.get("flaglike_tail_pairs_16", 0) or 0),
                }
                for entry in position_profiles.get(position, [])[:GUIDED_POOL_TOP_VALUES]
            ]
            for position in positions
        },
    }
    guided_result_path = artifacts_dir / GUIDED_POOL_RESULT_FILE_NAME
    _write_json(guided_result_path, guided_payload)
    guided_validation_path, guided_validations = validate_compare_aware_results(
        target=target,
        artifacts_dir=artifacts_dir / "guided_pool_validation",
        result_path=guided_result_path,
        transform_model=transform_model,
        validate_top=max(validate_top, GUIDED_POOL_VALIDATE_TOP),
        per_probe_timeout=per_probe_timeout,
        log=log,
        output_file_name=GUIDED_POOL_VALIDATION_FILE_NAME,
        compare_output_prefix="guided_pool_compare_aware",
    )
    return {
        "guided_pool_result_path": str(guided_result_path),
        "guided_pool_validation_path": str(guided_validation_path),
        "guided_entries": guided_entries,
        "guided_validations": guided_validations,
        "anchor_mode": anchor_mode,
        "frontier_submode": frontier_submode,
        "source_anchor": normalized_source_anchor,
        "frontier_role": normalized_frontier_role,
        "anchor_lineage": guided_payload["anchor_lineage"],
        "pair_generation_mode": guided_payload["pair_generation_mode"],
        "pair_set_mode": guided_payload["pair_set_mode"],
        "locked_pair_positions": guided_payload["locked_pair_positions"],
        "alternate_locked_pair_positions": guided_payload["alternate_locked_pair_positions"],
        "pair_set_comparison_summary": guided_payload["pair_set_comparison_summary"],
        "pair_generation_sources": pair_generation_sources,
        "pair_escape_mode": guided_payload["pair_escape_mode"],
        "pair_escape_pool_strategy": guided_payload["pair_escape_pool_strategy"],
        "pair_neighbor_generation_summary": guided_payload["pair_neighbor_generation_summary"],
        "pair_single_byte_guard_summary": guided_payload["pair_single_byte_guard_summary"],
        "pair_single_byte_guard_candidates": guided_payload["pair_single_byte_guard_candidates"],
        "pair_single_byte_guard_status_counts": guided_payload["pair_single_byte_guard_status_counts"],
        "pair_guard_soft_promoted_values": guided_payload["pair_guard_soft_promoted_values"],
        "pair_guard_nonbase_starved": guided_payload["pair_guard_nonbase_starved"],
        "pair_mutation_radius_summary": guided_payload["pair_mutation_radius_summary"],
        "pair_escape_source_values": guided_payload["pair_escape_source_values"],
        "pair_escape_source_counts": guided_payload["pair_escape_source_counts"],
        "pair_escape_source_origins": guided_payload["pair_escape_source_origins"],
        "lineage_value_mining_summary": guided_payload["lineage_value_mining_summary"],
        "pair_preserve_pool": guided_payload["pair_preserve_pool"],
        "pair_escape_pool": guided_payload["pair_escape_pool"],
        "pair_escape_candidates_kept": guided_payload["pair_escape_candidates_kept"],
        "pair_escape_candidates_dropped": guided_payload["pair_escape_candidates_dropped"],
        "pair_gate_failed_escape": guided_payload["pair_gate_failed_escape"],
        "pair_gate_kept_escape": guided_payload["pair_gate_kept_escape"],
        "pair_borderline_escape_candidates": guided_payload["pair_borderline_escape_candidates"],
        "pair_near_local_escape_candidates": guided_payload["pair_near_local_escape_candidates"],
        "pair_wide_local_escape_candidates": guided_payload["pair_wide_local_escape_candidates"],
        "pair_projected_boundary_candidates": guided_payload["pair_projected_boundary_candidates"],
        "pair_projected_preserve_candidates": guided_payload["pair_projected_preserve_candidates"],
        "pair_projected_preserve_entries": guided_payload["pair_projected_preserve_entries"],
        "pair_projected_boundary_entries": guided_payload["pair_projected_boundary_entries"],
        "pair_projected_boundary_wide_candidates": guided_payload["pair_projected_boundary_wide_candidates"],
        "pair_projected_winner_gate_summary": guided_payload["pair_projected_winner_gate_summary"],
        "pair_projected_winner_gate_status_counts": guided_payload["pair_projected_winner_gate_status_counts"],
        "pair_gate_input_summary": guided_payload["pair_gate_input_summary"],
        "pair_gate_borderline_summary": guided_payload["pair_gate_borderline_summary"],
        "pair_escape_source_statuses": guided_payload["pair_escape_source_statuses"],
        "pair_escape_lane_counts": guided_payload["pair_escape_lane_counts"],
        "pair_escape_status_by_lane": guided_payload["pair_escape_status_by_lane"],
        "pair_radius_band_counts": guided_payload["pair_radius_band_counts"],
        "pair_best_preserve_candidate": guided_payload["pair_best_preserve_candidate"],
        "pair_best_escape_candidate": guided_payload["pair_best_escape_candidate"],
        "pair_best_local_escape": guided_payload["pair_best_local_escape"],
        "pair_best_hard_escape": guided_payload["pair_best_hard_escape"],
        "pair_local_escape_candidate_count": guided_payload["pair_local_escape_candidate_count"],
        "pair_local_escape_borderline_count": guided_payload["pair_local_escape_borderline_count"],
        "pair_near_local_escape_count": guided_payload["pair_near_local_escape_count"],
        "pair_wide_local_escape_count": guided_payload["pair_wide_local_escape_count"],
        "pair_local_escape_reject_count": guided_payload["pair_local_escape_reject_count"],
        "pair_hard_escape_candidate_count": guided_payload["pair_hard_escape_candidate_count"],
        "pair_profile_preserve_entries": guided_payload["pair_profile_preserve_entries"],
        "pair_profile_escape_entries": guided_payload["pair_profile_escape_entries"],
        "pair_profile_kept_preserve": guided_payload["pair_profile_kept_preserve"],
        "pair_profile_kept_escape": guided_payload["pair_profile_kept_escape"],
        "pair_profile_drop_reasons": guided_payload["pair_profile_drop_reasons"],
        "pair_profile_truncation_summary": guided_payload["pair_profile_truncation_summary"],
        "exact1_projected_competition_summary": guided_payload["exact1_projected_competition_summary"],
        "pair_drop_reasons": pair_drop_reasons,
        "positions": positions,
        "value_pools": {str(key): list(values[:GUIDED_POOL_TOP_VALUES]) for key, values in value_pools.items()},
        "position_profiles": position_profiles,
        "pair_profiles": pair_profiles,
        "pair_frontier_pool": pair_frontier_pool,
        "triad_frontier_pool": triad_frontier_pool,
        "stage_stats": stage_stats,
        "pair_stage_stats": pair_stage_stats,
        "beam_limit": GUIDED_POOL_BEAM_LIMIT,
        "exact_floor": exact_floor,
        "feedback_value_pools": {key: list(values[:GUIDED_POOL_TOP_VALUES]) for key, values in feedback_value_pool_payload.items()},
        "exact1_feedback_value_pools": {
            key: list(values[:GUIDED_POOL_TOP_VALUES])
            for key, values in feedback_value_pool_payload.items()
            if frontier_submode == FRONTIER_EXACT1_SUBMODE
        },
        "exact0_feedback_value_pools": {
            key: list(values[:GUIDED_POOL_TOP_VALUES])
            for key, values in feedback_value_pool_payload.items()
            if frontier_submode != FRONTIER_EXACT1_SUBMODE
        },
        "feedback_sources": feedback_sources,
        "pair_pool_source_counts": pair_pool_source_counts,
    }


def _source_grouped_leaderboards(
    *,
    top_entries: Sequence[dict[str, object]],
    anchor_sources: dict[str, str],
    transform_model: SamplereverseTransformModel,
) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {"seed_anchor": [], "bridge_promoted": [], "frontier_anchor": []}
    candidate_sources = {
        f"{anchor}{DEFAULT_FIXED_SUFFIX_HEX}": source
        for anchor, source in anchor_sources.items()
    }
    for entry in top_entries:
        source = candidate_sources.get(str(entry.get("candidate_hex", "")))
        if not source:
            continue
        grouped.setdefault(source, []).append(_normalize_compare_entry(entry, transform_model=transform_model))
    for anchor, source in anchor_sources.items():
        if grouped.get(source):
            continue
        grouped.setdefault(source, []).append(
            _evaluate_candidate_hex(f"{anchor}{DEFAULT_FIXED_SUFFIX_HEX}", transform_model)
        )
    return grouped


def _variable_positions_from_entries(
    entries: Sequence[dict[str, object]],
    *,
    base_anchor: str,
) -> tuple[list[int], list[int]]:
    byte_scores: dict[int, int] = {}
    nibble_scores: dict[int, int] = {}
    base = bytes.fromhex(base_anchor)
    for entry in entries[:BRIDGE_VALIDATE_TOP]:
        candidate = bytes.fromhex(str(entry.get("cand8_hex", base_anchor)))
        for idx, (base_byte, candidate_byte) in enumerate(zip(base, candidate)):
            if base_byte != candidate_byte:
                byte_scores[idx] = byte_scores.get(idx, 0) + 1
        for idx in [int(item) for item in entry.get("pair_positions", []) if isinstance(item, int) or str(item).isdigit()]:
            if 0 <= idx < 8:
                byte_scores[idx] = byte_scores.get(idx, 0) + 2
        for idx in [int(item) for item in entry.get("triad_positions", []) if isinstance(item, int) or str(item).isdigit()]:
            if 0 <= idx < 8:
                byte_scores[idx] = byte_scores.get(idx, 0) + 3
        candidate_hex = str(entry.get("candidate_hex", "")).strip().lower()
        if not candidate_hex:
            cand8_hex = str(entry.get("cand8_hex", "")).strip().lower()
            if len(cand8_hex) == 16:
                candidate_hex = f"{cand8_hex}{DEFAULT_FIXED_SUFFIX_HEX}"
        for nibble_idx in _diff_nibbles(base_anchor, candidate_hex):
            nibble_scores[nibble_idx] = nibble_scores.get(nibble_idx, 0) + 1
    variable_bytes = [
        idx
        for idx, _ in sorted(byte_scores.items(), key=lambda item: (-item[1], item[0]))[:HOT_POSITION_LIMIT]
    ]
    variable_nibbles = [
        idx
        for idx, _ in sorted(nibble_scores.items(), key=lambda item: (-item[1], item[0] % 2, item[0]))[:HOT_NIBBLE_LIMIT]
    ]
    return variable_bytes, variable_nibbles


def _select_smt_base_entry(
    *,
    best_exact2_entry: dict[str, object] | None,
    frontier_validations: Sequence[dict[str, object]],
    fallback_entry: dict[str, object],
) -> dict[str, object]:
    exact2_distance = int(best_exact2_entry.get("runtime_ci_distance5", 1 << 30) or (1 << 30)) if best_exact2_entry else (1 << 30)
    compare_agree_frontiers = [
        item
        for item in frontier_validations
        if bool(item.get("compare_semantics_agree"))
        and int(item.get("runtime_ci_exact_wchars", 0) or 0) <= 1
    ]
    best_exact1_frontier = min(
        (
            item for item in compare_agree_frontiers if int(item.get("runtime_ci_exact_wchars", 0) or 0) == 1
        ),
        key=lambda item: (
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
        default=None,
    )
    best_exact0_frontier = min(
        (
            item for item in compare_agree_frontiers if int(item.get("runtime_ci_exact_wchars", 0) or 0) == 0
        ),
        key=lambda item: (
            int(item.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            str(item.get("candidate_hex", "")),
        ),
        default=None,
    )
    best_frontier = best_exact1_frontier
    if best_frontier is None and best_exact0_frontier and int(best_exact0_frontier.get("runtime_ci_distance5", 1 << 30) or (1 << 30)) < exact2_distance:
        best_frontier = best_exact0_frontier
    if best_frontier:
        frontier_submode = (
            FRONTIER_EXACT1_SUBMODE
            if int(best_frontier.get("runtime_ci_exact_wchars", 0) or 0) == 1
            else FRONTIER_EXACT0_SUBMODE
        )
        return {
            "candidate_hex": str(best_frontier.get("candidate_hex", "")),
            "cand8_hex": str(best_frontier.get("cand8_hex", "")),
            "ci_exact_wchars": int(best_frontier.get("runtime_ci_exact_wchars", 0) or 0),
            "ci_distance5": int(best_frontier.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
            "raw_distance10": int(best_frontier.get("offline_raw_distance10", 1 << 30) or (1 << 30)),
            "source_anchor": str(best_frontier.get("source_anchor", "")).strip().lower() or str(best_frontier.get("cand8_hex", "")).strip().lower(),
            "frontier_role": str(best_frontier.get("frontier_role", "")).strip() or _frontier_role_for_runtime_validation(best_frontier),
            "anchor_mode": FRONTIER_ANCHOR_MODE,
            "frontier_submode": frontier_submode,
            "anchor_lineage": str(best_frontier.get("anchor_lineage", "")).strip()
            or _lineage_root(
                source_anchor=str(best_frontier.get("source_anchor", "")).strip().lower()
                or str(best_frontier.get("cand8_hex", "")).strip().lower(),
                frontier_role=str(best_frontier.get("frontier_role", "")).strip() or _frontier_role_for_runtime_validation(best_frontier),
                anchor_mode=FRONTIER_ANCHOR_MODE,
            ),
        }
    return fallback_entry


def _smt_feedback_value_pools(
    *,
    base_anchor: str,
    variable_byte_positions: Sequence[int],
    comparison_entries: Sequence[dict[str, object]],
    preferred_entries: Sequence[dict[str, object]] = (),
    lineage_value_pools: dict[int, Sequence[int]] | None = None,
) -> dict[int, list[int]]:
    base_bytes = bytes.fromhex(base_anchor)
    feedback_entries = list(preferred_entries)
    feedback_entries.extend(entry for entry in comparison_entries if bool(entry.get("improvement_gate_passed")))
    if not feedback_entries:
        feedback_entries = list(comparison_entries[:BRIDGE_VALIDATE_TOP])
    counts = _feedback_counts_from_frontier_entries(feedback_entries)
    pools: dict[int, list[int]] = {}
    for position in variable_byte_positions:
        if not (0 <= int(position) < len(base_bytes)):
            continue
        ordered_values = [
            value
            for value, _ in sorted(
                counts.get(int(position), {}).items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]
        pools[int(position)] = _bounded_value_pool(
            base_value=base_bytes[int(position)],
            profile_values=[],
            feedback_values=[*[int(value) & 0xFF for value in (lineage_value_pools or {}).get(int(position), [])], *ordered_values],
        )
    return pools


def _normalized_smt_value_pools(
    value_pools: dict[int, Sequence[int]] | dict[str, Sequence[int]],
) -> dict[int, list[int]]:
    normalized: dict[int, list[int]] = {}
    for raw_position, raw_values in dict(value_pools or {}).items():
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        values: list[int] = []
        for raw_value in raw_values:
            try:
                value = int(raw_value) & 0xFF
            except (TypeError, ValueError):
                continue
            if value not in values:
                values.append(value)
        if values:
            normalized[position] = values
    return normalized


def _bounded_position_list(
    positions: Sequence[object],
    *,
    upper_bound: int,
    limit: int,
) -> list[int]:
    out: list[int] = []
    for raw_position in positions:
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if 0 <= position < upper_bound and position not in out:
            out.append(position)
        if len(out) >= limit:
            break
    return out


def _exact2_basin_smt_diagnostic_payload(
    *,
    best_exact2_entry: dict[str, object] | None,
    primary_smt_entry: dict[str, object],
    comparison_entries: Sequence[dict[str, object]],
    lineage_entries: Sequence[dict[str, object]],
    transform_model: SamplereverseTransformModel,
) -> dict[str, object]:
    if not best_exact2_entry:
        return {
            "attempted": False,
            "recommended": False,
            "reason": "no_compare_agree_exact2_entry",
        }
    exact2_anchor = (
        str(best_exact2_entry.get("cand8_hex", "")).strip().lower()
        or str(best_exact2_entry.get("candidate_hex", ""))[:16].strip().lower()
    )
    primary_anchor = (
        str(primary_smt_entry.get("cand8_hex", "")).strip().lower()
        or str(primary_smt_entry.get("candidate_hex", ""))[:16].strip().lower()
    )
    if len(exact2_anchor) != 16:
        return {
            "attempted": False,
            "recommended": False,
            "reason": "invalid_exact2_anchor",
            "base_anchor": exact2_anchor,
            "primary_base_anchor": primary_anchor,
        }
    exact2_entry = {
        "candidate_hex": str(best_exact2_entry.get("candidate_hex", "")) or f"{exact2_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
        "cand8_hex": exact2_anchor,
        "ci_exact_wchars": int(best_exact2_entry.get("runtime_ci_exact_wchars", 0) or 0),
        "ci_distance5": int(best_exact2_entry.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
        "raw_distance10": int(best_exact2_entry.get("offline_raw_distance10", 1 << 30) or (1 << 30)),
        "runtime_lhs_prefix_hex_10": str(best_exact2_entry.get("runtime_lhs_prefix_hex_10", "")),
        "frontier_role": str(best_exact2_entry.get("frontier_role", "")) or "exact2_seed",
        "anchor_mode": EXACT2_ANCHOR_MODE,
    }
    variable_bytes, variable_nibbles = _variable_positions_from_entries(
        comparison_entries,
        base_anchor=exact2_anchor,
    )
    feedback_value_pools = _smt_feedback_value_pools(
        base_anchor=exact2_anchor,
        variable_byte_positions=variable_bytes,
        comparison_entries=comparison_entries,
        preferred_entries=[],
    )
    return {
        "attempted": False,
        "recommended": primary_anchor != exact2_anchor,
        "reason": (
            "primary_smt_base_is_frontier; exact2_basin_diagnostic_only"
            if primary_anchor != exact2_anchor
            else "primary_smt_base_already_exact2"
        ),
        "base_anchor": exact2_anchor,
        "primary_base_anchor": primary_anchor,
        "anchor_mode": EXACT2_ANCHOR_MODE,
        "variable_byte_positions": variable_bytes,
        "variable_nibble_positions": variable_nibbles,
        "feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES])
            for key, values in feedback_value_pools.items()
        },
        "prefix_boundary": _prefix_boundary_breakdown_from_entry(
            exact2_entry,
            transform_model=transform_model,
            source="exact2_basin_smt_diagnostic",
        ),
        "comparison_entry_count": len(comparison_entries),
        "lineage_entry_count": len(lineage_entries),
    }


def _exact1_projected_winner_smt_entries(
    *,
    base_anchor: str,
    base_entry: dict[str, object],
) -> list[dict[str, object]]:
    winner_entries: list[dict[str, object]] = []
    status_root = dict(base_entry.get("pair_projected_competitive_status", {}))
    winner_root = dict(base_entry.get("pair_projected_competitive_winner", {}))
    flattened: list[tuple[str, dict[str, object], str]] = []
    for key, winner_or_map in winner_root.items():
        if isinstance(winner_or_map, dict) and any(isinstance(value, dict) for value in winner_or_map.values()):
            status_map = status_root.get(str(key), {})
            status_map = status_map if isinstance(status_map, dict) else {}
            for position_key, winner in winner_or_map.items():
                if isinstance(winner, dict):
                    flattened.append((str(position_key), winner, str(status_map.get(str(position_key), ""))))
        elif isinstance(winner_or_map, dict):
            flattened.append((str(key), winner_or_map, str(status_root.get(str(key), ""))))
    for position_key, winner, status in flattened:
        if not str(position_key).strip().isdigit():
            continue
        if status != "projected_beats_neighbor":
            continue
        if not isinstance(winner, dict) or str(winner.get("family", "")) != "projected_soft_family":
            continue
        winner_value = int(winner.get("value", -1) or -1)
        if not (0 <= winner_value <= 0xFF):
            continue
        winner_entries.append(
            {
                "candidate_hex": f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
                "cand8_hex": base_anchor,
                "pair_positions": [int(position_key)],
                "pair_values": [winner_value & 0xFF],
            }
        )
    seen_positions = {tuple(entry.get("pair_positions", [])) for entry in winner_entries}
    for item in base_entry.get("pair_projected_winner_available", []):
        if not isinstance(item, dict):
            continue
        position = int(item.get("position", -1) or -1)
        winner_value = int(item.get("value", -1) or -1)
        if not (0 <= position < 8 and 0 <= winner_value <= 0xFF):
            continue
        if (position,) in seen_positions:
            continue
        seen_positions.add((position,))
        winner_entries.append(
            {
                "candidate_hex": f"{base_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
                "cand8_hex": base_anchor,
                "pair_positions": [position],
                "pair_values": [winner_value & 0xFF],
            }
        )
    return winner_entries


def _exact1_smt_preferred_entries(
    *,
    base_anchor: str,
    base_entry: dict[str, object],
) -> list[dict[str, object]]:
    preferred_entries: list[dict[str, object]] = []
    def _bucket_entries(value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [dict(entry) for entry in value if isinstance(entry, dict)]
        if isinstance(value, dict):
            entries: list[dict[str, object]] = []
            for item in value.values():
                if isinstance(item, dict):
                    entries.append(dict(item))
                elif isinstance(item, list):
                    entries.extend(dict(entry) for entry in item if isinstance(entry, dict))
            return entries
        return []

    for bucket_key in (
        "pair_profile_kept_preserve",
        "pair_gate_kept_escape",
        "pair_near_local_escape_candidates",
        "pair_projected_preserve_entries",
        "pair_projected_boundary_entries",
    ):
        preferred_entries.extend(_bucket_entries(base_entry.get(bucket_key, [])))
    preferred_entries.extend(
        _exact1_projected_winner_smt_entries(
            base_anchor=base_anchor,
            base_entry=base_entry,
        )
    )
    return preferred_entries


def run_compare_aware_smt(
    *,
    target: Path,
    artifacts_dir: Path,
    base_entry: dict[str, object],
    comparison_entries: Sequence[dict[str, object]],
    lineage_entries: Sequence[dict[str, object]] = (),
    variable_byte_positions_override: Sequence[int] | None = None,
    variable_nibble_positions_override: Sequence[int] | None = None,
    value_pools_override: dict[int, Sequence[int]] | dict[str, Sequence[int]] | None = None,
    transform_model: SamplereverseTransformModel,
    per_probe_timeout: float,
    log,
) -> dict[str, object]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    base_anchor = str(base_entry.get("cand8_hex") or str(base_entry.get("candidate_hex", ""))[:16]).lower()
    anchor_mode = _guided_anchor_mode(base_entry)
    frontier_submode = _frontier_submode_from_entry(base_entry, default_anchor_mode=anchor_mode)
    normalized_source_anchor = str(base_entry.get("source_anchor", "")).strip().lower() or base_anchor
    filtered_entries = list(comparison_entries)
    lineage_value_pools: dict[int, list[int]] = {}
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        exact1_entries = [
            entry
            for entry in comparison_entries
            if _frontier_submode_from_entry(entry, default_anchor_mode=str(entry.get("anchor_mode", ""))) == FRONTIER_EXACT1_SUBMODE
            and (
                str(entry.get("source_anchor", "")).strip().lower() == normalized_source_anchor
                or str(entry.get("cand8_hex", "")).strip().lower() == base_anchor
            )
        ]
        if exact1_entries:
            filtered_entries = exact1_entries
    variable_bytes, variable_nibbles = _variable_positions_from_entries(filtered_entries, base_anchor=base_anchor)
    if frontier_submode == FRONTIER_EXACT1_SUBMODE and variable_bytes:
        lineage_value_pools, _, _, _ = _mine_exact1_lineage_value_sources(
            base_anchor=base_anchor,
            source_anchor=normalized_source_anchor,
            positions=variable_bytes,
            transform_model=transform_model,
            lineage_entries=lineage_entries,
        )
        for position_key, values in dict(base_entry.get("pair_escape_source_projected_kept_values", {})).items():
            try:
                position = int(position_key)
            except (TypeError, ValueError):
                continue
            merged = list(dict.fromkeys([
                *[int(value) & 0xFF for value in lineage_value_pools.get(position, [])],
                *[int(value) & 0xFF for value in values if isinstance(value, (int, str))],
            ]))
            if merged:
                lineage_value_pools[position] = merged
        for position_key, winner in dict(base_entry.get("pair_projected_competitive_winner", {})).items():
            try:
                position = int(position_key)
            except (TypeError, ValueError):
                continue
            if not isinstance(winner, dict) or str(winner.get("family", "")) != "projected_soft_family":
                continue
            winner_value = int(winner.get("value", -1))
            if not (0 <= winner_value <= 0xFF):
                continue
            merged = list(dict.fromkeys([
                *[int(value) & 0xFF for value in lineage_value_pools.get(position, [])],
                winner_value & 0xFF,
            ]))
            lineage_value_pools[position] = merged
    preferred_smt_entries: list[dict[str, object]] = []
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        preferred_smt_entries.extend(
            _exact1_smt_preferred_entries(
                base_anchor=base_anchor,
                base_entry=base_entry,
            )
        )
    if variable_byte_positions_override is not None:
        variable_bytes = _bounded_position_list(
            variable_byte_positions_override,
            upper_bound=INPUT_LENGTH,
            limit=HOT_POSITION_LIMIT,
        )
    if variable_nibble_positions_override is not None:
        variable_nibbles = _bounded_position_list(
            variable_nibble_positions_override,
            upper_bound=INPUT_LENGTH * 2,
            limit=HOT_NIBBLE_LIMIT,
        )
    feedback_value_pools = _smt_feedback_value_pools(
        base_anchor=base_anchor,
        variable_byte_positions=variable_bytes,
        comparison_entries=filtered_entries,
        preferred_entries=preferred_smt_entries,
        lineage_value_pools=lineage_value_pools,
    )
    if value_pools_override:
        feedback_value_pools = _normalized_smt_value_pools(value_pools_override)
    z3_result = solve_targeted_prefix8(
        base_anchor=base_anchor,
        variable_byte_positions=variable_bytes,
        variable_nibble_positions=variable_nibbles,
        value_pools=feedback_value_pools,
        prioritize_distance=anchor_mode == FRONTIER_ANCHOR_MODE,
        timeout_ms=1500,
    )
    z3_diagnostics = dict(getattr(z3_result, "diagnostics", None) or {})
    payload: dict[str, object] = {
        "base_anchor": base_anchor,
        "anchor_mode": anchor_mode,
        "frontier_submode": frontier_submode,
        "variable_byte_positions": variable_bytes,
        "variable_nibble_positions": variable_nibbles,
        "feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES]) for key, values in feedback_value_pools.items()
        },
        "prefix_boundary": _prefix_boundary_breakdown_from_entry(
            base_entry,
            transform_model=transform_model,
            source="smt_base",
        ),
        "attempted": z3_result.attempted,
        "summary": z3_result.summary,
        "evidence": z3_result.evidence or [],
        **z3_diagnostics,
        "top_entries": [],
        "validation_candidates": [],
    }
    entry: dict[str, object] | None = None
    validations: list[dict[str, object]] = []
    validation_path: Path | None = None
    if z3_result.candidate_hex:
        entry = _evaluate_candidate_hex(z3_result.candidate_hex, transform_model)
        entry.update(
            {
                "stage": "smt",
                "base_anchor": base_anchor,
                "positions_or_nibbles": variable_bytes or variable_nibbles,
                "source_anchor": str(base_entry.get("source_anchor", "")).strip().lower() or base_anchor,
                "frontier_role": str(base_entry.get("frontier_role", "")).strip(),
                "anchor_mode": anchor_mode,
                "anchor_lineage": _append_lineage(
                    str(base_entry.get("anchor_lineage", "")).strip()
                    or _lineage_root(
                        source_anchor=str(base_entry.get("source_anchor", "")).strip().lower() or base_anchor,
                        frontier_role=str(base_entry.get("frontier_role", "")).strip(),
                        anchor_mode=anchor_mode,
                    ),
                    "smt",
                ),
            }
        )
        payload["top_entries"] = [entry]
        payload["validation_candidates"] = [entry]
        result_path = artifacts_dir / SMT_RESULT_FILE_NAME
        _write_json(result_path, payload)
        validation_path, validations = validate_compare_aware_results(
            target=target,
            artifacts_dir=artifacts_dir / "smt_validation",
            result_path=result_path,
            transform_model=transform_model,
            validate_top=1,
            per_probe_timeout=per_probe_timeout,
            log=log,
            output_file_name=SMT_VALIDATION_FILE_NAME,
            compare_output_prefix="smt_compare_aware",
        )
    else:
        result_path = artifacts_dir / SMT_RESULT_FILE_NAME
        _write_json(result_path, payload)
    return {
        "result_path": str(result_path),
        "validation_path": str(validation_path) if validation_path else "",
        "entry": entry,
        "validations": validations,
        "variable_byte_positions": variable_bytes,
        "variable_nibble_positions": variable_nibbles,
        "payload": payload,
    }


def _exact2_basin_runtime_improved(
    validation: dict[str, object],
    *,
    baseline_exact: int,
    baseline_distance: int,
) -> bool:
    if not bool(validation.get("compare_semantics_agree")):
        return False
    runtime_exact = int(validation.get("runtime_ci_exact_wchars", 0) or 0)
    runtime_distance = int(validation.get("runtime_ci_distance5", 1 << 30) or (1 << 30))
    return runtime_exact > baseline_exact or runtime_distance < baseline_distance


def run_exact2_basin_value_pool_evaluation(
    *,
    target: Path,
    artifacts_dir: Path,
    base_entry: dict[str, object],
    exact2_basin_smt: dict[str, object],
    transform_model: SamplereverseTransformModel,
    per_probe_timeout: float,
    log,
    max_combinations: int = EXACT2_BASIN_VALUE_POOL_EVAL_MAX_COMBINATIONS,
) -> dict[str, object]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    base_anchor = (
        str(exact2_basin_smt.get("base_anchor", "")).strip().lower()
        or str(base_entry.get("cand8_hex", "")).strip().lower()
        or str(base_entry.get("candidate_hex", ""))[:16].strip().lower()
    )
    result_path = artifacts_dir / EXACT2_BASIN_VALUE_POOL_RESULT_FILE_NAME
    if len(base_anchor) != 16:
        payload = {
            "attempted": False,
            "classification": "exact2_basin_value_pool_invalid_base",
            "base_anchor": base_anchor,
            "generated_count": 0,
            "unique_count": 0,
            "validated_count": 0,
            "validation_candidates": [],
            "validations": [],
        }
        _write_json(result_path, payload)
        return {
            "result_path": str(result_path),
            "validation_path": "",
            "payload": payload,
            "validations": [],
            "promotable_validations": [],
        }

    base_bytes = bytes.fromhex(base_anchor)
    positions = _bounded_position_list(
        list(exact2_basin_smt.get("variable_byte_positions", [])),
        upper_bound=len(base_bytes),
        limit=HOT_POSITION_LIMIT,
    )
    raw_pools = _normalized_smt_value_pools(
        dict(exact2_basin_smt.get("feedback_value_pools", {}))
    )
    value_pools: dict[int, list[int]] = {}
    for position in positions:
        values = [int(base_bytes[position]) & 0xFF]
        for raw_value in raw_pools.get(position, []):
            value = int(raw_value) & 0xFF
            if value not in values:
                values.append(value)
        value_pools[position] = values

    estimated_combinations = 1
    for position in positions:
        estimated_combinations *= max(1, len(value_pools.get(position, [])))
    if estimated_combinations > max_combinations:
        payload = {
            "attempted": False,
            "classification": "exact2_basin_value_pool_over_cap",
            "base_anchor": base_anchor,
            "positions": positions,
            "value_pools": {str(key): list(values) for key, values in value_pools.items()},
            "generated_count": 0,
            "unique_count": 0,
            "validated_count": 0,
            "estimated_value_pool_combinations": estimated_combinations,
            "max_combinations": max_combinations,
            "validation_candidates": [],
            "validations": [],
        }
        _write_json(result_path, payload)
        return {
            "result_path": str(result_path),
            "validation_path": "",
            "payload": payload,
            "validations": [],
            "promotable_validations": [],
        }

    entries: list[dict[str, object]] = []
    seen_candidates: set[str] = set()
    pool_lists = [value_pools[position] for position in positions]
    combinations = itertools.product(*pool_lists) if pool_lists else [()]
    for values in combinations:
        candidate_prefix = bytearray(base_bytes)
        for position, value in zip(positions, values):
            candidate_prefix[position] = int(value) & 0xFF
        candidate_hex = bytes(candidate_prefix).hex() + DEFAULT_FIXED_SUFFIX_HEX
        if candidate_hex in seen_candidates:
            continue
        seen_candidates.add(candidate_hex)
        entry = _evaluate_candidate_hex(candidate_hex, transform_model)
        entry.update(
            {
                "stage": "exact2_basin_value_pool",
                "base_anchor": base_anchor,
                "source_anchor": base_anchor,
                "frontier_role": "exact2_basin_value_pool",
                "anchor_mode": EXACT2_ANCHOR_MODE,
                "anchor_lineage": _append_lineage(
                    _lineage_root(
                        source_anchor=base_anchor,
                        frontier_role="exact2_seed",
                        anchor_mode=EXACT2_ANCHOR_MODE,
                    ),
                    "value_pool_eval",
                ),
                "positions_or_nibbles": positions,
            }
        )
        entries.append(entry)
    entries.sort(
        key=lambda entry: (
            -int(entry.get("ci_exact_wchars", 0) or 0),
            int(entry.get("ci_distance5", 1 << 30) or (1 << 30)),
            int(entry.get("raw_distance10", 1 << 30) or (1 << 30)),
            str(entry.get("candidate_hex", "")),
        )
    )

    baseline_exact = int(
        base_entry.get("runtime_ci_exact_wchars", base_entry.get("ci_exact_wchars", 2)) or 2
    )
    baseline_distance = int(
        base_entry.get("runtime_ci_distance5", base_entry.get("ci_distance5", DEFAULT_BRIDGE_BASELINE_DISTANCE5))
        or DEFAULT_BRIDGE_BASELINE_DISTANCE5
    )
    payload: dict[str, object] = {
        "attempted": True,
        "classification": "exact2_basin_value_pool_pending_validation",
        "base_anchor": base_anchor,
        "positions": positions,
        "value_pools": {str(key): list(values) for key, values in value_pools.items()},
        "generated_count": estimated_combinations,
        "unique_count": len(entries),
        "validated_count": 0,
        "estimated_value_pool_combinations": estimated_combinations,
        "max_combinations": max_combinations,
        "baseline_exact_wchars": baseline_exact,
        "baseline_distance5": baseline_distance,
        "best_offline_candidate": entries[0] if entries else {},
        "best_runtime_candidate": {},
        "improved_over_exact2": False,
        "validation_candidates": entries,
        "validations": [],
    }
    _write_json(result_path, payload)

    validation_path: Path | None = None
    validations: list[dict[str, object]] = []
    if entries:
        validation_path, validations = validate_compare_aware_results(
            target=target,
            artifacts_dir=artifacts_dir / "value_pool_validation",
            result_path=result_path,
            transform_model=transform_model,
            validate_top=len(entries),
            per_probe_timeout=per_probe_timeout,
            log=log,
            output_file_name=EXACT2_BASIN_VALUE_POOL_VALIDATION_FILE_NAME,
            compare_output_prefix="exact2_basin_value_pool_compare_aware",
        )

    compare_agree_validations = [
        item for item in validations if bool(item.get("compare_semantics_agree"))
    ]
    best_runtime_candidate = (
        sorted(compare_agree_validations, key=_runtime_validation_sort_key)[0]
        if compare_agree_validations
        else {}
    )
    promotable_validations = [
        item
        for item in validations
        if _exact2_basin_runtime_improved(
            item,
            baseline_exact=baseline_exact,
            baseline_distance=baseline_distance,
        )
    ]
    improved = bool(promotable_validations)
    payload.update(
        {
            "classification": (
                "exact2_basin_deterministic_eval_improved"
                if improved
                else "exact2_basin_value_pools_exhausted_no_gain"
            ),
            "validated_count": len(validations),
            "best_runtime_candidate": best_runtime_candidate,
            "improved_over_exact2": improved,
            "validation_path": str(validation_path) if validation_path else "",
            "validations": validations,
            "promotable_validations": promotable_validations,
        }
    )
    _write_json(result_path, payload)
    return {
        "result_path": str(result_path),
        "validation_path": str(validation_path) if validation_path else "",
        "payload": payload,
        "validations": validations,
        "promotable_validations": promotable_validations,
    }


def run_compare_aware_baselines(
    *,
    target: Path,
    artifacts_dir: Path,
    search_budget: int,
    seeds: list[int],
    anchors: list[str],
    snapshot_interval: int,
    validate_top: int,
    per_probe_timeout: float,
    log,
) -> Path:
    transform_model = SamplereverseTransformModel()
    runs: list[dict[str, object]] = []
    unique_prefixes: set[str] = set()
    ci_thresholds = {"ci_exact_wchars_3": None, "ci_exact_wchars_4": None, "ci_exact_wchars_5": None}
    for seed in seeds:
        run_dir = artifacts_dir / f"seed_{seed}"
        result_path = run_compare_aware_refine(
            artifacts_dir=run_dir,
            search_budget=search_budget,
            seed=seed,
            anchors=anchors,
            snapshot_interval=snapshot_interval,
            log=log,
        )
        validation_path, validations = validate_compare_aware_results(
            target=target,
            artifacts_dir=run_dir,
            result_path=result_path,
            transform_model=transform_model,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        result_payload = json.loads(result_path.read_text(encoding="utf-8"))
        top_entries = _collect_top_entries(result_payload, transform_model, limit=5)
        for entry in top_entries:
            raw_prefix_hex = str(entry.get("raw_prefix_hex", "")).strip().lower()
            if raw_prefix_hex:
                unique_prefixes.add(raw_prefix_hex)
        for threshold in (3, 4, 5):
            if ci_thresholds[f"ci_exact_wchars_{threshold}"] is None and any(
                int(item.get("runtime_ci_exact_wchars", 0)) >= threshold for item in validations
            ):
                ci_thresholds[f"ci_exact_wchars_{threshold}"] = seed
        runs.append(
            {
                "seed": seed,
                "result_path": str(result_path),
                "validation_path": str(validation_path),
                "best": result_payload.get("best", {}),
                "top_entries": top_entries,
            }
        )
    summary = {
        "target": str(target),
        "search_budget": search_budget,
        "anchors": anchors,
        "seeds": seeds,
        "runs": runs,
        "unique_raw_prefix_hex": sorted(unique_prefixes),
        "milestones": ci_thresholds,
    }
    summary_path = artifacts_dir / BASELINE_SUMMARY_FILE_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


class CompareAwareSearchStrategy(SolverStrategy):
    name = "CompareAwareSearchStrategy"

    def preconditions(self, **kwargs: Any) -> bool:
        file_path = kwargs.get("file_path")
        transform_model = kwargs.get("transform_model")
        return isinstance(file_path, Path) and isinstance(transform_model, SamplereverseTransformModel)

    def estimate_cost(self, **kwargs: Any) -> float:
        return float(kwargs.get("search_budget", 200_000_000))

    def run(self, **kwargs: Any) -> StrategyResult:
        file_path = Path(kwargs["file_path"])
        artifacts_dir = Path(kwargs["artifacts_dir"])
        log = kwargs["log"]
        transform_model = kwargs.get("transform_model") or SamplereverseTransformModel()
        discovered_anchors = resolve_compare_aware_anchors(
            transform_model,
            kwargs.get("anchors", DEFAULT_ANCHORS),
        )
        main_anchor = discovered_anchors[0] if discovered_anchors else DEFAULT_ANCHORS[0]
        search_budget = max(1, int(kwargs.get("search_budget", 200_000_000)))
        seed = max(1, int(kwargs.get("seed", 20260420)))
        snapshot_interval = max(1, int(kwargs.get("snapshot_interval", 10_000_000)))
        validate_top = max(1, int(kwargs.get("validate_top", 5)))
        per_probe_timeout = max(0.5, float(kwargs.get("per_probe_timeout", 2.0)))

        bridge_run = run_compare_aware_bridge(
            target=file_path,
            artifacts_dir=artifacts_dir / "bridge",
            base_anchor=main_anchor,
            transform_model=transform_model,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        bridge_entries = list(bridge_run["bridge_entries"])
        bridge_validations = list(bridge_run["bridge_validations"])
        bridge_artifact = _make_search_artifact(
            tool_name="CompareAwareBridge",
            output_path=Path(str(bridge_run["bridge_result_path"])),
            summary=(
                f"bridge search complete: best exact={bridge_entries[0].get('exact', 0)} "
                f"dist10={bridge_entries[0].get('dist10', 1 << 30)}"
                if bridge_entries
                else "bridge search complete: no improving bridge candidates"
            ),
            strategy_name=self.name,
            evidence_kind="BridgeSearchEvidence",
            payload={
                "base_anchor": main_anchor,
                "resolved_anchors": discovered_anchors,
                "pairscan_path": bridge_run["pairscan_path"],
                "hot_positions": bridge_run["hot_positions"],
                "hot_nibbles": bridge_run["hot_nibbles"],
                "top_entries": bridge_entries[:8],
            },
            derived_entries=bridge_entries,
        )
        bridge_validation_artifact = _make_validation_artifact(
            tool_name="CompareAwareBridgeValidation",
            output_path=Path(str(bridge_run["bridge_validation_path"])),
            validations=bridge_validations,
            strategy_name=self.name,
        )

        if _bridge_progress(bridge_validations):
            candidates = _validated_candidates_from_runs(bridge_validations)
            return StrategyResult(
                strategy_name=self.name,
                summary=bridge_artifact.summary,
                candidates=candidates,
                artifacts=[bridge_artifact, bridge_validation_artifact],
                metadata={
                    "resolved_anchors": discovered_anchors,
                    "bridge": bridge_run,
                    "completed_stage": "bridge",
                },
            )

        guided_run = run_compare_aware_guided_pool(
            target=file_path,
            artifacts_dir=artifacts_dir / "guided_pool",
            base_anchor=main_anchor,
            bridge_entries=bridge_entries,
            transform_model=transform_model,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        guided_entries = list(guided_run["guided_entries"])
        guided_validations = list(guided_run["guided_validations"])
        guided_artifact = _make_search_artifact(
            tool_name="CompareAwareGuidedPool",
            output_path=Path(str(guided_run["guided_pool_result_path"])),
            summary=(
                f"guided pool complete: best ci_exact_wchars={guided_entries[0].get('ci_exact_wchars', 0)} "
                f"ci_distance5={guided_entries[0].get('ci_distance5', 1 << 30)}"
                if guided_entries
                else "guided pool complete: no bounded pool candidates"
            ),
            strategy_name=self.name,
            evidence_kind="BridgeSearchEvidence",
            payload={
                "base_anchor": main_anchor,
                "anchor_mode": guided_run.get("anchor_mode", ""),
                "source_anchor": guided_run.get("source_anchor", ""),
                "frontier_role": guided_run.get("frontier_role", ""),
                "anchor_lineage": guided_run.get("anchor_lineage", ""),
                "positions": guided_run["positions"],
                "value_pools": guided_run["value_pools"],
                "beam_limit": guided_run["beam_limit"],
                "pair_frontier_pool": guided_run.get("pair_frontier_pool", []),
                "triad_frontier_pool": guided_run.get("triad_frontier_pool", []),
                "top_entries": guided_entries[:GUIDED_POOL_VALIDATE_TOP],
            },
            derived_entries=guided_entries,
        )
        guided_validation_artifact = _make_validation_artifact(
            tool_name="CompareAwareGuidedPoolValidation",
            output_path=Path(str(guided_run["guided_pool_validation_path"])),
            validations=guided_validations,
            strategy_name=self.name,
        )

        if _bridge_progress(guided_validations):
            candidates = _validated_candidates_from_runs(bridge_validations, guided_validations)
            return StrategyResult(
                strategy_name=self.name,
                summary=guided_artifact.summary,
                candidates=candidates,
                artifacts=[
                    bridge_artifact,
                    bridge_validation_artifact,
                    guided_artifact,
                    guided_validation_artifact,
                ],
                metadata={
                    "resolved_anchors": discovered_anchors,
                    "bridge": bridge_run,
                    "guided_pool": guided_run,
                    "completed_stage": "guided_pool",
                },
            )

        promoted_bridge_anchors = _collect_promoted_bridge_anchors([*bridge_validations, *guided_validations])
        refine_anchors, anchor_sources = _refine_anchor_plan(main_anchor, promoted_bridge_anchors)
        result_path = run_compare_aware_refine(
            artifacts_dir=artifacts_dir,
            search_budget=search_budget,
            seed=seed,
            anchors=refine_anchors,
            snapshot_interval=snapshot_interval,
            log=log,
        )
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        top_entries = _annotate_entries_context(
            _collect_top_entries(payload, transform_model, limit=32),
            source_anchor=main_anchor,
            frontier_role="exact2_seed",
            anchor_mode=EXACT2_ANCHOR_MODE,
            stage_label="refine(seed)",
        )
        best_entry = (
            _normalize_compare_entry(payload.get("best", {}), transform_model=transform_model)
            if isinstance(payload.get("best"), dict)
            else {}
        )
        if best_entry:
            best_entry = _annotate_entry_context(
                best_entry,
                source_anchor=main_anchor,
                frontier_role="exact2_seed",
                anchor_mode=EXACT2_ANCHOR_MODE,
                anchor_lineage=_append_lineage(
                    _lineage_root(
                        source_anchor=main_anchor,
                        frontier_role="exact2_seed",
                        anchor_mode=EXACT2_ANCHOR_MODE,
                    ),
                    "refine(seed)",
                ),
            )
        validation_frontier = _diverse_validation_candidates(
            [
                *guided_entries[:32],
                *bridge_entries[:32],
                *top_entries[:32],
            ],
            transform_model=transform_model,
            validate_top=max(validate_top, GUIDED_POOL_VALIDATE_TOP),
        )
        payload["validation_candidates"] = validation_frontier
        _write_json(result_path, payload)
        validation_path, validations = validate_compare_aware_results(
            target=file_path,
            artifacts_dir=artifacts_dir,
            result_path=result_path,
            transform_model=transform_model,
            validate_top=max(validate_top, len(validation_frontier) or validate_top),
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        frontier_guided_runs: list[dict[str, object]] = []
        frontier_guided_entries: list[dict[str, object]] = []
        frontier_guided_validations: list[dict[str, object]] = []
        frontier_guided_artifact: ToolRunArtifact | None = None
        frontier_iterations: list[dict[str, object]] = []
        frontier_converged_reason = "no_compare_agree_frontier"

        final_result_path = result_path
        final_payload = payload
        final_top_entries = top_entries
        final_best_entry = best_entry
        final_validation_frontier = validation_frontier
        final_validation_path = validation_path
        final_validations = validations
        final_refine_anchors = refine_anchors
        final_anchor_sources = anchor_sources

        current_frontier_candidates = _collect_frontier_promoted_anchors(
            [*bridge_validations, *guided_validations, *validations],
            context_entries=[*top_entries, *guided_entries, *bridge_entries],
        )
        best_frontier_before = _best_compare_agree_frontier_entry([*bridge_validations, *guided_validations, *validations])
        frontier_exact1_stall_reason = ""
        frontier_exact0_stall_reason = ""
        frontier_active_lane = ""
        feedback_value_pools_by_source: dict[str, dict[int, list[int]]] = {}
        frontier_stall_stage = ""

        for frontier_iteration in range(1, FRONTIER_MAX_ITERATIONS + 1):
            active_lane = _active_frontier_lane(current_frontier_candidates)
            frontier_active_lane = active_lane
            active_frontier_candidates = [
                item
                for item in current_frontier_candidates
                if str(item.get("anchor", "")) != main_anchor
                and _frontier_lane(item.get("frontier_submode", "")) == active_lane
            ]
            if not active_frontier_candidates:
                frontier_converged_reason = "no_active_frontier"
                frontier_stall_stage = frontier_stall_stage or "frontier_refine"
                break

            iteration_guided_entries: list[dict[str, object]] = []
            iteration_guided_validations: list[dict[str, object]] = []
            iteration_runs: list[dict[str, object]] = []
            for frontier_candidate in active_frontier_candidates:
                anchor = str(frontier_candidate.get("anchor", ""))
                frontier_run = run_compare_aware_guided_pool(
                    target=file_path,
                    artifacts_dir=artifacts_dir / f"frontier_guided_{frontier_iteration}_{anchor}",
                    base_anchor=anchor,
                    bridge_entries=[],
                    transform_model=transform_model,
                    validate_top=validate_top,
                    per_probe_timeout=per_probe_timeout,
                    log=log,
                    source_anchor=str(frontier_candidate.get("source_anchor", "")).strip().lower() or anchor,
                    frontier_role=str(frontier_candidate.get("frontier_role", "frontier_anchor")),
                    anchor_lineage=str(frontier_candidate.get("anchor_lineage", "")),
                    feedback_value_pools=feedback_value_pools_by_source.get(
                        str(frontier_candidate.get("source_anchor", "")).strip().lower() or anchor,
                        {},
                    ),
                    lineage_entries=[
                        *final_top_entries,
                        *final_validations,
                        *frontier_guided_entries,
                        *frontier_guided_validations,
                        *current_frontier_candidates,
                    ],
                )
                iteration_guided_entries.extend(frontier_run["guided_entries"])
                iteration_guided_validations.extend(frontier_run["guided_validations"])
                feedback_value_pools_by_source[
                    str(frontier_run.get("source_anchor", "")).strip().lower() or anchor
                ] = {
                    int(key): [int(value) & 0xFF for value in values]
                    for key, values in dict(frontier_run.get("feedback_value_pools", {})).items()
                    if str(key).strip().isdigit()
                }
                iteration_runs.append(
                    {
                        "iteration": frontier_iteration,
                        "anchor": anchor,
                        "frontier_role": frontier_candidate.get("frontier_role", "frontier_anchor"),
                        "anchor_mode": frontier_run.get("anchor_mode", ""),
                        "frontier_submode": frontier_run.get("frontier_submode", ""),
                        "source_anchor": frontier_run.get("source_anchor", ""),
                        "anchor_lineage": frontier_run.get("anchor_lineage", ""),
                        "result_path": frontier_run["guided_pool_result_path"],
                        "validation_path": frontier_run["guided_pool_validation_path"],
                        "positions": frontier_run.get("positions", []),
                        "pair_stage_stats": frontier_run.get("pair_stage_stats", {}),
                        "stage_stats": frontier_run.get("stage_stats", []),
                        "pair_frontier_pool_size": len(frontier_run.get("pair_frontier_pool", [])),
                        "triad_frontier_pool_size": len(frontier_run.get("triad_frontier_pool", [])),
                        "improved_pair_frontier_pool_count": sum(
                            1 for item in frontier_run.get("pair_frontier_pool", []) if bool(item.get("improvement_gate_passed"))
                        ),
                        "improved_triad_frontier_pool_count": sum(
                            1 for item in frontier_run.get("triad_frontier_pool", []) if bool(item.get("improvement_gate_passed"))
                        ),
                        "feedback_value_pools": frontier_run.get("feedback_value_pools", {}),
                        "exact1_feedback_value_pools": frontier_run.get("exact1_feedback_value_pools", {}),
                        "exact0_feedback_value_pools": frontier_run.get("exact0_feedback_value_pools", {}),
                        "feedback_sources": frontier_run.get("feedback_sources", {}),
                        "pair_pool_source_counts": frontier_run.get("pair_pool_source_counts", {}),
                        "pair_set_mode": frontier_run.get("pair_set_mode", ""),
                        "alternate_locked_pair_positions": frontier_run.get("alternate_locked_pair_positions", []),
                        "pair_set_comparison_summary": frontier_run.get("pair_set_comparison_summary", {}),
                        "pair_escape_lane_counts": frontier_run.get("pair_escape_lane_counts", {}),
                        "pair_escape_status_by_lane": frontier_run.get("pair_escape_status_by_lane", {}),
                        "pair_best_local_escape": frontier_run.get("pair_best_local_escape", {}),
                        "pair_best_hard_escape": frontier_run.get("pair_best_hard_escape", {}),
                    }
                )

            frontier_guided_entries.extend(iteration_guided_entries)
            frontier_guided_validations.extend(iteration_guided_validations)
            frontier_guided_runs.extend(iteration_runs)

            frontier_refine_anchors, frontier_anchor_sources = _frontier_refine_anchor_plan(
                discovered_anchors,
                current_frontier_candidates,
                active_lane=active_lane,
            )
            frontier_result_path = run_compare_aware_refine(
                artifacts_dir=artifacts_dir / f"frontier_refine_{frontier_iteration}",
                search_budget=search_budget,
                seed=seed,
                anchors=frontier_refine_anchors,
                snapshot_interval=snapshot_interval,
                log=log,
            )
            frontier_payload = json.loads(frontier_result_path.read_text(encoding="utf-8"))
            context_map = _context_by_anchor(
                active_frontier_candidates,
                iteration_guided_entries,
                frontier_guided_entries,
                top_entries,
                guided_entries,
                bridge_entries,
            )
            context_map.setdefault(
                main_anchor,
                {
                    "source_anchor": main_anchor,
                    "frontier_role": "exact2_seed",
                    "anchor_mode": EXACT2_ANCHOR_MODE,
                    "anchor_lineage": _append_lineage(
                        _lineage_root(
                            source_anchor=main_anchor,
                            frontier_role="exact2_seed",
                            anchor_mode=EXACT2_ANCHOR_MODE,
                        ),
                        "refine(seed)",
                    ),
                },
            )
            frontier_top_entries = _annotate_entries_from_context_map(
                _collect_top_entries(frontier_payload, transform_model, limit=32),
                context_map=context_map,
                default_source_anchor=str(active_frontier_candidates[0].get("source_anchor", "")).strip().lower() or main_anchor,
                default_frontier_role=str(active_frontier_candidates[0].get("frontier_role", "frontier_anchor")),
                default_anchor_mode=FRONTIER_ANCHOR_MODE,
                stage_label="refine(frontier)",
            )
            frontier_best_entry = (
                _normalize_compare_entry(frontier_payload.get("best", {}), transform_model=transform_model)
                if isinstance(frontier_payload.get("best"), dict)
                else {}
            )
            if frontier_best_entry:
                frontier_best_entry = _annotate_entries_from_context_map(
                    [frontier_best_entry],
                    context_map=context_map,
                    default_source_anchor=str(active_frontier_candidates[0].get("source_anchor", "")).strip().lower() or main_anchor,
                    default_frontier_role=str(active_frontier_candidates[0].get("frontier_role", "frontier_anchor")),
                    default_anchor_mode=FRONTIER_ANCHOR_MODE,
                    stage_label="refine(frontier)",
                )[0]

            frontier_validation_frontier = _diverse_validation_candidates(
                [
                    *iteration_guided_entries[:32],
                    *frontier_guided_entries[:32],
                    *guided_entries[:32],
                    *bridge_entries[:32],
                    *frontier_top_entries[:32],
                ],
                transform_model=transform_model,
                validate_top=max(validate_top, GUIDED_POOL_VALIDATE_TOP),
            )
            frontier_payload["validation_candidates"] = frontier_validation_frontier
            _write_json(frontier_result_path, frontier_payload)
            frontier_validation_path, frontier_validations = validate_compare_aware_results(
                target=file_path,
                artifacts_dir=artifacts_dir / f"frontier_refine_{frontier_iteration}",
                result_path=frontier_result_path,
                transform_model=transform_model,
                validate_top=max(validate_top, len(frontier_validation_frontier) or validate_top),
                per_probe_timeout=per_probe_timeout,
                log=log,
            )

            frontier_context_entries = [
                *frontier_top_entries,
                *iteration_guided_entries,
                *frontier_guided_entries,
                *guided_entries,
                *bridge_entries,
                *active_frontier_candidates,
                *current_frontier_candidates,
            ]
            frontier_validation_entries = [
                *bridge_validations,
                *guided_validations,
                *frontier_guided_validations,
                *frontier_validations,
            ]
            refreshed_frontier_candidates = _collect_frontier_promoted_anchors(
                frontier_validation_entries,
                context_entries=frontier_context_entries,
            )
            improved_frontier_candidates = _improved_frontier_candidates(
                frontier_validation_entries,
                context_entries=frontier_context_entries,
                baseline_validations=[*bridge_validations, *guided_validations, *final_validations, *iteration_guided_validations],
            )
            second_hop_frontier_candidates = _validated_projected_preserve_second_hop_candidates(
                frontier_validation_entries,
                context_entries=frontier_context_entries,
            )
            best_frontier_after = _best_compare_agree_frontier_entry(
                frontier_validation_entries
            )
            best_improved_frontier = improved_frontier_candidates[0] if improved_frontier_candidates else None
            frontier_converged_reason = _frontier_iteration_converged_reason(
                validations=frontier_validation_entries,
                previous_best_frontier=best_frontier_before,
                current_best_frontier=best_improved_frontier or best_frontier_after,
                iteration_index=frontier_iteration,
            )
            continuation_frontier_candidates, frontier_converged_reason, used_second_hop = _frontier_continuation_candidates(
                improved_frontier_candidates=improved_frontier_candidates,
                second_hop_frontier_candidates=second_hop_frontier_candidates,
                frontier_converged_reason=frontier_converged_reason,
                iteration_index=frontier_iteration,
            )
            improved_pair_count = sum(
                int(run.get("improved_pair_frontier_pool_count", 0) or 0)
                for run in iteration_runs
            )
            improved_triad_count = sum(
                int(run.get("improved_triad_frontier_pool_count", 0) or 0)
                for run in iteration_runs
            )
            borderline_pair_count = sum(
                int(run.get("pair_stage_stats", {}).get("pair_local_escape_borderline_count", 0) or 0)
                for run in iteration_runs
            )
            near_pair_count = sum(
                int(run.get("pair_stage_stats", {}).get("pair_near_local_escape_count", 0) or 0)
                for run in iteration_runs
            )
            wide_pair_count = sum(
                int(run.get("pair_stage_stats", {}).get("pair_wide_local_escape_count", 0) or 0)
                for run in iteration_runs
            )
            kept_escape_count = sum(
                int(run.get("pair_stage_stats", {}).get("pair_gate_kept_escape", 0) or 0)
                for run in iteration_runs
            )
            if not improved_frontier_candidates and frontier_converged_reason == "continue" and not used_second_hop:
                frontier_converged_reason = "distance_not_improved"
            if frontier_converged_reason == "distance_not_improved":
                if improved_pair_count <= 0 and near_pair_count <= 0 and kept_escape_count <= 0:
                    frontier_stall_stage = "pair_pool"
                elif improved_triad_count <= 0:
                    frontier_stall_stage = "frontier_refine" if near_pair_count > 0 or kept_escape_count > 0 else "triad_pool"
                else:
                    frontier_stall_stage = "frontier_refine"
            exact1_projected_competition_reason = (
                _exact1_projected_competition_reason_from_runs(iteration_runs)
                if active_lane == FRONTIER_EXACT1_SUBMODE
                else ""
            )
            if active_lane == FRONTIER_EXACT1_SUBMODE:
                frontier_exact1_stall_reason = (
                    exact1_projected_competition_reason or frontier_converged_reason or frontier_stall_stage
                )
            else:
                frontier_exact0_stall_reason = frontier_converged_reason or frontier_stall_stage
            frontier_iterations.append(
                {
                    "iteration": frontier_iteration,
                    "frontier_active_lane": active_lane,
                    "active_frontier_anchors": active_frontier_candidates,
                    "active_frontier_candidates": active_frontier_candidates,
                    "guided_runs": iteration_runs,
                    "refine_result_path": str(frontier_result_path),
                    "refine_validation_path": str(frontier_validation_path),
                    "best_frontier_before": best_frontier_before,
                    "best_frontier_after": best_frontier_after,
                    "exact1_best_before": _best_compare_agree_frontier_entry_for_exact(
                        [*bridge_validations, *guided_validations, *iteration_guided_validations, *final_validations],
                        1,
                    ),
                    "exact1_best_after": _best_compare_agree_frontier_entry_for_exact(
                        [*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations],
                        1,
                    ),
                    "exact0_best_before": _best_compare_agree_frontier_entry_for_exact(
                        [*bridge_validations, *guided_validations, *iteration_guided_validations, *final_validations],
                        0,
                    ),
                    "exact0_best_after": _best_compare_agree_frontier_entry_for_exact(
                        [*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations],
                        0,
                    ),
                    "frontier_candidates_after": refreshed_frontier_candidates,
                    "improved_frontier_candidates": improved_frontier_candidates,
                    "second_hop_frontier_candidates": second_hop_frontier_candidates,
                    "frontier_continuation_candidates": continuation_frontier_candidates,
                    "used_second_hop_frontier_candidates": used_second_hop,
                    "improved_pair_frontier_pool_count": improved_pair_count,
                    "improved_triad_frontier_pool_count": improved_triad_count,
                    "borderline_pair_frontier_pool_count": borderline_pair_count,
                    "near_local_pair_frontier_pool_count": near_pair_count,
                    "wide_local_pair_frontier_pool_count": wide_pair_count,
                    "exact1_projected_competition_reason": exact1_projected_competition_reason,
                    "kept_escape_pair_frontier_pool_count": kept_escape_count,
                    "feedback_value_pools": {
                        source_anchor: {str(key): list(values) for key, values in value_pool.items()}
                        for source_anchor, value_pool in feedback_value_pools_by_source.items()
                    },
                    "frontier_converged_reason": frontier_converged_reason,
                }
            )

            final_result_path = frontier_result_path
            final_payload = frontier_payload
            final_top_entries = frontier_top_entries
            final_best_entry = frontier_best_entry
            final_validation_frontier = frontier_validation_frontier
            final_validation_path = frontier_validation_path
            final_validations = frontier_validations
            final_refine_anchors = frontier_refine_anchors
            final_anchor_sources = frontier_anchor_sources
            current_frontier_candidates = continuation_frontier_candidates
            best_frontier_before = best_frontier_after

            if frontier_converged_reason != "continue":
                break

        frontier_summary_path = artifacts_dir / FRONTIER_SUMMARY_FILE_NAME
        final_frontier_candidates = _collect_frontier_promoted_anchors(
            [*bridge_validations, *guided_validations, *frontier_guided_validations, *final_validations],
            context_entries=[*final_top_entries, *frontier_guided_entries, *guided_entries, *bridge_entries],
        )
        all_runtime_validations = [
            *bridge_validations,
            *guided_validations,
            *frontier_guided_validations,
            *final_validations,
        ]
        prefix_boundary_diagnostics = _prefix_boundary_diagnostics(
            all_runtime_validations,
            transform_model=transform_model,
        )
        _write_json(
            frontier_summary_path,
            {
                "frontier_anchor_candidates": final_frontier_candidates,
                "frontier_guided_runs": frontier_guided_runs,
                "frontier_iterations": frontier_iterations,
                "frontier_active_lane": frontier_active_lane,
                "best_frontier_before": frontier_iterations[0]["best_frontier_before"] if frontier_iterations else best_frontier_before,
                "best_frontier_after": frontier_iterations[-1]["best_frontier_after"] if frontier_iterations else best_frontier_before,
                "exact1_best_before": frontier_iterations[0]["exact1_best_before"] if frontier_iterations else _best_compare_agree_frontier_entry_for_exact([*bridge_validations, *guided_validations, *final_validations], 1),
                "exact1_best_after": frontier_iterations[-1]["exact1_best_after"] if frontier_iterations else _best_compare_agree_frontier_entry_for_exact([*bridge_validations, *guided_validations, *final_validations], 1),
                "exact0_best_before": frontier_iterations[0]["exact0_best_before"] if frontier_iterations else _best_compare_agree_frontier_entry_for_exact([*bridge_validations, *guided_validations, *final_validations], 0),
                "exact0_best_after": frontier_iterations[-1]["exact0_best_after"] if frontier_iterations else _best_compare_agree_frontier_entry_for_exact([*bridge_validations, *guided_validations, *final_validations], 0),
                "frontier_converged_reason": frontier_converged_reason,
                "improved_pair_frontier_pool_count": sum(
                    int(item.get("improved_pair_frontier_pool_count", 0) or 0)
                    for item in frontier_iterations
                ),
                "improved_triad_frontier_pool_count": sum(
                    int(item.get("improved_triad_frontier_pool_count", 0) or 0)
                    for item in frontier_iterations
                ),
                "feedback_value_pools": {
                    source_anchor: {str(key): list(values) for key, values in value_pool.items()}
                    for source_anchor, value_pool in feedback_value_pools_by_source.items()
                },
                "active_frontier_candidates": frontier_iterations[-1]["active_frontier_candidates"] if frontier_iterations else [],
                "frontier_stall_stage": frontier_stall_stage,
                "frontier_exact1_stall_reason": frontier_exact1_stall_reason,
                "frontier_exact0_stall_reason": frontier_exact0_stall_reason,
                "prefix_boundary_diagnostics": prefix_boundary_diagnostics,
                "exact1_pair_set_winner": next(
                    (
                        str(run.get("pair_set_mode", ""))
                        for iteration in reversed(frontier_iterations)
                        for run in reversed(iteration.get("guided_runs", []))
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                    ),
                    "",
                ),
                "exact1_local_escape_kept_count": sum(
                    len(run.get("pair_stage_stats", {}).get("pair_gate_kept_escape", []))
                    if isinstance(run.get("pair_stage_stats", {}).get("pair_gate_kept_escape", []), list)
                    else int(run.get("pair_stage_stats", {}).get("pair_gate_kept_escape", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_local_escape_borderline_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_local_escape_borderline_count", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_projected_local_compatible_count": sum(
                    int(run.get("pair_stage_stats", {}).get("projected_local_compatible_count", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_projected_competition_reason": _exact1_projected_competition_reason_from_runs(
                    [
                        run
                        for iteration in frontier_iterations
                        for run in iteration.get("guided_runs", [])
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                    ]
                ),
                "exact1_projected_competition_summary": next(
                    (
                        dict(run.get("pair_stage_stats", {}).get("exact1_projected_competition_summary", {}))
                        for iteration in reversed(frontier_iterations)
                        for run in reversed(iteration.get("guided_runs", []))
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                        and dict(run.get("pair_stage_stats", {}).get("exact1_projected_competition_summary", {}))
                    ),
                    {},
                ),
                "exact1_near_local_escape_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_near_local_escape_count", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_wide_local_escape_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_wide_local_escape_count", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_best_near_local_distance": min(
                    (
                        int(entry.get("ci_distance5", 1 << 30) or (1 << 30))
                        for iteration in frontier_iterations
                        for run in iteration.get("guided_runs", [])
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                        for entry in run.get("pair_near_local_escape_candidates", [])
                        if isinstance(entry, dict)
                    ),
                    default=None,
                ),
                "exact1_pair_quality_winner": next(
                    (
                        str(run.get("pair_set_mode", ""))
                        for iteration in reversed(frontier_iterations)
                        for run in reversed(iteration.get("guided_runs", []))
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                    ),
                    "",
                ),
                "exact1_hard_escape_filtered_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_drop_reasons", {}).get("gate_filtered_hard_escape", 0) or 0)
                    for iteration in frontier_iterations
                    for run in iteration.get("guided_runs", [])
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
            },
        )
        if frontier_guided_runs:
            frontier_guided_artifact = _make_search_artifact(
                tool_name="CompareAwareFrontierGuidedPool",
                output_path=frontier_summary_path,
                summary="frontier-guided search complete",
                strategy_name=self.name,
                evidence_kind="BridgeSearchEvidence",
                payload={
                    "frontier_anchor_candidates": final_frontier_candidates,
                    "frontier_guided_runs": frontier_guided_runs,
                    "frontier_iterations": frontier_iterations,
                    "frontier_converged_reason": frontier_converged_reason,
                    "frontier_stall_stage": frontier_stall_stage,
                    "top_entries": frontier_guided_entries[:GUIDED_POOL_VALIDATE_TOP],
                },
                derived_entries=frontier_guided_entries,
            )
        strata_summary = {
            "resolved_anchors": discovered_anchors,
            "refine_anchors": final_refine_anchors,
            "anchor_sources": final_anchor_sources,
            "frontier_anchors": final_frontier_candidates,
            "frontier_anchor_summary_path": str(frontier_summary_path),
            "best_exact2_runtime": next(
                (
                    item
                    for item in sorted(
                        [*bridge_validations, *guided_validations, *frontier_guided_validations, *final_validations],
                        key=_runtime_validation_sort_key,
                    )
                    if int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 2
                ),
                None,
            ),
            "best_frontier_runtime": _best_compare_agree_frontier_entry(
                [*bridge_validations, *guided_validations, *frontier_guided_validations, *final_validations]
            ),
            "frontier_lineages": [
                {
                    "anchor": str(item.get("anchor", "")),
                    "frontier_role": str(item.get("frontier_role", "")),
                    "anchor_lineage": str(item.get("anchor_lineage", "")),
                }
                for item in final_frontier_candidates
            ],
            "frontier_active_lane": frontier_active_lane,
            "frontier_exact1_stall_reason": frontier_exact1_stall_reason,
            "frontier_exact0_stall_reason": frontier_exact0_stall_reason,
            "frontier_stall_stage": frontier_stall_stage,
            "prefix_boundary_diagnostics": prefix_boundary_diagnostics,
            "exact1_pair_set_winner": next(
                (
                    str(run.get("pair_set_mode", ""))
                    for run in reversed(frontier_guided_runs)
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "",
            ),
            "exact1_local_escape_kept_count": sum(
                int(run.get("pair_stage_stats", {}).get("pair_gate_kept_escape", 0) or 0)
                for run in frontier_guided_runs
                if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
            ),
                "exact1_local_escape_borderline_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_local_escape_borderline_count", 0) or 0)
                    for run in frontier_guided_runs
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_projected_local_compatible_count": sum(
                    int(run.get("pair_stage_stats", {}).get("projected_local_compatible_count", 0) or 0)
                    for run in frontier_guided_runs
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "exact1_projected_competition_reason": _exact1_projected_competition_reason_from_runs(
                    [
                        run
                        for run in frontier_guided_runs
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                    ]
                ),
                "exact1_projected_competition_summary": next(
                    (
                        dict(run.get("pair_stage_stats", {}).get("exact1_projected_competition_summary", {}))
                        for run in reversed(frontier_guided_runs)
                        if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                        and dict(run.get("pair_stage_stats", {}).get("exact1_projected_competition_summary", {}))
                    ),
                    {},
                ),
                "exact1_near_local_escape_count": sum(
                    int(run.get("pair_stage_stats", {}).get("pair_near_local_escape_count", 0) or 0)
                    for run in frontier_guided_runs
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
            "exact1_wide_local_escape_count": sum(
                int(run.get("pair_stage_stats", {}).get("pair_wide_local_escape_count", 0) or 0)
                for run in frontier_guided_runs
                if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
            ),
            "exact1_best_near_local_distance": min(
                (
                    int(entry.get("ci_distance5", 1 << 30) or (1 << 30))
                    for run in frontier_guided_runs
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                    for entry in run.get("pair_near_local_escape_candidates", [])
                    if isinstance(entry, dict)
                ),
                default=None,
            ),
            "exact1_pair_quality_winner": next(
                (
                    str(run.get("pair_set_mode", ""))
                    for run in reversed(frontier_guided_runs)
                    if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
                ),
                "",
            ),
            "exact1_hard_escape_filtered_count": sum(
                int(run.get("pair_stage_stats", {}).get("pair_drop_reasons", {}).get("gate_filtered_hard_escape", 0) or 0)
                for run in frontier_guided_runs
                if str(run.get("frontier_submode", "")) == FRONTIER_EXACT1_SUBMODE
            ),
            "leaderboards": {
                "global": final_top_entries[:16],
                "by_source": _source_grouped_leaderboards(
                    top_entries=final_top_entries[:16],
                    anchor_sources=final_anchor_sources,
                    transform_model=transform_model,
                ),
            },
        }
        strata_summary_path = artifacts_dir / STRATA_SUMMARY_FILE_NAME
        _write_json(strata_summary_path, strata_summary)
        search_artifact = _make_search_artifact(
            tool_name="CompareAwareRefine",
            output_path=final_result_path,
            summary=(
                f"compare-aware refine complete: best ci_exact_wchars={final_best_entry.get('ci_exact_wchars', 0)} "
                f"ci_distance5={final_best_entry.get('ci_distance5', 1 << 30)}"
            ),
            strategy_name=self.name,
            evidence_kind="TransformEvidence",
            payload={
                "anchors": final_refine_anchors,
                "anchor_sources": final_anchor_sources,
                "search_budget": search_budget,
                "snapshot_interval": snapshot_interval,
                "transform": transform_model.describe(),
                "top_entries": final_top_entries[:8],
                "validation_frontier": final_validation_frontier,
                "frontier_anchor_summary": final_frontier_candidates,
                "frontier_summary_path": str(frontier_summary_path),
                "strata_summary_path": str(strata_summary_path),
                "frontier_iterations": frontier_iterations,
                "frontier_converged_reason": frontier_converged_reason,
                "frontier_stall_stage": frontier_stall_stage,
                "prefix_boundary_diagnostics": prefix_boundary_diagnostics,
            },
            derived_entries=final_top_entries,
        )
        validation_artifact = _make_validation_artifact(
            tool_name="CompareAwareValidation",
            output_path=final_validation_path,
            validations=final_validations,
            strategy_name=self.name,
        )

        smt_artifact: ToolRunArtifact | None = None
        smt_validation_artifact: ToolRunArtifact | None = None
        smt_run: dict[str, object] | None = None
        exact2_basin_smt_run: dict[str, object] | None = None
        exact2_basin_smt_artifact: ToolRunArtifact | None = None
        exact2_basin_smt_validation_artifact: ToolRunArtifact | None = None
        exact2_basin_value_pool_run: dict[str, object] | None = None
        exact2_basin_value_pool_artifact: ToolRunArtifact | None = None
        exact2_basin_value_pool_validation_artifact: ToolRunArtifact | None = None
        profile_transform_audit_run: dict[str, object] | None = None
        profile_transform_audit_artifact: ToolRunArtifact | None = None
        if not _bridge_progress(final_validations):
            comparison_entries = _unique_candidate_entries(
                [
                    *frontier_guided_entries[:BRIDGE_VALIDATE_TOP],
                    *guided_entries[:BRIDGE_VALIDATE_TOP],
                    *bridge_entries[:BRIDGE_VALIDATE_TOP],
                    *final_top_entries[:BRIDGE_VALIDATE_TOP],
                ]
            )
            best_exact2_validation = next(
                (
                    item
                    for item in sorted(final_validations, key=_runtime_validation_sort_key)
                    if int(item.get("runtime_ci_exact_wchars", 0) or 0) >= 2
                ),
                None,
            )
            default_smt_entry = (
                final_top_entries[0]
                if final_top_entries
                else frontier_guided_entries[0]
                if frontier_guided_entries
                else guided_entries[0]
                if guided_entries
                else bridge_entries[0]
                if bridge_entries
                else _evaluate_candidate_hex(
                    f"{main_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
                    transform_model,
                )
            )
            best_smt_entry = _select_smt_base_entry(
                best_exact2_entry=best_exact2_validation,
                frontier_validations=[*frontier_guided_validations, *final_validations],
                fallback_entry=default_smt_entry,
            )
            best_smt_anchor = (
                str(best_smt_entry.get("cand8_hex", "")).strip().lower()
                or str(best_smt_entry.get("candidate_hex", ""))[:16].strip().lower()
            )
            if best_smt_anchor:
                smt_context_entry = next(
                    (
                        entry
                        for entry in [
                            *frontier_guided_entries,
                            *current_frontier_candidates,
                            *final_top_entries,
                        ]
                        if (
                            str(entry.get("cand8_hex", "")).strip().lower()
                            or str(entry.get("candidate_hex", ""))[:16].strip().lower()
                        )
                        == best_smt_anchor
                    ),
                    {},
                )
                if smt_context_entry:
                    best_smt_entry = {**dict(smt_context_entry), **dict(best_smt_entry)}
            smt_run = run_compare_aware_smt(
                target=file_path,
                artifacts_dir=artifacts_dir / "smt",
                base_entry=best_smt_entry,
                comparison_entries=comparison_entries,
                lineage_entries=[
                    *final_top_entries,
                    *final_validations,
                    *frontier_guided_entries,
                    *frontier_guided_validations,
                    *current_frontier_candidates,
                ],
                transform_model=transform_model,
                per_probe_timeout=per_probe_timeout,
                log=log,
            )
            exact2_basin_smt = _exact2_basin_smt_diagnostic_payload(
                best_exact2_entry=best_exact2_validation,
                primary_smt_entry=best_smt_entry,
                comparison_entries=comparison_entries,
                lineage_entries=[
                    *final_top_entries,
                    *final_validations,
                    *frontier_guided_entries,
                    *frontier_guided_validations,
                    *current_frontier_candidates,
                ],
                transform_model=transform_model,
            )
            smt_run["exact2_basin_smt"] = exact2_basin_smt
            smt_run["payload"] = {
                **dict(smt_run.get("payload", {})),
                "exact2_basin_smt": exact2_basin_smt,
            }
            _write_json(Path(str(smt_run["result_path"])), dict(smt_run["payload"]))
            exact2_basin_anchor = str(exact2_basin_smt.get("base_anchor", "")).strip().lower()
            if exact2_basin_smt.get("recommended") and len(exact2_basin_anchor) == 16:
                exact2_basin_entry = {
                    "candidate_hex": str(best_exact2_validation.get("candidate_hex", ""))
                    or f"{exact2_basin_anchor}{DEFAULT_FIXED_SUFFIX_HEX}",
                    "cand8_hex": exact2_basin_anchor,
                    "ci_exact_wchars": int(best_exact2_validation.get("runtime_ci_exact_wchars", 0) or 0),
                    "ci_distance5": int(best_exact2_validation.get("runtime_ci_distance5", 1 << 30) or (1 << 30)),
                    "raw_distance10": int(best_exact2_validation.get("offline_raw_distance10", 1 << 30) or (1 << 30)),
                    "runtime_lhs_prefix_hex_10": str(best_exact2_validation.get("runtime_lhs_prefix_hex_10", "")),
                    "frontier_role": str(best_exact2_validation.get("frontier_role", "")) or "exact2_seed",
                    "anchor_mode": EXACT2_ANCHOR_MODE,
                    "anchor_lineage": _lineage_root(
                        source_anchor=exact2_basin_anchor,
                        frontier_role=str(best_exact2_validation.get("frontier_role", "")) or "exact2_seed",
                        anchor_mode=EXACT2_ANCHOR_MODE,
                    ),
                }
                exact2_basin_smt_run = run_compare_aware_smt(
                    target=file_path,
                    artifacts_dir=artifacts_dir / "smt_exact2_basin",
                    base_entry=exact2_basin_entry,
                    comparison_entries=comparison_entries,
                    lineage_entries=[
                        *final_top_entries,
                        *final_validations,
                        *frontier_guided_entries,
                        *frontier_guided_validations,
                        *current_frontier_candidates,
                    ],
                    variable_byte_positions_override=list(exact2_basin_smt.get("variable_byte_positions", [])),
                    variable_nibble_positions_override=list(exact2_basin_smt.get("variable_nibble_positions", [])),
                    value_pools_override=dict(exact2_basin_smt.get("feedback_value_pools", {})),
                    transform_model=transform_model,
                    per_probe_timeout=per_probe_timeout,
                    log=log,
                )
                exact2_basin_smt = {
                    **exact2_basin_smt,
                    "attempted": True,
                    "summary": str(dict(exact2_basin_smt_run.get("payload", {})).get("summary", "")),
                    "evidence": list(dict(exact2_basin_smt_run.get("payload", {})).get("evidence", [])),
                    "result_path": str(exact2_basin_smt_run.get("result_path", "")),
                    "validation_path": str(exact2_basin_smt_run.get("validation_path", "")),
                    "top_entries": list(dict(exact2_basin_smt_run.get("payload", {})).get("top_entries", [])),
                    "validation_candidates": list(
                        dict(exact2_basin_smt_run.get("payload", {})).get("validation_candidates", [])
                    ),
                    "validations": list(exact2_basin_smt_run.get("validations", [])),
                }
                exact2_basin_smt_run["exact2_basin_smt"] = exact2_basin_smt
                exact2_basin_smt_run["payload"] = {
                    **dict(exact2_basin_smt_run.get("payload", {})),
                    "exact2_basin_smt": exact2_basin_smt,
                }
                _write_json(Path(str(exact2_basin_smt_run["result_path"])), dict(exact2_basin_smt_run["payload"]))
                smt_run["exact2_basin_smt"] = exact2_basin_smt
                smt_run["exact2_basin_smt_run"] = exact2_basin_smt_run
                smt_run["payload"] = {
                    **dict(smt_run.get("payload", {})),
                    "exact2_basin_smt": exact2_basin_smt,
                }
                _write_json(Path(str(smt_run["result_path"])), dict(smt_run["payload"]))
                exact2_basin_smt_payload = dict(exact2_basin_smt_run["payload"])
                exact2_basin_smt_artifact = _make_search_artifact(
                    tool_name="CompareAwareExact2BasinSMT",
                    output_path=Path(str(exact2_basin_smt_run["result_path"])),
                    summary=str(exact2_basin_smt_payload.get("summary", "compare-aware exact2 basin smt complete")),
                    strategy_name=self.name,
                    evidence_kind="BridgeSearchEvidence",
                    payload=exact2_basin_smt_payload,
                    derived_entries=[exact2_basin_smt_run["entry"]] if exact2_basin_smt_run.get("entry") else [],
                )
                if exact2_basin_smt_run.get("validation_path"):
                    exact2_basin_smt_validation_artifact = _make_validation_artifact(
                        tool_name="CompareAwareExact2BasinSMTValidation",
                        output_path=Path(str(exact2_basin_smt_run["validation_path"])),
                        validations=list(exact2_basin_smt_run["validations"]),
                        strategy_name=self.name,
                    )
                exact2_payload = dict(exact2_basin_smt_run.get("payload", {}))
                exact2_validation_candidates = list(exact2_payload.get("validation_candidates", []))
                exact2_estimated_combinations = int(
                    exact2_payload.get("estimated_value_pool_combinations", 1 << 30)
                    or (1 << 30)
                )
                if (
                    bool(exact2_payload.get("attempted"))
                    and not exact2_validation_candidates
                    and exact2_estimated_combinations <= EXACT2_BASIN_VALUE_POOL_EVAL_MAX_COMBINATIONS
                ):
                    exact2_basin_value_pool_run = run_exact2_basin_value_pool_evaluation(
                        target=file_path,
                        artifacts_dir=artifacts_dir / "exact2_basin_value_pool",
                        base_entry=exact2_basin_entry,
                        exact2_basin_smt=exact2_basin_smt,
                        transform_model=transform_model,
                        per_probe_timeout=per_probe_timeout,
                        log=log,
                    )
                    exact2_basin_value_pool_payload = dict(
                        exact2_basin_value_pool_run.get("payload", {})
                    )
                    exact2_basin_value_pool_artifact = _make_search_artifact(
                        tool_name="CompareAwareExact2BasinValuePool",
                        output_path=Path(str(exact2_basin_value_pool_run["result_path"])),
                        summary=str(
                            exact2_basin_value_pool_payload.get(
                                "classification",
                                "compare-aware exact2 basin value-pool evaluation complete",
                            )
                        ),
                        strategy_name=self.name,
                        evidence_kind="BridgeSearchEvidence",
                        payload=exact2_basin_value_pool_payload,
                        derived_entries=list(
                            exact2_basin_value_pool_payload.get("validation_candidates", [])
                        ),
                    )
                    if exact2_basin_value_pool_run.get("validation_path"):
                        exact2_basin_value_pool_validation_artifact = _make_validation_artifact(
                            tool_name="CompareAwareExact2BasinValuePoolValidation",
                            output_path=Path(str(exact2_basin_value_pool_run["validation_path"])),
                            validations=list(exact2_basin_value_pool_run.get("validations", [])),
                            strategy_name=self.name,
                        )
            if not frontier_stall_stage:
                frontier_stall_stage = (
                    "smt_exact2_basin"
                    if exact2_basin_smt.get("attempted")
                    else "smt_exact2_basin_diagnostic"
                    if exact2_basin_smt.get("recommended")
                    else "frontier_smt"
                )
            smt_payload = dict(smt_run["payload"])
            smt_artifact = _make_search_artifact(
                tool_name="CompareAwareSMT",
                output_path=Path(str(smt_run["result_path"])),
                summary=str(smt_payload.get("summary", "compare-aware smt complete")),
                strategy_name=self.name,
                evidence_kind="BridgeSearchEvidence",
                payload=smt_payload,
                derived_entries=[smt_run["entry"]] if smt_run.get("entry") else [],
            )
            if smt_run.get("validation_path"):
                smt_validation_artifact = _make_validation_artifact(
                    tool_name="CompareAwareSMTValidation",
                    output_path=Path(str(smt_run["validation_path"])),
                    validations=list(smt_run["validations"]),
                    strategy_name=self.name,
                )

        profile_transform_audit_run = run_profile_transform_hypothesis_audit(
            artifacts_dir=artifacts_dir,
            transform_model=transform_model,
            runtime_validations=[
                *bridge_validations,
                *guided_validations,
                *frontier_guided_validations,
                *final_validations,
                *(list(smt_run.get("validations", [])) if smt_run else []),
                *(list(exact2_basin_smt_run.get("validations", [])) if exact2_basin_smt_run else []),
                *(list(exact2_basin_value_pool_run.get("validations", [])) if exact2_basin_value_pool_run else []),
            ],
            top_entries=[
                *final_top_entries[:BRIDGE_VALIDATE_TOP],
                *frontier_guided_entries[:BRIDGE_VALIDATE_TOP],
                *guided_entries[:BRIDGE_VALIDATE_TOP],
                *bridge_entries[:BRIDGE_VALIDATE_TOP],
            ],
            exact2_basin_value_pool_run=exact2_basin_value_pool_run,
            smt_run=smt_run,
            exact2_basin_smt_run=exact2_basin_smt_run,
            frontier_summary_path=frontier_summary_path,
            strata_summary_path=strata_summary_path,
            search_budget=search_budget,
            snapshot_interval=snapshot_interval,
            validate_top=validate_top,
            per_probe_timeout=per_probe_timeout,
            log=log,
        )
        profile_transform_audit_payload = dict(profile_transform_audit_run.get("payload", {}))
        profile_transform_audit_artifact = _make_search_artifact(
            tool_name="ProfileTransformHypothesisAudit",
            output_path=Path(str(profile_transform_audit_run["result_path"])),
            summary="profile transform hypothesis audit complete",
            strategy_name=self.name,
            evidence_kind="TransformEvidence",
            payload=profile_transform_audit_payload,
            derived_entries=[],
        )

        candidates = _validated_candidates_from_runs(
            bridge_validations,
            guided_validations,
            frontier_guided_validations,
            final_validations,
            list(smt_run["validations"]) if smt_run else [],
            list(exact2_basin_smt_run["validations"]) if exact2_basin_smt_run else [],
            list(exact2_basin_value_pool_run.get("promotable_validations", []))
            if exact2_basin_value_pool_run
            else [],
        )
        artifacts = [
            bridge_artifact,
            bridge_validation_artifact,
            guided_artifact,
            guided_validation_artifact,
        ]
        if frontier_guided_artifact is not None:
            artifacts.append(frontier_guided_artifact)
        artifacts.extend([
            search_artifact,
            validation_artifact,
        ])
        if smt_artifact is not None:
            artifacts.append(smt_artifact)
        if smt_validation_artifact is not None:
            artifacts.append(smt_validation_artifact)
        if exact2_basin_smt_artifact is not None:
            artifacts.append(exact2_basin_smt_artifact)
        if exact2_basin_smt_validation_artifact is not None:
            artifacts.append(exact2_basin_smt_validation_artifact)
        if exact2_basin_value_pool_artifact is not None:
            artifacts.append(exact2_basin_value_pool_artifact)
        if exact2_basin_value_pool_validation_artifact is not None:
            artifacts.append(exact2_basin_value_pool_validation_artifact)
        if profile_transform_audit_artifact is not None:
            artifacts.append(profile_transform_audit_artifact)
        return StrategyResult(
            strategy_name=self.name,
            summary=search_artifact.summary,
            candidates=candidates,
            artifacts=artifacts,
            metadata={
                "resolved_anchors": discovered_anchors,
                "bridge": bridge_run,
                "guided_pool": guided_run,
                "frontier_anchor_candidates": final_frontier_candidates,
                "frontier_guided_runs": frontier_guided_runs,
                "frontier_iterations": frontier_iterations,
                "result_path": str(final_result_path),
                "validation_path": str(final_validation_path),
                "strata_summary_path": str(strata_summary_path),
                "frontier_summary_path": str(frontier_summary_path),
                "best": final_best_entry,
                "top_entries": final_top_entries,
                "validations": final_validations,
                "smt": smt_run or {},
                "exact2_basin_smt": exact2_basin_smt_run or {},
                "exact2_basin_value_pool": exact2_basin_value_pool_run or {},
                "profile_transform_hypothesis_audit": profile_transform_audit_run or {},
                "prefix_boundary_diagnostics": prefix_boundary_diagnostics,
                "frontier_converged_reason": frontier_converged_reason,
                "frontier_stall_stage": frontier_stall_stage,
                "completed_stage": "smt" if smt_run else "refine",
            },
        )
