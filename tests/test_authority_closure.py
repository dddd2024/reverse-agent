"""Authority closure integration tests for the Architecture Spine v1 rework.

Covers the decision_packet section 8 required tests that target the
control-plane primitives directly rather than the full preflight.
"""

from __future__ import annotations

import pytest

from reverse_agent.control_plane.command_authority import (
    authorize_command,
    reconcile_command,
    validate_command_plan,
)
from reverse_agent.control_plane.execution_reconciliation import reconcile_executions
from reverse_agent.control_plane.legacy_adapter import (
    build_transition_command_plan,
    canonical_command,
    load_capability_policy,
    load_path_risk_floor,
    load_transition_scope,
)
from reverse_agent.control_plane.models import (
    CapabilityPolicy,
    ExecutionEnvelope,
    PathRiskFloor,
    TransitionAuthority,
    TransitionCommand,
    TransitionCommandPlan,
    TransitionDecision,
)
from reverse_agent.control_plane.transition import (
    _capability_forbidden_operations,
    _envelope_network_violations,
    _required_command_coverage_missing,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _decision(decision_id: str = "decision_test", round_id: str = "round_test") -> TransitionDecision:
    return TransitionDecision(
        decision_id=decision_id,
        round_id=round_id,
        status="APPROVED",
        mainline="engineering_branch",
        skill_profiles=("reverse-agent-iteration@v2",),
    )


def _structured_contract(*, allowed_commands: list, **extras) -> dict:
    contract = {
        "transition_kernel_required": True,
        "required_branch": "codex/example-v1",
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [],
        "allowed_commands": allowed_commands,
        "allowed_mutated_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**"],
        "capability_policy": {
            "runner_dispatch_allowed": False,
            "model_api_invocation_allowed": False,
            "external_reverse_tool_invocation_allowed": False,
            "unknown_binary_execution_allowed": False,
            "destructive_operations_allowed": False,
            "bmad_installation_allowed": False,
            "network_access_default_allowed": False,
            "local_network_exceptions": ["git push origin codex/example-v1"],
            "ci_network_exceptions": ["python -m pip install -e ."],
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
    contract.update(extras)
    return contract


def _structured_command(
    command: str = "git status --short",
    *,
    phase: str = "status",
    surface: str = "trusted_worker",
    operations: tuple[str, ...] = ("repository_observation",),
    network_access: bool = False,
    command_id: str = "",
) -> dict:
    if not command_id:
        # Derive a stable command_id from the command string so F8's
        # global-uniqueness requirement is satisfied for test fixtures.
        command_id = "test." + command.replace(" ", "_").replace("-", "_")
    return {
        "command": command,
        "command_id": command_id,
        "phase": phase,
        "required": True,
        "expected_exit_codes": [0],
        "execution_surface": surface,
        "operations": list(operations),
        "network_access": network_access,
    }


def _transition_authority(
    decision: TransitionDecision,
    plan: TransitionCommandPlan,
    *,
    allowed_paths: tuple[str, ...] = ("reverse_agent/example/**",),
    reference_paths: tuple[str, ...] = (),
    bootstrap_exception_files: tuple[str, ...] = (),
) -> TransitionAuthority:
    return TransitionAuthority(
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
        allowed_paths=allowed_paths,
        forbidden_paths=("frontend/**",),
        forbidden_operations=(),
        reference_paths=reference_paths,
        bootstrap_exception_files=bootstrap_exception_files,
    )


# ---------------------------------------------------------------------------
# Test 4: execution-surface mismatch rejection
# ---------------------------------------------------------------------------


def test_execution_surface_mismatch_rejects_envelope() -> None:
    """A plan-local command cannot be reconciled under a different surface."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[
            _structured_command("git status --short", surface="local"),
        ],
    )
    plan = build_transition_command_plan(decision, contract)
    envelope = ExecutionEnvelope(
        command="git status --short",
        execution_surface="ci_only",
        operations=("repository_observation",),
        exit_code=0,
    )
    errors = reconcile_command(plan, envelope)
    assert any("execution_surface_mismatch" in err for err in errors)


def test_authorize_command_rejects_cross_surface_envelope() -> None:
    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[
            _structured_command("git status --short", surface="local"),
        ],
    )
    plan = build_transition_command_plan(decision, contract)
    envelope = ExecutionEnvelope(
        command="git status --short",
        execution_surface="remote_observation",
    )
    errors = authorize_command(plan, envelope)
    assert any("execution_surface_mismatch" in err for err in errors)


# ---------------------------------------------------------------------------
# Test 6: bootstrap exception separation
# ---------------------------------------------------------------------------


def test_bootstrap_exception_commands_are_separately_marked() -> None:
    """Bootstrap exception commands must keep their marker through the plan."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("git status --short")],
    )
    contract["bootstrap_exception_commands"] = ["git rev-parse HEAD"]
    plan = build_transition_command_plan(decision, contract)

    bootstrap_commands = [cmd for cmd in plan.commands if cmd.bootstrap_exception]
    assert len(bootstrap_commands) == 1
    assert canonical_command(bootstrap_commands[0].command) == "git rev-parse HEAD"

    structured_commands = [cmd for cmd in plan.commands if not cmd.bootstrap_exception]
    assert len(structured_commands) == 1
    assert canonical_command(structured_commands[0].command) == "git status --short"


def test_reconcile_executions_classifies_bootstrap_exceptions() -> None:
    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("git status --short")],
    )
    contract["bootstrap_exception_commands"] = ["git rev-parse HEAD"]
    plan = build_transition_command_plan(decision, contract)
    envelopes = (
        ExecutionEnvelope(
            command="git status --short",
            execution_surface="trusted_worker",
            operations=("repository_observation",),
            exit_code=0,
        ),
        ExecutionEnvelope(
            command="git rev-parse HEAD",
            execution_surface="local",
            operations=(),
            exit_code=0,
            bootstrap_exception=True,
        ),
    )
    outcome = reconcile_executions(plan, envelopes)
    assert outcome.status == "POST_EXECUTION_RECONCILED"
    assert len(outcome.matched) == 2
    bootstrap_match = next(item for item in outcome.matched if item["bootstrap_exception"])
    assert canonical_command(bootstrap_match["command"]) == "git rev-parse HEAD"


