from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Sequence

try:
    from z3 import (
        And,
        Array,
        BV2Int,
        BitVec,
        BitVecSort,
        BitVecVal,
        If,
        Optimize,
        Select,
        Solver,
        Store,
        Sum,
        sat,
    )
except ImportError:  # pragma: no cover
    And = Array = BV2Int = BitVec = BitVecSort = BitVecVal = If = Optimize = Select = Solver = Store = Sum = sat = None


ENC_CONST = bytes.fromhex(
    "698b8fb18f3b4f9961726ba869132942e6ff36b8be4ebce3efd4c9a7e35ff74f"
    "ccb9ca9b7ab1b8129285ccfbd812419f93eb15e91fe68784d900eb89e4f8d310"
    "0d91af1223c308eba2fcfdc4c69882e781ed9eb5"
)
TARGET = "flag{".encode("utf-16le")
B64_TABLE = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


@dataclass
class Z3ProbeResult:
    attempted: bool
    summary: str
    candidate_hex: str = ""
    candidate_latin1: str = ""
    evidence: list[str] | None = None
    diagnostics: dict[str, object] | None = None


def _z3_ready() -> bool:
    return Solver is not None


def _optimize_ready() -> bool:
    return Optimize is not None


def _to_lower_ascii(x):
    return If((x >= BitVecVal(0x41, 8)) & (x <= BitVecVal(0x5A, 8)), x + BitVecVal(0x20, 8), x)


def _b64_char(idx):
    out = BitVecVal(B64_TABLE[0], 8)
    for i in range(1, 64):
        out = If(idx == BitVecVal(i, 8), BitVecVal(B64_TABLE[i], 8), out)
    return out


def _first_base64_chars_from_prefix(prefix_vars, needed_chars: int):
    expanded = []
    for c in prefix_vars:
        expanded.append(((c >> 4) & BitVecVal(0x0F, 8)) + BitVecVal(0x78, 8))
        expanded.append((c & BitVecVal(0x0F, 8)) + BitVecVal(0x7A, 8))
    raw = []
    for b in expanded:
        raw.append(b)
        raw.append(BitVecVal(0, 8))
    chars = []
    gi = 0
    while len(chars) < needed_chars:
        b0 = raw[gi]
        b1 = raw[gi + 1]
        b2 = raw[gi + 2]
        c0 = _b64_char((b0 >> 2) & BitVecVal(0x3F, 8))
        c1 = _b64_char((((b0 & BitVecVal(0x03, 8)) << 4) | (b1 >> 4)) & BitVecVal(0x3F, 8))
        c2 = _b64_char((((b1 & BitVecVal(0x0F, 8)) << 2) | (b2 >> 6)) & BitVecVal(0x3F, 8))
        c3 = _b64_char(b2 & BitVecVal(0x3F, 8))
        chars.extend([c0, c1, c2, c3])
        gi += 3
    return chars[:needed_chars]


def _symbolic_input_len_for_m(m: int) -> int:
    q = (m + 1) // 2
    groups = (q + 3) // 4
    required_raw = groups * 3
    return (required_raw + 3) // 4


