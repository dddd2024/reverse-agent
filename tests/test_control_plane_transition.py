from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import reverse_agent.project_gate as project_gate


def _write_decision(
    state_dir: Path,
    *,
    decision_id: str = "decision_bootstrap",
    round_id: str = "round_bootstrap",
    branch: str = "codex/example-v1",
    allowed_path: str = "reverse_agent/example/**",
    immutable: bool = False,
) -> None:
    contract = {
        "transition_kernel_required": True,
        "required_branch": branch,
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [
            "python -m pytest tests/test_project_gate.py -q",
            "git diff --check",
        ],
        "allowed_source_paths": [allowed_path],
        "forbidden_mutated_paths": ["frontend/**"],
        "direct_push_to_main_allowed": False,
        "merge_allowed": False,
        "force_push_allowed": False,
        "rebase_during_execution_allowed": False,
        "destructive_operations_allowed": False,
        "unknown_binary_execution_allowed": False,
        "model_api_invocation_allowed": False,
        "external_reverse_tool_invocation_allowed": False,
    }
    if immutable:
        contract.update({
            "decision_content_immutable_after_activation": True,
            "decision_immutability_required": True,
            "starting_head": "a" * 40,
        })
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": decision_id,
                "round_id": round_id,
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )


def _write_structured_decision(
    state_dir: Path,
    *,
    decision_id: str = "decision_structured",
    round_id: str = "round_structured",
    branch: str = "codex/example-v1",
) -> None:
    """Write a Decision contract that uses the new structured ``allowed_commands``."""

    contract = {
        "transition_kernel_required": True,
        "required_branch": branch,
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [
            "python -m pytest tests/test_project_gate.py -q",
        ],
        "allowed_commands": [
            {
                "command_id": "status.git_status",
                "command": "git status --short",
                "phase": "status",
                "required": True,
                "expected_exit_codes": [0],
                "execution_surface": "trusted_worker",
                "operations": ["repository_observation"],
                "network_access": False,
            },
            {
                "command_id": "validation.diff_check",
                "command": "git diff --check",
                "phase": "validation",
                "required": True,
                "expected_exit_codes": [0],
                "execution_surface": "trusted_worker",
                "operations": ["diff_validation"],
                "network_access": False,
            },
        ],
        "allowed_mutated_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**", "project_state/decision_packet.md"],
        "reference_paths": ["docs/roadmap/example.md"],
        "capability_policy": {
            "runner_dispatch_allowed": False,
            "model_api_invocation_allowed": False,
            "external_reverse_tool_invocation_allowed": False,
            "unknown_binary_execution_allowed": False,
            "destructive_operations_allowed": False,
            "bmad_installation_allowed": False,
            "network_access_default_allowed": False,
            "local_network_exceptions": [],
            "ci_network_exceptions": [],
            "remote_observation_read_only_allowed": True,
            "direct_push_to_main_allowed": False,
            "merge_allowed": False,
            "force_push_allowed": False,
            "rebase_during_execution_allowed": False,
            "tag_or_release_allowed": False,
        },
        "path_risk_floor": [
            {"pattern": ".github/workflows/**", "minimum_risk": "R2"},
            {"pattern": "**/secrets/**", "minimum_risk": "R3"},
        ],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": decision_id,
                "round_id": round_id,
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )


def _write_execution_log(
    state_dir: Path,
    *,
    commands: list[dict],
    decision_id: str = "decision_structured",
    round_id: str = "round_structured",
) -> None:
    """Write a minimal ``execution_log.json`` for reconciliation tests."""

    gates = state_dir / "gates"
    gates.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "artifact_name": "execution_log.json",
        "gate_name": "transition-execution-log",
        "gate_status": "PASSED",
        "decision_id": decision_id,
        "round_id": round_id,
        "report_id": "test_report",
        "generated_at": "2026-07-21T00:00:00Z",
        "source": "observed_codex_tool_transcript",
        "commands": commands,
    }
    (gates / "execution_log.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _registry(repo_root: Path) -> None:
    registry = repo_root / ".codex-skills" / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "skills": {"reverse-agent-iteration": {"status": "active", "version": 2}},
            }
        ),
        encoding="utf-8",
    )


