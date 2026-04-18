from __future__ import annotations

from typing import Iterable


_CORE_LINES = [
    "先做 strings 提取与关键词聚类（flag/check/correct/wrong/debug/compare/decrypt）。",
    "优先定位最终比较点（strcmp/memcmp/lstrcmp）并确认比较双方来源。",
    "模型推断必须绑定证据，不编造地址、API 调用或工具输出。",
]

_MODE_LINES = {
    "Static Analysis": [
        "静态优先：从常量、控制流、分支条件推导校验逻辑与输入变换。",
        "遇到可疑加密/编码时，先恢复最小可复现变换链再给答案。",
    ],
    "Dynamic Debug": [
        "动态优先：在最终 compare 前抓取关键缓冲区与期望值。",
        "优先覆盖反调试检查（IsDebuggerPresent/PEB/时间检测）后再跟踪主路径。",
    ],
    "Auto": [
        "自动模式下，若静态证据强则走静态；运行时线索强则走动态。",
    ],
}

_FULL_EXTRA_LINES = [
    "可借助 Frida/angr/Qiling 做自动化补证，但最终结论仍需回落到可解释证据链。",
    "若存在多候选，按“运行时通过 > 工具候选 > 纯模型猜测”排序。",
]

_GITHUB_SECURITY_REVERSE_LINES = [
    "参考 GitHub 上 OWASP MASTG/WSTG：优先白盒证据，再用动态验证闭环，避免只凭黑盒猜测。",
    "参考 GitHub 上 OWASP 测试方法：区分安全相关上下文，避免把编码当加密、避免脱离场景的误报。",
    "参考 GitHub 上 OWASP 流程：按“范围与目标 -> 映射入口点 -> 真实可利用验证 -> 报告”推进分析。",
]

_GITHUB_SECURITY_REVERSE_FULL_LINES = [
    "参考 GitHub 上 awesome-reverse-engineering：优先组合可复现工具链（IDA/Ghidra + x64dbg/WinDbg + Frida/angr）。",
    "跨平台样本优先确认格式与入口（PE/ELF/Mach-O/Android），再选对应调试与反混淆路径。",
]


def _iter_skill_lines(analysis_mode: str, profile: str) -> Iterable[str]:
    normalized_mode = (analysis_mode or "").strip()
    normalized_profile = (profile or "compact").strip().lower()
    yield from _CORE_LINES
    yield from _MODE_LINES.get(normalized_mode, _MODE_LINES["Auto"])
    yield from _GITHUB_SECURITY_REVERSE_LINES
    if normalized_profile == "full":
        yield from _FULL_EXTRA_LINES
        yield from _GITHUB_SECURITY_REVERSE_FULL_LINES


def get_ctf_reverse_skill_lines(analysis_mode: str, profile: str = "compact") -> list[str]:
    return list(_iter_skill_lines(analysis_mode=analysis_mode, profile=profile))
