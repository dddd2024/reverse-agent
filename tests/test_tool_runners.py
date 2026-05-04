import json
import sys
from pathlib import Path

from reverse_agent.evidence import StructuredEvidence
from reverse_agent import tool_runners
from reverse_agent.tool_runners import (
    ToolAutomationConfig,
    run_compare_probe,
    run_tool_automation,
)


def test_ollydbg_script_automation_runs_and_emits_artifact(tmp_path: Path) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")

    script = tmp_path / "olly_driver.py"
    script.write_text(
        "\n".join(
            [
                "import argparse",
                "import json",
                "from pathlib import Path",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--olly', required=True)",
                "parser.add_argument('--target', required=True)",
                "parser.add_argument('--out', required=True)",
                "args = parser.parse_args()",
                "payload = {",
                "    'summary': 'Olly script ok',",
                "    'evidence': [f'target:{args.target}', f'olly:{args.olly}'],",
                "    'candidates': [{'value': 'flag{', 'source': 'runtime_probe', 'confidence': 0.9}]",
                "}",
                "Path(args.out).write_text(json.dumps(payload), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )

    cfg = ToolAutomationConfig(
        enabled=True,
        ida_enabled=False,
        ollydbg_enabled=True,
        ollydbg_executable=sys.executable,
        ollydbg_script_path=str(script),
        ollydbg_timeout_seconds=30,
    )

    artifacts = run_tool_automation(
        file_path=target,
        analysis_mode="Dynamic Debug",
        config=cfg,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.tool_name == "OllyDbg"
    assert artifact.attempted is True
    assert artifact.success is True
    assert "Olly script ok" in artifact.summary
    assert Path(artifact.output_path).exists()
    assert any(item.startswith("target:") for item in artifact.evidence)
    assert any("runtime_candidate:flag{" in item for item in artifact.evidence)


def test_compare_probe_runner_parses_structured_payload(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "samplereverse.exe"
    target.write_bytes(b"MZ")

    script = tmp_path / "compare_probe_driver.py"
    script.write_text(
        "\n".join(
            [
                "import argparse",
                "import json",
                "from pathlib import Path",
                "p=argparse.ArgumentParser()",
                "p.add_argument('--target', required=True)",
                "p.add_argument('--out', required=True)",
                "a=p.parse_args()",
                "payload = {",
                "    'summary': 'compare ok',",
                "    'compare_site': '0x40258c',",
                "    'input_text': 'AAAAAAA',",
                "    'lhs_ptr': '0x1000',",
                "    'rhs_ptr': '0x2000',",
                "    'compare_count': 5,",
                "    'lhs_wide_text': 'flag{demo',",
                "    'lhs_wide_hex': '66006c00610067007b00',",
                "    'rhs_wide_text': 'flag{',",
                "    'rhs_wide_hex': '66006c00610067007b00',",
                "    'runtime_ci_exact_wchars': 5,",
                "    'runtime_ci_distance5': 0,",
                "    'runtime_lhs_prefix_hex': '66006c00610067007b00640065006d00',",
                "    'runtime_lhs_prefix_hex_10': '66006c00610067007b00',",
                "    'runtime_lhs_prefix_hex_16': '66006c00610067007b00640065006d00',",
                "    'runtime_lhs_prefix_bytes_captured': 16,",
                "    'offline_ci_exact_wchars': 5,",
                "    'offline_ci_distance5': 0,",
                "    'offline_raw_prefix_hex': '66006c00610067007b00',",
                "    'compare_semantics_agree': True,",
                "    'evidence': [",
                "        'runtime_compare:site=0x40258c',",
                "        'runtime_compare:lhs=flag{demo',",
                "        'runtime_compare:rhs=flag{',",
                "    ],",
                "    'candidates': [{'value': 'AAAAAAA', 'source': 'runtime_compare', 'confidence': 0.98}]",
                "}",
                "Path(a.out).write_text(json.dumps(payload), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "reverse_agent.tool_runners._resolve_compare_probe_script",
        lambda: str(script),
    )

    artifact = run_compare_probe(
        file_path=target,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
        timeout_seconds=30,
    )

    assert artifact.tool_name == "CompareProbe"
    assert artifact.attempted is True
    assert artifact.success is True
    assert "compare ok" in artifact.summary
    assert Path(artifact.output_path).exists()
    assert any(item.startswith("runtime_compare:lhs=") for item in artifact.evidence)
    assert any("runtime_candidate:AAAAAAA" in item for item in artifact.evidence)
    compare_evidence = next(item for item in artifact.structured_evidence if item.kind == "RuntimeCompareEvidence")
    assert compare_evidence.payload["runtime_ci_exact_wchars"] == 5
    assert compare_evidence.payload["runtime_ci_distance5"] == 0
    assert compare_evidence.payload["runtime_lhs_prefix_hex"] == "66006c00610067007b00640065006d00"
    assert compare_evidence.payload["runtime_lhs_prefix_hex_16"] == "66006c00610067007b00640065006d00"
    assert compare_evidence.payload["runtime_lhs_prefix_bytes_captured"] == 16
    assert compare_evidence.payload["lhs_ptr"] == "0x1000"
    assert compare_evidence.payload["rhs_ptr"] == "0x2000"
    assert compare_evidence.payload["compare_count"] == 5
    assert compare_evidence.payload["offline_ci_exact_wchars"] == 5
    assert compare_evidence.payload["offline_ci_distance5"] == 0
    assert compare_evidence.payload["offline_raw_prefix_hex"] == "66006c00610067007b00"
    assert compare_evidence.payload["compare_semantics_agree"] is True
    assert any(item.kind == "CandidateEvidence" for item in artifact.structured_evidence)


def test_compare_probe_payload_exposes_compare_count_and_ptrs() -> None:
    from reverse_agent.olly_scripts.compare_probe import _build_payload

    payload = _build_payload(
        success=True,
        summary="ok",
        compare_site="0x40258c",
        input_text="AAAAAAA",
        lhs_ptr="0x1000",
        rhs_ptr="0x2000",
        compare_count=5,
    )

    assert payload["lhs_ptr"] == "0x1000"
    assert payload["rhs_ptr"] == "0x2000"
    assert payload["compare_count"] == 5


def test_ollydbg_requires_script_for_automation(tmp_path: Path) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")
    cfg = ToolAutomationConfig(
        enabled=True,
        ida_enabled=False,
        ollydbg_enabled=True,
        ollydbg_executable=sys.executable,
        ollydbg_script_path=str(tmp_path / "missing.py"),
    )
    artifacts = run_tool_automation(
        file_path=target,
        analysis_mode="Dynamic Debug",
        config=cfg,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.tool_name == "OllyDbg"
    assert artifact.attempted is False
    assert artifact.success is False
    assert "自动化脚本" in artifact.error


def test_ollydbg_auto_enables_when_dynamic_and_paths_provided(tmp_path: Path) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")
    script = tmp_path / "olly_driver.py"
    script.write_text(
        "\n".join(
            [
                "import argparse",
                "import json",
                "from pathlib import Path",
                "p=argparse.ArgumentParser()",
                "p.add_argument('--olly', required=True)",
                "p.add_argument('--target', required=True)",
                "p.add_argument('--out', required=True)",
                "a=p.parse_args()",
                "Path(a.out).write_text(json.dumps({'summary':'ok','evidence':['e']}), encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
    )

    cfg = ToolAutomationConfig(
        enabled=True,
        ida_enabled=False,
        ollydbg_enabled=False,
        ollydbg_executable=sys.executable,
        ollydbg_script_path=str(script),
        ollydbg_timeout_seconds=30,
    )

    artifacts = run_tool_automation(
        file_path=target,
        analysis_mode="Dynamic Debug",
        config=cfg,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
    )
    assert len(artifacts) == 1
    assert artifacts[0].tool_name == "OllyDbg"
    assert artifacts[0].success is True


def test_ollydbg_reports_skip_reason_in_static_mode(tmp_path: Path) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")
    cfg = ToolAutomationConfig(
        enabled=True,
        ida_enabled=False,
        ollydbg_enabled=True,
        ollydbg_executable="C:\\dummy\\ollydbg.exe",
        ollydbg_script_path="C:\\dummy\\driver.py",
    )
    artifacts = run_tool_automation(
        file_path=target,
        analysis_mode="Static Analysis",
        config=cfg,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
    )
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.tool_name == "OllyDbg"
    assert artifact.attempted is False
    assert "仅在动态调试模式执行" in artifact.summary
    assert artifact.error == ""


def test_ida_parses_compare_contexts_into_evidence(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")
    out_json = tmp_path / "artifacts" / "demo_ida_evidence.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(
            {
                "entry": "0x401000",
                "strings": ["flag{"],
                "functions": ["sub_401000"],
                "compare_contexts": [
                    {
                        "call_ea": "0x402584",
                        "callee": "lstrcmpA",
                        "caller_func": "sub_402500",
                        "call_disasm": "call lstrcmpA",
                        "ref_strings": "flag{ | Flag : ",
                        "nearby": "push 5 || push offset aFlag",
                    }
                ],
                "control_id_contexts": [
                    {
                        "ea": "0x401234",
                        "caller_func": "sub_401200",
                        "insn": "push 3E8h",
                        "nearby": "mov ecx, esi || call sub_401000",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(tool_runners, "_resolve_ida_executable", lambda p: r"C:\ida\idat64.exe")
    monkeypatch.setattr(tool_runners, "_resolve_ida_script", lambda p: r"C:\ida\collect.py")

    def _fake_run(*args, **kwargs):  # noqa: ANN002, ANN003
        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Proc()

    monkeypatch.setattr(tool_runners.subprocess, "run", _fake_run)

    cfg = ToolAutomationConfig(enabled=True, ida_enabled=True, ida_timeout_seconds=30)
    artifact = tool_runners._run_ida(  # noqa: SLF001
        file_path=target,
        config=cfg,
        artifacts_dir=tmp_path / "artifacts",
        log=lambda _: None,
    )

    assert artifact.success is True
    assert "比较上下文 1 条" in artifact.summary
    assert "控件ID上下文 1 条" in artifact.summary
    assert any("IDA比较上下文" in line and "lstrcmpA" in line for line in artifact.evidence)
    assert any("IDA控件ID上下文" in line and "push 3E8h" in line for line in artifact.evidence)