def _transition_test_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _real_transition_fixture(
    tmp_path: Path,
    *,
    decision_id: str = "decision_real_transition",
    round_id: str = "round_real_transition",
) -> dict[str, Path | str]:
    """Create a real git-backed transition preflight fixture.

    The gate itself runs against an actual repository and branch.  The
    mutable ``project_state`` authority directory is kept outside that repo
    so generated gate artifacts do not become implementation deltas.
    """

    repo = tmp_path / "repo"
    state_dir = tmp_path / "project_state"
    repo.mkdir()
    _transition_test_git(repo, "init", "-q", "-b", "main")
    _transition_test_git(repo, "config", "user.email", "tests@example.invalid")
    _transition_test_git(repo, "config", "user.name", "tests")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _transition_test_git(repo, "add", "base.txt")
    _transition_test_git(repo, "commit", "-qm", "base")
    base_sha = _transition_test_git(repo, "rev-parse", "HEAD")
    branch = "codex/real-transition"
    _transition_test_git(repo, "checkout", "-qb", branch)

    _write_decision(
        state_dir,
        decision_id=decision_id,
        round_id=round_id,
        branch=branch,
    )
    decision_text = (state_dir / "decision_packet.md").read_text(encoding="utf-8")
    decision_text = decision_text.replace('"activation_base_sha": "' + "a" * 40 + '"', f'"activation_base_sha": "{base_sha}"')
    (state_dir / "decision_packet.md").write_text(decision_text, encoding="utf-8")
    repo_decision = repo / "project_state" / "decision_packet.md"
    repo_decision.parent.mkdir(parents=True, exist_ok=True)
    repo_decision.write_text(decision_text, encoding="utf-8")
    _transition_test_git(repo, "add", "project_state/decision_packet.md")
    _transition_test_git(repo, "commit", "-qm", "Decision activation")
    _registry(repo)
    project_gate.transition_command_plan(state_dir=state_dir)
    return {
        "repo": repo,
        "state": state_dir,
        "decision_id": decision_id,
        "round_id": round_id,
    }


def _state_bytes(state_dir: Path) -> dict[str, bytes]:
    """Return the complete filename-to-bytes mapping for a project_state fixture."""

    return {
        path.relative_to(state_dir).as_posix(): path.read_bytes()
        for path in sorted(state_dir.rglob("*"))
        if path.is_file()
    }


def test_transition_command_plan_rebinds_to_active_decision(tmp_path: Path) -> None:
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir)
    first = project_gate.transition_command_plan(state_dir=state_dir)
    assert first["plan_status"] == "PASSED"
    assert first["decision_id"] == "decision_bootstrap"
    persisted = json.loads((state_dir / "gates" / "command_plan.json").read_text(encoding="utf-8"))
    assert persisted["round_id"] == "round_bootstrap"

    _write_decision(state_dir, decision_id="decision_second", round_id="round_second")
    second = project_gate.transition_command_plan(state_dir=state_dir)
    assert second["decision_id"] == "decision_second"
    assert second["commands"] == first["commands"]


def test_transition_command_plan_generates_structured_allowed_commands(tmp_path: Path) -> None:
    """Phase A: structured ``allowed_commands`` must produce typed plan entries."""

    state_dir = tmp_path / "project_state"
    _write_structured_decision(state_dir)
    result = project_gate.transition_command_plan(state_dir=state_dir)
    assert result["plan_status"] == "PASSED"
    assert result["decision_id"] == "decision_structured"
    # Bootstrap exception commands are appended after structured commands.
    commands = result["commands"]
    structured = [cmd for cmd in commands if not cmd.get("bootstrap_exception")]
    bootstrap = [cmd for cmd in commands if cmd.get("bootstrap_exception")]
    assert len(structured) == 2
    assert all(cmd["execution_surface"] == "trusted_worker" for cmd in structured)
    assert all(cmd["operations"] for cmd in structured)
    assert len(bootstrap) == 1
    assert all(cmd["bootstrap_exception"] is True for cmd in bootstrap)


def test_transition_lint_rejects_manually_changed_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir)
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    plan_path = state_dir / "gates" / "command_plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["commands"][0]["command"] = "python unexpected.py"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(project_gate, "_derive_repo_root", lambda _state_dir: tmp_path)
    result = project_gate.transition_lint(state_dir=state_dir)
    assert result["gate_status"] == "BLOCKED"
    assert any(item["name"] == "command_plan_provenance" and item["status"] == "FAIL" for item in result["checks"])


def test_transition_lint_stale_previous_decision_plan_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2-0: a tracked command_plan.json belonging to an OLDER Decision/round is
    stale historical generated evidence and must NOT block a fresh Decision
    bootstrap. transition-lint validates the deterministic projection instead."""
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, decision_id="decision_old", round_id="round_old")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_decision(state_dir, decision_id="decision_fresh", round_id="round_fresh")
    monkeypatch.setattr(project_gate, "_derive_repo_root", lambda _state_dir: tmp_path)
    result = project_gate.transition_lint(state_dir=state_dir)
    assert result["gate_status"] == "PASSED"
    provenance = next(item for item in result["checks"] if item["name"] == "command_plan_provenance")
    assert provenance["status"] == "PASS"
    identity = next(item for item in result["checks"] if item["name"] == "command_plan_identity")
    assert identity["status"] == "PASS"


def test_transition_lint_missing_previous_plan_passes_when_bootstrap_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2-0: with no tracked command_plan.json at all, transition-lint must
    validate the deterministic projection and pass where the contract allows
    bootstrap."""
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir)
    _registry(tmp_path)
    monkeypatch.setattr(project_gate, "_derive_repo_root", lambda _state_dir: tmp_path)
    result = project_gate.transition_lint(state_dir=state_dir)
    assert result["gate_status"] == "PASSED"
    provenance = next(item for item in result["checks"] if item["name"] == "command_plan_provenance")
    assert provenance["status"] == "PASS"


