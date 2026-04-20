from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import time
from dataclasses import dataclass
from itertools import combinations, product
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


SAMPLEREVERSE_ENC_CONST = bytes.fromhex(
    "698b8fb18f3b4f9961726ba869132942e6ff36b8be4ebce3efd4c9a7e35ff74f"
    "ccb9ca9b7ab1b8129285ccfbd812419f93eb15e91fe68784d900eb89e4f8d310"
    "0d91af1223c308eba2fcfdc4c69882e781ed9eb5"
)
SAMPLEREVERSE_TARGET_PREFIX = "flag{".encode("utf-16le")
CHECKPOINT_FILE_NAME = "samplereverse_search_checkpoint.json"
OPTIMIZER_RESULT_FILE_NAME = "samplereverse_optimize_result.json"


@dataclass
class SampleSearchResult:
    enabled: bool
    summary: str
    candidates: list[str]
    evidence: list[str]


def run_samplereverse_resumable_search(
    file_path: Path,
    strings: list[str],
    seed_candidates: list[str],
    artifacts_dir: Path,
    log: LogFn,
    max_attempts: int = 250_000,
    max_seconds: float = 6 * 60 * 60,
    random_seed: int = 1337,
) -> SampleSearchResult:
    if not _looks_like_samplereverse(file_path, strings):
        return SampleSearchResult(
            enabled=False,
            summary="sample-specific solver skipped.",
            candidates=[],
            evidence=[],
        )

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = artifacts_dir / CHECKPOINT_FILE_NAME
    checkpoint = _load_checkpoint(checkpoint_path)
    starts_from = int(checkpoint.get("cartesian_index", 0))
    cartesian_length = int(checkpoint.get("cartesian_length", 4))
    best_hex = str(checkpoint.get("best_hex", ""))
    best_prefix_hex = str(checkpoint.get("best_prefix_hex", ""))
    best_score = int(checkpoint.get("best_score", 0))
    best_mask = int(checkpoint.get("best_mask", 0))
    best_distance = int(checkpoint.get("best_distance", 10_000))
    deadline_hit = False
    start_time = time.monotonic()
    now_epoch = time.time()
    started_epoch = float(checkpoint.get("started_epoch", 0.0) or 0.0)
    stored_budget_seconds = float(checkpoint.get("deadline_budget_seconds", 0.0) or 0.0)
    requested_budget_seconds = max(0.0, float(max_seconds))
    reset_deadline_window = (
        started_epoch <= 0
        or stored_budget_seconds <= 0
        or abs(stored_budget_seconds - requested_budget_seconds) >= 1.0
    )
    if reset_deadline_window:
        started_epoch = now_epoch
        evidence_reset_reason = (
            "fresh_start"
            if stored_budget_seconds <= 0
            else f"budget_changed:{int(stored_budget_seconds)}->{int(requested_budget_seconds)}"
        )
    else:
        evidence_reset_reason = ""
    deadline_epoch = float(checkpoint.get("deadline_epoch", 0.0) or 0.0)
    if reset_deadline_window or deadline_epoch <= 0:
        deadline_epoch = started_epoch + requested_budget_seconds
    remaining_seconds = max(0.0, deadline_epoch - now_epoch)
    deadline = start_time + remaining_seconds

    evidence: list[str] = [
        "runtime_probe:samplereverse_signature=1",
        "runtime_probe:transform=nibble_expand(+0x78,+0x7A)->utf16le->base64->rc4",
        "runtime_probe:compare=__wcsnicmp(...,L\"flag{\",5) with wide target prefix",
        f"runtime_probe:checkpoint={checkpoint_path}",
        f"runtime_probe:deadline_seconds={int(max_seconds)}",
        f"runtime_probe:deadline_epoch={int(deadline_epoch)}",
        f"runtime_probe:remaining_seconds={int(remaining_seconds)}",
        f"runtime_probe:deadline_budget_seconds={int(requested_budget_seconds)}",
    ]
    if evidence_reset_reason:
        evidence.append(f"runtime_probe:deadline_reset={evidence_reset_reason}")
    candidates: list[str] = []
    seen: set[str] = set()

    def _push_candidate(value: str) -> None:
        if value is None:
            return
        normalized = str(value)
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    token_candidates = _extract_token_candidates(strings)
    optimizer_seed_candidates = _load_optimizer_seed_candidates(artifacts_dir)
    if optimizer_seed_candidates:
        evidence.append(
            f"runtime_probe:optimizer_seed_count={len(optimizer_seed_candidates)}"
        )
    for value in [*optimizer_seed_candidates, *seed_candidates, *token_candidates]:
        _push_candidate(value)

    attempts = 0
    found = ""
    top_scored: list[tuple[int, int, str, str]] = []
    stochastic_enabled = False
    anneal_enabled = False
    tri_enabled = False
    reserve_attempts = max(120_000, max_attempts // 4)
    late_guard = max(120_000, max_attempts // 4)
    explore_limit = max(10_000, max_attempts - reserve_attempts - late_guard)

    def _time_exceeded() -> bool:
        return time.monotonic() >= deadline

    def _is_better(score: int, mask: int, candidate: str, prefix_hex: str) -> bool:
        nonlocal best_hex, best_score, best_mask, best_distance, best_prefix_hex
        cur_hex = candidate.encode("latin1", errors="ignore").hex()
        cur_obj = _objective_tuple(prefix_hex, score, mask)
        best_obj = (
            _objective_tuple(best_prefix_hex, best_score, best_mask)
            if best_prefix_hex
            else (-1, -1, -1, -1, -(10**9))
        )
        if cur_obj > best_obj:
            return True
        if cur_obj == best_obj and best_hex and cur_hex < best_hex:
            return True
        return False

    def _record_best(score: int, mask: int, candidate: str, prefix_hex: str) -> None:
        nonlocal best_hex, best_score, best_mask, best_distance, best_prefix_hex
        if _is_better(score, mask, candidate, prefix_hex):
            best_hex = candidate.encode("latin1", errors="ignore").hex()
            best_prefix_hex = prefix_hex
            best_score = score
            best_mask = mask
            best_distance = _prefix_distance(prefix_hex)
            evidence.append(
                "runtime_probe:best_update "
                f"score={score}/{len(SAMPLEREVERSE_TARGET_PREFIX)} "
                f"mask={mask:0{len(SAMPLEREVERSE_TARGET_PREFIX)}b} "
                f"candidate_hex={best_hex} dec_prefix_hex={prefix_hex}"
            )

    if best_hex:
        try:
            cached = bytes.fromhex(best_hex).decode("latin1")
            if "\x00" not in cached:
                _push_candidate(cached)
            if not best_prefix_hex:
                best_prefix_hex = _decrypt_prefix(
                    cached, len(SAMPLEREVERSE_TARGET_PREFIX)
                ).hex()
        except Exception:
            pass

    for candidate in candidates:
        if attempts >= explore_limit or _time_exceeded():
            break
        score, mask, prefix_hex = _score_candidate_prefix(candidate)
        attempts += 1
        _record_best(score, mask, candidate, prefix_hex)
        if score >= 2:
            top_scored.append((score, mask, candidate, prefix_hex))
        if score == len(SAMPLEREVERSE_TARGET_PREFIX):
            found = candidate
            break

    # Dependency-aware probe inspired by common CTF writeup strategy:
    # for L=7 (m40), first 5-byte prefix only depends on first 4 input bytes.
    dep_tiers = [
        ("AZ09", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("AZaz09", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"),
        ("printable78", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-!@#$%^&*()[]"),
    ]
    dep_tier = int(checkpoint.get("dep_tier", 0))
    dep_index = int(checkpoint.get("dep_index", 0))
    dep_budget = min(max(0, explore_limit - attempts), 80_000)
    if dep_budget > 0 and not found:
        evidence.append("runtime_probe:dependency_probe=L7(prefix4)+AAA,m40")
        spent = 0
        while dep_tier < len(dep_tiers) and spent < dep_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[dep_tier]
            total = len(dep_charset) ** 4
            idx = dep_index
            while idx < total and spent < dep_budget and not found:
                if _time_exceeded():
                    break
                prefix = _index_to_candidate(dep_charset, 4, idx)
                candidate = prefix + "AAA"
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    break
                idx += 1
            if found:
                dep_index = idx
                evidence.append(
                    "runtime_probe:dependency_probe_hit "
                    f"tier={tier_name} idx={idx} candidate={_display_candidate(found)}"
                )
                break
            if idx >= total:
                dep_tier += 1
                dep_index = 0
            else:
                dep_index = idx
                break

    # Additional dependency probes for higher effective key lengths:
    # - L=8 -> m44: first 5 decrypted bytes depend on prefix5 (mainly first 4.5 bytes)
    # - L=9 -> m48: first 5 decrypted bytes depend on prefix5
    m44_tier = int(checkpoint.get("m44_tier", 0))
    m44_index = int(checkpoint.get("m44_index", 0))
    m48_tier = int(checkpoint.get("m48_tier", 0))
    m48_index = int(checkpoint.get("m48_index", 0))
    m56_tier = int(checkpoint.get("m56_tier", 0))
    m56_index = int(checkpoint.get("m56_index", 0))
    m60_tier = int(checkpoint.get("m60_tier", 0))
    m60_index = int(checkpoint.get("m60_index", 0))
    m64_tier = int(checkpoint.get("m64_tier", 0))
    m64_index = int(checkpoint.get("m64_index", 0))
    m72_tier = int(checkpoint.get("m72_tier", checkpoint.get("m68_tier", 0)))
    m72_index = int(checkpoint.get("m72_index", checkpoint.get("m68_index", 0)))
    m76_tier = int(checkpoint.get("m76_tier", 0))
    m76_index = int(checkpoint.get("m76_index", 0))
    m80_tier = int(checkpoint.get("m80_tier", 0))
    m80_index = int(checkpoint.get("m80_index", 0))
    hi_budget = min(max(0, explore_limit - attempts), 90_000)
    if hi_budget > 0 and not found:
        evidence.append("runtime_probe:dependency_probe=L8/L9(prefix5),m44/m48")
        m44_budget = hi_budget // 2
        m48_budget = hi_budget - m44_budget
        m44_spent = 0
        m48_spent = 0

        # Probe m44 (L=8) first.
        while m44_tier < len(dep_tiers) and m44_spent < m44_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m44_tier]
            total = len(dep_charset) ** 5
            idx = m44_index
            while idx < total and m44_spent < m44_budget and not found:
                if _time_exceeded():
                    break
                prefix5 = _index_to_candidate(dep_charset, 5, idx)
                candidate = prefix5 + "AAA"  # length 8 => m44
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m44_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m44_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m44_index = idx
                break
            if idx >= total:
                m44_tier += 1
                m44_index = 0
            else:
                m44_index = idx
                break

        # Then probe m48 (L=9).
        while m48_tier < len(dep_tiers) and m48_spent < m48_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m48_tier]
            total = len(dep_charset) ** 5
            idx = m48_index
            while idx < total and m48_spent < m48_budget and not found:
                if _time_exceeded():
                    break
                prefix5 = _index_to_candidate(dep_charset, 5, idx)
                candidate = prefix5 + "AAAA"  # length 9 => m48
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m48_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m48_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m48_index = idx
                break
            if idx >= total:
                m48_tier += 1
                m48_index = 0
            else:
                m48_index = idx
                break

    # Longer-length dependency probes:
    # - L=10 -> m56: first 5 bytes depend on prefix6 (b0..b4 + hi4(b5))
    # - L=11 -> m60: first 5 bytes depend on prefix6
    hi2_budget = min(max(0, explore_limit - attempts), 90_000)
    if hi2_budget > 0 and not found:
        evidence.append("runtime_probe:dependency_probe=L10/L11(prefix6),m56/m60")
        m56_budget = hi2_budget // 2
        m60_budget = hi2_budget - m56_budget
        m56_spent = 0
        m60_spent = 0

        while m56_tier < len(dep_tiers) and m56_spent < m56_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m56_tier]
            total = len(dep_charset) ** 6
            idx = m56_index
            while idx < total and m56_spent < m56_budget and not found:
                if _time_exceeded():
                    break
                prefix6 = _index_to_candidate(dep_charset, 6, idx)
                candidate = prefix6 + "AAAA"  # length 10 => m56
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m56_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m56_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m56_index = idx
                break
            if idx >= total:
                m56_tier += 1
                m56_index = 0
            else:
                m56_index = idx
                break

        while m60_tier < len(dep_tiers) and m60_spent < m60_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m60_tier]
            total = len(dep_charset) ** 6
            idx = m60_index
            while idx < total and m60_spent < m60_budget and not found:
                if _time_exceeded():
                    break
                prefix6 = _index_to_candidate(dep_charset, 6, idx)
                candidate = prefix6 + "AAAAA"  # length 11 => m60
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m60_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m60_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m60_index = idx
                break
            if idx >= total:
                m60_tier += 1
                m60_index = 0
            else:
                m60_index = idx
                break

    # - L=12 -> m64: first 5 bytes depend on prefix7
    # - L=13 -> m72: first 5 bytes depend on prefix7
    hi3_budget = min(max(0, explore_limit - attempts), 90_000)
    if hi3_budget > 0 and not found:
        evidence.append(
            "runtime_probe:dependency_probe="
            f"L12/L13(prefix7),m64/m{_key_length_for_input_length(13)}"
        )
        m64_budget = hi3_budget // 2
        m72_budget = hi3_budget - m64_budget
        m64_spent = 0
        m72_spent = 0

        while m64_tier < len(dep_tiers) and m64_spent < m64_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m64_tier]
            total = len(dep_charset) ** 7
            idx = m64_index
            while idx < total and m64_spent < m64_budget and not found:
                if _time_exceeded():
                    break
                prefix7 = _index_to_candidate(dep_charset, 7, idx)
                candidate = prefix7 + "AAAAA"  # length 12 => m64
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m64_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m64_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m64_index = idx
                break
            if idx >= total:
                m64_tier += 1
                m64_index = 0
            else:
                m64_index = idx
                break

        while m72_tier < len(dep_tiers) and m72_spent < m72_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m72_tier]
            total = len(dep_charset) ** 7
            idx = m72_index
            while idx < total and m72_spent < m72_budget and not found:
                if _time_exceeded():
                    break
                prefix7 = _index_to_candidate(dep_charset, 7, idx)
                candidate = prefix7 + "AAAAAA"  # length 13 => m72
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m72_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m72_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m72_index = idx
                break
            if idx >= total:
                m72_tier += 1
                m72_index = 0
            else:
                m72_index = idx
                break

    # - L=14 -> m76: first 5 bytes depend on prefix8 (b0..b6 + hi4(b7))
    # - L=15 -> m80: first 5 bytes depend on prefix8
    hi4_budget = min(max(0, explore_limit - attempts), 90_000)
    if hi4_budget > 0 and not found:
        evidence.append(
            "runtime_probe:dependency_probe="
            f"L14/L15(prefix8),m{_key_length_for_input_length(14)}/m{_key_length_for_input_length(15)}"
        )
        m76_budget = hi4_budget // 2
        m80_budget = hi4_budget - m76_budget
        m76_spent = 0
        m80_spent = 0

        while m76_tier < len(dep_tiers) and m76_spent < m76_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m76_tier]
            total = len(dep_charset) ** 8
            idx = m76_index
            while idx < total and m76_spent < m76_budget and not found:
                if _time_exceeded():
                    break
                prefix8 = _index_to_candidate(dep_charset, 8, idx)
                candidate = prefix8 + "AAAAAA"  # length 14 => m76
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m76_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m76_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m76_index = idx
                break
            if idx >= total:
                m76_tier += 1
                m76_index = 0
            else:
                m76_index = idx
                break

        while m80_tier < len(dep_tiers) and m80_spent < m80_budget and not found and not _time_exceeded():
            tier_name, dep_charset = dep_tiers[m80_tier]
            total = len(dep_charset) ** 8
            idx = m80_index
            while idx < total and m80_spent < m80_budget and not found:
                if _time_exceeded():
                    break
                prefix8 = _index_to_candidate(dep_charset, 8, idx)
                candidate = prefix8 + "AAAAAAA"  # length 15 => m80
                score, mask, prefix_hex = _score_candidate_prefix(candidate)
                attempts += 1
                m80_spent += 1
                _record_best(score, mask, candidate, prefix_hex)
                if score >= 2:
                    top_scored.append((score, mask, candidate, prefix_hex))
                if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = candidate
                    evidence.append(
                        "runtime_probe:m80_hit "
                        f"tier={tier_name} idx={idx} candidate={_display_candidate(candidate)}"
                    )
                    break
                idx += 1
            if found:
                m80_index = idx
                break
            if idx >= total:
                m80_tier += 1
                m80_index = 0
            else:
                m80_index = idx
                break

    z3_enabled = os.getenv("REVERSE_AGENT_SAMPLE_ENABLE_Z3", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if z3_enabled and not found and not _time_exceeded():
        try:
            from .samplereverse_z3 import solve_with_partitions

            z3_result = solve_with_partitions(
                m_values=[40, 44, 48],
                branch_bytes=2,
                max_branches=2048,
                timeout_ms=120,
            )
            evidence.extend((z3_result.evidence or [])[:24])
            if z3_result.candidate_latin1:
                found = z3_result.candidate_latin1
                _push_candidate(found)
                evidence.append(
                    f"runtime_probe:z3_candidate_hex={z3_result.candidate_hex}"
                )
        except Exception as exc:
            evidence.append(f"runtime_probe:z3_error={type(exc).__name__}:{exc}")

    charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    while (
        not found
        and attempts < explore_limit
        and cartesian_length <= 6
        and not _time_exceeded()
    ):
        total = len(charset) ** cartesian_length
        idx = starts_from if cartesian_length == int(checkpoint.get("cartesian_length", 4)) else 0
        while idx < total and attempts < explore_limit:
            if _time_exceeded():
                break
            candidate = _index_to_candidate(charset, cartesian_length, idx)
            score, mask, prefix_hex = _score_candidate_prefix(candidate)
            attempts += 1
            _record_best(score, mask, candidate, prefix_hex)
            if score >= 2:
                top_scored.append((score, mask, candidate, prefix_hex))
            if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                found = candidate
                break
            idx += 1
        if found:
            starts_from = idx
            break
        if idx >= total:
            cartesian_length += 1
            starts_from = 0
        else:
            starts_from = idx
            break

    top_scored = _dedupe_top_scored(top_scored, limit=2048)

    # Local refinement stage: coordinate-descent from best seeds.
    late_stage_reserve = max(140_000, max_attempts // 4)
    refine_cap = min(reserve_attempts, max(60_000, max_attempts // 8))
    refine_budget = min(max(0, max_attempts - attempts - late_stage_reserve), refine_cap)
    if not found and refine_budget > 0 and not _time_exceeded():
        evidence.append("runtime_probe:refine=coordinate_descent(seed_top_scored)")
        seed_pool = [c for _, _, c, _ in sorted(top_scored, key=lambda x: (-x[0], -x[1], len(x[2])))]
        if best_hex:
            try:
                seed_pool.insert(0, bytes.fromhex(best_hex).decode("latin1"))
            except Exception:
                pass
        charsets = [
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-!@#$%^&*()[]",
        ]
        seen_refine: set[str] = set()
        refined_top: list[tuple[int, int, str, str]] = []
        for seed in seed_pool[:18]:
            if attempts >= max_attempts or _time_exceeded() or refine_budget <= 0:
                break
            if not seed or seed in seen_refine:
                continue
            seen_refine.add(seed)
            base = bytearray(seed.encode("latin1", errors="ignore"))
            if not base or len(base) > 18:
                continue
            for charset_refine in charsets:
                if refine_budget <= 0 or _time_exceeded() or attempts >= max_attempts or found:
                    break
                table = [ord(c) for c in charset_refine]
                improved = True
                # Multi-pass coordinate descent, bounded by remaining budget.
                while improved and refine_budget > 0 and not _time_exceeded() and not found:
                    improved = False
                    for pos in range(len(base)):
                        if refine_budget <= 0 or _time_exceeded() or found:
                            break
                        cur_candidate = bytes(base).decode("latin1")
                        cur_score, cur_mask, cur_hex = _score_candidate_prefix(cur_candidate)
                        attempts += 1
                        refine_budget -= 1
                        _record_best(cur_score, cur_mask, cur_candidate, cur_hex)
                        best_local = (cur_score, cur_mask, base[pos], cur_hex)
                        for b in table:
                            if b == base[pos] or refine_budget <= 0:
                                continue
                            old = base[pos]
                            base[pos] = b
                            cand = bytes(base).decode("latin1")
                            sc, mk, px = _score_candidate_prefix(cand)
                            attempts += 1
                            refine_budget -= 1
                            _record_best(sc, mk, cand, px)
                            if sc > best_local[0] or (sc == best_local[0] and mk > best_local[1]):
                                best_local = (sc, mk, b, px)
                                improved = True
                                if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                                    found = cand
                                    break
                            base[pos] = old
                            if _time_exceeded():
                                break
                        base[pos] = best_local[2]
                        cur = bytes(base).decode("latin1")
                        if best_local[0] >= 2:
                            refined_top.append((best_local[0], best_local[1], cur, best_local[3]))
                        if found:
                            break
                    if found or _time_exceeded():
                        break

        if refined_top:
            top_scored.extend(refined_top)

    top_scored = _dedupe_top_scored(top_scored, limit=2048)

    # Beam-search over typable candidates with objective(score, mask, -distance).
    beam_budget = min(max(0, max_attempts - attempts), 180_000)
    if not found and beam_budget > 0 and not _time_exceeded():
        evidence.append("runtime_probe:beam_probe=enabled")
        beam_width = 48
        expansion_positions = 8
        beam_seeds: list[str] = []
        if best_hex:
            try:
                beam_seeds.append(bytes.fromhex(best_hex).decode("latin1"))
            except Exception:
                pass
        for _, _, cand, _ in top_scored[:28]:
            beam_seeds.append(cand)
        if not beam_seeds:
            beam_seeds = ["AAAA", "FLAG", "KEY1", "PASS"]
        beam: list[tuple[tuple[int, int, int], str, str, int, int]] = []
        seen_beam: set[str] = set()
        for seed in beam_seeds:
            if not seed or seed in seen_beam:
                continue
            seen_beam.add(seed)
            sc, mk, px = _score_candidate_prefix(seed)
            attempts += 1
            beam_budget -= 1
            _record_best(sc, mk, seed, px)
            obj = _objective_tuple(px, sc, mk)
            beam.append((obj, seed, px, sc, mk))
            if sc >= 2:
                top_scored.append((sc, mk, seed, px))
            if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                found = seed
                break
            if beam_budget <= 0 or _time_exceeded():
                break
        beam.sort(key=lambda item: item[0], reverse=True)
        beam = beam[:beam_width]

        focused_bytes = _focused_charset_bytes()
        while not found and beam_budget > 0 and not _time_exceeded() and beam:
            next_beam: list[tuple[tuple[int, int, int], str, str, int, int]] = []
            seen_next: set[str] = set()
            for _, cand, _, _, _ in beam:
                if found or beam_budget <= 0 or _time_exceeded():
                    break
                work = bytearray(cand.encode("latin1", errors="ignore"))
                if not work:
                    continue
                max_pos = min(len(work), expansion_positions)
                for pos in range(max_pos):
                    if found or beam_budget <= 0 or _time_exceeded():
                        break
                    old = work[pos]
                    for b in focused_bytes:
                        if b == old or beam_budget <= 0:
                            continue
                        work[pos] = b
                        nxt = work.decode("latin1")
                        if nxt in seen_next:
                            continue
                        sc, mk, px = _score_candidate_prefix(nxt)
                        attempts += 1
                        beam_budget -= 1
                        _record_best(sc, mk, nxt, px)
                        obj = _objective_tuple(px, sc, mk)
                        next_beam.append((obj, nxt, px, sc, mk))
                        seen_next.add(nxt)
                        if sc >= 2:
                            top_scored.append((sc, mk, nxt, px))
                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                            found = nxt
                            break
                        if _time_exceeded():
                            break
                    work[pos] = old
            if not next_beam:
                break
            next_beam.sort(key=lambda item: item[0], reverse=True)
            beam = next_beam[:beam_width]

    top_scored.sort(key=lambda item: (-item[0], -item[1], len(item[2]), item[2]))
    for score, mask, candidate, prefix_hex in top_scored[:16]:
        evidence.append(
            "runtime_probe:prefix_preview "
            f"candidate={_display_candidate(candidate)} "
            f"score={score}/{len(SAMPLEREVERSE_TARGET_PREFIX)} "
            f"mask={mask:0{len(SAMPLEREVERSE_TARGET_PREFIX)}b} "
            f"dec_prefix_hex={prefix_hex}"
        )
        _push_candidate(candidate)

    if _time_exceeded() and not found:
        deadline_hit = True
        evidence.append("runtime_probe:deadline_reached=1")

    # Focused brute-force around current best mask (cheap when only 1~2 positions mismatch).
    post_focused_reserve = max(140_000, max_attempts // 4)
    focused_budget = min(
        max(0, max_attempts - attempts - post_focused_reserve),
        max(80_000, reserve_attempts // 2),
    )
    if (
        not found
        and not _time_exceeded()
        and focused_budget > 0
        and best_hex
        and best_score >= max(1, len(SAMPLEREVERSE_TARGET_PREFIX) - 2)
    ):
        try:
            base_bytes = bytearray(bytes.fromhex(best_hex))
        except Exception:
            base_bytes = bytearray()
        if base_bytes:
            mismatch_positions = [
                idx
                for idx in range(len(SAMPLEREVERSE_TARGET_PREFIX))
                if (best_mask & (1 << (len(SAMPLEREVERSE_TARGET_PREFIX) - 1 - idx))) == 0
                and idx < len(base_bytes)
            ]
            if 1 <= len(mismatch_positions) <= 2:
                evidence.append(
                    "runtime_probe:focused_probe="
                    f"score={best_score}/{len(SAMPLEREVERSE_TARGET_PREFIX)} "
                    f"mask={best_mask:0{len(SAMPLEREVERSE_TARGET_PREFIX)}b} "
                    f"mismatch={mismatch_positions}"
                )
                # Exhaustively mutate mismatched positions with full-byte values.
                if len(mismatch_positions) == 1:
                    p0 = mismatch_positions[0]
                    for b0 in _focused_charset_bytes():
                        if focused_budget <= 0 or _time_exceeded():
                            break
                        old0 = base_bytes[p0]
                        base_bytes[p0] = b0
                        candidate = bytes(base_bytes).decode("latin1")
                        sc, mk, px = _score_candidate_prefix(candidate)
                        attempts += 1
                        focused_budget -= 1
                        _record_best(sc, mk, candidate, px)
                        base_bytes[p0] = old0
                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                            found = candidate
                            break
                else:
                    p0, p1 = mismatch_positions
                    focused_bytes = _focused_charset_bytes()
                    for b0 in focused_bytes:
                        if found or focused_budget <= 0 or _time_exceeded():
                            break
                        old0 = base_bytes[p0]
                        base_bytes[p0] = b0
                        for b1 in focused_bytes:
                            if focused_budget <= 0 or _time_exceeded():
                                break
                            old1 = base_bytes[p1]
                            base_bytes[p1] = b1
                            candidate = bytes(base_bytes).decode("latin1")
                            sc, mk, px = _score_candidate_prefix(candidate)
                            attempts += 1
                            focused_budget -= 1
                            _record_best(sc, mk, candidate, px)
                            base_bytes[p1] = old1
                            if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                                found = candidate
                                break

                    # If still stuck at 3/5 with two mismatches, perturb one support byte
                    # (within first 7 bytes) and re-scan mismatch pairs.
                    tri_budget = min(max(0, max_attempts - attempts), 120_000)
                    if (
                        not found
                        and tri_budget > 0
                        and len(base_bytes) >= 7
                    ):
                        tri_enabled = True
                        support_positions = [
                            p for p in range(min(7, len(base_bytes))) if p not in (p0, p1)
                        ]
                        if support_positions:
                            evidence.append(
                                "runtime_probe:tri_probe="
                                f"mismatch=({p0},{p1}) supports={support_positions}"
                            )
                        for sp in support_positions:
                            if found or tri_budget <= 0 or _time_exceeded():
                                break
                            orig = base_bytes[sp]
                            support_samples = [orig, (orig + 1) & 0xFF, (orig ^ 0x10) & 0xFF, (orig ^ 0x20) & 0xFF]
                            for sb in support_samples:
                                if found or tri_budget <= 0 or _time_exceeded():
                                    break
                                if sb == 0:
                                    continue
                                base_bytes[sp] = sb
                                for b0 in focused_bytes:
                                    if found or tri_budget <= 0 or _time_exceeded():
                                        break
                                    old0 = base_bytes[p0]
                                    base_bytes[p0] = b0
                                    for b1 in focused_bytes:
                                        if tri_budget <= 0 or _time_exceeded():
                                            break
                                        old1 = base_bytes[p1]
                                        base_bytes[p1] = b1
                                        candidate = bytes(base_bytes).decode("latin1")
                                        sc, mk, px = _score_candidate_prefix(candidate)
                                        attempts += 1
                                        tri_budget -= 1
                                        _record_best(sc, mk, candidate, px)
                                        base_bytes[p1] = old1
                                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                                            found = candidate
                                            break
                                    base_bytes[p0] = old0
                                base_bytes[sp] = orig
                        base_bytes[p0] = old0

    triad_probe_reserve = max(100_000, max_attempts // 6)
    triad_probe_budget = min(
        max(0, max_attempts - attempts - triad_probe_reserve),
        180_000,
    )
    if not found and triad_probe_budget > 0 and not _time_exceeded() and best_hex:
        try:
            triad_base = bytearray(bytes.fromhex(best_hex))
        except Exception:
            triad_base = bytearray()
        if len(triad_base) >= 7:
            active_positions = list(range(min(7, len(triad_base))))
            shortlist = _top_single_byte_values(bytes(triad_base), active_positions, top_k=12)
            triads = list(combinations(active_positions, 3))
            position_strength: dict[int, tuple[int, int, int, int, int]] = {}
            for pos in active_positions:
                values = shortlist.get(pos, [triad_base[pos]])
                if len(values) <= 1:
                    position_strength[pos] = (-1, -1, -1, -1, -(10**9))
                    continue
                trial = bytearray(triad_base)
                trial[pos] = values[1]
                sc, mk, px = _score_candidate_bytes(bytes(trial))
                position_strength[pos] = _objective_tuple(px, sc, mk)
            triads.sort(
                key=lambda triad: tuple(
                    sorted((position_strength[pos] for pos in triad), reverse=True)
                ),
                reverse=True,
            )
            evidence.append(
                "runtime_probe:triad_probe="
                f"positions={active_positions} topk=12 triads={len(triads)}"
            )
            originals = {pos: triad_base[pos] for pos in active_positions}
            for p0, p1, p2 in triads:
                if found or triad_probe_budget <= 0 or _time_exceeded():
                    break
                values0 = shortlist.get(p0, [triad_base[p0]])
                values1 = shortlist.get(p1, [triad_base[p1]])
                values2 = shortlist.get(p2, [triad_base[p2]])
                for v0, v1, v2 in product(values0, values1, values2):
                    if found or triad_probe_budget <= 0 or _time_exceeded():
                        break
                    if (
                        v0 == originals[p0]
                        and v1 == originals[p1]
                        and v2 == originals[p2]
                    ):
                        continue
                    old0 = triad_base[p0]
                    old1 = triad_base[p1]
                    old2 = triad_base[p2]
                    triad_base[p0] = v0
                    triad_base[p1] = v1
                    triad_base[p2] = v2
                    candidate = bytes(triad_base).decode("latin1")
                    sc, mk, px = _score_candidate_prefix(candidate)
                    attempts += 1
                    triad_probe_budget -= 1
                    _record_best(sc, mk, candidate, px)
                    if sc >= 2:
                        top_scored.append((sc, mk, candidate, px))
                    triad_base[p0] = old0
                    triad_base[p1] = old1
                    triad_base[p2] = old2
                    if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                        found = candidate
                        break

    # Deterministic byte-level refinement around the best current candidates.
    byte_refine_reserve = max(120_000, max_attempts // 6)
    byte_refine_budget = min(max(0, max_attempts - attempts - byte_refine_reserve), 240_000)
    if not found and byte_refine_budget > 0 and not _time_exceeded():
        evidence.append("runtime_probe:byte_refine=coordinate_descent(full_bytes)")
        refine_seeds: list[bytes] = []
        if best_hex:
            try:
                refine_seeds.append(bytes.fromhex(best_hex))
            except Exception:
                pass
        for _, _, cand, _ in top_scored[:12]:
            try:
                raw = cand.encode("latin1", errors="ignore")
            except Exception:
                continue
            if raw:
                refine_seeds.append(raw)
        if not refine_seeds:
            refine_seeds = [b"AAAAAAA"]

        seen_refine: set[bytes] = set()
        focused_bytes = _focused_charset_bytes()
        for seed in refine_seeds:
            if found or byte_refine_budget <= 0 or _time_exceeded():
                break
            work = bytes(seed[:20])
            if not work or work in seen_refine:
                continue
            seen_refine.add(work)
            cur = bytearray(work)
            if len(cur) < 4:
                cur.extend(b"A" * (4 - len(cur)))
            cur_cand = bytes(cur).decode("latin1")
            cur_sc, cur_mk, cur_px = _score_candidate_prefix(cur_cand)
            cur_obj = _objective_tuple(cur_px, cur_sc, cur_mk)
            passes = 0
            improved = True
            while (
                improved
                and not found
                and byte_refine_budget > 0
                and not _time_exceeded()
                and passes < 4
            ):
                improved = False
                passes += 1
                for pos in range(len(cur)):
                    if found or byte_refine_budget <= 0 or _time_exceeded():
                        break
                    original = cur[pos]
                    best_local_obj = cur_obj
                    best_local_state = (original, cur_sc, cur_mk, cur_px)
                    for value in focused_bytes:
                        if value == original:
                            continue
                        cur[pos] = value
                        cand = bytes(cur).decode("latin1")
                        sc, mk, px = _score_candidate_prefix(cand)
                        attempts += 1
                        byte_refine_budget -= 1
                        _record_best(sc, mk, cand, px)
                        obj = _objective_tuple(px, sc, mk)
                        if sc >= 2:
                            top_scored.append((sc, mk, cand, px))
                        if obj > best_local_obj:
                            best_local_obj = obj
                            best_local_state = (value, sc, mk, px)
                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                            found = cand
                            break
                        if byte_refine_budget <= 0 or _time_exceeded():
                            break
                    if found:
                        break
                    cur[pos] = best_local_state[0]
                    if best_local_obj > cur_obj:
                        improved = True
                        cur_obj = best_local_obj
                        cur_sc = best_local_state[1]
                        cur_mk = best_local_state[2]
                        cur_px = best_local_state[3]
                    else:
                        cur[pos] = original

                if found or byte_refine_budget <= 0 or _time_exceeded():
                    break

                if len(cur) < 18:
                    original_len = len(cur)
                    cur.append(0x41)
                    best_append_obj = cur_obj
                    best_append_state = (0x41, cur_sc, cur_mk, cur_px)
                    for value in focused_bytes:
                        if found or byte_refine_budget <= 0 or _time_exceeded():
                            break
                        cur[-1] = value
                        cand = bytes(cur).decode("latin1")
                        sc, mk, px = _score_candidate_prefix(cand)
                        attempts += 1
                        byte_refine_budget -= 1
                        _record_best(sc, mk, cand, px)
                        obj = _objective_tuple(px, sc, mk)
                        if sc >= 2:
                            top_scored.append((sc, mk, cand, px))
                        if obj > best_append_obj:
                            best_append_obj = obj
                            best_append_state = (value, sc, mk, px)
                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                            found = cand
                            break
                    if found:
                        break
                    if best_append_obj > cur_obj:
                        cur[-1] = best_append_state[0]
                        improved = True
                        cur_obj = best_append_obj
                        cur_sc = best_append_state[1]
                        cur_mk = best_append_state[2]
                        cur_px = best_append_state[3]
                    else:
                        del cur[original_len:]

                if found or byte_refine_budget <= 0 or _time_exceeded():
                    break

                if len(cur) > 4:
                    best_delete_obj = cur_obj
                    best_delete_state: tuple[int, int, int, str] | None = None
                    for pos in range(len(cur)):
                        if found or byte_refine_budget <= 0 or _time_exceeded():
                            break
                        trial = bytearray(cur)
                        del trial[pos]
                        cand = bytes(trial).decode("latin1")
                        sc, mk, px = _score_candidate_prefix(cand)
                        attempts += 1
                        byte_refine_budget -= 1
                        _record_best(sc, mk, cand, px)
                        obj = _objective_tuple(px, sc, mk)
                        if sc >= 2:
                            top_scored.append((sc, mk, cand, px))
                        if obj > best_delete_obj:
                            best_delete_obj = obj
                            best_delete_state = (pos, sc, mk, px)
                        if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                            found = cand
                            break
                    if found:
                        break
                    if best_delete_state is not None:
                        del cur[best_delete_state[0]]
                        improved = True
                        cur_obj = best_delete_obj
                        cur_sc = best_delete_state[1]
                        cur_mk = best_delete_state[2]
                        cur_px = best_delete_state[3]

    # Stochastic local search with richer objective to push 3/5 or 4/5 prefixes to 5/5.
    anneal_reserve = max(80_000, max_attempts // 6)
    stochastic_budget = min(max(0, max_attempts - attempts - anneal_reserve), 220_000)
    if not found and stochastic_budget > 0 and not _time_exceeded():
        stochastic_enabled = True
        evidence.append("runtime_probe:stochastic_probe=enabled")
        seeds: list[bytes] = []
        if best_hex:
            try:
                seeds.append(bytes.fromhex(best_hex))
            except Exception:
                pass
        for _, _, cand, _ in top_scored[:20]:
            try:
                seeds.append(cand.encode("latin1", errors="ignore"))
            except Exception:
                continue
        if not seeds:
            seeds = [b"AAAA", b"SEPTA", b"FLAG", b"KEYS"]

        alphabet = (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz"
            "0123456789_{}-!@#$%^&*()[]"
        )
        alphabet_bytes = [ord(ch) for ch in alphabet]
        full_bytes = [b for b in range(1, 256)]

        rng = random.Random(random_seed)
        while stochastic_budget > 0 and not _time_exceeded() and not found:
            seed = rng.choice(seeds)
            if not seed:
                seed = b"AAAA"
            work = bytearray(seed[:20])
            if len(work) < 4:
                work.extend(b"A" * (4 - len(work)))
            score, mask, prefix_hex = _score_candidate_prefix(work.decode("latin1"))
            attempts += 1
            stochastic_budget -= 1
            _record_best(score, mask, work.decode("latin1"), prefix_hex)
            cur_obj = _objective_tuple(prefix_hex, score, mask)
            if score >= 2:
                top_scored.append((score, mask, work.decode("latin1"), prefix_hex))
            if score == len(SAMPLEREVERSE_TARGET_PREFIX):
                found = work.decode("latin1")
                break

            stagnation = 0
            max_steps = min(2000, stochastic_budget)
            for _ in range(max_steps):
                if stochastic_budget <= 0 or _time_exceeded() or found:
                    break
                prev = bytearray(work)
                work2 = bytearray(work)
                op = rng.random()
                if op < 0.08 and len(work2) < 24:
                    pos = rng.randrange(len(work2) + 1)
                    pool = full_bytes if rng.random() < 0.25 else alphabet_bytes
                    work2[pos:pos] = bytes([rng.choice(pool)])
                elif op < 0.16 and len(work2) > 4:
                    pos = rng.randrange(len(work2))
                    del work2[pos]
                else:
                    idx = rng.randrange(len(work2))
                    old = work2[idx]
                    pool = full_bytes if rng.random() < 0.22 else alphabet_bytes
                    work2[idx] = rng.choice(pool)
                    if work2[idx] == old:
                        continue
                work = work2
                cand = work.decode("latin1")
                sc, mk, px = _score_candidate_prefix(cand)
                attempts += 1
                stochastic_budget -= 1
                _record_best(sc, mk, cand, px)
                obj = _objective_tuple(px, sc, mk)
                if obj > cur_obj:
                    cur_obj = obj
                    stagnation = 0
                    if sc >= 2:
                        top_scored.append((sc, mk, cand, px))
                    if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                        found = cand
                        break
                else:
                    work = prev
                    stagnation += 1
                    if stagnation > 180:
                        # Random kick to escape local optimum.
                        kick_cnt = min(3, len(work))
                        for _k in range(kick_cnt):
                            j = rng.randrange(len(work))
                            work[j] = rng.choice(alphabet_bytes)
                        stagnation = 0
            if cur_obj[0] >= 3:
                seeds.append(bytes(work))

    # Multi-start simulated annealing with variable-length and mixed byte domains.
    anneal_budget = min(max(0, max_attempts - attempts), 220_000)
    if not found and anneal_budget > 0 and not _time_exceeded():
        anneal_enabled = True
        evidence.append("runtime_probe:anneal_probe=enabled")
        rng = random.Random(random_seed ^ 0x5EED5EED)
        seeds: list[bytearray] = []
        if best_hex:
            try:
                seeds.append(bytearray(bytes.fromhex(best_hex)))
            except Exception:
                pass
        for _, _, cand, _ in top_scored[:40]:
            try:
                seeds.append(bytearray(cand.encode("latin1", errors="ignore")))
            except Exception:
                continue
        if not seeds:
            seeds = [bytearray(b"AAAA"), bytearray(b"FLAG"), bytearray(b"KEY1")]

        printable_bytes = [ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}-!@#$%^&*()[]"]
        all_bytes = [b for b in range(1, 256)]

        def _obj_scalar(obj: tuple[int, int, int]) -> int:
            return obj[0] * 10000 + obj[1] * 256 + obj[2]

        rounds = min(24, max(1, anneal_budget // 9000))
        for _r in range(rounds):
            if found or anneal_budget <= 0 or _time_exceeded():
                break
            base = bytearray(rng.choice(seeds))
            if len(base) < 4:
                base.extend(b"A" * (4 - len(base)))
            if len(base) > 28:
                base = base[:28]
            if not base:
                base = bytearray(b"AAAA")

            cand0 = bytes(base).decode("latin1")
            sc0, mk0, px0 = _score_candidate_prefix(cand0)
            attempts += 1
            anneal_budget -= 1
            _record_best(sc0, mk0, cand0, px0)
            cur_obj = _objective_tuple(px0, sc0, mk0)
            cur_scalar = _obj_scalar(cur_obj)
            if sc0 >= 2:
                top_scored.append((sc0, mk0, cand0, px0))
            if sc0 == len(SAMPLEREVERSE_TARGET_PREFIX):
                found = cand0
                break

            temperature = 180.0
            steps = min(9000, anneal_budget)
            for _s in range(steps):
                if found or anneal_budget <= 0 or _time_exceeded():
                    break
                prev = bytearray(base)
                op = rng.random()
                if op < 0.06 and len(base) < 28:
                    pos = rng.randrange(len(base) + 1)
                    pool = all_bytes if rng.random() < 0.55 else printable_bytes
                    base[pos:pos] = bytes([rng.choice(pool)])
                elif op < 0.12 and len(base) > 4:
                    pos = rng.randrange(len(base))
                    del base[pos]
                else:
                    pos = rng.randrange(len(base))
                    pool = all_bytes if rng.random() < 0.55 else printable_bytes
                    base[pos] = rng.choice(pool)

                cand = bytes(base).decode("latin1")
                sc, mk, px = _score_candidate_prefix(cand)
                attempts += 1
                anneal_budget -= 1
                _record_best(sc, mk, cand, px)
                obj = _objective_tuple(px, sc, mk)
                scalar = _obj_scalar(obj)
                if sc >= 2:
                    top_scored.append((sc, mk, cand, px))
                if sc == len(SAMPLEREVERSE_TARGET_PREFIX):
                    found = cand
                    break

                accept = False
                if scalar >= cur_scalar:
                    accept = True
                else:
                    delta = scalar - cur_scalar
                    prob = math.exp(delta / max(1.0, temperature))
                    if rng.random() < prob:
                        accept = True
                if accept:
                    cur_obj = obj
                    cur_scalar = scalar
                    if sc >= 3 and len(seeds) < 200:
                        seeds.append(bytearray(base))
                else:
                    base = prev

                temperature = max(3.0, temperature * 0.9992)

    top_scored = _dedupe_top_scored_by_prefix(
        _dedupe_top_scored(top_scored, limit=4096),
        limit=2048,
    )
    for _, _, candidate, _ in top_scored[:48]:
        _push_candidate(candidate)

    evidence.append(
        "runtime_probe:stages "
        f"tri={'1' if tri_enabled else '0'} "
        f"stochastic={'1' if stochastic_enabled else '0'} "
        f"anneal={'1' if anneal_enabled else '0'}"
    )

    if best_hex:
        evidence.append(f"runtime_probe:best_candidate_hex={best_hex}")
        if best_score:
            evidence.append(
                "runtime_probe:best_candidate_score="
                f"{best_score}/{len(SAMPLEREVERSE_TARGET_PREFIX)} "
                f"mask={best_mask:0{len(SAMPLEREVERSE_TARGET_PREFIX)}b}"
            )

    if found:
        evidence.append(f"runtime_candidate:{found}")
        _push_candidate(found)
        summary = f"sample-specific solver found a prefix-valid candidate after {attempts} attempts."
    elif deadline_hit:
        summary = (
            "sample-specific solver reached time deadline without a full prefix hit; "
            f"attempts={attempts}, resume_len={cartesian_length}, resume_idx={starts_from}."
        )
    else:
        summary = (
            "sample-specific solver finished current budget without a full prefix hit; "
            f"attempts={attempts}, resume_len={cartesian_length}, resume_idx={starts_from}."
        )

    _save_checkpoint(
        checkpoint_path,
        {
            "cartesian_length": cartesian_length,
            "cartesian_index": starts_from,
            "dep_tier": dep_tier,
            "dep_index": dep_index,
            "m44_tier": m44_tier,
            "m44_index": m44_index,
            "m48_tier": m48_tier,
            "m48_index": m48_index,
            "m56_tier": m56_tier,
            "m56_index": m56_index,
            "m60_tier": m60_tier,
            "m60_index": m60_index,
            "m64_tier": m64_tier,
            "m64_index": m64_index,
            "m72_tier": m72_tier,
            "m72_index": m72_index,
            "m76_tier": m76_tier,
            "m76_index": m76_index,
            "m80_tier": m80_tier,
            "m80_index": m80_index,
            # Keep legacy aliases so older checkpoints can still resume cleanly.
            "m68_tier": m72_tier,
            "m68_index": m72_index,
            "best_hex": best_hex,
            "best_prefix_hex": best_prefix_hex,
            "best_score": best_score,
            "best_mask": best_mask,
            "best_distance": best_distance,
            "started_epoch": started_epoch,
            "deadline_epoch": deadline_epoch,
            "deadline_budget_seconds": requested_budget_seconds,
        },
    )
    log(summary)
    return SampleSearchResult(
        enabled=True,
        summary=summary,
        candidates=candidates[:120],
        evidence=evidence[:80],
    )


def _looks_like_samplereverse(file_path: Path, strings: list[str]) -> bool:
    name_hit = "samplereverse" in file_path.name.lower()
    string_hit = any("密钥不正确" in s for s in strings[:3000]) and any(
        "输入的密钥是" in s for s in strings[:3000]
    )
    data_hit = SAMPLEREVERSE_ENC_CONST[:24] in file_path.read_bytes()
    return bool(name_hit or (string_hit and data_hit))


def _extract_token_candidates(strings: list[str], limit: int = 240) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    token_pat = re.compile(r"\b[A-Za-z0-9_]{4,16}\b")
    for line in strings[:3000]:
        for m in token_pat.finditer(line):
            token = m.group(0)
            if token in seen:
                continue
            seen.add(token)
            if token.isdigit():
                continue
            out.append(token)
            out.append(token.upper())
            out.append(token.lower())
            out.append(token + "1")
            out.append(token + "123")
            if len(out) >= limit:
                return out
    return out


def _score_candidate_prefix(candidate: str) -> tuple[int, int, str]:
    prefix = _decrypt_prefix(candidate, len(SAMPLEREVERSE_TARGET_PREFIX))
    score = 0
    mask = 0
    for idx, ch in enumerate(prefix):
        target = SAMPLEREVERSE_TARGET_PREFIX[idx]
        if _to_lower_ascii(ch) == _to_lower_ascii(target):
            score += 1
            mask |= (1 << (len(SAMPLEREVERSE_TARGET_PREFIX) - 1 - idx))
    return score, mask, prefix.hex()


def _score_candidate_bytes(candidate: bytes) -> tuple[int, int, str]:
    return _score_candidate_prefix(candidate.decode("latin1"))


def _decrypt_prefix(candidate: str, prefix_len: int) -> bytes:
    key_bytes = candidate.encode("latin1", errors="ignore")
    expanded = _expand_input_bytes(key_bytes)
    b64_text = _to_base64_utf16(expanded)
    key = b64_text.encode("utf-16le")[: len(b64_text)]
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = 0
    j = 0
    out = bytearray()
    for idx in range(prefix_len):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        ks = s[(s[i] + s[j]) & 0xFF]
        out.append(SAMPLEREVERSE_ENC_CONST[idx] ^ ks)
    return bytes(out)


def _expand_input_bytes(data: bytes) -> bytes:
    out = bytearray()
    for c in data:
        out.append(((c >> 4) & 0x0F) + 0x78)
        out.append((c & 0x0F) + 0x7A)
    return bytes(out)


def _to_base64_utf16(data: bytes) -> str:
    raw = bytearray()
    for b in data:
        raw.extend((b, 0))
    return base64.b64encode(bytes(raw)).decode("ascii")


def _index_to_candidate(charset: str, length: int, index: int) -> str:
    base = len(charset)
    chars = [charset[0]] * length
    cur = index
    for pos in range(length - 1, -1, -1):
        chars[pos] = charset[cur % base]
        cur //= base
    return "".join(chars)


def _key_length_for_input_length(input_len: int) -> int:
    return 4 * math.ceil((4 * input_len) / 3)


def _load_checkpoint(path: Path) -> dict[str, int | str | float]:
    default_payload = {
        "cartesian_length": 4,
        "cartesian_index": 0,
        "dep_tier": 0,
        "dep_index": 0,
        "m44_tier": 0,
        "m44_index": 0,
        "m48_tier": 0,
        "m48_index": 0,
        "m56_tier": 0,
        "m56_index": 0,
        "m60_tier": 0,
        "m60_index": 0,
        "m64_tier": 0,
        "m64_index": 0,
        "m72_tier": 0,
        "m72_index": 0,
        "m76_tier": 0,
        "m76_index": 0,
        "m80_tier": 0,
        "m80_index": 0,
        # Keep legacy fields for backward-compatible checkpoint reads.
        "m68_tier": 0,
        "m68_index": 0,
        "best_hex": "",
        "best_prefix_hex": "",
        "best_score": 0,
        "best_mask": 0,
        "best_distance": 10_000,
        "started_epoch": 0.0,
        "deadline_epoch": 0.0,
        "deadline_budget_seconds": 0.0,
    }
    if not path.exists():
        return default_payload
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_payload
    if not isinstance(data, dict):
        return default_payload
    payload: dict[str, int | str | float] = {}
    for key, default in default_payload.items():
        raw_value = data.get(key, default)
        if key == "m72_tier":
            raw_value = data.get("m72_tier", data.get("m68_tier", default))
        elif key == "m72_index":
            raw_value = data.get("m72_index", data.get("m68_index", default))
        if isinstance(default, str):
            payload[key] = str(raw_value)
        elif isinstance(default, float):
            payload[key] = float(raw_value)
        else:
            payload[key] = int(raw_value)
    return payload


def _save_checkpoint(path: Path, payload: dict[str, int | str | float]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _to_lower_ascii(value: int) -> int:
    if 0x41 <= value <= 0x5A:
        return value + 0x20
    return value


def _is_typable_candidate(value: str) -> bool:
    if not value:
        return False
    return all(0x21 <= ord(ch) <= 0x7E for ch in value)


def _focused_charset_bytes() -> list[int]:
    # Keep 0x00 excluded because GUI text setters treat it as terminator.
    return list(range(1, 256))


def _prefix_distance(prefix_hex: str) -> int:
    raw = bytes.fromhex(prefix_hex)
    compare_len = min(len(raw), len(SAMPLEREVERSE_TARGET_PREFIX))
    distance = sum(
        abs(raw[i] - SAMPLEREVERSE_TARGET_PREFIX[i]) for i in range(compare_len)
    )
    if len(raw) < len(SAMPLEREVERSE_TARGET_PREFIX):
        distance += 0x100 * (len(SAMPLEREVERSE_TARGET_PREFIX) - len(raw))
    elif len(raw) > len(SAMPLEREVERSE_TARGET_PREFIX):
        distance += sum(raw[len(SAMPLEREVERSE_TARGET_PREFIX) :])
    return distance


def _wide_prefix_metrics(prefix_hex: str) -> tuple[int, int]:
    try:
        raw = bytes.fromhex(prefix_hex)
    except Exception:
        return (0, 0)
    char_count = min(len(raw) // 2, len(SAMPLEREVERSE_TARGET_PREFIX) // 2)
    contiguous = 0
    matched = 0
    for idx in range(char_count):
        raw_code = raw[idx * 2] | (raw[idx * 2 + 1] << 8)
        target_code = (
            SAMPLEREVERSE_TARGET_PREFIX[idx * 2]
            | (SAMPLEREVERSE_TARGET_PREFIX[idx * 2 + 1] << 8)
        )
        if _to_lower_ascii(raw_code) == _to_lower_ascii(target_code):
            matched += 1
            if idx == contiguous:
                contiguous += 1
    return contiguous, matched


def _objective_tuple(prefix_hex: str, score: int, mask: int) -> tuple[int, int, int, int, int]:
    wide_prefix, wide_matched = _wide_prefix_metrics(prefix_hex)
    return (wide_prefix, wide_matched, score, mask, -_prefix_distance(prefix_hex))


def _top_single_byte_values(
    base_bytes: bytes,
    positions: list[int],
    top_k: int = 12,
) -> dict[int, list[int]]:
    focused_bytes = _focused_charset_bytes()
    work = bytearray(base_bytes)
    out: dict[int, list[int]] = {}
    for pos in positions:
        if pos >= len(work):
            continue
        original = work[pos]
        scored: list[tuple[tuple[int, int, int, int, int], int]] = []
        for value in [original, *[b for b in focused_bytes if b != original]]:
            work[pos] = value
            score, mask, prefix_hex = _score_candidate_bytes(bytes(work))
            scored.append((_objective_tuple(prefix_hex, score, mask), value))
        work[pos] = original
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        values: list[int] = []
        seen_values: set[int] = set()
        for _, value in scored:
            if value in seen_values:
                continue
            seen_values.add(value)
            values.append(value)
            if len(values) >= max(1, top_k):
                break
        if original not in seen_values:
            values.insert(0, original)
        out[pos] = values
    return out


def _load_optimizer_seed_candidates(
    artifacts_dir: Path,
    limit: int = 32,
) -> list[str]:
    result_path = artifacts_dir / OPTIMIZER_RESULT_FILE_NAME
    if not result_path.exists():
        return []
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    out: list[str] = []
    seen: set[str] = set()

    def _push_hex(raw_hex: str) -> None:
        if len(out) >= limit:
            return
        normalized = str(raw_hex or "").strip().lower()
        if not normalized or normalized in seen:
            return
        try:
            raw = bytes.fromhex(normalized)
        except Exception:
            return
        if len(raw) != 7:
            return
        candidate = (raw + b"AAAAAA").decode("latin1")
        seen.add(normalized)
        out.append(candidate)

    for key in ("best_prefix", "best_dist4", "best_dist6", "best_dist10"):
        entry = payload.get(key, {})
        if isinstance(entry, dict):
            _push_hex(str(entry.get("cand7_hex", "")))
    elite_prefixes = payload.get("elite_prefixes", [])
    if isinstance(elite_prefixes, list):
        for entry in elite_prefixes:
            if len(out) >= limit:
                break
            if isinstance(entry, dict):
                _push_hex(str(entry.get("cand7_hex", "")))
    return out


def _dedupe_top_scored(
    items: list[tuple[int, int, str, str]], limit: int = 2048
) -> list[tuple[int, int, str, str]]:
    best_by_candidate: dict[str, tuple[int, int, str, str]] = {}
    for score, mask, candidate, prefix_hex in items:
        cur = best_by_candidate.get(candidate)
        nxt = (score, mask, candidate, prefix_hex)
        if cur is None or (score, mask, -_prefix_distance(prefix_hex), candidate) > (
            cur[0],
            cur[1],
            -_prefix_distance(cur[3]),
            cur[2],
        ):
            best_by_candidate[candidate] = nxt
    out = list(best_by_candidate.values())
    out.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            _prefix_distance(item[3]),
            len(item[2]),
            item[2],
        )
    )
    return out[:limit]


def _dedupe_top_scored_by_prefix(
    items: list[tuple[int, int, str, str]], limit: int = 2048
) -> list[tuple[int, int, str, str]]:
    best_by_prefix: dict[str, tuple[int, int, str, str]] = {}
    for score, mask, candidate, prefix_hex in items:
        cur = best_by_prefix.get(prefix_hex)
        nxt = (score, mask, candidate, prefix_hex)
        if cur is None or _objective_tuple(prefix_hex, score, mask) > _objective_tuple(
            cur[3], cur[0], cur[1]
        ) or (
            _objective_tuple(prefix_hex, score, mask)
            == _objective_tuple(cur[3], cur[0], cur[1])
            and candidate.encode("latin1", errors="ignore").hex()
            < cur[2].encode("latin1", errors="ignore").hex()
        ):
            best_by_prefix[prefix_hex] = nxt
    out = list(best_by_prefix.values())
    out.sort(
        key=lambda item: (
            _objective_tuple(item[3], item[0], item[1]),
            -len(item[2]),
            item[2],
        ),
        reverse=True,
    )
    return out[:limit]


def _display_candidate(candidate: str) -> str:
    out: list[str] = []
    for ch in candidate:
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            out.append(ch)
        else:
            out.append(f"\\x{o:02x}")
    return "".join(out)
