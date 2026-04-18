from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


def solve_with_angr_stdin(
    file_path: Path,
    success_markers: list[str],
    fail_markers: list[str],
    max_input_len: int = 24,
    timeout_seconds: int = 90,
    max_candidates: int = 3,
    max_steps: int = 220,
    max_active_states: int = 160,
    log: LogFn | None = None,
) -> list[str]:
    logger = log or (lambda _: None)
    try:
        import angr  # type: ignore
        import claripy  # type: ignore
    except Exception:
        logger("angr 未安装，跳过符号执行后备求解。")
        return []

    logger("正在尝试 angr 符号执行后备求解...")
    start = time.monotonic()
    try:
        project = angr.Project(str(file_path), auto_load_libs=False)
        sym_bytes = [claripy.BVS(f"in_{i}", 8) for i in range(max_input_len)]
        stdin_bv = claripy.Concat(*sym_bytes, claripy.BVV(b"\n"))
        state = project.factory.full_init_state(args=[str(file_path)], stdin=stdin_bv)
        state.options.add(angr.options.ZERO_FILL_UNCONSTRAINED_MEMORY)
        state.options.add(angr.options.ZERO_FILL_UNCONSTRAINED_REGISTERS)
        for b in sym_bytes:
            state.solver.add(b >= 0x20, b <= 0x7E)

        simgr = project.factory.simulation_manager(state)
        candidates: list[str] = []
        seen: set[str] = set()
        steps = 0
        while simgr.active and time.monotonic() - start < timeout_seconds and steps < max_steps:
            steps += 1
            simgr.step()
            if len(simgr.active) > max_active_states:
                simgr.active = simgr.active[:max_active_states]
            if fail_markers:
                simgr.move(
                    "active",
                    "avoid",
                    lambda s: _state_has_any_marker(s, fail_markers),
                )

            stash_states = [*simgr.deadended, *simgr.active]
            for st in stash_states:
                if success_markers and not _state_has_any_marker(st, success_markers):
                    continue
                if fail_markers and _state_has_any_marker(st, fail_markers):
                    continue
                inp = _extract_input(st, sym_bytes)
                if not inp or inp in seen:
                    continue
                seen.add(inp)
                candidates.append(inp)
                if len(candidates) >= max_candidates:
                    logger(f"angr 找到候选 {len(candidates)} 个。")
                    return candidates
        if candidates:
            logger(f"angr 找到候选 {len(candidates)} 个。")
        else:
            logger("angr 未找到可用候选。")
        return candidates
    except Exception as exc:  # noqa: BLE001
        logger(f"angr 求解失败：{exc}")
        return []


def _state_has_any_marker(state, markers: list[str]) -> bool:  # noqa: ANN001
    out = ((state.posix.dumps(1) or b"") + b"\n" + (state.posix.dumps(2) or b"")).lower()
    return any(m.lower().encode("utf-8", errors="ignore") in out for m in markers if m)


def _extract_input(state, sym_bytes: list) -> str:  # noqa: ANN001
    try:
        import claripy  # type: ignore
    except Exception:
        return ""
    joined = claripy.Concat(*sym_bytes)
    data = state.solver.eval(joined, cast_to=bytes)
    value = data.split(b"\x00", 1)[0].decode("utf-8", errors="ignore").strip()
    return value
