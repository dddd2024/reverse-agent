import json
import os

import ida_auto
import ida_funcs
import ida_name
import ida_pro
import idautils
import idc

INTERESTING_KEYWORDS = [
    "flag",
    "key",
    "password",
    "correct",
    "wrong",
    "error",
    "success",
    "fail",
    "input",
    "check",
    "verify",
    "cmp",
    "strcmp",
    "memcmp",
    "debug",
    "decrypt",
    "encrypt",
    "xor",
    "md5",
    "sha",
]


def _parse_out_path() -> str:
    env_path = os.environ.get("REVERSE_AGENT_IDA_OUT", "").strip()
    if env_path:
        return env_path
    for arg in idc.ARGV[1:]:
        if arg.startswith("--out="):
            return arg.split("=", 1)[1]
    return os.path.join(os.getcwd(), "ida_evidence.json")


def _text_score(text: str) -> int:
    value = (text or "").lower()
    score = 0
    for kw in INTERESTING_KEYWORDS:
        if kw in value:
            score += 3
    if "{" in value or "}" in value:
        score += 3
    if any(ch.isdigit() for ch in value):
        score += 1
    if len(value) <= 64:
        score += 1
    return score


def _collect_strings(limit: int = 800) -> list[str]:
    values: list[tuple[int, str]] = []
    seen: set[str] = set()
    st = idautils.Strings()
    st.setup(minlen=4)
    for item in st:
        s = str(item)
        if s and s not in seen:
            seen.add(s)
            values.append((_text_score(s), s))
            if len(values) >= 20000:
                break

    values.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [s for _, s in values[:limit]]


def _collect_functions(limit: int = 1000) -> list[str]:
    values: list[tuple[int, str]] = []
    seen: set[str] = set()
    for ea in idautils.Functions():
        name = ida_name.get_short_name(ea) or ida_funcs.get_func_name(ea)
        if not name or name in seen:
            continue
        seen.add(name)
        score = _text_score(name)
        if name.lower().startswith(("sub_", "nullsub_")):
            score -= 2
        values.append((score, name))
    values.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in values[:limit]]


def _format_ea(ea: int) -> str:
    return f"0x{ea:X}"


def _safe_disasm(ea: int) -> str:
    try:
        return idc.generate_disasm_line(ea, 0) or ""
    except Exception:
        return ""


def _safe_strlit(ea: int) -> str:
    for strtype in (idc.STRTYPE_C, idc.STRTYPE_C_16):
        try:
            raw = idc.get_strlit_contents(ea, -1, strtype)
        except Exception:
            raw = None
        if not raw:
            continue
        if isinstance(raw, bytes):
            enc = "utf-16-le" if strtype == idc.STRTYPE_C_16 else "utf-8"
            return raw.decode(enc, errors="ignore").strip()
        return str(raw).strip()
    return ""


def _collect_compare_contexts(limit: int = 60) -> list[dict[str, str]]:
    compare_names = ("strcmp", "memcmp", "lstrcmp", "strncmp")
    imports: list[tuple[int, str]] = []
    for ea, name in idautils.Names():
        lower_name = (name or "").lower()
        if any(key in lower_name for key in compare_names):
            imports.append((ea, name))
    contexts: list[dict[str, str]] = []
    seen: set[tuple[int, int]] = set()
    for callee_ea, callee_name in imports:
        for xref in idautils.XrefsTo(callee_ea, 0):
            call_ea = int(xref.frm)
            func_ea = idc.get_func_attr(call_ea, idc.FUNCATTR_START)
            if func_ea == idc.BADADDR:
                continue
            key = (func_ea, call_ea)
            if key in seen:
                continue
            seen.add(key)
            disasm = _safe_disasm(call_ea)
            nearby: list[str] = []
            cur = call_ea
            for _ in range(4):
                cur = idc.prev_head(cur, func_ea)
                if cur == idc.BADADDR or cur < func_ea:
                    break
                line = _safe_disasm(cur)
                if line:
                    nearby.append(line)
            nearby.reverse()

            ref_strings: list[str] = []
            scan_eas = [*([idc.prev_head(call_ea, func_ea)] if func_ea != idc.BADADDR else []), call_ea]
            for insn_ea in scan_eas:
                if insn_ea == idc.BADADDR:
                    continue
                for ref in idautils.DataRefsFrom(insn_ea):
                    s = _safe_strlit(int(ref))
                    if s and s not in ref_strings:
                        ref_strings.append(s)
            contexts.append(
                {
                    "call_ea": _format_ea(call_ea),
                    "caller_func": ida_funcs.get_func_name(func_ea) or "",
                    "callee": callee_name,
                    "call_disasm": disasm,
                    "nearby": " || ".join(nearby[:4]),
                    "ref_strings": " | ".join(ref_strings[:3]),
                }
            )
            if len(contexts) >= limit:
                return contexts
    contexts.sort(key=lambda item: (-_text_score(item.get("ref_strings", "")), item["call_ea"]))
    return contexts[:limit]


