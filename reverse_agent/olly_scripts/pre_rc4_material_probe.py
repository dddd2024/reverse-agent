from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:  # Support both package imports in tests and direct script execution.
    from .compare_probe import _candidate_to_gui_text, _escape_runtime_text, _terminate_target, _trigger_decrypt
except ImportError:  # pragma: no cover - exercised by subprocess execution
    from compare_probe import _candidate_to_gui_text, _escape_runtime_text, _terminate_target, _trigger_decrypt


COMPARE_SITE_OFFSET = 0x258C
DEFAULT_MATCH_LIMIT = 3


def _normalize_memory_match(item: dict[str, object] | None) -> dict[str, object]:
    item = item or {}
    return {
        "material": str(item.get("material", "")),
        "status": str(item.get("status", "unavailable") or "unavailable"),
        "match_kind": str(item.get("match_kind", "")),
        "address": str(item.get("address", "")),
        "protection": str(item.get("protection", "")),
        "size": int(item.get("size", 0) or 0),
        "preview_hex": str(item.get("preview_hex", "")),
    }


def _probe_point_statuses(matches: list[dict[str, object]]) -> dict[str, str]:
    by_name = {str(item.get("material", "")): str(item.get("status", "")) for item in matches}

    def status(name: str) -> str:
        return "available" if by_name.get(name) == "available" else "unavailable"

    return {
        "raw_input": status("raw_input"),
        "expanded_bytes": status("expanded_bytes"),
        "utf16le_payload": status("utf16le_payload"),
        "base64_material": status("base64_ascii"),
        "rc4_ksa_key": status("rc4_ksa_key"),
        "rc4_encrypted_const": status("rc4_encrypted_const"),
        "rc4_output": status("rc4_output"),
        "compare_buffer": status("compare_buffer"),
    }


def _build_payload(
    *,
    success: bool,
    summary: str,
    candidate_hex: str = "",
    compare_site: str = "",
    compare_hit: bool = False,
    matches: list[dict[str, object]] | None = None,
    probe_points: dict[str, str] | None = None,
    evidence: list[str] | None = None,
    error: str = "",
) -> dict[str, object]:
    normalized_matches = [_normalize_memory_match(item) for item in matches or []]
    return {
        "success": success,
        "summary": summary,
        "candidate_hex": candidate_hex,
        "compare_site": compare_site,
        "compare_hit": compare_hit,
        "matches": normalized_matches,
        "probe_points": probe_points or _probe_point_statuses(normalized_matches),
        "evidence": evidence or [],
        "error": error,
    }