def test_transition_lint_current_plan_manually_changed_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """G2-0: a tracked plan that claims to belong to the CURRENT Decision/round
    but diverges from the deterministic projection must FAIL CLOSED."""
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, decision_id="decision_current", round_id="round_current")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    plan_path = state_dir / "gates" / "command_plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["commands"][0]["execution_surface"] = "trusted_worker"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(project_gate, "_derive_repo_root", lambda _state_dir: tmp_path)
    result = project_gate.transition_lint(state_dir=state_dir)
    assert result["gate_status"] == "BLOCKED"
    provenance = next(item for item in result["checks"] if item["name"] == "command_plan_provenance")
    assert provenance["status"] == "FAIL"


def test_transition_command_plan_generated_equals_deterministic_projection(tmp_path: Path) -> None:
    """G2-0: transition-command-plan persists exactly the deterministic
    projection that transition-lint validated."""
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, decision_id="decision_projection", round_id="round_projection")
    generated = project_gate.transition_command_plan(state_dir=state_dir)
    assert generated["plan_status"] == "PASSED"
    decision, contract = project_gate.load_transition_decision(state_dir / "decision_packet.md")
    projected = project_gate.build_transition_command_plan(decision, contract)
    persisted = json.loads((state_dir / "gates" / "command_plan.json").read_text(encoding="utf-8"))
    assert persisted == projected.to_dict()
    assert generated["commands"] == projected.to_dict()["commands"]


def _install_envelope_git_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    branch: str,
    base_sha: str,
    decision_commit: str,
    committed_files: str = "",
    working_files: str = "",
    staged_files: str = "",
) -> None:
    """Install a ``_transition_git`` stub returning the given values."""

    def fake_git(_repo_root: Path, *args: str, check: bool = True) -> str:
        del check
        if args == ("rev-parse", f"{base_sha}^{{commit}}"):
            return base_sha
        if args == ("rev-parse", "HEAD"):
            return "c" * 40
        if args == ("rev-list", "--reverse", f"{base_sha}..HEAD"):
            return decision_commit
        if args == ("diff-tree", "--no-commit-id", "--name-only", "-r", decision_commit):
            return "project_state/decision_packet.md"
        if args == ("rev-parse", f"{decision_commit}:project_state/decision_packet.md"):
            return "d" * 40
        if args == ("rev-parse", "HEAD:project_state/decision_packet.md"):
            return "d" * 40
        if args == ("diff", "--name-only", "--", "project_state/decision_packet.md"):
            return ""
        if args == ("diff", "--cached", "--name-only", "--", "project_state/decision_packet.md"):
            return ""
        if args == ("status", "--short", "--untracked-files=all", "--", "project_state/decision_packet.md"):
            return ""
        if args == ("branch", "--show-current"):
            return branch
        if args == ("merge-base", "HEAD", base_sha):
            return base_sha
        if args == ("log", "-1", "--format=%H", "--", "project_state/decision_packet.md"):
            return decision_commit
        if args == ("diff", "--name-only", f"{decision_commit}..HEAD"):
            return committed_files
        if args == ("diff", "--name-only"):
            return working_files
        if args == ("diff", "--cached", "--name-only"):
            return staged_files
        raise AssertionError(args)

    monkeypatch.setattr(project_gate, "_transition_git", fake_git)
    monkeypatch.setattr(
        project_gate.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )


def test_transition_preflight_uses_decision_branch_and_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, branch="codex/different-v2", allowed_path="reverse_agent/different/**")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_execution_log(
        state_dir,
        decision_id="decision_bootstrap",
        round_id="round_bootstrap",
        commands=[
            {"index": 1, "command": "python -m pytest tests/test_project_gate.py -q", "phase": "test", "exit_code": 0},
            {"index": 2, "command": "git diff --check", "phase": "validation", "exit_code": 0},
        ],
    )
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/different-v2",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="reverse_agent/different/module.py",
    )
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path)
    # Phase B: pre mode returns PRE_EXECUTION_AUTHORIZED (not PASSED).
    assert result["gate_status"] == "PRE_EXECUTION_AUTHORIZED"
    branch_check = next(item for item in result["checks"] if item["name"] == "branch_identity")
    assert "expected=codex/different-v2" in branch_check["detail"]


def test_transition_preflight_dry_run_is_project_state_byte_and_path_stable(
    tmp_path: Path,
) -> None:
    """A diagnostic preflight must not persist any project_state artifact."""

    fixture = _real_transition_fixture(tmp_path)
    state_dir = fixture["state"]
    before = _state_bytes(state_dir)

    result = project_gate.transition_preflight(
        state_dir=state_dir,
        repo_root=fixture["repo"],
        write_result=False,
    )

    assert result["gate_status"] == "PRE_EXECUTION_AUTHORIZED", result
    assert _state_bytes(state_dir) == before


