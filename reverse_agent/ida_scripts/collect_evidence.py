import json
import os

import ida_auto
import ida_funcs
import ida_name
import ida_pro
import idautils
import idc


def _parse_out_path() -> str:
    env_path = os.environ.get("REVERSE_AGENT_IDA_OUT", "").strip()
    if env_path:
        return env_path
    for arg in idc.ARGV[1:]:
        if arg.startswith("--out="):
            return arg.split("=", 1)[1]
    return os.path.join(os.getcwd(), "ida_evidence.json")


def _collect_strings(limit: int = 800) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    st = idautils.Strings()
    st.setup(minlen=4)
    for item in st:
        s = str(item)
        if s and s not in seen:
            seen.add(s)
            values.append(s)
            if len(values) >= limit:
                break
    return values


def _collect_functions(limit: int = 1000) -> list[str]:
    values: list[str] = []
    for ea in idautils.Functions():
        name = ida_name.get_short_name(ea) or ida_funcs.get_func_name(ea)
        if name:
            values.append(name)
            if len(values) >= limit:
                break
    return values


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
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    ida_pro.qexit(0)


if __name__ == "__main__":
    main()
