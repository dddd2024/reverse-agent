from __future__ import annotations


def get_analysis_template(analysis_mode: str) -> str:
    mode = (analysis_mode or "Static Analysis").strip()
    if mode != "Dynamic Debug":
        return "\n".join(
            [
                "分析模板：静态逆向",
                "1) 识别可疑字符串与符号。",
                "2) 基于常量与控制流线索推断校验逻辑。",
                "3) 交叉验证候选 flag 格式并排除误报。",
                "4) 只返回一个最可能的最终 flag。",
            ]
        )

    return "\n".join(
        [
            "分析模板：动态调试",
            "1) 运行时准备",
            "   - 确认架构（x86/x64）、壳特征与导入表。",
            "   - 在可控环境中运行并记录行为时间线。",
            "2) 断点规划",
            "   - 程序入口 / main / WinMain",
            "   - 字符串比较和内存比较 API（strcmp、memcmp、lstrcmp 等）",
            "   - 加解密相关 API 与自定义解码循环",
            "   - 失败分支、退出路径与反调试分支",
            "3) 反调试检查",
            "   - 检查 PEB BeingDebugged / NtGlobalFlag 相关逻辑",
            "   - 跟踪 IsDebuggerPresent / CheckRemoteDebuggerPresent / 时间检测",
            "   - 必要时绕过后继续跟踪",
            "4) 数据提取点",
            "   - 在最终 compare/check 前抓取关键缓冲区",
            "   - 记录变换后的输入与期望值",
            "   - 捕获 key/iv/salt/seed 及关键分支条件",
            "5) 验证",
            "   - 通过同一路径复现最终候选 flag",
            "   - 确认格式符合题目要求",
            "6) 输出",
            "   - 第一行只输出最终 flag。",
            "   - 然后基于运行时证据做简要说明。",
        ]
    )