def test_transition_preflight_write_is_bootstrap_expiry_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated successful writes retain the first bootstrap expiry bytes."""

    fixture = _real_transition_fixture(tmp_path)
    state_dir = fixture["state"]
    bootstrap_path = state_dir / "gates" / "bootstrap_state.json"
    monkeypatch.setattr(project_gate, "_utc_now_iso", lambda: "2026-08-26T01:00:00+00:00")

    first = project_gate.transition_preflight(
        state_dir=state_dir,
        repo_root=fixture["repo"],
        write_result=True,
    )
    first_bytes = bootstrap_path.read_bytes()
    monkeypatch.setattr(project_gate, "_utc_now_iso", lambda: "2026-08-26T02:00:00+00:00")
    second = project_gate.transition_preflight(
        state_dir=state_dir,
        repo_root=fixture["repo"],
        write_result=True,
    )

    assert first["gate_status"] == "PRE_EXECUTION_AUTHORIZED", first
    assert second["gate_status"] == "PRE_EXECUTION_AUTHORIZED", second
    assert bootstrap_path.read_bytes() == first_bytes


@pytest.mark.parametrize(
    ("old_decision", "old_round"),
    [
        ("decision_inherited", "round_real_transition"),
        ("decision_real_transition", "round_inherited"),
    ],
)
def test_transition_preflight_write_rebinds_inherited_bootstrap_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_decision: str,
    old_round: str,
) -> None:
    """A successful write rebinds an expired bootstrap to current authority."""

    fixture = _real_transition_fixture(tmp_path)
    state_dir = fixture["state"]
    bootstrap_path = state_dir / "gates" / "bootstrap_state.json"
    bootstrap_path.write_text(
        json.dumps(
            {
                "status": "BOOTSTRAP_EXPIRED",
                "decision_id": old_decision,
                "round_id": old_round,
                "expired_at": "2026-08-25T23:00:00+00:00",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(project_gate, "_utc_now_iso", lambda: "2026-08-26T03:00:00+00:00")

    result = project_gate.transition_preflight(
        state_dir=state_dir,
        repo_root=fixture["repo"],
        write_result=True,
    )
    rebound = json.loads(bootstrap_path.read_text(encoding="utf-8"))

    assert result["gate_status"] == "PRE_EXECUTION_AUTHORIZED", result
    assert rebound == {
        "status": "BOOTSTRAP_EXPIRED",
        "decision_id": fixture["decision_id"],
        "round_id": fixture["round_id"],
        "expired_at": "2026-08-26T03:00:00+00:00",
    }


def test_active_immutability_structured_evidence_in_preflight_and_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, immutable=True)
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="reverse_agent/example/module.py",
    )
    preflight = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path, write_result=False)
    pre_check = next(item for item in preflight["checks"] if item["name"] == "decision_content_immutability")
    assert pre_check["status"] == "PASS" and pre_check["evidence"]["applicable"] is True
    reconcile = project_gate.transition_reconcile(state_dir=state_dir, repo_root=tmp_path, write_result=False)
    reconcile_check = next(item for item in reconcile["checks"] if item["name"] == "decision_content_immutability")
    assert reconcile_check["status"] == "PASS" and reconcile_check["evidence"]["applicable"] is True


def test_transition_preflight_blocks_when_execution_log_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phase B: missing execution evidence is fine in pre mode (pre-authorized)."""

    state_dir = tmp_path / "project_state"
    _write_decision(state_dir, branch="codex/example-v1", allowed_path="reverse_agent/example/**")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="reverse_agent/example/module.py",
    )
    # Phase B: pre mode must NOT consume execution_log as completion evidence;
    # a missing log is acceptable for pre-execution authorization.
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path, mode="pre")
    assert result["gate_status"] == "PRE_EXECUTION_AUTHORIZED"


def test_transition_preflight_enforces_structured_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Phases A-C: structured contract surfaces capability, path-risk, reference checks."""

    state_dir = tmp_path / "project_state"
    _write_structured_decision(state_dir, branch="codex/example-v1")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_execution_log(
        state_dir,
        decision_id="decision_structured",
        round_id="round_structured",
        commands=[
            {
                "index": 1,
                "command": "git status --short",
                "command_id": "status.git_status",
                "phase": "status",
                "exit_code": 0,
                "operations": ["repository_observation"],
                "execution_surface": "trusted_worker",
            },
            {
                "index": 2,
                "command": "git diff --check",
                "command_id": "validation.diff_check",
                "phase": "validation",
                "exit_code": 0,
                "operations": ["diff_validation"],
                "execution_surface": "trusted_worker",
            },
        ],
    )
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="reverse_agent/example/module.py",
    )
    # Phase B: execution_reconciliation + execution_evidence_present checks
    # only appear in post mode (pre mode passes empty envelopes on purpose).
    # Required coverage now matches the stable (command_id, execution_surface)
    # identity: the fixture above declares the exact command_id and the exact
    # execution_surface for every required command so the post gate can
    # reconcile against the plan's authorized identities.
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path, mode="post")
    check_names = {item["name"] for item in result["checks"]}
    assert "capability_policy_enforced" in check_names
    assert "path_risk_floor_enforced" in check_names
    assert "reference_paths_read_only" in check_names
    assert "execution_reconciliation" in check_names
    assert "execution_evidence_present" in check_names
    assert result["gate_status"] == "POST_EXECUTION_RECONCILED", result["checks"]


def test_transition_preflight_blocks_when_envelope_command_undeclared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase B: undeclared commands in the execution log must block."""

    state_dir = tmp_path / "project_state"
    _write_structured_decision(state_dir, branch="codex/example-v1")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_execution_log(
        state_dir,
        decision_id="decision_structured",
        round_id="round_structured",
        commands=[
            {
                "index": 1,
                "command": "git status --short",
                "phase": "status",
                "exit_code": 0,
                "operations": ["repository_observation"],
                "execution_surface": "local",
            },
            {
                "index": 2,
                "command": "rm -rf /",
                "phase": "destructive",
                "exit_code": 0,
                "execution_surface": "local",
            },
        ],
    )
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="reverse_agent/example/module.py",
    )
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path, mode="post")
    assert result["gate_status"] == "BLOCKED"
    reconciliation = next(item for item in result["checks"] if item["name"] == "execution_reconciliation")
    assert reconciliation["status"] == "FAIL"
    assert "undeclared_command" in reconciliation["detail"]


