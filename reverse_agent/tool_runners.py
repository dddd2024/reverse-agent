from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


@dataclass
class ToolAutomationConfig:
    enabled: bool = False
    ida_enabled: bool = True
    ida_executable: str = ""
    ida_script_path: str = ""
    ida_timeout_seconds: int = 180
    ollydbg_enabled: bool = False
    ollydbg_executable: str = ""
    ollydbg_script_path: str = ""
    ollydbg_timeout_seconds: int = 120


@dataclass
class ToolRunArtifact:
    tool_name: str
    enabled: bool
    attempted: bool
    success: bool
    command: str = ""
    summary: str = ""
    output_path: str = ""
    error: str = ""
    evidence: list[str] = field(default_factory=list)


def _resolve_ida_executable(user_path: str) -> str:
    if user_path.strip():
        p = Path(user_path.strip())
        if not p.exists():
            return ""
        if p.is_file():
            return str(p)
        if p.is_dir():
            for name in ("idat64.exe", "idat.exe", "ida64.exe", "ida.exe"):
                candidate = p / name
                if candidate.exists() and candidate.is_file():
                    return str(candidate)
            return ""
    for candidate in ("idat64.exe", "idat.exe", "ida64.exe", "ida.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return ""


def _resolve_ida_script(user_path: str) -> str:
    if user_path.strip():
        p = Path(user_path.strip())
        return str(p) if p.exists() else ""
    default_script = Path(__file__).parent / "ida_scripts" / "collect_evidence.py"
    return str(default_script) if default_script.exists() else ""


def run_tool_automation(
    file_path: Path,
    analysis_mode: str,
    config: ToolAutomationConfig,
    artifacts_dir: Path,
    log: LogFn,
) -> list[ToolRunArtifact]:
    artifacts: list[ToolRunArtifact] = []
    if not config.enabled:
        log("工具链自动分析未启用，跳过 IDA/OllyDbg。")
        return artifacts

    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if config.ida_enabled:
        artifacts.append(_run_ida(file_path, config, artifacts_dir, log))

    if analysis_mode == "Dynamic Debug" and config.ollydbg_enabled:
        artifacts.append(_run_ollydbg_placeholder(config))

    return artifacts


def _run_ida(
    file_path: Path,
    config: ToolAutomationConfig,
    artifacts_dir: Path,
    log: LogFn,
) -> ToolRunArtifact:
    artifact = ToolRunArtifact(
        tool_name="IDA",
        enabled=True,
        attempted=False,
        success=False,
    )
    ida_executable = _resolve_ida_executable(config.ida_executable)
    if not ida_executable:
        artifact.error = "未找到 IDA 可执行文件（请在界面配置 ida/idat 路径）。"
        artifact.summary = "IDA 自动分析未执行。"
        return artifact

    ida_script = _resolve_ida_script(config.ida_script_path)
    if not ida_script:
        artifact.error = "未找到 IDA 脚本（请在界面配置脚本路径）。"
        artifact.summary = "IDA 自动分析未执行。"
        return artifact

    output_path = artifacts_dir / f"{file_path.stem}_ida_evidence.json"
    ida_log_path = artifacts_dir / f"{file_path.stem}_ida.log"
    artifact.output_path = str(output_path)
    command_args = [
        ida_executable,
        "-A",
        f"-L{ida_log_path}",
        f"-S{ida_script}",
        str(file_path),
    ]
    artifact.command = " ".join(shlex.quote(a) for a in command_args)
    artifact.attempted = True
    log("正在执行 IDA 自动化分析...")

    env = dict(os.environ)
    env["REVERSE_AGENT_IDA_OUT"] = str(output_path)

    try:
        proc = subprocess.run(
            command_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.ida_timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired:
        artifact.error = f"IDA 执行超时（>{config.ida_timeout_seconds} 秒）。"
        artifact.summary = "IDA 自动分析超时。"
        return artifact

    if proc.returncode != 0:
        details = (proc.stderr or proc.stdout or "").strip()
        if not details and ida_log_path.exists():
            details = ida_log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        artifact.error = details[:2000] if details else "IDA 未返回可读错误信息。"
        artifact.summary = f"IDA 执行失败（退出码 {proc.returncode}）。"
        return artifact

    if not output_path.exists():
        artifact.error = "IDA 执行结束但未生成证据文件。"
        artifact.summary = "IDA 自动分析输出缺失。"
        return artifact

    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception as exc:
        artifact.error = f"IDA 证据文件解析失败：{exc}"
        artifact.summary = "IDA 输出不可解析。"
        return artifact

    strings = data.get("strings", [])[:20]
    funcs = data.get("functions", [])[:20]
    artifact.evidence = [
        *(f"IDA字符串: {s}" for s in strings),
        *(f"IDA函数: {f}" for f in funcs),
    ]
    artifact.success = True
    artifact.summary = (
        f"IDA 自动分析完成：字符串 {len(data.get('strings', []))} 条，"
        f"函数 {len(data.get('functions', []))} 个。"
    )
    return artifact


def _run_ollydbg_placeholder(config: ToolAutomationConfig) -> ToolRunArtifact:
    has_exe = bool(config.ollydbg_executable.strip())
    has_script = bool(config.ollydbg_script_path.strip())
    guidance = "已预留 OllyDbg 接口，当前版本仅记录配置，不执行自动调试。"
    if not has_exe:
        guidance += " 未配置 OllyDbg 路径。"
    if not has_script:
        guidance += " 未配置 OllyDbg 脚本路径。"
    return ToolRunArtifact(
        tool_name="OllyDbg",
        enabled=True,
        attempted=False,
        success=False,
        summary=guidance,
        error="OllyDbg 自动执行尚未在第一版实现。",
    )
