from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


COMPARE_SITE_OFFSET = 0x258C
DEFAULT_PROBE_INPUTS = [
    "AAAAAAA",
    "AAAAAAAA",
    "AAAAAAAAAAAA",
    "AAAAAAAAAAAAA",
]


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
            out.append(chr(0x0100 | b))
    return "".join(out)


def _trigger_decrypt(button) -> None:  # noqa: ANN001
    try:
        invoke = getattr(button, "invoke", None)
        if callable(invoke):
            invoke()
            return
    except Exception:
        pass
    button.click()


def _terminate_target(app, pid: int | None) -> None:  # noqa: ANN001
    try:
        if app is not None:
            app.kill()
    except Exception:
        pass
    try:
        if pid is not None:
            import psutil

            proc = psutil.Process(pid)
            proc.kill()
            proc.wait(timeout=2)
    except Exception:
        pass


def _build_payload(
    *,
    success: bool,
    summary: str,
    compare_site: str = "",
    input_text: str = "",
    lhs_wide_text: str = "",
    lhs_wide_hex: str = "",
    rhs_wide_text: str = "",
    rhs_wide_hex: str = "",
    evidence: list[str] | None = None,
    candidates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "success": success,
        "summary": summary,
        "compare_site": compare_site,
        "input_text": input_text,
        "lhs_wide_text": lhs_wide_text,
        "lhs_wide_hex": lhs_wide_hex,
        "rhs_wide_text": rhs_wide_text,
        "rhs_wide_hex": rhs_wide_hex,
        "evidence": evidence or [],
        "candidates": candidates or [],
    }