def _write_payload(out_path: Path, payload: dict[str, object]) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _read_materials(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan runtime memory for samplereverse pre-RC4 materials")
    parser.add_argument("--target", required=True, help="Path to target executable")
    parser.add_argument("--out", required=True, help="Output JSON path")
    parser.add_argument("--materials", required=True, help="Expected material JSON path")
    parser.add_argument("--probe-hex", required=True, help="Probe candidate as raw low-byte hex")
    parser.add_argument("--per-probe-timeout", type=float, default=2.2)
    parser.add_argument("--match-limit", type=int, default=DEFAULT_MATCH_LIMIT)
    args = parser.parse_args()

    target = Path(args.target)
    out_path = Path(args.out)
    materials_path = Path(args.materials)
    evidence = [f"pre_rc4_probe:target={target}", f"pre_rc4_probe:materials={materials_path}"]
    materials_payload = _read_materials(materials_path)
    materials = list(materials_payload.get("materials", [])) if isinstance(materials_payload.get("materials"), list) else []

    if not target.exists():
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="PreRC4MaterialProbe failed: target missing.",
                candidate_hex=args.probe_hex,
                evidence=[*evidence, "pre_rc4_probe:error=target_missing"],
                error="target_missing",
            ),
        )
    if not materials:
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="PreRC4MaterialProbe failed: expected materials missing.",
                candidate_hex=args.probe_hex,
                evidence=[*evidence, "pre_rc4_probe:error=materials_missing"],
                error="materials_missing",
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
                summary="PreRC4MaterialProbe failed: missing frida or pywinauto.",
                candidate_hex=args.probe_hex,
                evidence=[*evidence, f"pre_rc4_probe:error={_escape_runtime_text(str(exc))}"],
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

    materials_json = json.dumps(materials, ensure_ascii=True)
    match_limit = max(1, int(args.match_limit or DEFAULT_MATCH_LIMIT))
    script_source = f"""
const compareOffset = ptr("{COMPARE_SITE_OFFSET:#x}");
const expectedMaterials = {materials_json};
const matchLimit = {match_limit};
const maxRanges = 2048;

function hexToPattern(hex) {{
    const clean = String(hex || "").toLowerCase().replace(/[^0-9a-f]/g, "");
    if (clean.length < 2 || clean.length % 2 !== 0) {{
        return "";
    }}
    return clean.match(/../g).join(" ");
}}

function readPreview(address, size) {{
    try {{
        const readSize = Math.min(Math.max(size, 16), 64);
        const raw = address.readByteArray(readSize);
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

function enumerateReadableRanges() {{
    const protections = ["rw-", "r--", "r-x", "rwx"];
    let out = [];
    for (const protection of protections) {{
        try {{
            const ranges = Process.enumerateRangesSync({{ protection: protection, coalesce: true }});
            for (const range of ranges) {{
                if (range.size > 0 && range.size <= 0x2000000) {{
                    out.push({{ base: range.base, size: range.size, protection: protection }});
                    if (out.length >= maxRanges) {{
                        return out;
                    }}
                }}
            }}
        }} catch (error) {{
        }}
    }}
    return out;
}}

function scanOne(material, ranges) {{
    const name = String(material.name || "");
    const probes = [
        {{ kind: "full", hex: String(material.hex || "") }},
        {{ kind: "prefix", hex: String(material.prefix_hex || "") }},
    ];
    for (const probe of probes) {{
        const pattern = hexToPattern(probe.hex);
        if (!pattern) {{
            continue;
        }}
        for (const range of ranges) {{
            let matches = [];
            try {{
                matches = Memory.scanSync(range.base, range.size, pattern);
            }} catch (error) {{
                matches = [];
            }}
            if (matches.length > 0) {{
                const first = matches[0];
                return {{
                    material: name,
                    status: "available",
                    match_kind: probe.kind,
                    address: first.address.toString(),
                    protection: range.protection,
                    size: first.size,
                    preview_hex: readPreview(first.address, first.size),
                    match_count_capped: Math.min(matches.length, matchLimit),
                }};
            }}
        }}
    }}
    return {{
        material: name,
        status: "unavailable",
        match_kind: "",
        address: "",
        protection: "",
        size: 0,
        preview_hex: "",
        match_count_capped: 0,
    }};
}}

function scanMaterials() {{
    const ranges = enumerateReadableRanges();
    let out = [];
    for (const material of expectedMaterials) {{
        out.push(scanOne(material, ranges));
    }}
    return out;
}}

const mainModule = Process.enumerateModules()[0];
const compareSite = mainModule.base.add(compareOffset);

Interceptor.attach(compareSite, {{
    onEnter(args) {{
        const matches = scanMaterials();
        send({{
            type: "pre_rc4",
            compare_site: compareSite.toString(),
            matches: matches,
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
        evidence.append(f"pre_rc4_probe:title={_escape_runtime_text(win.window_text() or '')}")
        evidence.append(f"pre_rc4_probe:probe_hex={args.probe_hex}")
        before_count = len(messages)
        input_edit.set_edit_text(_candidate_to_gui_text(candidate))
        _trigger_decrypt(decrypt_btn)

        captured: dict[str, object] | None = None
        deadline = time.monotonic() + max(0.3, float(args.per_probe_timeout))
        while time.monotonic() < deadline:
            if len(messages) > before_count:
                for payload in messages[before_count:]:
                    if str(payload.get("type", "")) == "pre_rc4":
                        captured = payload
                        break
                if captured:
                    break
            if script_errors:
                raise RuntimeError(script_errors[-1])
            time.sleep(0.05)

        if not captured:
            return _write_payload(
                out_path,
                _build_payload(
                    success=False,
                    summary="PreRC4MaterialProbe did not hit compare trigger.",
                    candidate_hex=args.probe_hex,
                    compare_hit=False,
                    evidence=[*evidence, "pre_rc4_probe:error=no_compare_hit"],
                    error="no_compare_hit",
                ),
            )

        matches = [
            _normalize_memory_match(item)
            for item in list(captured.get("matches", []))
            if isinstance(item, dict)
        ]
        available = [item["material"] for item in matches if item["status"] == "available"]
        evidence.append(f"pre_rc4_probe:available={','.join(available) if available else '<none>'}")
        return _write_payload(
            out_path,
            _build_payload(
                success=True,
                summary="PreRC4MaterialProbe completed memory scan after compare trigger.",
                candidate_hex=args.probe_hex,
                compare_site=str(captured.get("compare_site", "")),
                compare_hit=True,
                matches=matches,
                evidence=evidence,
            ),
        )
    except Exception as exc:
        evidence.append(f"pre_rc4_probe:error={_escape_runtime_text(str(exc))}")
        return _write_payload(
            out_path,
            _build_payload(
                success=False,
                summary="PreRC4MaterialProbe execution failed.",
                candidate_hex=args.probe_hex,
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