# ---------------------------------------------------------------------------
# Test 7: capability flag mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "operation"),
    [
        ("runner_dispatch_allowed", "runner_dispatch"),
        ("model_api_invocation_allowed", "model_api_invocation"),
        ("external_reverse_tool_invocation_allowed", "external_reverse_tool_invocation"),
        ("unknown_binary_execution_allowed", "unknown_binary_execution"),
        ("destructive_operations_allowed", "destructive"),
        ("bmad_installation_allowed", "bmad_installation"),
        ("direct_push_to_main_allowed", "direct_push_main"),
        ("merge_allowed", "merge"),
        ("force_push_allowed", "force_push"),
        ("rebase_during_execution_allowed", "rebase"),
        ("tag_or_release_allowed", "tag_or_release"),
    ],
)
def test_capability_flag_false_adds_forbidden_operation(flag: str, operation: str) -> None:
    """Every capability flag that is ``False`` must map to a forbidden operation."""

    policy = CapabilityPolicy(**{flag: False})
    forbidden = _capability_forbidden_operations(policy)
    assert operation in forbidden


def test_capability_flag_true_removes_forbidden_operation() -> None:
    policy = CapabilityPolicy(merge_allowed=True, force_push_allowed=True)
    forbidden = _capability_forbidden_operations(policy)
    assert "merge" not in forbidden
    assert "force_push" not in forbidden


