from __future__ import annotations

import time
from pathlib import Path

from ..evidence import StructuredEvidence
from ..tool_runners import ToolRunArtifact


def escape_runtime_text(value: str) -> str:
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


def candidate_to_gui_text(candidate: str) -> str:
    try:
        raw = candidate.encode("latin1", errors="ignore")
    except Exception:
        return candidate
    out: list[str] = []
    for byte in raw:
        if 0x20 <= byte <= 0x7E:
            out.append(chr(byte))
        elif byte == 0:
            continue
        else:
            out.append(chr(0x0100 | byte))
    return "".join(out)


def is_windows_gui_exe(file_path: Path) -> bool:
    try:
        data = file_path.read_bytes()[:2048]
        if len(data) < 0x40 or data[:2] != b"MZ":
            return False
        pe_off = int.from_bytes(data[0x3C:0x40], "little")
        if pe_off + 0x5E >= len(data):
            data = file_path.read_bytes()[: max(pe_off + 0x60, 4096)]
        if data[pe_off : pe_off + 4] != b"PE\x00\x00":
            return False
        optional_header_off = pe_off + 24
        subsystem_off = optional_header_off + 0x44
        subsystem = int.from_bytes(data[subsystem_off : subsystem_off + 2], "little")
        return subsystem == 2
    except Exception:
        return False


def collect_gui_runtime_outputs(
    file_path: Path,
    probe_inputs: list[str],
    per_action_delay: float = 0.18,
) -> ToolRunArtifact | None:
    if not is_windows_gui_exe(file_path):
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

    artifact = ToolRunArtifact(
        tool_name="GUIProbe",
        enabled=True,
        attempted=True,
        success=False,
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
        title = escape_runtime_text(win.window_text() or "")
        evidence.append(f"runtime_gui:title={title}")
        evidence.append("runtime_gui:controls=button:1000,edit:1001,edit:1002")
        samples: list[dict[str, str]] = []
        for candidate in probe_inputs:
            input_edit.set_edit_text(candidate_to_gui_text(candidate))
            decrypt_btn.click()
            time.sleep(per_action_delay)
            try:
                output = output_edit.get_value() or ""
            except Exception:
                output = output_edit.window_text() or ""
            escaped_candidate = escape_runtime_text(candidate)
            escaped_output = escape_runtime_text(output[:220])
            evidence.append(f"runtime_gui:probe_input={escaped_candidate}")
            evidence.append(f"runtime_gui:probe_output={escaped_output}")
            samples.append({"input": escaped_candidate, "output": escaped_output})
        artifact.success = True
        artifact.summary = "GUI 运行时证据采集成功。"
        artifact.evidence = evidence
        artifact.structured_evidence.append(
            StructuredEvidence(
                kind="RuntimeGuiEvidence",
                source_tool="GUIProbe",
                summary=artifact.summary,
                payload={"title": title, "samples": samples},
                confidence=0.8,
            )
        )
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


def validate_candidates_with_gui_session(
    file_path: Path,
    candidates: list[str],
    success_markers: list[str],
    fail_markers: list[str],
    per_action_delay: float = 0.12,
) -> tuple[str, list[dict[str, str]]]:
    try:
        from pywinauto import Application
    except ImportError as exc:
        raise RuntimeError("GUI 动态校验依赖 pywinauto，当前环境未安装。") from exc

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
            input_edit.set_edit_text(candidate_to_gui_text(cand))
            decrypt_btn.click()
            time.sleep(per_action_delay)
            try:
                output = output_edit.get_value() or ""
            except Exception:
                output = output_edit.window_text() or ""
            lower = output.lower()
            has_success = any(marker.lower() in lower for marker in success_markers if marker)
            has_fail = any(marker.lower() in lower for marker in merged_fail_markers if marker)
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
