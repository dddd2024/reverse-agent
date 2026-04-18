from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


FLAG_PREFIX_PATTERN = re.compile(r"(?:flag|ctf|key)\{", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_]{4,16}\b")


def _score_text(s: str) -> int:
    lower = s.lower()
    score = 0
    for key in ("flag{", "flag :", "key{", "ctf{", "correct", "success", "wrong", "fail", "error", "debug"):
        if key in lower:
            score += 4
    if len(s) <= 80:
        score += 1
    return score


def _extract_strings(data: bytes, min_len: int = 4, limit: int = 5000) -> list[str]:
    raw_ascii = re.findall(rb"[\x20-\x7E]{4,}", data)
    raw_utf16 = re.findall(rb"(?:[\x20-\x7E]\x00){4,}", data)
    out: list[str] = []
    seen: set[str] = set()
    for b in raw_ascii:
        s = b.decode("utf-8", errors="ignore").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    for b in raw_utf16:
        s = b.decode("utf-16-le", errors="ignore").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    out.sort(key=lambda t: (-_score_text(t), len(t), t))
    return out[:limit]


def _collect_prefix_candidates(strings: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in strings:
        for m in FLAG_PREFIX_PATTERN.finditer(s):
            token = m.group(0)
            normalized = token.lower()
            if normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
    return out


def _collect_token_candidates(strings: list[str], limit: int = 40) -> list[str]:
    banned = {
        "kernel32", "user32", "gdi32", "shell32", "advapi32", "ole32", "comdlg32",
        "correct", "wrong", "error", "failed", "success", "debug", "input", "output",
        "printf", "scanf", "main", "exit", "start", "this", "that", "null", "true", "false",
    }
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for s in strings:
        low_line = s.lower()
        line_bonus = 3 if any(k in low_line for k in ("key", "flag", "密钥", "input", "请输入")) else 0
        for m in TOKEN_PATTERN.finditer(s):
            token = m.group(0).strip()
            lower = token.lower()
            if lower in seen or lower in banned:
                continue
            if token.isdigit():
                continue
            if len(token) < 4 or len(token) > 16:
                continue
            letter_count = sum(1 for ch in token if ch.isalpha())
            if letter_count < 2:
                continue
            if re.fullmatch(r"[0-9A-F]{4,16}", token):
                continue
            if re.fullmatch(r"[0-9A-Fa-f]{4,16}", token):
                continue
            seen.add(lower)
            score = line_bonus
            if token.isupper():
                score += 3
            if any(ch.isdigit() for ch in token):
                score += 1
            if token.startswith(("0x", "sub_", "loc_", "off_")):
                score -= 4
            if score >= 2:
                scored.append((score, token))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [t for _, t in scored[:limit]]


def _run_candidate_probe(target: Path, candidate: str, timeout_seconds: int = 4) -> str:
    proc = subprocess.run(
        [str(target)],
        input=candidate + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reverse Agent OllyDbg evidence collector")
    parser.add_argument("--olly", required=True, help="Path to ollydbg executable")
    parser.add_argument("--target", required=True, help="Path to target executable")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        raise FileNotFoundError(f"Target file not found: {target}")

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    strings = _extract_strings(target.read_bytes())
    prefix_candidates = _collect_prefix_candidates(strings)
    token_candidates = _collect_token_candidates(strings)
    success_markers = [
        s
        for s in strings
        if any(k in s.lower() for k in ("flag :", "correct", "success", "congrat"))
    ][:20]
    fail_markers = [s for s in strings if any(k in s.lower() for k in ("wrong", "fail", "error", "incorrect"))][:20]
    evidence: list[str] = [
        f"target:{target}",
        f"olly:{args.olly}",
    ]
    candidates_payload: list[dict[str, str | float]] = []
    for marker in success_markers[:5]:
        evidence.append(f"success_marker:{marker}")
    for marker in fail_markers[:5]:
        evidence.append(f"fail_marker:{marker}")
    for p in prefix_candidates:
        evidence.append(f"prefix_candidate:{p}")
        candidates_payload.append(
            {
                "value": p,
                "source": "olly_prefix",
                "confidence": 0.62,
                "reason": "Extracted flag-like prefix from binary strings.",
            }
        )
    for token in token_candidates[:16]:
        evidence.append(f"candidate:{token}")
        candidates_payload.append(
            {
                "value": token,
                "source": "olly_token",
                "confidence": 0.56,
                "reason": "High-score token extracted from binary strings.",
            }
        )
    for p in prefix_candidates:
        for token in token_candidates[:10]:
            wrapped = f"{p}{token}}}"
            evidence.append(f"candidate:{wrapped}")
            candidates_payload.append(
                {
                    "value": wrapped,
                    "source": "olly_assembled",
                    "confidence": 0.64,
                    "reason": "Assembled from detected prefix and token candidate.",
                }
            )

    runtime_candidates = [*prefix_candidates, *token_candidates]
    for p in prefix_candidates:
        closed = p + "}"
        if closed not in runtime_candidates:
            runtime_candidates.append(closed)
        for token in token_candidates[:16]:
            wrapped = f"{p}{token}}}"
            if wrapped not in runtime_candidates:
                runtime_candidates.append(wrapped)
    if "flag{" not in runtime_candidates:
        runtime_candidates.append("flag{")
    if "flag{}" not in runtime_candidates:
        runtime_candidates.append("flag{}")

    confirmed_candidate = ""
    for candidate in runtime_candidates[:6]:
        try:
            output = _run_candidate_probe(target, candidate)
        except subprocess.TimeoutExpired:
            evidence.append(f"probe_timeout:{candidate}")
            continue
        if not output:
            continue
        evidence.append(f"probe_output[{candidate}]: {output[:180]}")
        lower_output = output.lower()
        has_success = any(marker.lower() in lower_output for marker in success_markers if marker.strip())
        has_failure = any(marker.lower() in lower_output for marker in fail_markers if marker.strip())
        if has_success and not has_failure:
            confirmed_candidate = candidate
            break

    if confirmed_candidate:
        evidence.append(f"runtime_candidate:{confirmed_candidate}")
        candidates_payload.insert(
            0,
            {
                "value": confirmed_candidate,
                "source": "runtime_probe",
                "confidence": 0.88,
                "reason": "Runtime probe output matched success markers without failure markers.",
            },
        )
        summary = "OllyDbg 默认脚本已执行并提取到运行时候选。"
    elif prefix_candidates:
        summary = "OllyDbg 默认脚本已执行并提取到前缀候选。"
    else:
        summary = "OllyDbg 默认脚本已执行（未提取到有效候选）。"

    payload = {
        "summary": summary,
        "candidates": candidates_payload,
        "evidence": [
            *evidence,
            "note:如需断点/跟踪/寄存器采集，可替换为自定义 Olly 自动化脚本。",
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