# ---------------------------------------------------------------------------
# #637 closure: capability vocabulary shared with the compatibility registry.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "operation"),
    [
        ("runner_dispatch_allowed", "runner_dispatch"),
        ("model_api_invocation_allowed", "model_api_invocation"),
        ("external_reverse_tool_invocation_allowed", "external_reverse_tool_invocation"),
        ("unknown_binary_execution_allowed", "unknown_binary_execution"),
        ("destructive_operations_allowed", "destructive"),
        ("bmad_installation_allowed", "bmad_installation"),
        ("direct_push_to_main_allowed", "direct_push_main"),
        ("merge_allowed", "merge"),
        ("force_push_allowed", "force_push"),
        ("rebase_during_execution_allowed", "rebase"),
        ("tag_or_release_allowed", "tag_or_release"),
    ],
)
def test_capability_flag_true_removes_operation_from_forbidden(flag: str, operation: str) -> None:
    """When a capability flag is True, its operation must be absent from the
    forbidden set (grantable). It therefore MUST have a compatibility entry."""
    from reverse_agent.control_plane.command_authority import OPERATION_SURFACE_ADMISSIBILITY

    policy = CapabilityPolicy(**{flag: True})
    forbidden = _capability_forbidden_operations(policy)
    assert operation not in forbidden
    # The grantable operation must be representable on an admissible surface,
    # otherwise the flag is semantically dead for current typed Decisions.
    assert operation in OPERATION_SURFACE_ADMISSIBILITY
    assert OPERATION_SURFACE_ADMISSIBILITY[operation]


def test_capability_forbidden_operations_derive_from_canonical_mapping() -> None:
    """_capability_forbidden_operations must enumerate exactly the canonical
    capability-flag vocabulary shared with the compatibility registry."""
    from reverse_agent.control_plane.command_authority import CAPABILITY_OPERATION_MAPPING

    all_allowed = _capability_forbidden_operations(CapabilityPolicy(
        runner_dispatch_allowed=True,
        model_api_invocation_allowed=True,
        external_reverse_tool_invocation_allowed=True,
        unknown_binary_execution_allowed=True,
        destructive_operations_allowed=True,
        bmad_installation_allowed=True,
        direct_push_to_main_allowed=True,
        merge_allowed=True,
        force_push_allowed=True,
        rebase_during_execution_allowed=True,
        tag_or_release_allowed=True,
    ))
    assert all_allowed == ()
    assert set(CAPABILITY_OPERATION_MAPPING.values()) == {
        "runner_dispatch",
        "model_api_invocation",
        "external_reverse_tool_invocation",
        "unknown_binary_execution",
        "destructive",
        "bmad_installation",
        "direct_push_main",
        "merge",
        "force_push",
        "rebase",
        "tag_or_release",
    }


def test_capability_vocabulary_registry_completeness_invariant() -> None:
    """GRANTABLE_CAPABILITY_OPERATIONS must be a subset of
    OPERATION_SURFACE_ADMISSIBILITY. If a developer adds a capability flag to
    the canonical mapping but forgets the compatibility entry, this test
    FAILS immediately instead of at a future real Decision execution."""
    from reverse_agent.control_plane.command_authority import (
        CAPABILITY_OPERATION_MAPPING,
        OPERATION_SURFACE_ADMISSIBILITY,
    )

    missing = sorted(
        operation
        for operation in CAPABILITY_OPERATION_MAPPING.values()
        if operation not in OPERATION_SURFACE_ADMISSIBILITY
    )
    assert missing == []


def test_load_capability_policy_reads_structured_mapping() -> None:
    contract = _structured_contract(allowed_commands=[])
    policy = load_capability_policy(contract)
    assert policy.merge_allowed is False
    assert policy.direct_push_to_main_allowed is False
    assert "git push origin codex/example-v1" in policy.local_network_exceptions
    assert "python -m pip install -e ." in policy.ci_network_exceptions