def test_transition_preflight_blocks_when_path_risk_floor_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase D: mutating a secrets path must trigger the path risk floor."""

    state_dir = tmp_path / "project_state"
    _write_structured_decision(state_dir, branch="codex/example-v1")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_execution_log(
        state_dir,
        decision_id="decision_structured",
        round_id="round_structured",
        commands=[
            {"index": 1, "command": "git status --short", "phase": "status", "exit_code": 0},
            {"index": 2, "command": "git diff --check", "phase": "validation", "exit_code": 0},
        ],
    )
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="config/secrets/api.key",
    )
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path)
    assert result["gate_status"] == "BLOCKED"
    floor_check = next(item for item in result["checks"] if item["name"] == "path_risk_floor_enforced")
    assert floor_check["status"] == "FAIL"
    assert "secrets" in floor_check["detail"]


def test_transition_preflight_blocks_when_reference_path_mutated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase C: reference (read-only) paths must not appear in mutated paths."""

    state_dir = tmp_path / "project_state"
    _write_structured_decision(state_dir, branch="codex/example-v1")
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _write_execution_log(
        state_dir,
        decision_id="decision_structured",
        round_id="round_structured",
        commands=[
            {"index": 1, "command": "git status --short", "phase": "status", "exit_code": 0},
            {"index": 2, "command": "git diff --check", "phase": "validation", "exit_code": 0},
        ],
    )
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="docs/roadmap/example.md",
    )
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path)
    assert result["gate_status"] == "BLOCKED"
    reference_check = next(item for item in result["checks"] if item["name"] == "reference_paths_read_only")
    assert reference_check["status"] == "FAIL"


def test_transition_preflight_blocks_reference_path_even_when_in_allowed_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F10: a reference/read-only path must fail closed when writable scope conflicts.

    The shared transition authority loader now rejects a path that appears in
    both ``allowed_mutated_paths`` and the reference (read-only) class before
    any mutation is observed.  The early loader rejection is the legitimate,
    fail-closed outcome; the gate must NOT keep running to a downstream
    ``reference_paths_read_only`` check that is never reached.
    """

    state_dir = tmp_path / "project_state"
    # Build a contract where a reference path is ALSO in allowed_mutated_paths
    # (a misconfiguration that the shared loader must fail closed on).
    contract = {
        "transition_kernel_required": True,
        "required_branch": "codex/example-v1",
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [],
        "allowed_commands": [
            {
                "command": "git status --short",
                "phase": "status",
                "required": True,
                "expected_exit_codes": [0],
                "execution_surface": "trusted_worker",
                "operations": ["repository_observation"],
                "network_access": False,
            },
        ],
        "allowed_mutated_paths": ["docs/roadmap/example.md"],
        "forbidden_mutated_paths": ["frontend/**"],
        "reference_paths": ["docs/roadmap/example.md"],
        "capability_policy": {
            "network_access_default_allowed": False,
            "local_network_exceptions": [],
            "ci_network_exceptions": [],
        },
        "path_risk_floor": [],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": "decision_ref",
                "round_id": "round_ref",
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )
    _registry(tmp_path)
    project_gate.transition_command_plan(state_dir=state_dir)
    _install_envelope_git_stub(
        monkeypatch,
        branch="codex/example-v1",
        base_sha="a" * 40,
        decision_commit="b" * 40,
        committed_files="docs/roadmap/example.md",
    )
    result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path)
    # The shared loader fails closed before the downstream per-path checks run.
    # Verify the conflict is rejected at authority load time.
    assert result["gate_status"] == "BLOCKED"
    blocking = "\n".join(result.get("blocking_reasons") or [])
    assert "allowed_reference_path_conflict" in blocking, result["blocking_reasons"]
    check_names = {item["name"] for item in result["checks"]}
    assert "reference_paths_read_only" not in check_names


def test_transition_preflight_blocks_when_mutation_grant_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Phase D: a command mutating a path outside its grant must block.

    Rule #1: every observed mutated path must belong to the command's
    authorized set (``produced_artifacts ∪ allowed_mutated_paths``).
    """

    from reverse_agent.control_plane.models import (
        ExecutionEnvelope as TransitionExecutionEnvelope,
    )
    from reverse_agent.control_plane.models import (
        TransitionAuthority,
        TransitionCommand,
        TransitionCommandPlan,
        TransitionDecision,
    )
    from reverse_agent.control_plane.transition import validate_transition

    decision = TransitionDecision(
        "decision_grants", "round_grants", "APPROVED", "engineering_branch",
        ("reverse-agent-iteration@v2",),
    )
    plan = TransitionCommandPlan(
        decision_id=decision.decision_id,
        round_id=decision.round_id,
        commands=(
            TransitionCommand(
                "python -m pytest tests/test_x.py -q",
                "test", True, (0,), "local", ("unit_test",),
                command_id="test.unit",
                allowed_mutated_paths=("tests/test_x.py",),
            ),
        ),
    )
    authority = TransitionAuthority(
        decision=decision,
        command_plan=plan,
        expected_decision_id=decision.decision_id,
        expected_round_id=decision.round_id,
        active_skills=("reverse-agent-iteration@v2",),
        legal_mainlines=("engineering_branch",),
        expected_branch="codex/example-v1",
        actual_branch="codex/example-v1",
        base_sha="a" * 40,
        merge_base_sha="a" * 40,
        decision_commit_sha="b" * 40,
        decision_is_ancestor=True,
        observed_paths=(),
        allowed_paths=("tests/**",),
        forbidden_paths=("frontend/**",),
        forbidden_operations=(),
    )
    # Envelope mutates a path NOT in the command's allowed_mutated_paths.
    envelope = TransitionExecutionEnvelope(
        command="python -m pytest tests/test_x.py -q",
        execution_surface="local",
        mutated_paths=("tests/test_OTHER.py",),  # not granted
        operations=("unit_test",),
        command_id="test.unit",
    )
    result = validate_transition(authority, (envelope,), mode="post")
    assert result.gate_status == "BLOCKED"
    grant_check = next(c for c in result.checks if c["name"] == "mutation_grants_enforced")
    assert grant_check["status"] == "FAIL"
    assert "tests/test_OTHER.py" in grant_check["detail"]