def _collect_local_check_contexts(limit: int = 60) -> list[dict[str, str]]:
    contexts: list[dict[str, str]] = []
    seen: set[int] = set()
    for func_ea in idautils.Functions():
        fn = ida_funcs.get_func(func_ea)
        if not fn:
            continue
        for ea in idautils.FuncItems(func_ea):
            if idc.print_insn_mnem(ea).lower() != "call":
                continue
            call_ea = int(ea)
            if call_ea in seen:
                continue
            seen.add(call_ea)

            nearby_insn: list[tuple[int, str]] = []
            cur = call_ea
            for _ in range(8):
                cur = idc.prev_head(cur, func_ea)
                if cur == idc.BADADDR or cur < func_ea:
                    break
                line = _safe_disasm(cur)
                if line:
                    nearby_insn.append((int(cur), line))
            nearby_insn.reverse()

            ref_strings: list[str] = []
            imm_args: list[str] = []
            for insn_ea, line in nearby_insn:
                for ref in idautils.DataRefsFrom(insn_ea):
                    s = _safe_strlit(int(ref))
                    if s and s not in ref_strings:
                        ref_strings.append(s)
                low = line.lower()
                if low.startswith("push "):
                    token = line.split(" ", 1)[1].strip()
                    if token.startswith("0x") or token.isdigit():
                        imm_args.append(token)

            if not ref_strings:
                continue
            score = _text_score(" | ".join(ref_strings))
            # Heuristic: keep contexts that look like key checking nearby.
            if score < 2 and not imm_args:
                continue

            callee = idc.print_operand(call_ea, 0) or ""
            contexts.append(
                {
                    "call_ea": _format_ea(call_ea),
                    "caller_func": ida_funcs.get_func_name(func_ea) or "",
                    "callee": callee,
                    "call_disasm": _safe_disasm(call_ea),
                    "nearby": " || ".join(line for _, line in nearby_insn[:6]),
                    "ref_strings": " | ".join(ref_strings[:4]),
                    "imm_args": " | ".join(imm_args[:4]),
                    "kind": "local_call_context",
                }
            )
            if len(contexts) >= limit:
                return contexts
    contexts.sort(
        key=lambda item: (
            -_text_score(f"{item.get('ref_strings', '')} {item.get('nearby', '')}"),
            item["call_ea"],
        )
    )
    return contexts[:limit]


def _collect_control_id_contexts(limit: int = 40) -> list[dict[str, str]]:
    target_ids = {"3E8", "3E9", "3EA", "1000", "1001", "1002"}
    contexts: list[dict[str, str]] = []
    seen: set[int] = set()
    for func_ea in idautils.Functions():
        fn = ida_funcs.get_func(func_ea)
        if not fn:
            continue
        for ea in idautils.FuncItems(func_ea):
            line = _safe_disasm(ea)
            if not line:
                continue
            low = line.lower()
            if not low.startswith("push "):
                continue
            operand = low.split(" ", 1)[1].strip()
            token = operand.replace("0x", "").upper()
            if token not in target_ids:
                continue
            if int(ea) in seen:
                continue
            seen.add(int(ea))
            nearby: list[str] = []
            cur = int(ea)
            for _ in range(5):
                cur = idc.prev_head(cur, func_ea)
                if cur == idc.BADADDR or cur < func_ea:
                    break
                prev_line = _safe_disasm(cur)
                if prev_line:
                    nearby.append(prev_line)
            nearby.reverse()
            contexts.append(
                {
                    "ea": _format_ea(int(ea)),
                    "caller_func": ida_funcs.get_func_name(func_ea) or "",
                    "insn": line,
                    "nearby": " || ".join(nearby[:5]),
                    "kind": "control_id_context",
                }
            )
            if len(contexts) >= limit:
                return contexts
    contexts.sort(key=lambda item: item["ea"])
    return contexts[:limit]


def main() -> None:
    ida_auto.auto_wait()
    out_path = _parse_out_path()
    try:
        entry_ea = idc.get_inf_attr(idc.INF_START_IP)
    except Exception:
        entry_ea = idc.get_inf_attr(idc.INF_START_EA)
    payload = {
        "entry": hex(entry_ea),
        "strings": _collect_strings(),
        "functions": _collect_functions(),
        "compare_contexts": _collect_compare_contexts(),
        "local_check_contexts": _collect_local_check_contexts(),
        "control_id_contexts": _collect_control_id_contexts(),
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    ida_pro.qexit(0)


if __name__ == "__main__":
    main()
