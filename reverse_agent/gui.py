from __future__ import annotations

import shutil
import subprocess
import threading
import time
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

from .models import ModelError
from .pipeline import run_pipeline
from .tool_runners import ToolAutomationConfig


class App(tk.Tk):
    ANALYSIS_LABEL_TO_VALUE = {
        "自动判断": "Auto",
        "静态分析": "Static Analysis",
        "动态调试": "Dynamic Debug",
    }
    MODEL_LABEL_TO_VALUE = {
        "Copilot CLI": "Copilot CLI",
        "本地模型": "Local Model",
    }
    SKILL_PROFILE_LABEL_TO_VALUE = {
        "精简": "compact",
        "完整": "full",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Reverse Agent - 逆向解题助手")
        self.geometry("1040x820")

        detected_copilot_cmd = self._detect_copilot_command_template()
        self.input_var = tk.StringVar()
        self.analysis_mode_var = tk.StringVar(value="自动判断")
        self.model_var = tk.StringVar(
            value="Copilot CLI" if detected_copilot_cmd else "本地模型"
        )
        self.copilot_cmd_var = tk.StringVar(
            value=detected_copilot_cmd
            or 'gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s'
        )
        self.copilot_timeout_var = tk.StringVar(value="300")
        self.local_url_var = tk.StringVar(value="http://127.0.0.1:11434")
        self.local_model_var = tk.StringVar(value="qwen2.5-coder:7b")
        self.local_key_var = tk.StringVar(value="")

        default_ida_dir = Path(r"E:\Program Files\ida_pro")
        default_olly_dir = Path(r"E:\Program Files\ollydbg")
        default_olly_script = (
            Path(__file__).parent / "olly_scripts" / "collect_evidence.py"
        )
        self.tool_enabled_var = tk.BooleanVar(value=False)
        self.ida_enabled_var = tk.BooleanVar(value=True)
        self.ida_path_var = tk.StringVar(
            value=str(default_ida_dir) if default_ida_dir.exists() else ""
        )
        self.ida_script_var = tk.StringVar(value="")
        self.ida_timeout_var = tk.StringVar(value="180")
        self.olly_enabled_var = tk.BooleanVar(value=False)
        self.olly_path_var = tk.StringVar(
            value=str(default_olly_dir) if default_olly_dir.exists() else ""
        )
        self.olly_script_var = tk.StringVar(
            value=str(default_olly_script) if default_olly_script.exists() else ""
        )
        self.olly_timeout_var = tk.StringVar(value="120")
        self.runtime_validate_var = tk.BooleanVar(value=True)
        self.ctf_skill_enabled_var = tk.BooleanVar(value=True)
        self.ctf_skill_profile_var = tk.StringVar(value="精简")

        self.flag_var = tk.StringVar(value="")
        self.report_var = tk.StringVar(value="")
        self._build()
        if not detected_copilot_cmd:
            self._append_log(
                "未检测到 Copilot CLI 命令，默认切换到本地模型。你也可以手动填写命令模板。"
            )

    @staticmethod
    def _detect_copilot_command_template() -> str:
        if shutil.which("copilot"):
            return 'copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s'
        if shutil.which("github-copilot-cli"):
            return 'github-copilot-cli -p "{prompt}" --allow-all-tools --allow-all-paths -s'
        if App._is_gh_copilot_available():
            return 'gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s'
        return ""

    @staticmethod
    def _is_gh_copilot_available() -> bool:
        if shutil.which("gh") is None:
            return False
        try:
            proc = subprocess.run(
                ["gh", "copilot", "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if proc.returncode == 0:
            return True
        output = f"{proc.stdout}\n{proc.stderr}".lower()
        if "unknown command" in output or "not a gh command" in output:
            return False
        return False

    @staticmethod
    def _parse_timeout(value: str, field_name: str) -> int:
        try:
            timeout = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数秒数。") from exc
        if timeout <= 0:
            raise ValueError(f"{field_name} 必须大于 0。")
        return timeout

    def _build(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)

        ttk.Label(root, text="输入（EXE 文件路径或 URL）:").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        ttk.Entry(root, textvariable=self.input_var).grid(
            row=1, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Button(root, text="浏览", command=self._browse).grid(
            row=1, column=1, sticky="ew"
        )

        ttk.Label(root, text="模型类型:").grid(row=2, column=0, sticky="w", pady=(10, 4))
        ttk.Label(root, text="解题模式:").grid(row=2, column=1, sticky="w", pady=(10, 4))

        analysis_combo = ttk.Combobox(
            root,
            values=list(self.ANALYSIS_LABEL_TO_VALUE.keys()),
            textvariable=self.analysis_mode_var,
            state="readonly",
        )
        analysis_combo.grid(row=3, column=1, sticky="ew")

        model_combo = ttk.Combobox(
            root,
            values=list(self.MODEL_LABEL_TO_VALUE.keys()),
            textvariable=self.model_var,
            state="readonly",
        )
        model_combo.grid(row=3, column=0, sticky="ew", padx=(0, 8))
        model_combo.bind("<<ComboboxSelected>>", lambda _: self._toggle_model_fields())

        self.copilot_frame = ttk.LabelFrame(root, text="Copilot CLI 配置", padding=8)
        self.copilot_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(self.copilot_frame, text="命令模板:").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.copilot_frame, textvariable=self.copilot_cmd_var).grid(
            row=1, column=0, sticky="ew"
        )
        ttk.Label(
            self.copilot_frame,
            text='支持 "{prompt}" 占位符；未提供时会自动追加提示词。',
        ).grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(self.copilot_frame, text="调用超时(秒):").grid(
            row=3, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(self.copilot_frame, textvariable=self.copilot_timeout_var).grid(
            row=4, column=0, sticky="ew"
        )
        self.copilot_frame.columnconfigure(0, weight=1)

        self.local_frame = ttk.LabelFrame(root, text="本地模型配置", padding=8)
        self.local_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        ttk.Label(self.local_frame, text="Base URL:").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.local_frame, textvariable=self.local_url_var).grid(
            row=1, column=0, sticky="ew"
        )
        ttk.Label(self.local_frame, text="模型名称:").grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )
        ttk.Entry(self.local_frame, textvariable=self.local_model_var).grid(
            row=1, column=1, sticky="ew", padx=(8, 0)
        )
        ttk.Label(self.local_frame, text="API Key（可选）:").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(self.local_frame, textvariable=self.local_key_var, show="*").grid(
            row=3, column=0, columnspan=2, sticky="ew"
        )
        self.local_frame.columnconfigure(0, weight=1)
        self.local_frame.columnconfigure(1, weight=1)

        self.tool_frame = ttk.LabelFrame(root, text="工具链自动分析配置", padding=8)
        self.tool_frame.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Checkbutton(
            self.tool_frame,
            text="启用工具链自动分析（IDA + OllyDbg）",
            variable=self.tool_enabled_var,
        ).grid(row=0, column=0, columnspan=4, sticky="w")

        ttk.Checkbutton(
            self.tool_frame, text="启用 IDA 自动分析", variable=self.ida_enabled_var
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(self.tool_frame, text="IDA 路径:").grid(row=2, column=0, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.ida_path_var).grid(
            row=2, column=1, sticky="ew", padx=(6, 6)
        )
        ttk.Label(self.tool_frame, text="IDA 脚本:").grid(row=2, column=2, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.ida_script_var).grid(
            row=2, column=3, sticky="ew", padx=(6, 0)
        )
        ttk.Label(self.tool_frame, text="IDA 超时(秒):").grid(row=3, column=0, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.ida_timeout_var).grid(
            row=3, column=1, sticky="ew", padx=(6, 6), pady=(4, 0)
        )

        ttk.Checkbutton(
            self.tool_frame, text="启用 OllyDbg 自动化", variable=self.olly_enabled_var
        ).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Label(self.tool_frame, text="OllyDbg 路径:").grid(row=5, column=0, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.olly_path_var).grid(
            row=5, column=1, sticky="ew", padx=(6, 6)
        )
        ttk.Label(self.tool_frame, text="OllyDbg 脚本:").grid(row=5, column=2, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.olly_script_var).grid(
            row=5, column=3, sticky="ew", padx=(6, 0)
        )
        ttk.Label(self.tool_frame, text="OllyDbg 超时(秒):").grid(row=6, column=0, sticky="w")
        ttk.Entry(self.tool_frame, textvariable=self.olly_timeout_var).grid(
            row=6, column=1, sticky="ew", padx=(6, 6), pady=(4, 0)
        )
        ttk.Checkbutton(
            self.tool_frame,
            text="启用本地运行时校验（会执行样本 EXE）",
            variable=self.runtime_validate_var,
        ).grid(row=7, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(
            self.tool_frame,
            text="仅在隔离环境启用此选项，避免在主机直接执行未知样本。",
        ).grid(row=8, column=0, columnspan=4, sticky="w")
        ttk.Checkbutton(
            self.tool_frame,
            text="启用 CTF/逆向 Skill 增强（提示词注入）",
            variable=self.ctf_skill_enabled_var,
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(self.tool_frame, text="Skill 注入强度:").grid(
            row=10, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Combobox(
            self.tool_frame,
            values=list(self.SKILL_PROFILE_LABEL_TO_VALUE.keys()),
            textvariable=self.ctf_skill_profile_var,
            state="readonly",
        ).grid(row=10, column=1, sticky="ew", padx=(6, 6), pady=(4, 0))
        self.tool_frame.columnconfigure(1, weight=1)
        self.tool_frame.columnconfigure(3, weight=1)

        ttk.Button(root, text="开始解题", command=self._start).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0)
        )

        ttk.Label(root, text="最终 Flag:").grid(row=8, column=0, sticky="w", pady=(12, 4))
        ttk.Entry(root, textvariable=self.flag_var).grid(
            row=9, column=0, columnspan=2, sticky="ew"
        )

        ttk.Label(root, text="报告文件:").grid(row=10, column=0, sticky="w", pady=(8, 4))
        ttk.Entry(root, textvariable=self.report_var).grid(
            row=11, column=0, columnspan=2, sticky="ew"
        )

        ttk.Label(root, text="运行日志:").grid(row=12, column=0, sticky="w", pady=(8, 4))
        self.log_box = tk.Text(root, height=16)
        self.log_box.grid(row=13, column=0, columnspan=2, sticky="nsew")

        root.columnconfigure(0, weight=1)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(13, weight=1)
        self._toggle_model_fields()

    def _toggle_model_fields(self) -> None:
        mode = self.model_var.get()
        if mode == "Copilot CLI":
            self.copilot_frame.state(["!disabled"])
            self.local_frame.state(["disabled"])
        else:
            self.copilot_frame.state(["disabled"])
            self.local_frame.state(["!disabled"])

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 EXE 文件",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
        )
        if path:
            self.input_var.set(path)

    def _append_log(self, msg: str) -> None:
        self.log_box.insert(tk.END, msg + "\n")
        self.log_box.see(tk.END)

    @staticmethod
    def _phase_from_log(msg: str) -> str:
        if "正在提取可打印字符串" in msg:
            return "提取字符串"
        if "自动模式判定" in msg:
            return "分析模式判定"
        if "正在执行 IDA 自动化分析" in msg:
            return "IDA 自动分析"
        if "正在执行 OllyDbg 自动化脚本" in msg:
            return "OllyDbg 自动化"
        if "检测到证据规模较大" in msg:
            return "提示词精简"
        if "正在调用模型" in msg:
            return "模型求解"
        if "运行时校验" in msg:
            return "运行时校验"
        if msg.startswith("报告:"):
            return "写入报告"
        if msg.startswith("完成。Flag:"):
            return "完成"
        if msg.startswith("错误:"):
            return "失败"
        return ""

    def _start(self) -> None:
        self.flag_var.set("")
        self.report_var.set("")
        self._append_log("开始执行解题流程...")
        if self.runtime_validate_var.get():
            self._append_log(
                "⚠ 已默认启用运行时校验：将执行样本进行候选验证，请确保在隔离环境运行。"
            )

        t = threading.Thread(target=self._run_worker, daemon=True)
        t.start()

    def _run_worker(self) -> None:
        start_ts = time.monotonic()
        stop_event = threading.Event()
        state_lock = threading.Lock()
        status_state = {"phase": "初始化", "phase_since": start_ts}

        def log_with_status(msg: str) -> None:
            phase = self._phase_from_log(msg)
            now = time.monotonic()
            with state_lock:
                if phase and phase != status_state["phase"]:
                    status_state["phase"] = phase
                    status_state["phase_since"] = now
            self.after(0, self._append_log, msg)

        def heartbeat() -> None:
            while not stop_event.wait(5):
                now = time.monotonic()
                with state_lock:
                    phase = str(status_state["phase"])
                    phase_elapsed = int(now - float(status_state["phase_since"]))
                total_elapsed = int(now - start_ts)
                self.after(
                    0,
                    self._append_log,
                    f"[状态] 当前阶段：{phase}，总耗时 {total_elapsed}s（本阶段 {phase_elapsed}s）",
                )

        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            analysis_mode = self.ANALYSIS_LABEL_TO_VALUE[self.analysis_mode_var.get()]
            model_type = self.MODEL_LABEL_TO_VALUE[self.model_var.get()]
            auto_enable = bool(
                self.ida_path_var.get().strip()
                or self.ida_script_var.get().strip()
                or self.olly_path_var.get().strip()
                or self.olly_script_var.get().strip()
            )
            enabled = self.tool_enabled_var.get() or auto_enable
            if enabled and not self.tool_enabled_var.get():
                self.after(
                    0,
                    self._append_log,
                    "检测到已配置工具路径/脚本，自动启用工具链自动分析。",
                )
            olly_auto_enable = (
                analysis_mode == "Dynamic Debug"
                and not self.olly_enabled_var.get()
                and bool(self.olly_path_var.get().strip())
                and bool(self.olly_script_var.get().strip())
            )
            if olly_auto_enable:
                self.after(
                    0,
                    self._append_log,
                    "检测到 OllyDbg 路径与脚本均已配置，已自动启用 OllyDbg 自动化。",
                )
            tool_config = ToolAutomationConfig(
                enabled=enabled,
                ida_enabled=self.ida_enabled_var.get(),
                ida_executable=self.ida_path_var.get(),
                ida_script_path=self.ida_script_var.get(),
                ida_timeout_seconds=self._parse_timeout(
                    self.ida_timeout_var.get(), "IDA 超时"
                ),
                ollydbg_enabled=self.olly_enabled_var.get() or olly_auto_enable,
                ollydbg_executable=self.olly_path_var.get(),
                ollydbg_script_path=self.olly_script_var.get(),
                ollydbg_timeout_seconds=self._parse_timeout(
                    self.olly_timeout_var.get(), "OllyDbg 超时"
                ),
            )
            result = run_pipeline(
                input_value=self.input_var.get(),
                analysis_mode=analysis_mode,
                model_type=model_type,
                copilot_command=self.copilot_cmd_var.get(),
                local_base_url=self.local_url_var.get(),
                local_model=self.local_model_var.get(),
                local_api_key=self.local_key_var.get(),
                tool_config=tool_config,
                runtime_validation_enabled=self.runtime_validate_var.get(),
                reports_dir=Path("solve_reports"),
                log=log_with_status,
                copilot_timeout_seconds=self._parse_timeout(
                    self.copilot_timeout_var.get(), "Copilot 超时"
                ),
                ctf_skill_enabled=self.ctf_skill_enabled_var.get(),
                ctf_skill_profile=self.SKILL_PROFILE_LABEL_TO_VALUE[
                    self.ctf_skill_profile_var.get()
                ],
            )
            self.after(0, self.flag_var.set, result.selected_flag)
            self.after(0, self.report_var.set, result.report_path)
            self.after(0, self._append_log, f"完成。Flag: {result.selected_flag}")
            self.after(0, self._append_log, f"报告: {result.report_path}")
        except ModelError as exc:
            self.after(0, self._append_log, f"错误: {exc}")
        except Exception as exc:
            tb = traceback.format_exc()
            self.after(0, self._append_log, f"错误: {exc}")
            self.after(0, self._append_log, tb)
        finally:
            stop_event.set()


def launch_gui() -> None:
    app = App()
    app.mainloop()