@pytest.mark.parametrize("missing", ["required_branch", "forbidden_mutated_paths"])
def test_transition_authority_missing_scope_fails_closed(tmp_path: Path, missing: str) -> None:
    state_dir = tmp_path / "project_state"
    _write_decision(state_dir)
    path = state_dir / "decision_packet.md"
    text = path.read_text(encoding="utf-8")
    marker = "```json decision_contract\n"
    start = text.index(marker) + len(marker)
    end = text.index("\n```", start)
    contract = json.loads(text[start:end])
    contract.pop(missing)
    path.write_text(text[:start] + json.dumps(contract) + text[end:], encoding="utf-8")
    if missing == "required_branch":
        result = project_gate.transition_command_plan(state_dir=state_dir)
    else:
        project_gate.transition_command_plan(state_dir=state_dir)
        result = project_gate.transition_preflight(state_dir=state_dir, repo_root=tmp_path)
    assert result.get("plan_status", result.get("gate_status")) == "BLOCKED"


def _write_collision_decision(
    state_dir: Path,
    *,
    decision_id: str = "decision_collision",
    round_id: str = "round_collision",
    branch: str = "codex/example-v1",
    commands: list[str],
    structured_command_ids: list[str] | None = None,
) -> None:
    """Write a Decision that exercises bootstrap command-ID semantics.

    The two bootstrap commands passed in share an identical long prefix so
    the old ``bootstrap.<command[:64]>`` derivation would collide.
    """

    if structured_command_ids is None:
        structured_command_ids = ["status.git_status"]
    structured_commands = []
    for i, cid in enumerate(structured_command_ids):
        if i == 1:
            cmd_str = "git diff --check"
            phase = "validation"
            ops = ["diff_validation"]
        else:
            cmd_str = "git status --short"
            phase = "status"
            ops = ["repository_observation"]
        structured_commands.append(
            {
                "command_id": cid,
                "command": cmd_str,
                "phase": phase,
                "required": True,
                "expected_exit_codes": [0],
                "execution_surface": "trusted_worker",
                "operations": ops,
                "network_access": False,
            }
        )
    contract = {
        "transition_kernel_required": True,
        "required_branch": branch,
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": commands,
        "allowed_commands": structured_commands,
        "allowed_mutated_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**"],
        "reference_paths": ["docs/roadmap/example.md"],
        "capability_policy": {
            "runner_dispatch_allowed": False,
            "model_api_invocation_allowed": False,
            "external_reverse_tool_invocation_allowed": False,
            "unknown_binary_execution_allowed": False,
            "destructive_operations_allowed": False,
            "bmad_installation_allowed": False,
            "network_access_default_allowed": False,
            "local_network_exceptions": [],
            "ci_network_exceptions": [],
            "remote_observation_read_only_allowed": True,
            "direct_push_to_main_allowed": False,
            "merge_allowed": False,
            "force_push_allowed": False,
            "rebase_during_execution_allowed": False,
            "tag_or_release_allowed": False,
        },
        "path_risk_floor": [],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": decision_id,
                "round_id": round_id,
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )


