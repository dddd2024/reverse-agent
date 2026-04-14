import sys
from pathlib import Path

from reverse_agent.tool_runners import ToolAutomationConfig, run_tool_automation


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
                "    'evidence': [f'target:{args.target}', f'olly:{args.olly}']",
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


def test_ollydbg_requires_script_for_automation(tmp_path: Path) -> None:
    target = tmp_path / "demo.exe"
    target.write_bytes(b"MZ")
    cfg = ToolAutomationConfig(
        enabled=True,
        ida_enabled=False,
        ollydbg_enabled=True,
        ollydbg_executable=sys.executable,
        ollydbg_script_path="",
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