# ---------------------------------------------------------------------------
# Test 8: local network denial and exact exceptions
# ---------------------------------------------------------------------------


def test_local_network_access_denied_by_default() -> None:
    """Network operations on a local surface must be denied unless excepted."""

    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        local_network_exceptions=("git push origin codex/example-v1",),
    )
    forbidden_envelope = ExecutionEnvelope(
        command="git pull origin main",
        execution_surface="local",
        operations=("network_access",),
        exit_code=0,
    )
    violations = _envelope_network_violations((forbidden_envelope,), policy)
    assert any("git pull origin main" in v for v in violations)


def test_local_network_access_allowed_for_exact_exception() -> None:
    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        local_network_exceptions=("git push origin codex/example-v1",),
    )
    allowed_envelope = ExecutionEnvelope(
        command="git push origin codex/example-v1",
        execution_surface="local",
        operations=("network_access", "push"),
        exit_code=0,
    )
    violations = _envelope_network_violations((allowed_envelope,), policy)
    assert violations == ()


def test_ci_network_access_allowed_only_for_exact_exception() -> None:
    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        ci_network_exceptions=("python -m pip install -e .",),
    )
    allowed = ExecutionEnvelope(
        command="python -m pip install -e .",
        execution_surface="ci_only",
        operations=("dependency_install", "network_access"),
        exit_code=0,
    )
    forbidden = ExecutionEnvelope(
        command="python -m pip install -e .[extra]",
        execution_surface="ci_only",
        operations=("dependency_install", "network_access"),
        exit_code=0,
    )
    assert _envelope_network_violations((allowed,), policy) == ()
    violations = _envelope_network_violations((forbidden,), policy)
    assert any("pip install" in v for v in violations)


@pytest.mark.parametrize(
    ("surface", "exceptions_field", "command"),
    [
        ("local", "local_network_exceptions", "git push origin codex/example-v1"),
        ("ci_only", "ci_network_exceptions", "python -m pip install -e ."),
        ("trusted_worker", "trusted_worker_network_exceptions", "python -m pytest -q"),
        ("github_control_plane", "github_control_plane_network_exceptions", "gh pr create"),
        ("user_local", "user_local_network_exceptions", "docker compose up -d"),
    ],
)
def test_network_exceptions_are_surface_aware(
    surface: str,
    exceptions_field: str,
    command: str,
) -> None:
    """G2-2: network exceptions must be routed by exact execution surface."""

    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        **{exceptions_field: (command,)},
    )
    allowed = ExecutionEnvelope(
        command=command,
        execution_surface=surface,
        operations=("network_access",),
        exit_code=0,
    )
    # An identical command on a different surface with no exception must deny.
    other_surface = "ci_only" if surface != "ci_only" else "local"
    forbidden = ExecutionEnvelope(
        command=command,
        execution_surface=other_surface,
        operations=("network_access",),
        exit_code=0,
    )
    assert _envelope_network_violations((allowed,), policy) == ()
    violations = _envelope_network_violations((forbidden,), policy)
    assert any("network_access_violation" in v for v in violations)


def test_remote_observation_network_is_always_denied() -> None:
    """G2-2: remote_observation is read-only and never carries a network
    exception, so any network operation on it must fail closed."""

    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        remote_observation_read_only_allowed=True,
    )
    envelope = ExecutionEnvelope(
        command="gh api repos/dddd2024/reverse-agent",
        execution_surface="remote_observation",
        operations=("read_only_audit", "network_access"),
        exit_code=0,
    )
    violations = _envelope_network_violations((envelope,), policy)
    assert any("network_access_violation" in v for v in violations)


