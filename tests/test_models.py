import subprocess

import pytest

from reverse_agent.models import CopilotCliBackend, ModelError


def test_copilot_backend_timeout_kills_tree(monkeypatch) -> None:
    killed: list[int] = []

    class _FakeProc:
        pid = 4321
        returncode = 0

        def communicate(self, timeout=None):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd="copilot", timeout=timeout or 1)

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc())  # noqa: ARG005
    monkeypatch.setattr(
        CopilotCliBackend,
        "_terminate_process_tree",
        staticmethod(lambda pid: killed.append(pid)),
    )

    backend = CopilotCliBackend(command_template='python -c "print(1)"', timeout_seconds=1)
    with pytest.raises(ModelError, match="timed out"):
        backend.solve("hello")
    assert killed == [4321]


def test_copilot_backend_reads_stdout(monkeypatch) -> None:
    class _FakeProc:
        pid = 1
        returncode = 0

        def communicate(self, timeout=None):  # noqa: ANN001, ARG002
            return ("flag{ok}\n", "")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc())  # noqa: ARG005
    backend = CopilotCliBackend(command_template='python -c "print(1)"', timeout_seconds=1)
    assert backend.solve("hello") == "flag{ok}"