def _write_payload(out_path: Path, payload: dict[str, object]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture compare-time wide strings for samplereverse")
    parser.add_argument("--target", required=True, help="Path to target executable")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument(
        "--probe-input",
        action="append",
        default=[],
        help="Probe input to try; may be repeated.",
    )
    parser.add_argument(
        "--probe-hex",
        action="append",
        default=[],
        help="Probe candidate as raw low-byte hex; may be repeated.",
    )
    parser.add_argument(
        "--per-probe-timeout",
        type=float,
        default=2.2,
        help="Seconds to wait for compare hook after each click.",
    )
    args = parser.parse_args()

    target = Path(args.target)
    out_path = Path(args.out)
    evidence: list[str] = [f"runtime_compare:target={target}"]

    if not target.exists():
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="CompareProbe 失败：目标文件不存在。",
                evidence=[*evidence, "runtime_compare:error=target_missing"],
            ),
        )

    try:
        import frida
        from pywinauto import Application
    except Exception as exc:
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="CompareProbe 失败：缺少 frida 或 pywinauto 依赖。",
                evidence=[*evidence, f"runtime_compare:error={_escape_runtime_text(str(exc))}"],
            ),
        )

    probe_inputs: list[str] = []
    for item in args.probe_hex:
        try:
            probe_inputs.append(bytes.fromhex(item).decode("latin1"))
        except Exception:
            evidence.append(f"runtime_compare:invalid_probe_hex={_escape_runtime_text(item)}")
    probe_inputs.extend(item for item in args.probe_input if item)
    if not probe_inputs:
        probe_inputs = DEFAULT_PROBE_INPUTS[:]

    messages: list[dict[str, object]] = []
    script_errors: list[str] = []
    pid: int | None = None
    session = None
    app = None

    def on_message(message: dict[str, object], data: object) -> None:  # noqa: ANN401
        message_type = str(message.get("type", ""))
        if message_type == "send":
            payload = message.get("payload", {})
            if isinstance(payload, dict):
                messages.append(payload)
            return
        if message_type == "error":
            stack = str(message.get("stack", "")).strip()
            if stack:
                script_errors.append(stack)

    script_source = f"""
const compareOffset = ptr("{COMPARE_SITE_OFFSET:#x}");
const maxChars = 16;

function readWide(ptrValue) {{
    try {{
        if (ptrValue.isNull()) {{
            return "";
        }}
        return ptrValue.readUtf16String(maxChars) || "";
    }} catch (error) {{
        return "";
    }}
}}

function readWideHex(ptrValue) {{
    try {{
        if (ptrValue.isNull()) {{
            return "";
        }}
        const raw = ptrValue.readByteArray(maxChars * 2);
        if (!raw) {{
            return "";
        }}
        const bytes = new Uint8Array(raw);
        let out = [];
        for (let i = 0; i < bytes.length; i++) {{
            out.push(("0" + bytes[i].toString(16)).slice(-2));
        }}
        return out.join("");
    }} catch (error) {{
        return "";
    }}
}}

const mainModule = Process.enumerateModules()[0];
const compareSite = mainModule.base.add(compareOffset);

Interceptor.attach(compareSite, {{
    onEnter(args) {{
        const stackBase = ptr(this.context.sp || this.context.esp);
        const lhsPtr = stackBase.readPointer();
        const rhsPtr = stackBase.add(4).readPointer();
        const count = stackBase.add(8).readU32();
        send({{
            type: "compare",
            compare_site: compareSite.toString(),
            lhs_ptr: lhsPtr.toString(),
            rhs_ptr: rhsPtr.toString(),
            count: count,
            lhs_wide_text: readWide(lhsPtr),
            lhs_wide_hex: readWideHex(lhsPtr),
            rhs_wide_text: readWide(rhsPtr),
            rhs_wide_hex: readWideHex(rhsPtr),
        }});
    }}
}});
"""

    try:
        pid = frida.spawn([str(target)])
        session = frida.attach(pid)
        script = session.create_script(script_source)
        script.on("message", on_message)
        script.load()
        frida.resume(pid)
        time.sleep(1.0)

        app = Application(backend="uia").connect(process=pid)
        win = None
        last_exc = None
        for _ in range(60):
            try:
                win = app.top_window()
                title = win.window_text() or ""
                if title or win.exists(timeout=0.1):
                    break
            except Exception as exc:  # pragma: no cover - best effort UI attach
                last_exc = exc
                time.sleep(0.1)
        if win is None:
            raise RuntimeError(f"无法连接到目标窗口: {last_exc}")

        input_edit = win.child_window(auto_id="1001", control_type="Edit")
        decrypt_btn = win.child_window(auto_id="1000", control_type="Button")
        evidence.append(f"runtime_compare:title={_escape_runtime_text(win.window_text() or '')}")

        captured: dict[str, object] | None = None
        captured_input = ""
        captured_output = ""
        for candidate in probe_inputs:
            evidence.append(f"runtime_compare:probe_input={_escape_runtime_text(candidate)}")
            before_count = len(messages)
            input_edit.set_edit_text(_candidate_to_gui_text(candidate))
            _trigger_decrypt(decrypt_btn)

            deadline = time.monotonic() + max(0.3, args.per_probe_timeout)
            while time.monotonic() < deadline:
                if len(messages) > before_count:
                    for payload in messages[before_count:]:
                        if str(payload.get("type", "")) == "compare":
                            captured = payload
                            captured_input = candidate
                            break
                    if captured:
                        break
                if script_errors:
                    raise RuntimeError(script_errors[-1])
                time.sleep(0.05)

            if captured:
                evidence.append("runtime_compare:probe_output=<captured_before_gui_flush>")
                break
            evidence.append("runtime_compare:probe_output=<no_compare_hit_within_deadline>")

        if not captured:
            if script_errors:
                raise RuntimeError(script_errors[-1])
            return _write_payload(
                out_path,
                _build_payload(
                    success=False,
                    summary="CompareProbe 未命中 compare 断点。",
                    evidence=[*evidence, "runtime_compare:error=no_compare_hit"],
                ),
            )

        lhs_wide_text = str(captured.get("lhs_wide_text", "") or "")
        rhs_wide_text = str(captured.get("rhs_wide_text", "") or "")
        lhs_wide_hex = str(captured.get("lhs_wide_hex", "") or "")
        rhs_wide_hex = str(captured.get("rhs_wide_hex", "") or "")
        compare_site = str(captured.get("compare_site", "") or "")
        evidence.extend(
            [
                f"runtime_compare:site={compare_site}",
                f"runtime_compare:input={_escape_runtime_text(captured_input)}",
                f"runtime_compare:lhs={_escape_runtime_text(lhs_wide_text)}",
                f"runtime_compare:rhs={_escape_runtime_text(rhs_wide_text)}",
                f"runtime_compare:lhs_ptr={captured.get('lhs_ptr', '')}",
                f"runtime_compare:rhs_ptr={captured.get('rhs_ptr', '')}",
                f"runtime_compare:count={captured.get('count', '')}",
                f"runtime_compare:gui_output={_escape_runtime_text(captured_output[:220])}",
            ]
        )
        candidates: list[dict[str, object]] = []
        prefix_match = lhs_wide_text.lower().startswith("flag{")
        evidence.append(f"runtime_compare:lhs_prefix_match={1 if prefix_match else 0}")
        if prefix_match and captured_input:
            candidates.append(
                {
                    "value": captured_input,
                    "source": "runtime_compare",
                    "confidence": 0.98,
                    "reason": "Compare-time lhs wide string already starts with flag{.",
                }
            )

        summary = "CompareProbe 已捕获 compare 前真值。"
        return _write_payload(
            out_path,
            _build_payload(
                success=True,
                summary=summary,
                compare_site=compare_site,
                input_text=captured_input,
                lhs_wide_text=lhs_wide_text,
                lhs_wide_hex=lhs_wide_hex,
                rhs_wide_text=rhs_wide_text,
                rhs_wide_hex=rhs_wide_hex,
                evidence=evidence,
                candidates=candidates,
            ),
        )
    except Exception as exc:
        evidence.append(f"runtime_compare:error={_escape_runtime_text(str(exc))}")
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="CompareProbe 执行失败。",
                evidence=evidence,
            ),
        )
    finally:
        _terminate_target(app, pid)
        try:
            if session is not None:
                session.detach()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