def test_load_capability_policy_reads_surface_network_exceptions() -> None:
    """G2-2: load_capability_policy must parse the surface-aware exception
    lists from the structured capability policy."""

    contract = _structured_contract(allowed_commands=[])
    contract["capability_policy"] = {
        "network_access_default_allowed": False,
        "local_network_exceptions": ["git push origin codex/example-v1"],
        "ci_network_exceptions": ["python -m pip install -e ."],
        "trusted_worker_network_exceptions": ["python -m pytest -q"],
        "github_control_plane_network_exceptions": ["gh pr create"],
        "user_local_network_exceptions": ["docker compose up -d"],
        "remote_observation_read_only_allowed": True,
    }
    policy = load_capability_policy(contract)
    assert "python -m pytest -q" in policy.trusted_worker_network_exceptions
    assert "gh pr create" in policy.github_control_plane_network_exceptions
    assert "docker compose up -d" in policy.user_local_network_exceptions


# ---------------------------------------------------------------------------
# Test 10: allowed/forbidden and reference path conflicts block
# ---------------------------------------------------------------------------


def test_allowed_forbidden_path_conflict_blocks_scope_loading() -> None:
    """A path appearing in both allowed and forbidden lists must fail closed."""

    decision = _decision()
    contract = _structured_contract(allowed_commands=[])
    contract["allowed_mutated_paths"] = ["reverse_agent/example/**"]
    contract["forbidden_mutated_paths"] = ["reverse_agent/example/**"]
    with pytest.raises(ValueError, match="allowed_forbidden_path_conflict"):
        load_transition_scope(decision, contract)


def test_allowed_reference_path_conflict_blocks_scope_loading() -> None:
    decision = _decision()
    contract = _structured_contract(allowed_commands=[])
    contract["allowed_mutated_paths"] = ["docs/roadmap/example.md"]
    contract["reference_paths"] = ["docs/roadmap/example.md"]
    with pytest.raises(ValueError, match="allowed_reference_path_conflict"):
        load_transition_scope(decision, contract)


def test_reference_only_paths_share_the_read_only_path_class() -> None:
    decision = _decision()
    contract = _structured_contract(allowed_commands=[])
    contract["allowed_mutated_paths"] = ["docs/reference-only/example.md"]
    contract["reference_only_paths"] = ["docs/reference-only/example.md"]
    with pytest.raises(ValueError, match="allowed_reference_path_conflict"):
        load_transition_scope(decision, contract)


