from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:  # Support package imports in tests and direct script execution.
    from .compare_probe import _candidate_to_gui_text, _escape_runtime_text, _terminate_target, _trigger_decrypt
except ImportError:  # pragma: no cover - exercised by subprocess execution
    from compare_probe import _candidate_to_gui_text, _escape_runtime_text, _terminate_target, _trigger_decrypt


COMPARE_SITE_OFFSET = 0x258C


HOOK_RESULT_KEYS = (
    "utf16le_payload",
    "base64_input",
    "base64_output",
    "rc4_key",
    "rc4_input",
    "rc4_output",
    "compare_buffer",
)


def _empty_hook_results() -> dict[str, str]:
    return {key: "unavailable" for key in HOOK_RESULT_KEYS}


def _normalize_static_point(item: dict[str, object] | None) -> dict[str, object]:
    item = item or {}
    module_offset = item.get("module_offset")
    try:
        module_offset_int = int(module_offset) if module_offset is not None else None
    except (TypeError, ValueError):
        module_offset_int = None
    return {
        "kind": str(item.get("kind", "")),
        "name": str(item.get("name", "")),
        "address": str(item.get("address", "")),
        "module_offset": module_offset_int,
        "confidence": str(item.get("confidence", "low") or "low"),
        "evidence": [str(value) for value in item.get("evidence", []) if value is not None]
        if isinstance(item.get("evidence"), list)
        else [],
        "hook_kind": str(item.get("hook_kind", "memory_access") or "memory_access"),
        "hookable": bool(item.get("hookable", module_offset_int is not None)),
        "size": int(item.get("size", 1) or 1),
    }


def _normalize_hook_event(item: dict[str, object] | None) -> dict[str, object]:
    item = item or {}
    return {
        "point_kind": str(item.get("point_kind", "")),
        "point_name": str(item.get("point_name", "")),
        "hook_kind": str(item.get("hook_kind", "")),
        "address": str(item.get("address", "")),
        "module_offset": str(item.get("module_offset", "")),
        "operation": str(item.get("operation", "")),
        "from": str(item.get("from", "")),
        "hit_count": int(item.get("hit_count", 0) or 0),
        "registers": dict(item.get("registers", {})) if isinstance(item.get("registers"), dict) else {},
        "stack_preview_hex": str(item.get("stack_preview_hex", "")),
        "buffer_preview_hex": str(item.get("buffer_preview_hex", "")),
        "buffer_preview_ascii": str(item.get("buffer_preview_ascii", "")),
        "lhs_ptr": str(item.get("lhs_ptr", "")),
        "rhs_ptr": str(item.get("rhs_ptr", "")),
        "compare_count": item.get("compare_count"),
        "lhs_preview_hex": str(item.get("lhs_preview_hex", "")),
        "rhs_preview_hex": str(item.get("rhs_preview_hex", "")),
    }


def _hook_results_from_events(events: list[dict[str, object]]) -> dict[str, str]:
    results = _empty_hook_results()
    for event in events:
        kind = str(event.get("point_kind", ""))
        if kind == "compare":
            results["compare_buffer"] = "available"
        elif kind == "utf16le":
            results["utf16le_payload"] = "inferred"
        elif kind == "base64":
            results["base64_input"] = "inferred"
            results["base64_output"] = "inferred"
        elif kind == "rc4_ksa":
            results["rc4_key"] = "inferred"
        elif kind == "rc4_prga":
            results["rc4_input"] = "inferred"
            results["rc4_output"] = "inferred"
        elif kind == "encrypted_const":
            results["rc4_input"] = "inferred"
    return results


def _build_payload(
    *,
    success: bool,
    summary: str,
    candidate_hex: str = "",
    static_points: dict[str, list[dict[str, object]]] | None = None,
    hook_events: list[dict[str, object]] | None = None,
    hook_results: dict[str, str] | None = None,
    evidence: list[str] | None = None,
    error: str = "",
) -> dict[str, object]:
    normalized_events = [_normalize_hook_event(item) for item in hook_events or []]
    return {
        "success": success,
        "summary": summary,
        "candidate_hex": candidate_hex,
        "static_points": static_points or {},
        "hook_events": normalized_events,
        "hook_results": hook_results or _hook_results_from_events(normalized_events),
        "evidence": evidence or [],
        "error": error,
    }


