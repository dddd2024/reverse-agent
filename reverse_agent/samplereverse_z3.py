from __future__ import annotations

import base64
from dataclasses import dataclass

try:
    from z3 import Array, BitVec, BitVecSort, BitVecVal, If, Select, Solver, Store, sat
except ImportError:  # pragma: no cover
    Array = BitVec = BitVecSort = BitVecVal = If = Select = Solver = Store = sat = None


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


def _z3_ready() -> bool:
    return Solver is not None


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


def _decrypt_prefix(candidate: bytes) -> bytes:
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
    for idx in range(5):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        ks = s[(s[i] + s[j]) & 0xFF]
        out.append(ENC_CONST[idx] ^ ks)
    return bytes(out)


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
                if dec[:5].lower() == TARGET:
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