@pytest.mark.parametrize("grant_field", ["allowed_mutated_paths", "produced_artifacts"])
def test_command_writable_grants_cannot_overlap_reference_paths(grant_field: str) -> None:
    decision = _decision()
    raw_command = _structured_command(
        "python -m pytest tests/test_authority_closure.py -q",
        phase="test",
        command_id="test.reference_grant",
        operations=("unit_test",),
    )
    raw_command[grant_field] = ["docs/roadmap/**"]
    contract = _structured_contract(
        allowed_commands=[raw_command],
        reference_paths=["docs/roadmap/example.md"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        reference_paths=("docs/roadmap/example.md",),
    )

    result = validate_transition(authority, mode="pre")

    assert result.gate_status == "BLOCKED"
    check = next(
        item for item in result.checks
        if item["name"] == "reference_write_grants_disjoint"
    )
    assert check["status"] == "FAIL"
    assert "docs/roadmap" in check["detail"]


def test_load_path_risk_floor_rejects_invalid_risk() -> None:
    contract = {"path_risk_floor": [{"pattern": ".env", "minimum_risk": "R4"}]}
    with pytest.raises(ValueError, match="invalid_path_risk_floor_risk"):
        load_path_risk_floor(contract)


def test_path_risk_floor_matches_secrets_under_subdirectory() -> None:
    """The ``**/secrets/**`` pattern must match paths with ``secrets`` anywhere."""

    floor = PathRiskFloor((("**/secrets/**", "R3"),))
    assert floor.risk_for_path("config/secrets/api.key") == "R3"
    assert floor.risk_for_path("secrets/root.key") == "R3"
    assert floor.risk_for_path("docs/readme.md") is None


def test_path_risk_floor_matches_workflow_glob() -> None:
    floor = PathRiskFloor(((".github/workflows/**", "R2"),))
    assert floor.risk_for_path(".github/workflows/ci.yml") == "R2"
    assert floor.risk_for_path(".github/workflows/release.yml") == "R2"
    assert floor.risk_for_path(".github/dependabot.yml") is None


def test_path_risk_floor_matches_binary_extensions() -> None:
    floor = PathRiskFloor((("**/*.exe", "R3"), ("**/*.dll", "R3")))
    assert floor.risk_for_path("tools/patcher.exe") == "R3"
    assert floor.risk_for_path("bin/native.dll") == "R3"
    assert floor.risk_for_path("source/main.py") is None


# ---------------------------------------------------------------------------
# Test 1 + 2: structured plan generation + identity invalidation
# ---------------------------------------------------------------------------


def test_structured_command_plan_carries_decision_identity() -> None:
    decision = _decision(decision_id="decision_alpha", round_id="round_alpha")
    contract = _structured_contract(allowed_commands=[_structured_command()])
    plan = build_transition_command_plan(decision, contract)
    assert plan.decision_id == "decision_alpha"
    assert plan.round_id == "round_alpha"
    errors = validate_command_plan(plan)
    assert errors == ()


def test_plan_identity_invalidates_when_decision_changes() -> None:
    contract = _structured_contract(allowed_commands=[_structured_command()])
    first = build_transition_command_plan(_decision(), contract)
    second = build_transition_command_plan(
        _decision(decision_id="decision_beta", round_id="round_beta"),
        contract,
    )
    assert first.decision_id == "decision_test"
    assert second.decision_id == "decision_beta"
    assert [c.command for c in first.commands] == [c.command for c in second.commands]


# ---------------------------------------------------------------------------
# Test 3: undeclared command rejection
# ---------------------------------------------------------------------------


def test_reconcile_executions_blocks_undeclared_command() -> None:
    decision = _decision()
    contract = _structured_contract(allowed_commands=[_structured_command("git status --short")])
    plan = build_transition_command_plan(decision, contract)
    envelope = ExecutionEnvelope(
        command="rm -rf /",
        execution_surface="local",
        operations=("destructive_delete",),
        exit_code=0,
    )
    outcome = reconcile_executions(plan, (envelope,))
    assert outcome.status == "BLOCKED"
    assert "rm -rf /" in outcome.undeclared


# ---------------------------------------------------------------------------
# Test 5: missing execution evidence rejection
# ---------------------------------------------------------------------------


def test_reconcile_executions_blocks_when_no_envelopes() -> None:
    decision = _decision()
    contract = _structured_contract(allowed_commands=[_structured_command()])
    plan = build_transition_command_plan(decision, contract)
    outcome = reconcile_executions(plan, ())
    assert outcome.status == "PRE_EXECUTION_AUTHORIZED"
    assert outcome.missing_evidence is True
    assert "missing_execution_evidence" in outcome.blocking_reasons


# ---------------------------------------------------------------------------
# P1: required evidence identity must bind command_id + execution_surface
# ---------------------------------------------------------------------------


def test_required_command_coverage_binds_command_id_and_exact_surface() -> None:
    command = TransitionCommand(
        command="python -m pytest tests/test_authority_closure.py -q",
        phase="test",
        required=True,
        expected_exit_codes=(0,),
        execution_surface="trusted_worker",
        operations=("unit_test",),
        command_id="validation.authority_closure",
        required_evidence_source="local_command_evidence",
    )
    plan = TransitionCommandPlan(
        decision_id="decision_evidence_identity",
        round_id="round_evidence_identity",
        commands=(command,),
    )
    wrong_surface = ExecutionEnvelope(
        command=command.command,
        command_id=command.command_id,
        execution_surface="user_local",
        exit_code=0,
    )
    wrong_id = ExecutionEnvelope(
        command=command.command,
        command_id="validation.other",
        execution_surface=command.execution_surface,
        exit_code=0,
    )
    exact = ExecutionEnvelope(
        command=command.command,
        command_id=command.command_id,
        execution_surface=command.execution_surface,
        exit_code=0,
    )

    assert _required_command_coverage_missing(plan, (wrong_surface,)) == (
        command.command_id,
    )
    assert _required_command_coverage_missing(plan, (wrong_id,)) == (
        command.command_id,
    )
    assert _required_command_coverage_missing(plan, (exact,)) == ()


# ---------------------------------------------------------------------------
# P1 closures for review findings 3921389187 / 3921389199 / 3921697391
# ---------------------------------------------------------------------------


def test_required_coverage_binds_command_id_surface_and_command_text() -> None:
    """P1-3921389187: an envelope must not mark a required command covered with
    the required ID + surface but the text of a different optional command."""

    command = TransitionCommand(
        command="python -m pytest tests/test_authority_closure.py -q",
        phase="test",
        required=True,
        expected_exit_codes=(0,),
        execution_surface="local",
        operations=("unit_test",),
        command_id="validation.authority_closure",
        required_evidence_source="local_command_evidence",
    )
    optional = TransitionCommand(
        command="python -m pytest tests/other_test.py -q",
        phase="test",
        required=False,
        expected_exit_codes=(0,),
        execution_surface="local",
        operations=("unit_test",),
        command_id="validation.other",
        required_evidence_source="local_command_evidence",
    )
    plan = TransitionCommandPlan(
        decision_id="decision_coverage_triple",
        round_id="round_coverage_triple",
        commands=(command, optional),
    )
    # Same required command_id + exact required surface, but a different
    # optional command's text: must NOT satisfy required coverage.
    spoof = ExecutionEnvelope(
        command=optional.command,
        command_id=command.command_id,
        execution_surface=command.execution_surface,
        exit_code=0,
    )
    assert _required_command_coverage_missing(plan, (spoof,)) == (
        command.command_id,
    )
    # Optional command text under its own ID is not required coverage either.
    optional_only = ExecutionEnvelope(
        command=optional.command,
        command_id=optional.command_id,
        execution_surface=optional.execution_surface,
        exit_code=0,
    )
    assert _required_command_coverage_missing(plan, (optional_only,)) == (
        command.command_id,
    )
    # Exact required ID + surface + required command text passes.
    exact = ExecutionEnvelope(
        command=command.command,
        command_id=command.command_id,
        execution_surface=command.execution_surface,
        exit_code=0,
    )
    assert _required_command_coverage_missing(plan, (exact,)) == ()


def test_bootstrap_exception_files_are_writable_grants_for_conflict_check() -> None:
    """P1-3921389199: bootstrap_exception_files are pre-preflight writable
    grants and must participate in the reference/write disjointness check."""

    decision = _decision()
    raw_command = _structured_command(
        "python -m pytest tests/test_authority_closure.py -q",
        phase="test",
        command_id="test.bootstrap_reference_grant",
        operations=("unit_test",),
    )
    contract = _structured_contract(
        allowed_commands=[raw_command],
        bootstrap_exception_files=["docs/roadmap/example.md"],
        reference_paths=["docs/roadmap/example.md"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        reference_paths=("docs/roadmap/example.md",),
        bootstrap_exception_files=("docs/roadmap/example.md",),
    )

    result = validate_transition(authority, mode="pre")

    assert result.gate_status == "BLOCKED"
    check = next(
        item for item in result.checks
        if item["name"] == "reference_write_grants_disjoint"
    )
    assert check["status"] == "FAIL"
    assert "docs/roadmap/example.md" in check["detail"]


def test_legal_bootstrap_exception_files_do_not_conflict() -> None:
    """P1-3921389199: legal bootstrap exception files must remain unblocked."""

    decision = _decision()
    raw_command = _structured_command("git status --short")
    contract = _structured_contract(
        allowed_commands=[raw_command],
        bootstrap_exception_files=["project_state/gates/command_plan.json"],
        reference_paths=["docs/roadmap/example.md"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        reference_paths=("docs/roadmap/example.md",),
        bootstrap_exception_files=("project_state/gates/command_plan.json",),
    )

    result = validate_transition(authority, mode="pre")

    assert result.gate_status == "PASSED"
    check = next(
        item for item in result.checks
        if item["name"] == "reference_write_grants_disjoint"
    )
    assert check["status"] == "PASS"


def test_bootstrap_exception_files_exposed_by_scope_loader() -> None:
    """P1-3921389199: the loaded scope representation must expose the bootstrap
    writable grant so transition.py never re-parses the Decision JSON."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[],
        bootstrap_exception_files=["project_state/gates/command_plan.json"],
    )
    scope = load_transition_scope(decision, contract)
    assert scope["bootstrap_exception_files"] == ("project_state/gates/command_plan.json",)


def test_bootstrap_reference_pattern_overlap_blocks_preflight() -> None:
    """P1-3921697391/P1-3921389199: an exact bootstrap grant that overlaps a
    reference path must fail closed through the shared conflict check."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("git status --short")],
        bootstrap_exception_files=["docs/roadmap/example.md"],
        reference_paths=["docs/roadmap/example.md"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        reference_paths=("docs/roadmap/example.md",),
        bootstrap_exception_files=("docs/roadmap/example.md",),
    )
    result = validate_transition(authority, mode="pre")
    assert result.gate_status == "BLOCKED"


@pytest.mark.parametrize(
    ("writable", "reference", "expect_conflict"),
    [
        ("docs/*.md", "docs/example.*", True),
        ("docs/**", "docs/roadmap/*.md", True),
        ("docs/*.md", "tests/*.md", False),
        ("src/**", "src/generated/**", True),
        ("docs/roadmap/**", "docs/roadmap/example.md", True),
        ("AGENTS.md", "docs/**", False),
        ("reverse_agent/project_gate.py", "AGENTS.md", False),
    ],
)
def test_pattern_may_intersect_authority_semantics(
    writable: str,
    reference: str,
    expect_conflict: bool,
) -> None:
    """P1-3921697391: the grant-intersection check must use sound language
    intersection (may-intersect, fail closed), not pattern-string matching."""

    from reverse_agent.control_plane.transition import _patterns_may_intersect_text

    assert _patterns_may_intersect_text(writable, reference) is expect_conflict


def test_wildcard_grant_intersection_blocks_preflight() -> None:
    """P1-3921697391: intersecting wildcard grants must block before execution."""

    decision = _decision()
    raw_command = _structured_command("git status --short")
    contract = _structured_contract(
        allowed_commands=[raw_command],
        allowed_mutated_paths=["docs/*.md"],
        reference_paths=["docs/example.*"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        allowed_paths=("docs/*.md",),
        reference_paths=("docs/example.*",),
    )
    result = validate_transition(authority, mode="pre")
    assert result.gate_status == "BLOCKED"
    check = next(
        item for item in result.checks
        if item["name"] == "reference_write_grants_disjoint"
    )
    assert check["status"] == "FAIL"


def test_non_overlapping_glob_grants_pass() -> None:
    """P1-3921697391: provably disjoint glob grants must continue to pass."""

    decision = _decision()
    raw_command = _structured_command("git status --short")
    contract = _structured_contract(
        allowed_commands=[raw_command],
        allowed_mutated_paths=["docs/*.md"],
        reference_paths=["tests/*.md"],
    )
    plan = build_transition_command_plan(decision, contract)
    authority = _transition_authority(
        decision,
        plan,
        allowed_paths=("docs/*.md",),
        reference_paths=("tests/*.md",),
    )
    result = validate_transition(authority, mode="pre")
    assert result.gate_status == "PASSED"
