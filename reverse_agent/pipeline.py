from __future__ import annotations

import hashlib
import itertools
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from .advanced_solvers import solve_with_angr_stdin
from .dynamic_templates import get_analysis_template
from .models import CopilotCliBackend, LocalOpenAIBackend, ModelError
from .sample_solver import run_samplereverse_resumable_search
from .skills import get_ctf_reverse_skill_lines
from .tool_runners import ToolAutomationConfig, ToolRunArtifact, run_tool_automation

LogFn = Callable[[str], None]


def _read_int_env(name: str, default: int, min_value: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


def _read_float_env(name: str, default: float, min_value: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


FLAG_PATTERNS = [
    re.compile(r"flag\{[A-Za-z0-9_!@#$%^&*+=:;,.?/\-]{1,120}\}", re.IGNORECASE),
    re.compile(r"ctf\{[A-Za-z0-9_!@#$%^&*+=:;,.?/\-]{1,120}\}", re.IGNORECASE),
    re.compile(r"key\{[A-Za-z0-9_!@#$%^&*+=:;,.?/\-]{1,120}\}", re.IGNORECASE),
]
FLAG_PREFIX_PATTERN = re.compile(r"(?:flag|ctf|key)\{", re.IGNORECASE)
PLAIN_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{4,80}$")
HEX32_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")
RUNTIME_HINT_PATTERNS = [
    re.compile(r"isdebuggerpresent", re.IGNORECASE),
    re.compile(r"checkremotedebuggerpresent", re.IGNORECASE),
    re.compile(r"beingdebugged", re.IGNORECASE),
    re.compile(r"ntglobalflag", re.IGNORECASE),
    re.compile(r"outputdebugstring", re.IGNORECASE),
    re.compile(r"virtualprotect", re.IGNORECASE),
    re.compile(r"unpack", re.IGNORECASE),
    re.compile(r"decrypt", re.IGNORECASE),
]
NEGATIVE_ANSWER_TOKENS = {"NOT_FOUND", "UNKNOWN", "N/A", "NONE", "NULL"}
PLACEHOLDER_PATTERNS = [
    re.compile(r"^(?:flag|ctf|key)\{\.\.\.\}$", re.IGNORECASE),
    re.compile(r"^(?:flag|ctf|key)\{[?*]{1,20}\}$", re.IGNORECASE),
]
PLACEHOLDER_HINT_PATTERN = re.compile(r"(?:占位|示例|example|猜测|可能|最稳妥)", re.IGNORECASE)


@dataclass
class SolveResult:
    input_value: str
    resolved_path: str
    analysis_mode: str
    model_name: str
    candidates: list[str]
    selected_flag: str
    prompt: str
    model_output: str
    extracted_strings_count: int
    tool_artifacts: list[ToolRunArtifact]
    candidate_validations: list[dict[str, str]] = field(default_factory=list)
    report_path: str = ""


def _escape_runtime_text(value: str) -> str:
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "\r":
            out.append("\\r")
        elif ch == "\n":
            out.append("\\n")
        elif 0x20 <= code <= 0x7E or 0x4E00 <= code <= 0x9FFF:
            out.append(ch)
        else:
            out.append(f"\\x{code:02x}")
    return "".join(out)


def _candidate_to_gui_text(candidate: str) -> str:
    try:
        raw = candidate.encode("latin1", errors="ignore")
    except Exception:
        return candidate
    out: list[str] = []
    for b in raw:
        if 0x20 <= b <= 0x7E:
            out.append(chr(b))
        elif b == 0:
            continue
        else:
            # Preserve the low byte seen by the target while avoiding control
            # characters that Edit controls may swallow or normalize.
            out.append(chr(0x0100 | b))
    return "".join(out)


def _looks_like_samplereverse(strings: list[str], file_path: Path) -> bool:
    if "samplereverse" in file_path.name.lower():
        return True
    return any("输入的密钥是" in s for s in strings[:3000]) and any(
        "密钥不正确" in s for s in strings[:3000]
    )


def _probe_gui_runtime_outputs(
    file_path: Path,
    strings: list[str],
    seed_candidates: list[str],
    per_action_delay: float = 0.18,
) -> ToolRunArtifact | None:
    if not _is_windows_gui_exe(file_path):
        return None
    if not _looks_like_samplereverse(strings, file_path):
        return None

    try:
        from pywinauto import Application
    except ImportError:
        return ToolRunArtifact(
            tool_name="GUIProbe",
            enabled=False,
            attempted=False,
            success=False,
            summary="GUI 运行时证据采集未执行（缺少 pywinauto）。",
            error="缺少 pywinauto。",
        )

    probe_inputs = ["AAAAAAA", "flag{"]
    for candidate in seed_candidates[:4]:
        normalized = candidate.strip()
        if not normalized or normalized in probe_inputs:
            continue
        probe_inputs.append(normalized)
    probe_inputs = probe_inputs[:4]

    artifact = ToolRunArtifact(
        tool_name="GUIProbe",
        enabled=True,
        attempted=True,
        success=False,
        summary="GUI 运行时证据采集中。",
    )
    evidence: list[str] = []
    try:
        app = Application(backend="uia").start(str(file_path))
    except Exception as exc:
        artifact.summary = "GUI 运行时证据采集启动失败。"
        artifact.error = str(exc)
        return artifact

    try:
        time.sleep(1.0)
        win = app.top_window()
        input_edit = win.child_window(auto_id="1001", control_type="Edit")
        decrypt_btn = win.child_window(auto_id="1000", control_type="Button")
        output_edit = win.child_window(auto_id="1002", control_type="Edit")
        evidence.append(f"runtime_gui:title={_escape_runtime_text(win.window_text() or '')}")
        evidence.append(
            "runtime_gui:controls=button:1000,edit:1001,edit:1002"
        )
        for candidate in probe_inputs:
            input_edit.set_edit_text(_candidate_to_gui_text(candidate))
            decrypt_btn.click()
            time.sleep(per_action_delay)
            try:
                output = output_edit.get_value() or ""
            except Exception:
                output = output_edit.window_text() or ""
            evidence.append(
                f"runtime_gui:probe_input={_escape_runtime_text(candidate)}"
            )
            evidence.append(
                f"runtime_gui:probe_output={_escape_runtime_text(output[:220])}"
            )
        artifact.success = True
        artifact.summary = "GUI 运行时证据采集成功。"
        artifact.evidence = evidence
        return artifact
    except Exception as exc:
        artifact.summary = "GUI 运行时证据采集失败。"
        artifact.error = str(exc)
        artifact.evidence = evidence
        return artifact
    finally:
        try:
            app.kill()
        except Exception:
            pass


def is_url(value: str) -> bool:
    parts = urlparse(value.strip())
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def resolve_input(input_value: str, workspace_dir: Path, log: LogFn) -> Path:
    value = input_value.strip()
    if not value:
        raise ValueError("Input is empty.")

    if is_url(value):
        workspace_dir.mkdir(parents=True, exist_ok=True)
        name = Path(urlparse(value).path).name or "downloaded.bin"
        target = workspace_dir / name
        log(f"正在下载文件: {value}")
        with requests.get(value, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            with target.open("wb") as out:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        out.write(chunk)
        log(f"下载完成: {target}")
        return target

    path = Path(value)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path


def extract_strings(
    file_path: Path, min_length: int = 4, max_items: int = 6000
) -> list[str]:
    data = file_path.read_bytes()
    strings: list[str] = []

    ascii_pattern = rb"[\x20-\x7E]{" + str(min_length).encode() + rb",}"
    for m in re.finditer(ascii_pattern, data):
        strings.append(m.group(0).decode("utf-8", errors="ignore"))
        if len(strings) >= max_items:
            break

    # Also capture UTF-16LE strings (common in Windows GUI binaries).
    if len(strings) < max_items:
        i = 0
        data_len = len(data)
        while i + 1 < data_len and len(strings) < max_items:
            j = i
            chars: list[str] = []
            while j + 1 < data_len:
                cp = data[j] | (data[j + 1] << 8)
                if cp == 0:
                    break
                if not (
                    0x20 <= cp <= 0x7E
                    or 0x4E00 <= cp <= 0x9FFF
                    or cp in {0x3002, 0x3001, 0xFF0C, 0xFF1A, 0xFF01, 0xFF08, 0xFF09}
                ):
                    break
                chars.append(chr(cp))
                j += 2
            if len(chars) >= min_length:
                text = "".join(chars).strip()
                if text:
                    strings.append(text)
                i = j + 2
            else:
                i += 2
    # keep order, remove duplicates
    seen: set[str] = set()
    deduped: list[str] = []
    for s in strings:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _choose_prompt_budget(
    strings_count: int, tool_evidence_count: int, analysis_mode: str
) -> tuple[int, int]:
    if strings_count > 4000 or tool_evidence_count > 300:
        return (180, 80)
    if strings_count > 1800 or tool_evidence_count > 180:
        return (260, 120)
    if analysis_mode == "Dynamic Debug":
        return (360, 150)
    return (500, 200)


def _resolve_analysis_mode(
    requested_mode: str,
    strings: list[str],
    pre_candidates: list[str],
    tool_config: ToolAutomationConfig,
    log: LogFn,
) -> str:
    if requested_mode in {"Static Analysis", "Dynamic Debug"}:
        return requested_mode

    # Auto mode: prefer static when already finding strong local flag patterns.
    if pre_candidates:
        log("自动模式判定：已发现静态候选，使用 Static Analysis。")
        return "Static Analysis"

    runtime_hint_count = 0
    for s in strings[:1200]:
        if any(p.search(s) for p in RUNTIME_HINT_PATTERNS):
            runtime_hint_count += 1
            if runtime_hint_count >= 3:
                break

    olly_ready = bool(
        tool_config.ollydbg_enabled
        or (
            tool_config.ollydbg_executable.strip()
            and tool_config.ollydbg_script_path.strip()
        )
    )
    olly_script_path = (tool_config.ollydbg_script_path or "").strip().lower().replace("/", "\\")
    using_default_olly_script = (not olly_script_path) or olly_script_path.endswith(
        "\\olly_scripts\\collect_evidence.py"
    )
    if olly_ready and not pre_candidates and not using_default_olly_script:
        log("自动模式判定：未发现强静态候选且 OllyDbg 可用，优先使用 Dynamic Debug。")
        return "Dynamic Debug"
    if runtime_hint_count >= 2 and olly_ready:
        log("自动模式判定：检测到运行时/反调试线索且 OllyDbg 可用，使用 Dynamic Debug。")
        return "Dynamic Debug"
    if runtime_hint_count >= 3:
        log("自动模式判定：检测到明显运行时/反调试线索，使用 Dynamic Debug。")
        return "Dynamic Debug"

    log("自动模式判定：默认使用 Static Analysis。")
    return "Static Analysis"


def _extract_flag_prefix_hint(value: str) -> str:
    for line in value.splitlines():
        candidate = line.strip().strip("`").strip("'").strip('"')
        lower = candidate.lower()
        if lower in {"flag{", "ctf{", "key{"}:
            return candidate
    m = re.search(r"`(flag\{|ctf\{|key\{)`", value, flags=re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _is_placeholder_candidate(value: str) -> bool:
    candidate = (value or "").strip().strip("`").strip("'").strip('"')
    if not candidate:
        return False
    if any(p.fullmatch(candidate) for p in PLACEHOLDER_PATTERNS):
        return True
    if "..." in candidate and candidate.lower().startswith(("flag{", "ctf{", "key{")):
        return True
    if PLACEHOLDER_HINT_PATTERN.search(candidate):
        return True
    return False


def _is_prefix_only_candidate(value: str) -> bool:
    normalized = _normalize_candidate(value).lower()
    return normalized in {"flag{", "ctf{", "key{"}


def _normalize_candidate(value: str) -> str:
    return (value or "").strip().strip("`").strip("'").strip('"')


def _escape_control_for_prompt(value: str) -> str:
    out: list[str] = []
    for ch in (value or ""):
        code = ord(ch)
        if ch in {"\n", "\r", "\t"}:
            out.append(" ")
        elif code == 0:
            out.append("\\x00")
        elif 0x00 <= code < 0x20 or code == 0x7F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def find_flag_candidates(texts: list[str]) -> list[str]:
    joined = "\n".join(texts)
    hits: list[str] = []
    seen: set[str] = set()
    for pat in FLAG_PATTERNS:
        for m in pat.finditer(joined):
            v = m.group(0).strip()
            if v not in seen:
                seen.add(v)
                hits.append(v)
    return hits


def find_prefix_candidates(texts: list[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for m in FLAG_PREFIX_PATTERN.finditer(text):
            value = m.group(0).lower()
            if value not in seen:
                seen.add(value)
                hits.append(value)
    return hits


def find_binary_prefix_candidates(file_path: Path) -> list[str]:
    raw = file_path.read_bytes().lower()
    hits: list[str] = []
    for token in ("flag{", "ctf{", "key{"):
        ascii_hit = token.encode("ascii")
        wide_hit = b"".join(bytes((ch, 0x00)) for ch in ascii_hit)
        if ascii_hit in raw or wide_hit in raw:
            hits.append(token)
    return hits


def _extract_upper_token_candidates(strings: list[str], limit: int = 180) -> list[str]:
    values: list[tuple[int, str]] = []
    seen: set[str] = set()
    for s in strings[:2200]:
        if not re.fullmatch(r"[A-Z0-9]{4,12}", s):
            continue
        if s in seen:
            continue
        seen.add(s)
        letter_count = sum(1 for ch in s if ch.isalpha())
        if letter_count < 2:
            continue
        if len(set(s)) <= 2:
            continue
        if re.fullmatch(r"[PQSVW0-9]{4,12}", s):
            continue
        if re.fullmatch(r"[A-F0-9]{4,12}", s):
            continue
        score = 0
        if s.isupper():
            score += 3
        if any(ch.isdigit() for ch in s):
            score += 1
        if len(s) in {4, 5, 6, 8}:
            score += 1
        values.append((score, s))
    values.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [s for _, s in values[:limit]]


def _extract_tool_candidates(tool_evidence: list[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    marker_patterns = [
        re.compile(r"(?:runtime_candidate|prefix_candidate|candidate)\s*[:：]\s*(\S+)", re.IGNORECASE),
    ]
    for line in tool_evidence:
        for pat in FLAG_PATTERNS:
            for m in pat.finditer(line):
                v = m.group(0).strip()
                if v and v not in seen:
                    seen.add(v)
                    candidates.append(v)
        for pat in marker_patterns:
            m = pat.search(line)
            if not m:
                continue
            token = m.group(1).strip().strip("`").strip("'").strip('"')
            if _is_placeholder_candidate(token):
                continue
            if token.lower() in {"flag{", "ctf{", "key{"}:
                if token not in seen:
                    seen.add(token)
                    candidates.append(token)
                continue
            if PLAIN_TOKEN_PATTERN.fullmatch(token) and token not in seen:
                seen.add(token)
                candidates.append(token)
    return candidates


def build_prompt(
    file_path: Path,
    strings: list[str],
    pre_candidates: list[str],
    analysis_mode: str,
    tool_evidence: list[str] | None = None,
    max_string_lines: int = 500,
    max_tool_evidence_lines: int = 200,
    ctf_skill_enabled: bool = True,
    ctf_skill_profile: str = "compact",
) -> str:
    head = strings[:max_string_lines]
    safe_head = [_escape_control_for_prompt(item) for item in head]
    safe_candidates = [_escape_control_for_prompt(item) for item in pre_candidates]
    safe_tool_evidence = (
        [_escape_control_for_prompt(item) for item in tool_evidence[:max_tool_evidence_lines]]
        if tool_evidence
        else ["- <none>"]
    )
    template = get_analysis_template(analysis_mode)
    skill_lines = (
        get_ctf_reverse_skill_lines(analysis_mode=analysis_mode, profile=ctf_skill_profile)
        if ctf_skill_enabled
        else []
    )
    lines = [
        "你是逆向工程解题助手，请根据证据推断最终 flag。",
        f"文件名: {file_path.name}",
        f"文件路径: {file_path}",
        f"分析模式: {analysis_mode}",
        "",
        template,
        *(
            [
                "",
                "CTF逆向Skill增强（项目内自定义，参考公开资料白名单化整理）:",
                *[f"- {line}" for line in skill_lines],
            ]
            if skill_lines
            else []
        ),
        "",
        "本地预检测 flag 候选:",
        *([f"- {c}" for c in safe_candidates] or ["- <none>"]),
        "",
        "可打印字符串样本:",
        *safe_head,
        "",
        "工具证据样本:",
        *safe_tool_evidence,
        "",
        "任务:",
        "1) 推断最可能的最终 flag。",
        "2) 第一行只输出一个 flag。",
        "3) 如果不是 flag{} 格式，也必须在第一行只输出答案本体（例如 SEPTA）。",
        "4) 从第二行开始请按优秀 CTF writeup 风格输出，且对新手友好：",
        "   - 思路概览（先讲目标与路线）",
        "   - 关键证据（字符串/函数/分支/常量）",
        "   - 逐步推导（从证据到答案）",
        "   - 易错点与如何自查",
        "5) 不要编造未出现的工具输出或地址。",
    ]
    return "\n".join(lines)


def _extract_first_flag(value: str) -> str:
    for pat in FLAG_PATTERNS:
        m = pat.search(value)
        if m:
            v = m.group(0)
            if _is_placeholder_candidate(v):
                continue
            return v
    return ""


def _extract_best_answer_line(value: str) -> str:
    best = ""
    best_score = -10**9

    pattern_extractors = [
        re.compile(r"(?:最短可通过输入为|最终答案为|flag为|答案为)\s*[:：]?\s*([A-Za-z0-9_\-]{4,80})"),
        re.compile(r"(?:input(?: is)?|answer(?: is)?)\s*[:：]?\s*([A-Za-z0-9_\-]{4,80})", re.IGNORECASE),
    ]
    for pat in pattern_extractors:
        m = pat.search(value)
        if m:
            token = m.group(1).strip()
            if PLAIN_TOKEN_PATTERN.fullmatch(token) and not _is_placeholder_candidate(token):
                return token

    for idx, line in enumerate(value.splitlines()):
        candidate = line.strip().strip("`").strip("'").strip('"')
        if not candidate or not PLAIN_TOKEN_PATTERN.fullmatch(candidate):
            continue
        if _is_placeholder_candidate(candidate):
            continue
        score = 100 - idx
        if HEX32_PATTERN.fullmatch(candidate):
            score -= 60
        if candidate.isupper():
            score += 20
        if 4 <= len(candidate) <= 12:
            score += 15
        if score > best_score:
            best = candidate
            best_score = score
    return best


def _is_negative_answer(value: str) -> bool:
    return value.strip().upper() in NEGATIVE_ANSWER_TOKENS


def _find_md5_literals(texts: list[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    pat = re.compile(r"\b[a-fA-F0-9]{32}\b")
    for s in texts:
        for m in pat.finditer(s):
            v = m.group(0).lower()
            if v not in seen:
                seen.add(v)
                hits.append(v)
    return hits


def _is_flag_like(value: str) -> bool:
    return any(p.fullmatch(value.strip()) for p in FLAG_PATTERNS)


def _collect_runtime_markers(strings: list[str], tool_evidence: list[str]) -> tuple[list[str], list[str]]:
    corpus = [*(strings[:1200]), *tool_evidence]
    success_markers = [
        "correct!",
        "correct",
        "success",
        "flag :",
        "请输入的密钥是",
    ]
    fail_markers = [
        "密钥不正确",
        "incorrect",
        "wrong",
        "failed",
        "error",
    ]
    for item in corpus:
        lower = item.lower()
        if any(k in lower for k in ("correct", "success", "flag :")) and item not in success_markers:
            success_markers.append(item)
        if any(k in lower for k in ("wrong", "fail", "error", "incorrect", "不正确")) and item not in fail_markers:
            fail_markers.append(item)
    return success_markers[:20], fail_markers[:20]


def _rank_candidates(
    selected_flag: str,
    pre_candidates: list[str],
    prefix_candidates: list[str],
    tool_candidates: list[str],
    angr_candidates: list[str],
    model_flag: str,
    model_prefix_hint: str,
    model_best_answer: str,
    recovered_tokens: list[str],
) -> list[tuple[str, int]]:
    scores: dict[str, int] = {}
    prefix_set = {
        _normalize_candidate(item).lower()
        for item in prefix_candidates
        if _normalize_candidate(item)
    }

    def add(candidate: str, score: int) -> None:
        value = _normalize_candidate(candidate)
        if not value:
            return
        if _is_placeholder_candidate(value):
            return
        scores[value] = scores.get(value, 0) + score

    add(selected_flag, 20)
    for c in pre_candidates:
        add(c, 60)
    for c in prefix_candidates:
        add(c, 70)
    for c in tool_candidates:
        add(c, 55)
    for c in angr_candidates:
        add(c, 90)
    add(model_flag, 45)
    add(model_prefix_hint, 35)
    if not _is_negative_answer(model_best_answer):
        add(model_best_answer, 25)
    for c in recovered_tokens:
        add(c, 30)

    for value in list(scores.keys()):
        if _is_negative_answer(value):
            scores[value] -= 200
        if _is_placeholder_candidate(value):
            scores[value] -= 500
        if _is_prefix_only_candidate(value):
            if value.lower() in prefix_set:
                scores[value] += 30
            else:
                scores[value] -= 80
        if _is_flag_like(value):
            scores[value] += 20
        if 4 <= len(value) <= 80:
            scores[value] += 3

    ranked = sorted(scores.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    return ranked


def _crack_md5_upper4(md5_hex: str) -> str:
    target = md5_hex.lower()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for chars in itertools.product(alphabet, repeat=4):
        token = "".join(chars)
        if hashlib.md5(token.encode("ascii")).hexdigest() == target:
            return token
    return ""


def _validate_candidate_with_exe(
    file_path: Path,
    candidate: str,
    success_markers: list[str],
    fail_markers: list[str],
    timeout_seconds: int = 5,
) -> tuple[bool, str]:
    if not candidate or _is_placeholder_candidate(candidate):
        return False, ""
    proc = subprocess.run(
        [str(file_path)],
        input=candidate + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    lower = output.lower()
    has_success = any(m.lower() in lower for m in success_markers if m)
    has_fail = any(m.lower() in lower for m in fail_markers if m)
    if has_success and not has_fail:
        return True, output[:220]
    return False, output[:220]


def _validate_candidates_with_gui_session(
    file_path: Path,
    candidates: list[str],
    success_markers: list[str],
    fail_markers: list[str],
    per_action_delay: float = 0.12,
) -> tuple[str, list[dict[str, str]]]:
    try:
        from pywinauto import Application
    except ImportError as exc:
        raise RuntimeError(
            "GUI 动态校验依赖 pywinauto，当前环境未安装。"
        ) from exc

    app = Application(backend="uia").start(str(file_path))
    records: list[dict[str, str]] = []
    selected = ""
    try:
        time.sleep(0.9)
        win = app.top_window()
        input_edit = win.child_window(auto_id="1001", control_type="Edit")
        decrypt_btn = win.child_window(auto_id="1000", control_type="Button")
        output_edit = win.child_window(auto_id="1002", control_type="Edit")
        merged_fail_markers = [*fail_markers, "密钥不正确", "wrong", "incorrect", "error"]
        for cand in candidates:
            input_edit.set_edit_text(_candidate_to_gui_text(cand))
            decrypt_btn.click()
            time.sleep(per_action_delay)
            try:
                output = output_edit.get_value() or ""
            except Exception:
                output = output_edit.window_text() or ""
            lower = output.lower()
            has_success = any(m.lower() in lower for m in success_markers if m)
            has_fail = any(m.lower() in lower for m in merged_fail_markers if m)
            ok = has_success and not has_fail
            records.append(
                {
                    "candidate": cand,
                    "validated": "yes" if ok else "no",
                    "evidence": output[:220],
                }
            )
            if ok:
                selected = cand
                break
    finally:
        app.kill()
    return selected, records


def _is_windows_gui_exe(file_path: Path) -> bool:
    try:
        data = file_path.read_bytes()[:2048]
        if len(data) < 0x40 or data[:2] != b"MZ":
            return False
        pe_off = int.from_bytes(data[0x3C:0x40], "little")
        if pe_off + 0x5E >= len(data):
            # Need full header if offset is beyond initial slice.
            data = file_path.read_bytes()[: max(pe_off + 0x60, 4096)]
        if data[pe_off : pe_off + 4] != b"PE\x00\x00":
            return False
        optional_header_off = pe_off + 24
        subsystem_off = optional_header_off + 0x44
        subsystem = int.from_bytes(data[subsystem_off : subsystem_off + 2], "little")
        return subsystem == 2  # IMAGE_SUBSYSTEM_WINDOWS_GUI
    except Exception:
        return False


def run_pipeline(
    input_value: str,
    analysis_mode: str,
    model_type: str,
    copilot_command: str,
    local_base_url: str,
    local_model: str,
    local_api_key: str,
    tool_config: ToolAutomationConfig,
    runtime_validation_enabled: bool,
    reports_dir: Path,
    log: LogFn,
    copilot_timeout_seconds: int = 300,
    ctf_skill_enabled: bool = True,
    ctf_skill_profile: str = "compact",
) -> SolveResult:
    workdir = Path(tempfile.gettempdir()) / "reverse_agent_downloads"
    file_path = resolve_input(input_value, workdir, log)

    # User requirement: input should be exe file or its path/url.
    if file_path.suffix.lower() != ".exe":
        log("警告: 输入文件不是 .exe，仍继续执行。")

    log("正在提取可打印字符串...")
    strings = extract_strings(file_path)
    pre_candidates = find_flag_candidates(strings)
    string_prefix_candidates = find_prefix_candidates(strings)
    binary_prefix_candidates = find_binary_prefix_candidates(file_path)
    prefix_candidates = [
        *string_prefix_candidates,
        *[item for item in binary_prefix_candidates if item not in string_prefix_candidates],
    ]
    if prefix_candidates:
        pre_candidates = [
            *pre_candidates,
            *[c for c in prefix_candidates if c not in pre_candidates],
        ]
    token_candidates = _extract_upper_token_candidates(strings)
    if token_candidates:
        pre_candidates = [*pre_candidates, *[c for c in token_candidates if c not in pre_candidates]]
        log(f"静态字符串中提取到口令型候选 {len(token_candidates)} 个。")
    resolved_analysis_mode = _resolve_analysis_mode(
        requested_mode=analysis_mode,
        strings=strings,
        pre_candidates=pre_candidates,
        tool_config=tool_config,
        log=log,
    )
    analysis_mode = resolved_analysis_mode
    artifacts_dir = reports_dir / "tool_artifacts" / file_path.stem
    tool_artifacts = run_tool_automation(
        file_path=file_path,
        analysis_mode=analysis_mode,
        config=tool_config,
        artifacts_dir=artifacts_dir,
        log=log,
    )
    tool_evidence = [line for a in tool_artifacts for line in a.evidence]
    tool_candidates = _extract_tool_candidates(tool_evidence)
    gui_probe_artifact = _probe_gui_runtime_outputs(
        file_path=file_path,
        strings=strings,
        seed_candidates=pre_candidates,
    )
    if gui_probe_artifact:
        tool_artifacts.append(gui_probe_artifact)
        tool_evidence.extend(gui_probe_artifact.evidence)
        gui_probe_candidates = _extract_tool_candidates(gui_probe_artifact.evidence)
        for candidate in gui_probe_candidates:
            if candidate not in tool_candidates:
                tool_candidates.append(candidate)
    sample_probe_result = run_samplereverse_resumable_search(
        file_path=file_path,
        strings=strings,
        seed_candidates=pre_candidates,
        artifacts_dir=artifacts_dir,
        log=log,
        max_attempts=_read_int_env(
            "REVERSE_AGENT_SAMPLE_MAX_ATTEMPTS", default=250_000, min_value=10_000
        ),
        max_seconds=_read_float_env(
            "REVERSE_AGENT_SAMPLE_MAX_SECONDS", default=6 * 60 * 60, min_value=30.0
        ),
        random_seed=_read_int_env(
            "REVERSE_AGENT_SAMPLE_RANDOM_SEED", default=1337, min_value=1
        ),
    )
    hard_stop_due_deadline = False
    if sample_probe_result.enabled:
        probe_artifact = ToolRunArtifact(
            tool_name="SampleProbe",
            enabled=True,
            attempted=True,
            success=True,
            summary=sample_probe_result.summary,
            output_path=str(artifacts_dir / "samplereverse_search_checkpoint.json"),
            evidence=sample_probe_result.evidence,
        )
        tool_artifacts.append(probe_artifact)
        tool_evidence.extend(sample_probe_result.evidence)
        for candidate in sample_probe_result.candidates:
            if candidate not in pre_candidates:
                pre_candidates.append(candidate)
        probe_candidates = _extract_tool_candidates(sample_probe_result.evidence)
        for candidate in probe_candidates:
            if candidate not in tool_candidates:
                tool_candidates.append(candidate)
        hard_stop_due_deadline = any(
            line.strip() == "runtime_probe:deadline_reached=1"
            for line in sample_probe_result.evidence
        )
        if hard_stop_due_deadline:
            log("SampleProbe 达到截止时间，后续流程按硬截止策略终止（跳过 angr/模型/运行时校验）。")
    olly_ready = bool(
        tool_config.ollydbg_enabled
        or tool_config.ollydbg_executable.strip()
    )
    if (
        analysis_mode == "Static Analysis"
        and not tool_candidates
        and tool_config.enabled
        and olly_ready
    ):
        log("静态阶段未提取到强工具候选，自动追加一次 Olly 动态证据采集。")
        dynamic_tool_config = ToolAutomationConfig(
            enabled=True,
            ida_enabled=False,
            ida_executable=tool_config.ida_executable,
            ida_script_path=tool_config.ida_script_path,
            ida_timeout_seconds=tool_config.ida_timeout_seconds,
            ollydbg_enabled=True,
            ollydbg_executable=tool_config.ollydbg_executable,
            ollydbg_script_path=tool_config.ollydbg_script_path,
            ollydbg_timeout_seconds=tool_config.ollydbg_timeout_seconds,
        )
        extra_artifacts = run_tool_automation(
            file_path=file_path,
            analysis_mode="Dynamic Debug",
            config=dynamic_tool_config,
            artifacts_dir=artifacts_dir,
            log=log,
        )
        if extra_artifacts:
            tool_artifacts.extend(extra_artifacts)
            extra_evidence = [line for a in extra_artifacts for line in a.evidence]
            tool_evidence.extend(extra_evidence)
            extra_candidates = _extract_tool_candidates(extra_evidence)
            if extra_candidates:
                tool_candidates.extend([c for c in extra_candidates if c not in tool_candidates])
    for artifact in tool_artifacts:
        log(f"{artifact.tool_name}: {artifact.summary}")
        if artifact.error:
            log(f"{artifact.tool_name} 错误: {artifact.error}")
    if tool_candidates:
        log(f"工具证据中提取到候选 {len(tool_candidates)} 个。")
        pre_candidates = [
            *pre_candidates,
            *[c for c in tool_candidates if c not in pre_candidates],
        ]
    success_markers, fail_markers = _collect_runtime_markers(strings, tool_evidence)
    angr_candidates: list[str] = []
    if not hard_stop_due_deadline:
        angr_candidates = solve_with_angr_stdin(
            file_path=file_path,
            success_markers=success_markers,
            fail_markers=fail_markers,
            max_input_len=32,
            timeout_seconds=70,
            log=log,
        )
    else:
        log("已跳过 angr 后备求解（达到样本截止时间）。")
    if angr_candidates:
        pre_candidates = [
            *pre_candidates,
            *[c for c in angr_candidates if c not in pre_candidates],
        ]

    max_string_lines, max_tool_evidence_lines = _choose_prompt_budget(
        strings_count=len(strings),
        tool_evidence_count=len(tool_evidence),
        analysis_mode=analysis_mode,
    )
    if max_string_lines < 500 or max_tool_evidence_lines < 200:
        log(
            "检测到证据规模较大，已启用自适应精简提示词。"
            f"（字符串上限 {max_string_lines}，工具证据上限 {max_tool_evidence_lines}）"
        )

    prompt = build_prompt(
        file_path,
        strings,
        pre_candidates,
        analysis_mode,
        tool_evidence=tool_evidence,
        max_string_lines=max_string_lines,
        max_tool_evidence_lines=max_tool_evidence_lines,
        ctf_skill_enabled=ctf_skill_enabled,
        ctf_skill_profile=ctf_skill_profile,
    )

    if pre_candidates:
        log(f"在模型分析前已发现本地候选 {len(pre_candidates)} 个。")

    model_output = ""
    selected_flag = pre_candidates[0] if pre_candidates else ""
    runtime_validation_enabled_effective = (
        runtime_validation_enabled and not hard_stop_due_deadline
    )

    if model_type == "Copilot CLI":
        backend = CopilotCliBackend(
            command_template=copilot_command,
            timeout_seconds=copilot_timeout_seconds,
        )
        model_name = f"Copilot CLI ({copilot_command})"
    elif model_type == "Local Model":
        backend = LocalOpenAIBackend(
            base_url=local_base_url, model=local_model, api_key=local_api_key
        )
        model_name = f"Local Model ({local_model})"
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

    skip_model_for_mass_validation = (
        runtime_validation_enabled_effective
        and file_path.suffix.lower() == ".exe"
        and len(pre_candidates) >= 120
        and not tool_candidates
        and not angr_candidates
    )
    if hard_stop_due_deadline:
        log("已跳过模型调用（达到样本截止时间）。")
    elif skip_model_for_mass_validation:
        log("候选规模较大且将执行动态校验，跳过模型调用以优先本地验证。")
    else:
        log(f"正在调用模型: {model_name}")
        try:
            model_output = backend.solve(prompt)
        except ModelError as exc:
            # Retry once with a compact prompt when Copilot times out on large context.
            if model_type == "Copilot CLI" and "Copilot CLI call timed out." in str(exc):
                log("Copilot 调用超时，正在使用精简证据重试一次...")
                compact_prompt = build_prompt(
                    file_path,
                    strings,
                    pre_candidates,
                    analysis_mode,
                    tool_evidence=tool_evidence,
                    max_string_lines=max(100, max_string_lines // 2),
                    max_tool_evidence_lines=max(50, max_tool_evidence_lines // 2),
                    ctf_skill_enabled=ctf_skill_enabled,
                    ctf_skill_profile=ctf_skill_profile,
                )
                prompt = compact_prompt
                try:
                    model_output = backend.solve(prompt)
                except ModelError as retry_exc:
                    log(f"模型调用重试仍失败，回退本地证据候选：{retry_exc}")
                    model_output = ""
            else:
                raise
    model_flag = _extract_first_flag(model_output)
    model_prefix_hint = _extract_flag_prefix_hint(model_output)
    if model_flag:
        selected_flag = model_flag
    elif model_output.strip():
        best_answer = _extract_best_answer_line(model_output)
        if _is_negative_answer(best_answer):
            best_answer = ""
        selected_flag = (
            best_answer
            or model_prefix_hint
            or selected_flag
        )
    if not selected_flag and tool_candidates:
        selected_flag = tool_candidates[0]
    if _is_placeholder_candidate(selected_flag) or _is_prefix_only_candidate(selected_flag):
        selected_flag = ""

    # Deterministic local enhancement: if executable appears to compare MD5 digest,
    # try recovering 4-char uppercase base token and validate candidates by execution.
    md5_hits = _find_md5_literals(strings)
    recovered_tokens: list[str] = []
    for md5_value in md5_hits[:3]:
        recovered = _crack_md5_upper4(md5_value)
        if recovered:
            recovered_tokens.append(recovered)
    if recovered_tokens:
        log(f"本地恢复到 {len(recovered_tokens)} 个 MD5 前缀候选。")

    model_best_answer = _extract_best_answer_line(model_output)
    ranked_candidates = _rank_candidates(
        selected_flag=selected_flag,
        pre_candidates=pre_candidates,
        prefix_candidates=prefix_candidates,
        tool_candidates=tool_candidates,
        angr_candidates=angr_candidates,
        model_flag=model_flag,
        model_prefix_hint=model_prefix_hint,
        model_best_answer=model_best_answer,
        recovered_tokens=recovered_tokens,
    )
    candidate_pool = [c for c, _ in ranked_candidates]
    for priority in [*prefix_candidates, model_flag, model_best_answer, selected_flag]:
        candidate = _normalize_candidate(priority)
        if (
            not candidate
            or _is_placeholder_candidate(candidate)
            or _is_negative_answer(candidate)
        ):
            continue
        if candidate in candidate_pool:
            candidate_pool.remove(candidate)
        candidate_pool.insert(0, candidate)
    if ranked_candidates:
        top_preview = ", ".join(f"{c}({s})" for c, s in ranked_candidates[:3])
        log(f"候选评分Top3: {top_preview}")
    # Common pattern in this class of crackmes: uppercase check with length >= 5
    for c in list(candidate_pool):
        if len(c) == 4 and c.isupper():
            ext = c + "A"
            if ext not in candidate_pool:
                candidate_pool.append(ext)

    candidate_pool = [c for c in candidate_pool if not _is_placeholder_candidate(c)]
    validation_records: list[dict[str, str]] = []

    gui_subsystem = file_path.suffix.lower() == ".exe" and _is_windows_gui_exe(file_path)
    if (
        runtime_validation_enabled_effective
        and file_path.suffix.lower() == ".exe"
        and candidate_pool
    ):
        if gui_subsystem:
            max_gui_candidates = 60
            validation_pool = candidate_pool[:max_gui_candidates]
            log(
                "检测到 GUI 子系统 EXE，使用窗口自动化进行动态校验。"
                f"（候选上限 {len(validation_pool)}）"
            )
            try:
                chosen, gui_records = _validate_candidates_with_gui_session(
                    file_path=file_path,
                    candidates=validation_pool,
                    success_markers=success_markers,
                    fail_markers=fail_markers,
                )
                validation_records.extend(gui_records)
                if chosen:
                    log(f"GUI 动态校验通过，选定候选: {chosen}")
                    selected_flag = chosen
                else:
                    selected_flag = ""
            except RuntimeError as exc:
                log(f"GUI 动态校验不可用：{exc}")
                validation_records = [
                    {"candidate": cand, "validated": "skipped_gui", "evidence": ""}
                    for cand in validation_pool[:10]
                ]
                selected_flag = ""
        else:
            max_validation_candidates = 12
            per_candidate_timeout = 3
            total_validation_budget = 45
            validation_pool = candidate_pool[:max_validation_candidates]
            total_candidates = len(validation_pool)
            started = time.monotonic()
            for idx, cand in enumerate(validation_pool, start=1):
                if time.monotonic() - started > total_validation_budget:
                    log("运行时校验达到总预算上限，停止继续尝试。")
                    break
                log(f"运行时校验候选 {idx}/{total_candidates}: {cand}")
                try:
                    ok, out_excerpt = _validate_candidate_with_exe(
                        file_path=file_path,
                        candidate=cand,
                        success_markers=success_markers,
                        fail_markers=fail_markers,
                        timeout_seconds=per_candidate_timeout,
                    )
                    validation_records.append(
                        {
                            "candidate": cand,
                            "validated": "yes" if ok else "no",
                            "evidence": out_excerpt,
                        }
                    )
                    if ok:
                        log(f"运行时校验通过，选定候选: {cand}")
                        selected_flag = cand
                        break
                except subprocess.TimeoutExpired:
                    validation_records.append(
                        {
                            "candidate": cand,
                            "validated": "timeout",
                            "evidence": "",
                        }
                    )
                    continue
    elif file_path.suffix.lower() == ".exe" and candidate_pool:
        if hard_stop_due_deadline:
            log("已跳过运行时校验（达到样本截止时间）。")
        else:
            log("已跳过运行时校验（未启用“执行样本验证”开关）。")
        validation_records = [
            {"candidate": cand, "validated": "skipped", "evidence": ""}
            for cand in candidate_pool[:10]
        ]

    if runtime_validation_enabled_effective and candidate_pool:
        if not any(item.get("validated") == "yes" for item in validation_records):
            selected_flag = ""

    if runtime_validation_enabled_effective and _is_prefix_only_candidate(selected_flag):
        has_validated_prefix = any(
            item.get("candidate") == selected_flag and item.get("validated") == "yes"
            for item in validation_records
        )
        if not has_validated_prefix:
            selected_flag = ""

    if not runtime_validation_enabled_effective and candidate_pool:
        selected_flag = candidate_pool[0]

    if hard_stop_due_deadline:
        if candidate_pool:
            validation_records = [
                {"candidate": cand, "validated": "deadline_stop", "evidence": ""}
                for cand in candidate_pool[:10]
            ]
        selected_flag = ""

    if not selected_flag:
        selected_flag = "NOT_FOUND"

    reports_dir.mkdir(parents=True, exist_ok=True)
    from .reporter import write_report

    result = SolveResult(
        input_value=input_value,
        resolved_path=str(file_path),
        analysis_mode=analysis_mode,
        model_name=model_name,
        candidates=pre_candidates,
        selected_flag=selected_flag,
        prompt=prompt,
        model_output=model_output,
        extracted_strings_count=len(strings),
        tool_artifacts=tool_artifacts,
        candidate_validations=validation_records,
    )
    report_path = write_report(result, reports_dir=reports_dir)
    result.report_path = str(report_path)
    return result
