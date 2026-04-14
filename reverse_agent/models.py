from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass

import requests


class ModelError(RuntimeError):
    pass


@dataclass
class CopilotCliBackend:
    command_template: str
    timeout_seconds: int = 180

    @staticmethod
    def _extract_executable(template: str) -> str:
        value = template.strip()
        if not value:
            return ""
        if value[0] in {"'", '"'}:
            q = value[0]
            end = value.find(q, 1)
            return value[1:end] if end > 1 else value[1:]
        return value.split(maxsplit=1)[0]

    def solve(self, prompt: str) -> str:
        template = self.command_template.strip()
        if not template:
            raise ModelError("Copilot CLI command is empty.")

        executable = self._extract_executable(template)
        if executable and shutil.which(executable) is None:
            raise ModelError(
                "Copilot CLI command executable was not found. "
                f'Configured command: "{template}". '
                "Please install the CLI or update Command Template in GUI "
                '(for example: copilot "{prompt}", github-copilot-cli "{prompt}", or gh copilot -p "{prompt}").'
            )

        try:
            args = shlex.split(template, posix=False)
        except ValueError as exc:
            raise ModelError(f"Invalid Copilot CLI command template: {template}") from exc
        if not args:
            raise ModelError("Copilot CLI command is empty.")

        placeholder = "{prompt}"
        has_placeholder = any(placeholder in arg for arg in args)
        if has_placeholder:
            args = [arg.replace(placeholder, prompt) for arg in args]
        else:
            args.append(prompt)

        # Ensure non-interactive behavior for Copilot CLI so GUI does not hang.
        is_gh_copilot = len(args) >= 2 and args[0] == "gh" and args[1] == "copilot"
        is_direct_copilot = args[0] in {"copilot", "github-copilot-cli"}
        if is_gh_copilot or is_direct_copilot:
            has_prompt_flag = "-p" in args or "--prompt" in args
            if not has_prompt_flag:
                args.extend(["-p", prompt])

            has_silent_flag = "-s" in args or "--silent" in args
            if not has_silent_flag:
                args.append("-s")

            if "--allow-all-tools" not in args:
                args.append("--allow-all-tools")
            if "--allow-all-paths" not in args:
                args.append("--allow-all-paths")

        try:
            proc = subprocess.run(
                args,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModelError(
                "Copilot CLI call timed out. "
                "Use non-interactive flags in Command Template "
                '(recommended: gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s).'
            ) from exc
        if proc.returncode != 0:
            error_text = (proc.stderr.strip() or proc.stdout.strip()).strip()
            if "not recognized as an internal or external command" in error_text.lower() or "不是内部或外部命令" in error_text:
                raise ModelError(
                    "Copilot CLI command executable was not found. "
                    f'Configured command: "{template}". '
                    "Please install the CLI or update Command Template in GUI."
                )
            raise ModelError(
                f"Copilot CLI command failed (code {proc.returncode}): "
                f"{error_text}"
            )
        output = (proc.stdout or "").strip()
        if not output:
            stderr_text = (proc.stderr or "").strip()
            if stderr_text:
                raise ModelError(f"Copilot CLI returned no stdout. Stderr: {stderr_text}")
            raise ModelError(
                "Copilot CLI returned empty output. "
                "Try command template: gh copilot -p \"{prompt}\" --allow-all-tools --allow-all-paths -s"
            )
        return output


@dataclass
class LocalOpenAIBackend:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: int = 180

    def solve(self, prompt: str) -> str:
        base = self.base_url.strip().rstrip("/")
        if not base:
            raise ModelError("Local model base URL is empty.")
        if not self.model.strip():
            raise ModelError("Local model name is empty.")

        url = f"{base}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key.strip()}"

        payload = {
            "model": self.model.strip(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a reverse engineering assistant. "
                        "Find the most likely flag and return it directly. "
                        "If uncertain, still provide the best candidate."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
        }

        resp = requests.post(
            url, headers=headers, data=json.dumps(payload), timeout=self.timeout_seconds
        )
        if resp.status_code >= 400:
            raise ModelError(
                f"Local model request failed ({resp.status_code}): {resp.text[:500]}"
            )
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            raise ModelError(f"Unexpected local model response: {data}") from exc