def _collision_commands() -> tuple[str, str]:
    """Two commands whose first 64 characters are identical but that differ."""

    prefix = "git fetch --depth=1 origin refs/heads/branch-owner-abc-xyz-1234567890-"
    assert len(prefix) >= 64
    assert (prefix + "TAIL-A")[:64] == (prefix + "TAIL-B")[:64]
    return prefix + "TAIL-A", prefix + "TAIL-B"


def test_bootstrap_command_ids_are_distinct_under_prefix_collision(tmp_path: Path) -> None:
    """Two bootstrap commands sharing an identical first 64 characters
    must yield distinct, deterministic command IDs.
    """

    from reverse_agent.control_plane.legacy_adapter import (
        build_transition_command_plan,
    )
    from reverse_agent.control_plane.models import TransitionDecision

    state_dir = tmp_path / "project_state"
    cmd_a, cmd_b = _collision_commands()
    _write_collision_decision(state_dir, commands=[cmd_a, cmd_b])
    decision, contract = project_gate.load_transition_decision(state_dir / "decision_packet.md")
    plan = build_transition_command_plan(decision, contract)
    assert project_gate.validate_transition_command_plan(plan) == ()
    bootstrap = [
        cmd for cmd in plan.commands if cmd.bootstrap_exception
    ]
    ids = [cmd.command_id for cmd in bootstrap]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert all(i for i in ids)
    assert all(i.startswith("bootstrap.") for i in ids)
    # ID is bounded: fixed "bootstrap." prefix + 64 hex digest
    assert all(len(i) == len("bootstrap.") + 64 for i in ids)
    # Same projection from the same Decision yields the same IDs.
    plan2 = build_transition_command_plan(decision, contract)
    ids2 = [cmd.command_id for cmd in plan2.commands if cmd.bootstrap_exception]
    assert ids2 == ids


def test_bootstrap_command_canonical_duplicates_are_deduped(tmp_path: Path) -> None:
    """Exact duplicate canonical bootstrap commands remain de-duplicated."""

    from reverse_agent.control_plane.legacy_adapter import (
        build_transition_command_plan,
    )
    from reverse_agent.control_plane.models import TransitionDecision

    state_dir = tmp_path / "project_state"
    cmd = "python -m pytest tests/test_project_gate.py -q"
    _write_collision_decision(state_dir, commands=[cmd, "  python   -m   pytest   tests/test_project_gate.py   -q  "])
    decision, contract = project_gate.load_transition_decision(state_dir / "decision_packet.md")
    plan = build_transition_command_plan(decision, contract)
    assert project_gate.validate_transition_command_plan(plan) == ()
    bootstrap = [cmd for cmd in plan.commands if cmd.bootstrap_exception]
    assert len(bootstrap) == 1
    assert bootstrap[0].command_id
    assert bootstrap[0].command_id.startswith("bootstrap.")


def test_structured_allowed_commands_ids_are_unchanged(tmp_path: Path) -> None:
    """Structured ``allowed_commands`` keep their authored command_id values."""

    state_dir = tmp_path / "project_state"
    ids = ["status.git_status", "validation.diff_check"]
    _write_collision_decision(state_dir, commands=[_collision_commands()[0]], structured_command_ids=ids)
    result = project_gate.transition_command_plan(state_dir=state_dir)
    assert result["plan_status"] == "PASSED"
    observed = [cmd for cmd in result["commands"] if not cmd.get("bootstrap_exception")]
    observed_ids = [cmd["command_id"] for cmd in observed]
    assert observed_ids == ids


