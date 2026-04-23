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
from ..transforms.samplereverse import SamplereverseTransformModel, score_compare_prefix
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
FRONTIER_SUMMARY_FILE_NAME = "samplereverse_compare_aware_frontier_summary.json"

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
EXACT1_PAIR_LOCK_LIMIT = 3
EXACT1_PAIR_DISTANCE_ESCAPE = 24
EXACT1_PAIR_PRESERVE_VALUE_LIMIT = 6
EXACT1_PAIR_ESCAPE_VALUE_LIMIT = 6
EXACT1_PAIR_PROFILE_PRESERVE_TOP = 4
EXACT1_PAIR_PROFILE_ESCAPE_TOP = 2
EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX = 5
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
    if normalized_role == "exact1_frontier":
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
    pair_positions = [int(item) for item in candidate.get("pair_positions", []) if isinstance(item, int) or str(item).isdigit()]
    for position in pair_positions:
        if 0 <= position < len(candidate_bytes) and 0 <= position < len(baseline_bytes):
            delta = abs(candidate_bytes[position] - baseline_bytes[position])
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
    return {
        "passed": lane == "local_escape" and score <= EXACT1_PAIR_ESCAPE_KEEP_SCORE_MAX,
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


def _exact1_neighbor_value_maps(
    *,
    base_value: int,
    profile_values: Sequence[int],
    incoming_values: Sequence[int],
    lineage_values: Sequence[int],
) -> tuple[dict[int, list[str]], dict[int, list[str]]]:
    base = int(base_value) & 0xFF
    preserve_sources: dict[int, list[str]] = {}
    escape_sources: dict[int, list[str]] = {}

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

    for value in list(preserve_sources):
        escape_sources.pop(value, None)

    return preserve_sources, escape_sources


def _bounded_exact1_value_map(
    value_origins: dict[int, list[str]],
    *,
    limit: int,
) -> dict[int, list[str]]:
    bounded: dict[int, list[str]] = {}
    for value, origins in value_origins.items():
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
    return {
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
        "pair_candidate_origin": str(entry.get("pair_candidate_origin", "")),
        "pair_mutation_radius": int(entry.get("pair_mutation_radius", 0) or 0),
        "pair_neighbor_mode": str(entry.get("pair_neighbor_mode", "")),
        "pair_value_origin_by_pos": {
            str(key): list(value)
            for key, value in dict(entry.get("pair_value_origin_by_pos", {})).items()
        },
    }


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


def _exact1_pair_set_selection_key(result: dict[str, object]) -> tuple[int, int, int, int, int]:
    diagnostics = dict(result.get("pair_frontier_diagnostics", {}))
    pair_drop_reasons = dict(result.get("pair_drop_reasons", {}))
    best_local_by_pair = diagnostics.get("pair_best_local_escape", {})
    best_local_score = min(
        (
            int(entry.get("pair_escape_signal_score", 1 << 30) or (1 << 30))
            for entry in best_local_by_pair.values()
            if isinstance(entry, dict)
        ),
        default=1 << 30,
    )
    return (
        -len(diagnostics.get("pair_gate_kept_escape", [])),
        -sum(1 for entry in result.get("pair_frontier_pool", []) if bool(entry.get("improvement_gate_passed"))),
        int(pair_drop_reasons.get("gate_filtered_local_escape", 0) or 0),
        int(pair_drop_reasons.get("gate_filtered_hard_escape", 0) or 0),
        best_local_score,
    )


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
            preserve_left_origins, escape_left_origins = _exact1_neighbor_value_maps(
                base_value=base_bytes[left],
                profile_values=left_values,
                incoming_values=[int(value) & 0xFF for value in incoming.get(int(left), [])],
                lineage_values=[int(value) & 0xFF for value in lineage_sources.get(int(left), [])],
            )
            preserve_right_origins, escape_right_origins = _exact1_neighbor_value_maps(
                base_value=base_bytes[right],
                profile_values=right_values,
                incoming_values=[int(value) & 0xFF for value in incoming.get(int(right), [])],
                lineage_values=[int(value) & 0xFF for value in lineage_sources.get(int(right), [])],
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
                escape_entries.sort(
                    key=lambda item: _exact1_escape_profile_sort_key(
                        item,
                        transform_model,
                        baseline_entry=baseline_entry,
                    )
                )
            pair_key = f"{left},{right}"
            kept_escape = escape_entries[: min(max(0, top_per_pair), EXACT1_PAIR_PROFILE_ESCAPE_TOP)]
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
            "pair_best_preserve_candidate": None,
            "pair_best_escape_candidate": None,
            "pair_best_local_escape": {},
            "pair_best_hard_escape": {},
            "pair_local_escape_candidate_count": 0,
            "pair_hard_escape_candidate_count": 0,
            "pair_escape_source_statuses": {},
            "pair_escape_status_by_lane": {},
            "pair_escape_lane_counts": {},
            "pair_gate_input_summary": {},
            "pair_profile_preserve_entries": dict((pair_profile_details or {}).get("pair_profile_preserve_entries", {})),
            "pair_profile_escape_entries": dict((pair_profile_details or {}).get("pair_profile_escape_entries", {})),
            "pair_profile_kept_preserve": dict((pair_profile_details or {}).get("pair_profile_kept_preserve", {})),
            "pair_profile_kept_escape": dict((pair_profile_details or {}).get("pair_profile_kept_escape", {})),
            "pair_profile_drop_reasons": dict((pair_profile_details or {}).get("pair_profile_drop_reasons", {})),
            "pair_profile_truncation_summary": dict((pair_profile_details or {}).get("pair_profile_truncation_summary", {})),
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
                compact = _compact_pair_candidate(candidate)
                diagnostics["pair_gate_input_summary"].setdefault(pair_key, []).append(compact)
                lane = str(signal.get("lane", ""))
                pair_lane_counts[lane] = pair_lane_counts.get(lane, 0) + 1
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
                    if bool(signal.get("passed")):
                        local_kept_candidates.append(candidate)
                    else:
                        candidate["pair_drop_reason"] = "gate_filtered_local_escape"
                        local_filtered_candidates.append(candidate)
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
            elif pair_status_by_lane["local_escape"] == "gate_filtered_local_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_local_escape"
            elif pair_status_by_lane["hard_escape"] == "gate_filtered_hard_escape":
                diagnostics["pair_escape_source_statuses"][pair_key] = "gate_filtered_hard_escape"
            else:
                diagnostics["pair_escape_source_statuses"][pair_key] = pair_status_by_lane["local_escape"]

            for candidate in local_kept_candidates[:EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR]:
                candidate_hex = _candidate_hex_from_entry(candidate)
                if candidate_hex in seen_local:
                    continue
                seen_local.add(candidate_hex)
                candidate["pair_drop_reason"] = ""
                accepted_local.append(candidate)
                diagnostics["pair_gate_kept_escape"].append(_compact_pair_candidate(candidate))
            for candidate in local_kept_candidates[EXACT1_PAIR_TOP_LOCAL_ESCAPE_PER_PAIR:]:
                candidate["pair_drop_reason"] = "escape_signal_but_ranked_out"
                drop_reasons["escape_signal_but_ranked_out"] = drop_reasons.get("escape_signal_but_ranked_out", 0) + 1
                diagnostics["pair_escape_candidates_dropped"].append(_compact_pair_candidate(candidate))
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
                    "pair_gate_kept_escape": len(primary_pair_run["pair_frontier_diagnostics"].get("pair_gate_kept_escape", [])),
                    "gate_filtered_local_escape": int(primary_pair_run["pair_drop_reasons"].get("gate_filtered_local_escape", 0) or 0),
                    "gate_filtered_hard_escape": int(primary_pair_run["pair_drop_reasons"].get("gate_filtered_hard_escape", 0) or 0),
                },
                "alternate_pair_set": {
                    "locked_pair_positions": [list(item) for item in alternate_locked_pair_positions],
                    "selection_key": list(_exact1_pair_set_selection_key(exact1_pair_runs[-1])) if len(exact1_pair_runs) > 1 else [],
                    "pair_gate_kept_escape": len(exact1_pair_runs[-1]["pair_frontier_diagnostics"].get("pair_gate_kept_escape", [])) if len(exact1_pair_runs) > 1 else 0,
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
                    "pair_gate_failed_escape": len(pair_frontier_diagnostics.get("pair_gate_failed_escape", [])),
                    "pair_gate_input_summary": pair_frontier_diagnostics.get("pair_gate_input_summary", {}),
                    "pair_best_local_escape": pair_frontier_diagnostics.get("pair_best_local_escape", {}),
                    "pair_best_hard_escape": pair_frontier_diagnostics.get("pair_best_hard_escape", {}),
                    "pair_neighbor_generation_summary": pair_generation_details.get("pair_neighbor_generation_summary", {}),
                    "pair_mutation_radius_summary": pair_frontier_diagnostics.get(
                        "pair_mutation_radius_summary",
                        pair_generation_details.get("pair_mutation_radius_summary", {}),
                    ),
                    "pair_local_escape_candidate_count": int(
                        pair_frontier_diagnostics.get("pair_local_escape_candidate_count", 0) or 0
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
                    "pair_profile_truncation_summary": pair_frontier_diagnostics.get("pair_profile_truncation_summary", {}),
                }
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
        "pair_gate_input_summary": pair_frontier_diagnostics.get("pair_gate_input_summary", {}),
        "pair_escape_source_statuses": pair_frontier_diagnostics.get("pair_escape_source_statuses", {}),
        "pair_escape_lane_counts": pair_frontier_diagnostics.get("pair_escape_lane_counts", {}),
        "pair_escape_status_by_lane": pair_frontier_diagnostics.get("pair_escape_status_by_lane", {}),
        "pair_best_preserve_candidate": pair_frontier_diagnostics.get("pair_best_preserve_candidate"),
        "pair_best_escape_candidate": pair_frontier_diagnostics.get("pair_best_escape_candidate"),
        "pair_best_local_escape": pair_frontier_diagnostics.get("pair_best_local_escape", {}),
        "pair_best_hard_escape": pair_frontier_diagnostics.get("pair_best_hard_escape", {}),
        "pair_local_escape_candidate_count": int(
            pair_frontier_diagnostics.get("pair_local_escape_candidate_count", 0) or 0
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
        "pair_drop_reasons": pair_drop_reasons,
        "positions": positions,
        "exact_floor": exact_floor,
        "beam_limit": GUIDED_POOL_BEAM_LIMIT,
        "exploration_slots": GUIDED_POOL_EXPLORATION_SLOTS,
        "value_pool_limit": GUIDED_POOL_TOP_VALUES,
        "best": guided_entries[0] if guided_entries else base_entry,
        "top_entries": guided_entries[:GUIDED_POOL_BEAM_LIMIT],
        "validation_candidates": guided_entries[:GUIDED_POOL_VALIDATE_TOP],
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
        "pair_gate_input_summary": guided_payload["pair_gate_input_summary"],
        "pair_escape_source_statuses": guided_payload["pair_escape_source_statuses"],
        "pair_escape_lane_counts": guided_payload["pair_escape_lane_counts"],
        "pair_escape_status_by_lane": guided_payload["pair_escape_status_by_lane"],
        "pair_best_preserve_candidate": guided_payload["pair_best_preserve_candidate"],
        "pair_best_escape_candidate": guided_payload["pair_best_escape_candidate"],
        "pair_best_local_escape": guided_payload["pair_best_local_escape"],
        "pair_best_hard_escape": guided_payload["pair_best_hard_escape"],
        "pair_local_escape_candidate_count": guided_payload["pair_local_escape_candidate_count"],
        "pair_hard_escape_candidate_count": guided_payload["pair_hard_escape_candidate_count"],
        "pair_profile_preserve_entries": guided_payload["pair_profile_preserve_entries"],
        "pair_profile_escape_entries": guided_payload["pair_profile_escape_entries"],
        "pair_profile_kept_preserve": guided_payload["pair_profile_kept_preserve"],
        "pair_profile_kept_escape": guided_payload["pair_profile_kept_escape"],
        "pair_profile_drop_reasons": guided_payload["pair_profile_drop_reasons"],
        "pair_profile_truncation_summary": guided_payload["pair_profile_truncation_summary"],
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


def run_compare_aware_smt(
    *,
    target: Path,
    artifacts_dir: Path,
    base_entry: dict[str, object],
    comparison_entries: Sequence[dict[str, object]],
    lineage_entries: Sequence[dict[str, object]] = (),
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
    preferred_smt_entries: list[dict[str, object]] = []
    if frontier_submode == FRONTIER_EXACT1_SUBMODE:
        preferred_smt_entries.extend(
            dict(entry)
            for entry in base_entry.get("pair_profile_kept_preserve", [])
            if isinstance(entry, dict)
        )
        preferred_smt_entries.extend(
            dict(entry)
            for entry in base_entry.get("pair_gate_kept_escape", [])
            if isinstance(entry, dict)
        )
        preferred_smt_entries.extend(
            dict(entry)
            for entry in dict(base_entry.get("pair_best_local_escape", {})).values()
            if isinstance(entry, dict)
        )
    feedback_value_pools = _smt_feedback_value_pools(
        base_anchor=base_anchor,
        variable_byte_positions=variable_bytes,
        comparison_entries=filtered_entries,
        preferred_entries=preferred_smt_entries,
        lineage_value_pools=lineage_value_pools,
    )
    z3_result = solve_targeted_prefix8(
        base_anchor=base_anchor,
        variable_byte_positions=variable_bytes,
        variable_nibble_positions=variable_nibbles,
        prioritize_distance=anchor_mode == FRONTIER_ANCHOR_MODE,
        timeout_ms=1500,
    )
    payload: dict[str, object] = {
        "base_anchor": base_anchor,
        "anchor_mode": anchor_mode,
        "frontier_submode": frontier_submode,
        "variable_byte_positions": variable_bytes,
        "variable_nibble_positions": variable_nibbles,
        "feedback_value_pools": {
            str(key): list(values[:GUIDED_POOL_TOP_VALUES]) for key, values in feedback_value_pools.items()
        },
        "attempted": z3_result.attempted,
        "summary": z3_result.summary,
        "evidence": z3_result.evidence or [],
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

            refreshed_frontier_candidates = _collect_frontier_promoted_anchors(
                [*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations],
                context_entries=[*frontier_top_entries, *iteration_guided_entries, *frontier_guided_entries, *guided_entries, *bridge_entries],
            )
            improved_frontier_candidates = _improved_frontier_candidates(
                [*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations],
                context_entries=[*frontier_top_entries, *iteration_guided_entries, *frontier_guided_entries, *guided_entries, *bridge_entries],
                baseline_validations=[*bridge_validations, *guided_validations, *final_validations, *iteration_guided_validations],
            )
            best_frontier_after = _best_compare_agree_frontier_entry(
                [*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations]
            )
            best_improved_frontier = improved_frontier_candidates[0] if improved_frontier_candidates else None
            frontier_converged_reason = _frontier_iteration_converged_reason(
                validations=[*bridge_validations, *guided_validations, *frontier_guided_validations, *frontier_validations],
                previous_best_frontier=best_frontier_before,
                current_best_frontier=best_improved_frontier or best_frontier_after,
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
            if not improved_frontier_candidates and frontier_converged_reason == "continue":
                frontier_converged_reason = "distance_not_improved"
            if frontier_converged_reason == "distance_not_improved":
                if improved_pair_count <= 0:
                    frontier_stall_stage = "pair_pool"
                elif improved_triad_count <= 0:
                    frontier_stall_stage = "triad_pool"
                else:
                    frontier_stall_stage = "frontier_refine"
            if active_lane == FRONTIER_EXACT1_SUBMODE:
                frontier_exact1_stall_reason = frontier_converged_reason or frontier_stall_stage
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
                    "improved_pair_frontier_pool_count": improved_pair_count,
                    "improved_triad_frontier_pool_count": improved_triad_count,
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
            current_frontier_candidates = improved_frontier_candidates
            best_frontier_before = best_frontier_after

            if frontier_converged_reason != "continue":
                break

        frontier_summary_path = artifacts_dir / FRONTIER_SUMMARY_FILE_NAME
        final_frontier_candidates = _collect_frontier_promoted_anchors(
            [*bridge_validations, *guided_validations, *frontier_guided_validations, *final_validations],
            context_entries=[*final_top_entries, *frontier_guided_entries, *guided_entries, *bridge_entries],
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
            if not frontier_stall_stage:
                frontier_stall_stage = "frontier_smt"
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

        candidates = _validated_candidates_from_runs(
            bridge_validations,
            guided_validations,
            frontier_guided_validations,
            final_validations,
            list(smt_run["validations"]) if smt_run else [],
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
                "frontier_converged_reason": frontier_converged_reason,
                "frontier_stall_stage": frontier_stall_stage,
                "completed_stage": "smt" if smt_run else "refine",
            },
        )