def _write_payload(out_path: Path, payload: dict[str, object]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _read_points(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Hook samplereverse Base64/RC4 construction evidence points")
    parser.add_argument("--target", required=True, help="Path to target executable")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--points", required=True, help="Static point JSON path")
    parser.add_argument("--probe-hex", required=True, help="Probe candidate as raw low-byte hex")
    parser.add_argument("--per-probe-timeout", type=float, default=2.2)
    args = parser.parse_args()

    target = Path(args.target)
    out_path = Path(args.out)
    points_path = Path(args.points)
    evidence = [f"base64_rc4_probe:target={target}", f"base64_rc4_probe:points={points_path}"]
    points_payload = _read_points(points_path)
    static_points = points_payload.get("static_points", {})
    if not isinstance(static_points, dict):
        static_points = {}

    flat_points: list[dict[str, object]] = []
    for values in static_points.values():
        if not isinstance(values, list):
            continue
        flat_points.extend(_normalize_static_point(item) for item in values if isinstance(item, dict))
    hookable_points = [
        item
        for item in flat_points
        if bool(item.get("hookable")) and item.get("module_offset") is not None
    ]

    if not target.exists():
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="Base64RC4BreakpointProbe failed: target missing.",
                candidate_hex=args.probe_hex,
                static_points=static_points,
                evidence=[*evidence, "base64_rc4_probe:error=target_missing"],
                error="target_missing",
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
                summary="Base64RC4BreakpointProbe failed: missing frida or pywinauto.",
                candidate_hex=args.probe_hex,
                static_points=static_points,
                evidence=[*evidence, f"base64_rc4_probe:error={_escape_runtime_text(str(exc))}"],
                error=str(exc),
            ),
        )

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

    points_json = json.dumps(hookable_points, ensure_ascii=True)
    script_source = f"""
const compareOffset = ptr("{COMPARE_SITE_OFFSET:#x}");
const staticPoints = {points_json};
const hitCounts = {{}};

function hexBytes(raw) {{
    if (!raw) {{
        return "";
    }}
    const bytes = new Uint8Array(raw);
    let out = [];
    for (let i = 0; i < bytes.length; i++) {{
        out.push(("0" + bytes[i].toString(16)).slice(-2));
    }}
    return out.join("");
}}

function readBytes(ptrValue, size) {{
    try {{
        if (ptrValue.isNull()) {{
            return "";
        }}
        const raw = ptrValue.readByteArray(Math.min(Math.max(size, 16), 96));
        return hexBytes(raw);
    }} catch (error) {{
        return "";
    }}
}}

function readAscii(ptrValue, size) {{
    try {{
        if (ptrValue.isNull()) {{
            return "";
        }}
        const raw = ptrValue.readByteArray(Math.min(Math.max(size, 16), 96));
        if (!raw) {{
            return "";
        }}
        const bytes = new Uint8Array(raw);
        let out = [];
        for (let i = 0; i < bytes.length; i++) {{
            const value = bytes[i];
            out.push(value >= 32 && value <= 126 ? String.fromCharCode(value) : ".");
        }}
        return out.join("");
    }} catch (error) {{
        return "";
    }}
}}

function contextRegs(context) {{
    let regs = {{}};
    for (const name of ["eax", "ebx", "ecx", "edx", "esi", "edi", "esp", "ebp", "eip", "rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rsp", "rbp", "rip"]) {{
        try {{
            if (context[name] !== undefined) {{
                regs[name] = context[name].toString();
            }}
        }} catch (error) {{
        }}
    }}
    return regs;
}}

function recordHit(point, detail) {{
    const key = String(point.kind || "") + ":" + String(point.name || "");
    hitCounts[key] = (hitCounts[key] || 0) + 1;
    detail.type = "base64_rc4_hook";
    detail.point_kind = String(point.kind || "");
    detail.point_name = String(point.name || "");
    detail.hook_kind = String(point.hook_kind || "");
    detail.module_offset = "0x" + Number(point.module_offset || 0).toString(16);
    detail.hit_count = hitCounts[key];
    send(detail);
}}

const mainModule = Process.enumerateModules()[0];
const compareSite = mainModule.base.add(compareOffset);

for (const point of staticPoints) {{
    try {{
        const offset = Number(point.module_offset || 0);
        const address = mainModule.base.add(offset);
        if (String(point.hook_kind || "memory_access") === "interceptor") {{
            Interceptor.attach(address, {{
                onEnter(args) {{
                    const sp = ptr(this.context.sp || this.context.esp || 0);
                    recordHit(point, {{
                        address: address.toString(),
                        registers: contextRegs(this.context),
                        stack_preview_hex: readBytes(sp, 64),
                        buffer_preview_hex: readBytes(address, Number(point.size || 32)),
                        buffer_preview_ascii: readAscii(address, Number(point.size || 32)),
                    }});
                }}
            }});
        }} else if (typeof MemoryAccessMonitor !== "undefined") {{
            MemoryAccessMonitor.enable({{ base: address, size: Math.max(1, Number(point.size || 1)) }}, {{
                onAccess(details) {{
                    recordHit(point, {{
                        address: address.toString(),
                        operation: String(details.operation || ""),
                        from: details.from ? details.from.toString() : "",
                        registers: contextRegs(details.context || {{}}),
                        buffer_preview_hex: readBytes(address, Number(point.size || 32)),
                        buffer_preview_ascii: readAscii(address, Number(point.size || 32)),
                    }});
                }}
            }});
        }} else {{
            send({{
                type: "base64_rc4_hook_error",
                point_kind: String(point.kind || ""),
                point_name: String(point.name || ""),
                error: "MemoryAccessMonitor unavailable",
            }});
        }}
    }} catch (error) {{
        send({{
            type: "base64_rc4_hook_error",
            point_kind: String(point.kind || ""),
            point_name: String(point.name || ""),
            error: String(error),
        }});
    }}
}}

Interceptor.attach(compareSite, {{
    onEnter(args) {{
        const stackBase = ptr(this.context.sp || this.context.esp);
        const lhsPtr = stackBase.readPointer();
        const rhsPtr = stackBase.add(4).readPointer();
        const count = stackBase.add(8).readU32();
        send({{
            type: "base64_rc4_hook",
            point_kind: "compare",
            point_name: "wide_flag_prefix_compare",
            hook_kind: "interceptor",
            address: compareSite.toString(),
            module_offset: "0x" + compareOffset.toString(16),
            hit_count: 1,
            registers: contextRegs(this.context),
            stack_preview_hex: readBytes(stackBase, 64),
            lhs_ptr: lhsPtr.toString(),
            rhs_ptr: rhsPtr.toString(),
            compare_count: count,
            lhs_preview_hex: readBytes(lhsPtr, Math.max(10, count * 2)),
            rhs_preview_hex: readBytes(rhsPtr, Math.max(10, count * 2)),
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
                if win.window_text() or win.exists(timeout=0.1):
                    break
            except Exception as exc:  # pragma: no cover - best effort UI attach
                last_exc = exc
                time.sleep(0.1)
        if win is None:
            raise RuntimeError(f"cannot connect target window: {last_exc}")

        input_edit = win.child_window(auto_id="1001", control_type="Edit")
        decrypt_btn = win.child_window(auto_id="1000", control_type="Button")
        candidate = bytes.fromhex(args.probe_hex).decode("latin1")
        evidence.append(f"base64_rc4_probe:title={_escape_runtime_text(win.window_text() or '')}")
        evidence.append(f"base64_rc4_probe:probe_hex={args.probe_hex}")
        before_count = len(messages)
        input_edit.set_edit_text(_candidate_to_gui_text(candidate))
        _trigger_decrypt(decrypt_btn)

        deadline = time.monotonic() + max(0.3, float(args.per_probe_timeout))
        while time.monotonic() < deadline:
            if script_errors:
                raise RuntimeError(script_errors[-1])
            time.sleep(0.05)

        hook_events = [
            _normalize_hook_event(payload)
            for payload in messages[before_count:]
            if str(payload.get("type", "")) == "base64_rc4_hook"
        ]
        hook_errors = [
            str(payload.get("error", ""))
            for payload in messages[before_count:]
            if str(payload.get("type", "")) == "base64_rc4_hook_error"
        ]
        if hook_errors:
            evidence.extend(f"base64_rc4_probe:hook_error={_escape_runtime_text(item)}" for item in hook_errors if item)
        evidence.append(f"base64_rc4_probe:hook_events={len(hook_events)}")
        return _write_payload(
            out_path,
            _build_payload(
                success=True,
                summary="Base64RC4BreakpointProbe completed scripted breakpoint attempt.",
                candidate_hex=args.probe_hex,
                static_points=static_points,
                hook_events=hook_events,
                evidence=evidence,
            ),
        )
    except Exception as exc:
        evidence.append(f"base64_rc4_probe:error={_escape_runtime_text(str(exc))}")
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="Base64RC4BreakpointProbe execution failed.",
                candidate_hex=args.probe_hex,
                static_points=static_points,
                evidence=evidence,
                error=str(exc),
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