def test_bootstrap_command_plan_provenance_rejects_tampered_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing transition lint / provenance behavior is unaffected."""

    from tests.test_project_gate import _write_decision as _pg_write_decision

    # Use the same decision shape used elsewhere in this suite.
    state_dir = tmp_path / "project_state"
    contract = {
        "transition_kernel_required": True,
        "required_branch": "codex/example-v1",
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [
            "python -m pytest tests/test_project_gate.py -q",
            "git diff --check",
        ],
        "allowed_source_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**"],
        "direct_push_to_main_allowed": False,
        "merge_allowed": False,
        "force_push_allowed": False,
        "rebase_during_execution_allowed": False,
        "destructive_operations_allowed": False,
        "unknown_binary_execution_allowed": False,
        "model_api_invocation_allowed": False,
        "external_reverse_tool_invocation_allowed": False,
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": "decision_provenance",
                "round_id": "round_provenance",
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )
    project_gate.transition_command_plan(state_dir=state_dir)
    plan_path = state_dir / "gates" / "command_plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["commands"][0]["command"] = "python unexpected.py"
    plan_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(project_gate, "_derive_repo_root", lambda _sd: tmp_path)
    result = project_gate.transition_lint(state_dir=state_dir)
    assert result["gate_status"] == "BLOCKED"
    assert any(
        item["name"] == "command_plan_provenance" and item["status"] == "FAIL"
        for item in result["checks"]
    )


def test_project_gate_transition_command_plan_preserves_bootstrap_collision(
    tmp_path: Path,
) -> None:
    """#178 AC#2: the public project-gate generation entrypoint must
    project a collision Decision into a PASSED plan that retains two
    distinct colliding bootstrap commands with distinct command IDs.

    This exercises ``project_gate.transition_command_plan`` directly, not
    the lower-level build/validate pair, closing the only remaining
    acceptance-coverage gap reported by Owner audit.
    """

    state_dir = tmp_path / "project_state"
    cmd_a, cmd_b = _collision_commands()
    _write_collision_decision(state_dir, commands=[cmd_a, cmd_b])
    result = project_gate.transition_command_plan(state_dir=state_dir)
    assert result["plan_status"] == "PASSED"
    assert result["decision_id"] == "decision_collision"
    bootstrap = [
        cmd for cmd in result["commands"] if cmd.get("bootstrap_exception")
    ]
    assert len(bootstrap) == 2
    bootstrap_commands = {cmd["command"] for cmd in bootstrap}
    assert cmd_a in bootstrap_commands
    assert cmd_b in bootstrap_commands
    ids = [cmd["command_id"] for cmd in bootstrap]
    assert len(ids) == 2
    assert all(i for i in ids)
    assert ids[0] != ids[1]
    assert all(i.startswith("bootstrap.") for i in ids)


def _write_136_incompatible_decision(state_dir: Path) -> None:
    """A fresh typed Decision with the #636 shape: enum-valid
    github_control_plane token + checkout/source-edit operations."""
    contract = {
        "transition_kernel_required": True,
        "required_branch": "codex/example-v1",
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [],
        "allowed_commands": [
            {
                "command_id": "materialize.mutation",
                "command": "git status --short",
                "phase": "implementation",
                "required": True,
                "expected_exit_codes": [0],
                "execution_surface": "github_control_plane",
                "operations": ["source_edit", "commit"],
                "network_access": False,
            },
        ],
        "allowed_mutated_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**"],
    }
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "decision_packet.md").write_text(
        "```json decision_meta\n"
        + json.dumps(
            {
                "schema_version": 1,
                "decision_id": "decision_636_shape",
                "round_id": "round_636_shape",
                "status": "APPROVED",
                "mainline": "engineering_branch",
                "skill_profiles": ["reverse-agent-iteration@v2"],
            }
        )
        + "\n```\n\n```json decision_contract\n"
        + json.dumps(contract)
        + "\n```\n",
        encoding="utf-8",
    )


def test_636_shape_rejected_by_state_gate_and_decision_preflight_same_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #636 plan is BLOCKED by the same canonical validator that State
    Gate (transition-lint / transition-command-plan) and Decision Preflight
    (transition-command-plan -> preflight) both consume. No divergence: the
    same bad plan cannot be accepted by one gate and rejected by the other."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    state_dir = tmp_path / "project_state"
    _write_136_incompatible_decision(state_dir)
    _registry(tmp_path)

    # State Gate / Decision Preflight authoring gate (transition-command-plan)
    # compiles the projection and validates it with the canonical validator.
    result = project_gate.transition_command_plan(state_dir=state_dir)
    assert result["plan_status"] == "BLOCKED"
    assert any(
        "operation_surface_incompatible" in reason or "legacy_local_surface" in reason
        for reason in (result.get("blocking_reasons") or [])
    )

    # The compiled projection itself is rejected by the shared validator.
    decision, contract = project_gate.load_transition_decision(state_dir / "decision_packet.md")
    plan = project_gate.build_transition_command_plan(decision, contract)
    errors = validate_command_plan(plan)
    assert any("operation_surface_incompatible:source_edit:github_control_plane" in e for e in errors)
    assert any("operation_surface_incompatible:commit:github_control_plane" in e for e in errors)

    # Decision Preflight cleanliness: no stale tracked plan may make the same
    # bad decision green. With no tracked plan present, preflight still loads
    # the tracked command_plan.json that transition-command-plan blocked for.
    import subprocess as _subprocess

    def fake_git(_repo_root: Path, *args: str, check: bool = True) -> str:
        del check
        if args == ("branch", "--show-current"):
            return "codex/example-v1"
        if args == ("merge-base", "HEAD", "a" * 40):
            return "a" * 40
        if args == ("log", "-1", "--format=%H", "--", "project_state/decision_packet.md"):
            return "b" * 40
        if args == ("diff", "--name-only", f"{'b'*40}..HEAD"):
            return ""
        if args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(project_gate, "_transition_git", fake_git)
    monkeypatch.setenv("GITHUB_HEAD_REF", "codex/example-v1")
    monkeypatch.setattr(
        project_gate.subprocess,
        "run",
        lambda *args, **kwargs: _subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    preflight = project_gate.transition_preflight(
        state_dir=state_dir, repo_root=tmp_path, mode="pre"
    )
    assert preflight["gate_status"] == "BLOCKED"