def _build_solver(m: int, timeout_ms: int):
    solver = Solver()
    solver.set(timeout=timeout_ms)
    sym_n = _symbolic_input_len_for_m(m)
    x = [BitVec(f"x{i}", 8) for i in range(sym_n)]
    b64_need = (m + 1) // 2
    b64_chars = _first_base64_chars_from_prefix(x, b64_need)
    key = []
    for i in range(m):
        if i % 2 == 0:
            key.append(b64_chars[i // 2])
        else:
            key.append(BitVecVal(0, 8))

    s = Array("S0", BitVecSort(8), BitVecSort(8))
    for i in range(256):
        s = Store(s, BitVecVal(i, 8), BitVecVal(i, 8))
    j = BitVecVal(0, 8)
    for i in range(256):
        i_bv = BitVecVal(i, 8)
        si = Select(s, i_bv)
        j = (j + si + key[i % m]) & BitVecVal(0xFF, 8)
        sj = Select(s, j)
        s = Store(s, i_bv, sj)
        s = Store(s, j, si)

    i_state = BitVecVal(0, 8)
    j_state = BitVecVal(0, 8)
    for idx in range(5):
        i_state = (i_state + BitVecVal(1, 8)) & BitVecVal(0xFF, 8)
        si = Select(s, i_state)
        j_state = (j_state + si) & BitVecVal(0xFF, 8)
        sj = Select(s, j_state)
        s = Store(s, i_state, sj)
        s = Store(s, j_state, si)
        ks_idx = (Select(s, i_state) + Select(s, j_state)) & BitVecVal(0xFF, 8)
        ks = Select(s, ks_idx)
        dec = BitVecVal(ENC_CONST[idx], 8) ^ ks
        solver.add(_to_lower_ascii(dec) == BitVecVal(TARGET[idx], 8))
    return solver, x


def _candidate_from_prefix(prefix: bytes, m: int) -> bytes:
    if m == 40:
        return prefix + b"AAA"
    if m == 44:
        return prefix + b"AAA"
    if m == 48:
        return prefix + b"AAAA"
    if m == 56:
        return prefix + b"AAAA"
    if m == 60:
        return prefix + b"AAAAA"
    if m == 64:
        return prefix + b"AAAAA"
    if m == 68:
        return prefix + b"AAAAAA"
    return prefix


def _decrypt_prefix(candidate: bytes, prefix_len: int = 10) -> bytes:
    expanded = bytearray()
    for c in candidate:
        expanded.append(((c >> 4) & 0x0F) + 0x78)
        expanded.append((c & 0x0F) + 0x7A)
    raw = bytearray()
    for b in expanded:
        raw.extend((b, 0))
    b64_text = base64.b64encode(bytes(raw)).decode("ascii")
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
        out.append(ENC_CONST[idx] ^ ks)
    return bytes(out)


def _abs_int(expr):
    return If(expr >= 0, expr, -expr)


def solve_targeted_prefix8(
    *,
    base_anchor: str,
    variable_byte_positions: list[int],
    variable_nibble_positions: list[int],
    value_pools: dict[int, Sequence[int]] | None = None,
    prioritize_distance: bool = False,
    timeout_ms: int = 1500,
) -> Z3ProbeResult:
    if not _optimize_ready():
        return Z3ProbeResult(
            attempted=False,
            summary="z3 optimize not installed",
            evidence=["runtime_probe:z3=missing_optimize"],
        )

    base_anchor = str(base_anchor).strip().lower()
    if len(base_anchor) != 16:
        return Z3ProbeResult(
            attempted=False,
            summary="invalid base anchor",
            evidence=[f"runtime_probe:z3_invalid_anchor={base_anchor}"],
        )

    base_bytes = bytes.fromhex(base_anchor)
    opt = Optimize()
    opt.set(timeout=timeout_ms)
    prefix_vars = [BitVec(f"tp8_{idx}", 8) for idx in range(8)]

    selected_bytes = {int(item) for item in variable_byte_positions if 0 <= int(item) < 8}
    selected_nibbles = {int(item) for item in variable_nibble_positions if 0 <= int(item) < 15}
    normalized_value_pools: dict[int, list[int]] = {}
    for raw_position, raw_values in dict(value_pools or {}).items():
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if position not in selected_bytes or not (0 <= position < len(base_bytes)):
            continue
        values: list[int] = [base_bytes[position]]
        for raw_value in raw_values:
            try:
                value = int(raw_value) & 0xFF
            except (TypeError, ValueError):
                continue
            if value not in values:
                values.append(value)
        normalized_value_pools[position] = values

    value_pool_sizes = {
        int(position): len(values) for position, values in sorted(normalized_value_pools.items())
    }
    estimated_value_pool_combinations = 1
    for position in sorted(selected_bytes):
        estimated_value_pool_combinations *= max(1, value_pool_sizes.get(int(position), 256))
    diagnostics: dict[str, object] = {
        "solver_type": "Optimize",
        "timeout_ms": int(timeout_ms),
        "symbolic_prefix_bytes": len(prefix_vars),
        "symbolic_compare_bytes": len(TARGET),
        "selected_byte_count": len(selected_bytes),
        "selected_nibble_count": len(selected_nibbles),
        "value_pool_sizes": {str(key): value for key, value in value_pool_sizes.items()},
        "estimated_value_pool_combinations": estimated_value_pool_combinations,
    }

    for idx, base_byte in enumerate(base_bytes):
        var = prefix_vars[idx]
        hi = (base_byte >> 4) & 0x0F
        lo = base_byte & 0x0F
        if idx not in selected_bytes:
            if idx < 7:
                if idx * 2 not in selected_nibbles:
                    opt.add(((var >> 4) & BitVecVal(0x0F, 8)) == BitVecVal(hi, 8))
                if idx * 2 + 1 not in selected_nibbles:
                    opt.add((var & BitVecVal(0x0F, 8)) == BitVecVal(lo, 8))
            else:
                if 14 not in selected_nibbles:
                    opt.add(((var >> 4) & BitVecVal(0x0F, 8)) == BitVecVal(hi, 8))
                opt.add((var & BitVecVal(0x0F, 8)) == BitVecVal(lo, 8))
        elif idx in normalized_value_pools:
            opt.add(
                Sum(
                    [
                        If(var == BitVecVal(value, 8), 1, 0)
                        for value in normalized_value_pools[idx]
                    ]
                )
                >= 1
            )

    candidate_vars = [*prefix_vars, *[BitVecVal(0x41, 8) for _ in range(7)]]
    b64_chars = _first_base64_chars_from_prefix(candidate_vars, 40)
    key = []
    for idx in range(80):
        if idx % 2 == 0:
            key.append(b64_chars[idx // 2])
        else:
            key.append(BitVecVal(0, 8))

    s = Array("TP8_S0", BitVecSort(8), BitVecSort(8))
    for idx in range(256):
        s = Store(s, BitVecVal(idx, 8), BitVecVal(idx, 8))
    j = BitVecVal(0, 8)
    for idx in range(256):
        i_bv = BitVecVal(idx, 8)
        si = Select(s, i_bv)
        j = (j + si + key[idx % 80]) & BitVecVal(0xFF, 8)
        sj = Select(s, j)
        s = Store(s, i_bv, sj)
        s = Store(s, j, si)

    decrypted = []
    i_state = BitVecVal(0, 8)
    j_state = BitVecVal(0, 8)
    for idx in range(10):
        i_state = (i_state + BitVecVal(1, 8)) & BitVecVal(0xFF, 8)
        si = Select(s, i_state)
        j_state = (j_state + si) & BitVecVal(0xFF, 8)
        sj = Select(s, j_state)
        s = Store(s, i_state, sj)
        s = Store(s, j_state, si)
        ks_idx = (Select(s, i_state) + Select(s, j_state)) & BitVecVal(0xFF, 8)
        ks = Select(s, ks_idx)
        decrypted.append(BitVecVal(ENC_CONST[idx], 8) ^ ks)

    prefix_ok = True
    exact_terms = []
    distance_terms = []
    for idx, dec in enumerate(decrypted):
        target_bv = BitVecVal(TARGET[idx], 8)
        match = _to_lower_ascii(dec) == target_bv
        prefix_ok = And(prefix_ok, match)
        exact_terms.append(If(prefix_ok, 1, 0))
        distance_terms.append(_abs_int(BV2Int(dec) - TARGET[idx]))

    exact = Sum(exact_terms)
    dist4 = Sum(distance_terms[:4])
    dist6 = Sum(distance_terms[:6])
    dist10 = Sum(distance_terms[:10])

    if prioritize_distance:
        opt.minimize(dist4)
        opt.minimize(dist6)
        opt.minimize(dist10)
        opt.maximize(exact)
    else:
        opt.maximize(exact)
        opt.minimize(dist4)
        opt.minimize(dist6)
        opt.minimize(dist10)
    result = opt.check()
    if result != sat:
        reason_unknown = ""
        if str(result) == "unknown":
            try:
                reason_unknown = str(opt.reason_unknown())
            except Exception:  # pragma: no cover - defensive against z3py variants
                reason_unknown = ""
            diagnostics["z3_reason_unknown"] = reason_unknown
        return Z3ProbeResult(
            attempted=True,
            summary=f"targeted z3 finished with {result}",
            evidence=[
                f"runtime_probe:z3_targeted result={result} base={base_anchor}",
                f"runtime_probe:z3_targeted solver_type=Optimize timeout_ms={timeout_ms}",
                f"runtime_probe:z3_targeted symbolic_compare_bytes={len(TARGET)}",
                f"runtime_probe:z3_targeted estimated_value_pool_combinations={estimated_value_pool_combinations}",
                f"runtime_probe:z3_targeted reason_unknown={reason_unknown}",
                f"runtime_probe:z3_targeted bytes={','.join(str(v) for v in sorted(selected_bytes))}",
                f"runtime_probe:z3_targeted nibbles={','.join(str(v) for v in sorted(selected_nibbles))}",
                "runtime_probe:z3_targeted value_pools="
                + ",".join(
                    f"{position}:{'/'.join(f'{value:02x}' for value in values)}"
                    for position, values in sorted(normalized_value_pools.items())
                ),
            ],
            diagnostics=diagnostics,
        )

    model = opt.model()
    prefix = bytes(model.eval(var, model_completion=True).as_long() & 0xFF for var in prefix_vars)
    candidate = prefix + (b"A" * 7)
    dec = _decrypt_prefix(candidate, prefix_len=len(TARGET))[: len(TARGET)]
    evidence = [
        f"runtime_probe:z3_targeted base={base_anchor}",
        f"runtime_probe:z3_targeted solver_type=Optimize timeout_ms={timeout_ms}",
        f"runtime_probe:z3_targeted symbolic_compare_bytes={len(TARGET)}",
        f"runtime_probe:z3_targeted estimated_value_pool_combinations={estimated_value_pool_combinations}",
        f"runtime_probe:z3_targeted bytes={','.join(str(v) for v in sorted(selected_bytes))}",
        f"runtime_probe:z3_targeted nibbles={','.join(str(v) for v in sorted(selected_nibbles))}",
        "runtime_probe:z3_targeted value_pools="
        + ",".join(
            f"{position}:{'/'.join(f'{value:02x}' for value in values)}"
            for position, values in sorted(normalized_value_pools.items())
        ),
        f"runtime_probe:z3_targeted dec_prefix_hex={dec.hex()}",
    ]
    return Z3ProbeResult(
        attempted=True,
        summary="targeted z3 completed",
        candidate_hex=candidate.hex(),
        candidate_latin1=candidate.decode("latin1"),
        evidence=evidence,
        diagnostics=diagnostics,
    )


def solve_with_partitions(
    m_values: list[int],
    branch_bytes: int = 2,
    max_branches: int = 4096,
    timeout_ms: int = 150,
) -> Z3ProbeResult:
    if not _z3_ready():
        return Z3ProbeResult(
            attempted=False,
            summary="z3 not installed",
            evidence=["runtime_probe:z3=missing"],
        )

    evidence: list[str] = []
    for m in m_values:
        solver, x = _build_solver(m, timeout_ms=timeout_ms)
        total = 256 ** branch_bytes
        end = min(total, max_branches)
        sat_count = 0
        unknown_count = 0
        for idx in range(end):
            vals = []
            cur = idx
            for _ in range(branch_bytes):
                vals.append(cur % 256)
                cur //= 256
            vals = list(reversed(vals))

            solver.push()
            for pos, val in enumerate(vals):
                solver.add(x[pos] == BitVecVal(val, 8))
            result = solver.check()
            if result == sat:
                sat_count += 1
                model = solver.model()
                prefix = bytes(model[v].as_long() & 0xFF for v in x)
                candidate = _candidate_from_prefix(prefix, m)
                dec = _decrypt_prefix(candidate)
                evidence.append(
                    f"runtime_probe:z3_sat m={m} idx={idx} prefix_hex={prefix.hex()} dec_prefix_hex={dec.hex()}"
                )
                if dec[: len(TARGET)].lower() == TARGET:
                    text = candidate.decode("latin1", errors="ignore")
                    evidence.append(
                        f"runtime_candidate:{text}"
                    )
                    return Z3ProbeResult(
                        attempted=True,
                        summary=f"z3 partition hit for m={m} at idx={idx}",
                        candidate_hex=candidate.hex(),
                        candidate_latin1=text,
                        evidence=evidence,
                    )
            elif str(result) == "unknown":
                unknown_count += 1
            solver.pop()
        evidence.append(
            f"runtime_probe:z3_window m={m} checked={end} sat={sat_count} unknown={unknown_count}"
        )
    return Z3ProbeResult(
        attempted=True,
        summary="z3 partition probe completed without hit",
        evidence=evidence,
    )
