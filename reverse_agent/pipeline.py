from __future__ import annotations

import hashlib
import itertools
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from .dynamic_templates import get_analysis_template
from .models import CopilotCliBackend, LocalOpenAIBackend
from .tool_runners import ToolAutomationConfig, ToolRunArtifact, run_tool_automation

LogFn = Callable[[str], None]


FLAG_PATTERNS = [
    re.compile(r"flag\{[^\r\n}]{1,300}\}", re.IGNORECASE),
    re.compile(r"ctf\{[^\r\n}]{1,300}\}", re.IGNORECASE),
    re.compile(r"key\{[^\r\n}]{1,300}\}", re.IGNORECASE),
]
PLAIN_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{4,80}$")
HEX32_PATTERN = re.compile(r"^[a-fA-F0-9]{32}$")


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
    report_path: str = ""


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


def extract_strings(file_path: Path, min_length: int = 4) -> list[str]:
    data = file_path.read_bytes()
    pattern = rb"[\x20-\x7E]{" + str(min_length).encode() + rb",}"
    raw = re.findall(pattern, data)
    strings = [s.decode("utf-8", errors="ignore") for s in raw]
    # keep order, remove duplicates
    seen: set[str] = set()
    deduped: list[str] = []
    for s in strings:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


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


def build_prompt(
    file_path: Path,
    strings: list[str],
    pre_candidates: list[str],
    analysis_mode: str,
    tool_evidence: list[str] | None = None,
) -> str:
    head = strings[:500]
    template = get_analysis_template(analysis_mode)
    lines = [
        "你是逆向工程解题助手，请根据证据推断最终 flag。",
        f"文件名: {file_path.name}",
        f"文件路径: {file_path}",
        f"分析模式: {analysis_mode}",
        "",
        template,
        "",
        "本地预检测 flag 候选:",
        *([f"- {c}" for c in pre_candidates] or ["- <none>"]),
        "",
        "可打印字符串样本:",
        *head,
        "",
        "工具证据样本:",
        *(tool_evidence[:200] if tool_evidence else ["- <none>"]),
        "",
        "任务:",
        "1) 推断最可能的最终 flag。",
        "2) 第一行只输出一个 flag。",
        "3) 如果不是 flag{} 格式，也必须在第一行只输出答案本体（例如 SEPTA）。",
        "4) 随后用简短证据说明原因。",
    ]
    return "\n".join(lines)


def _extract_first_flag(value: str) -> str:
    for pat in FLAG_PATTERNS:
        m = pat.search(value)
        if m:
            return m.group(0)
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
            if PLAIN_TOKEN_PATTERN.fullmatch(token):
                return token

    for idx, line in enumerate(value.splitlines()):
        candidate = line.strip().strip("`").strip("'").strip('"')
        if not candidate or not PLAIN_TOKEN_PATTERN.fullmatch(candidate):
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


def _crack_md5_upper4(md5_hex: str) -> str:
    target = md5_hex.lower()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for chars in itertools.product(alphabet, repeat=4):
        token = "".join(chars)
        if hashlib.md5(token.encode("ascii")).hexdigest() == target:
            return token
    return ""


def _validate_candidate_with_exe(file_path: Path, candidate: str, timeout_seconds: int = 5) -> bool:
    if not candidate:
        return False
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
    return "Correct!" in output


def run_pipeline(
    input_value: str,
    analysis_mode: str,
    model_type: str,
    copilot_command: str,
    local_base_url: str,
    local_model: str,
    local_api_key: str,
    tool_config: ToolAutomationConfig,
    reports_dir: Path,
    log: LogFn,
) -> SolveResult:
    workdir = Path(tempfile.gettempdir()) / "reverse_agent_downloads"
    file_path = resolve_input(input_value, workdir, log)

    # User requirement: input should be exe file or its path/url.
    if file_path.suffix.lower() != ".exe":
        log("警告: 输入文件不是 .exe，仍继续执行。")

    log("正在提取可打印字符串...")
    strings = extract_strings(file_path)
    pre_candidates = find_flag_candidates(strings)
    artifacts_dir = reports_dir / "tool_artifacts" / file_path.stem
    tool_artifacts = run_tool_automation(
        file_path=file_path,
        analysis_mode=analysis_mode,
        config=tool_config,
        artifacts_dir=artifacts_dir,
        log=log,
    )
    tool_evidence = [line for a in tool_artifacts for line in a.evidence]
    for artifact in tool_artifacts:
        log(f"{artifact.tool_name}: {artifact.summary}")
        if artifact.error:
            log(f"{artifact.tool_name} 错误: {artifact.error}")

    prompt = build_prompt(
        file_path,
        strings,
        pre_candidates,
        analysis_mode,
        tool_evidence=tool_evidence,
    )

    if pre_candidates:
        log(f"在模型分析前已发现本地候选 {len(pre_candidates)} 个。")

    model_output = ""
    selected_flag = pre_candidates[0] if pre_candidates else ""

    if model_type == "Copilot CLI":
        backend = CopilotCliBackend(command_template=copilot_command)
        model_name = f"Copilot CLI ({copilot_command})"
    elif model_type == "Local Model":
        backend = LocalOpenAIBackend(
            base_url=local_base_url, model=local_model, api_key=local_api_key
        )
        model_name = f"Local Model ({local_model})"
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

    log(f"正在调用模型: {model_name}")
    model_output = backend.solve(prompt)
    model_flag = _extract_first_flag(model_output)
    if model_flag:
        selected_flag = model_flag
    elif model_output.strip():
        selected_flag = _extract_best_answer_line(model_output) or selected_flag

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

    candidate_pool: list[str] = []
    for c in [selected_flag, model_flag, *_extract_best_answer_line(model_output).split()[:1], *recovered_tokens]:
        value = (c or "").strip()
        if value and value not in candidate_pool:
            candidate_pool.append(value)
    # Common pattern in this class of crackmes: uppercase check with length >= 5
    for c in list(candidate_pool):
        if len(c) == 4 and c.isupper():
            ext = c + "A"
            if ext not in candidate_pool:
                candidate_pool.append(ext)

    if file_path.suffix.lower() == ".exe" and candidate_pool:
        for cand in candidate_pool:
            try:
                if _validate_candidate_with_exe(file_path, cand):
                    log(f"运行时校验通过，选定候选: {cand}")
                    selected_flag = cand
                    break
            except subprocess.TimeoutExpired:
                continue

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
    )
    report_path = write_report(result, reports_dir=reports_dir)
    result.report_path = str(report_path)
    return result
