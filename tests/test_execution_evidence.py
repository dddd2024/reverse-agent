"""Phase A: execution evidence schema and bootstrap state tests.

Covers the new stable command_id, required_evidence_source, authority_origin
fields, the strict execution record schema, and the BOOTSTRAP_OPEN /
BOOTSTRAP_EXPIRED lifecycle.
"""

from __future__ import annotations

import json

import pytest

from reverse_agent.control_plane.legacy_adapter import (
    build_transition_command_plan,
    load_bootstrap_state,
    persist_bootstrap_state,
)
from reverse_agent.control_plane.models import (
    ExecutionEnvelope,
    ExecutionRecord,
    TransitionCommand,
    TransitionDecision,
)


def _decision() -> TransitionDecision:
    return TransitionDecision(
        decision_id="decision_evidence",
        round_id="round_evidence",
        status="APPROVED",
        mainline="engineering_branch",
        skill_profiles=("reverse-agent-iteration@v2",),
    )


def _structured_contract(*, allowed_commands: list) -> dict:
    return {
        "transition_kernel_required": True,
        "required_branch": "codex/example-v1",
        "activation_base_sha": "a" * 40,
        "bootstrap_exception_files": ["reverse_agent/project_gate.py"],
        "bootstrap_exception_commands": [],
        "allowed_commands": allowed_commands,
        "allowed_mutated_paths": ["reverse_agent/example/**"],
        "forbidden_mutated_paths": ["frontend/**"],
        "capability_policy": {
            "network_access_default_allowed": False,
            "local_network_exceptions": [],
            "ci_network_exceptions": [],
        },
        "path_risk_floor": [],
    }


def _structured_command(
    command_id: str = "status.git_status",
    command: str = "git status --short",
    *,
    required_evidence_source: str = "local_provenance",
    authority_origin: str = "normal_plan",
    network_access: bool = False,
    operations: tuple[str, ...] = ("repository_observation",),
) -> dict:
    return {
        "command_id": command_id,
        "command": command,
        "phase": "status",
        "required": True,
        "required_evidence_source": required_evidence_source,
        "expected_exit_codes": [0],
        "execution_surface": "local",
        "operations": list(operations),
        "network_access": network_access,
        "authority_origin": authority_origin,
    }


# --- Phase A.1: stable command_id + authority origin --------------------


def test_structured_command_plan_carries_command_id() -> None:
    """Structured commands must preserve command_id through the plan."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("status.git_status")],
    )
    plan = build_transition_command_plan(decision, contract)
    assert plan.commands[0].command_id == "status.git_status"
    assert plan.commands[0].authority_origin == "normal_plan"


# --- G2-1: explicit execution surfaces ----------------------------------


def test_structured_command_requires_explicit_execution_surface() -> None:
    """A current/new structured command with a missing execution_surface must
    FAIL CLOSED. Only load_legacy_command_plan may normalize a historical
    missing surface to the legacy ``local`` token."""

    from reverse_agent.control_plane.command_authority import validate_command_plan

    decision = _decision()
    raw = _structured_command("status.git_status")
    raw.pop("execution_surface")
    contract = _structured_contract(allowed_commands=[raw])
    with pytest.raises(ValueError, match="missing_execution_surface"):
        build_transition_command_plan(decision, contract)


def test_structured_command_blank_execution_surface_fails_closed() -> None:
    decision = _decision()
    raw = _structured_command("status.git_status")
    raw["execution_surface"] = "   "
    contract = _structured_contract(allowed_commands=[raw])
    with pytest.raises(ValueError, match="missing_execution_surface"):
        build_transition_command_plan(decision, contract)


@pytest.mark.parametrize(
    ("surface", "operations"),
    [
        ("github_control_plane", ["push"]),
        ("trusted_worker", ["source_edit"]),
        ("ci_only", ["unit_test"]),
        ("remote_observation", ["read_only_audit"]),
        ("user_local", ["machine_specific_execution"]),
    ],
)
def test_canonical_execution_surfaces_parse(surface: str, operations: list) -> None:
    from reverse_agent.control_plane.command_authority import validate_command_plan

    decision = _decision()
    raw = _structured_command("status.git_status")
    raw["execution_surface"] = surface
    raw["operations"] = operations
    contract = _structured_contract(allowed_commands=[raw])
    plan = build_transition_command_plan(decision, contract)
    assert plan.commands[0].execution_surface == surface
    assert validate_command_plan(plan) == ()


def test_current_structured_local_surface_fails_closed() -> None:
    """#636: current/new structured authoring must never select the legacy
    ``local`` surface. Only ``load_legacy_command_plan`` keeps historical
    ``local`` evidence readable."""

    from reverse_agent.control_plane.command_authority import validate_command_plan

    decision = _decision()
    raw = _structured_command("status.git_status")
    raw["execution_surface"] = "local"
    contract = _structured_contract(allowed_commands=[raw])
    plan = build_transition_command_plan(decision, contract)
    errors = validate_command_plan(plan)
    assert any(
        "legacy_local_surface_forbidden_in_current_authoring" in err for err in errors
    )


def test_user_local_requires_machine_specific_declaration() -> None:
    """G2-1: a user_local command without the explicit machine-specific
    capability declaration must be BLOCKED."""

    from reverse_agent.control_plane.command_authority import validate_command_plan
    from reverse_agent.control_plane.models import TransitionCommandPlan, TransitionDecision

    decision = _decision()
    raw = _structured_command("status.git_status")
    raw["execution_surface"] = "user_local"
    raw["operations"] = ["repository_observation"]
    contract = _structured_contract(allowed_commands=[raw])
    with pytest.raises(ValueError, match="user_local_requires_machine_specific_execution"):
        build_transition_command_plan(decision, contract)

    # Direct model construction without the declaration also fails closed.
    plan = TransitionCommandPlan(
        decision_id=decision.decision_id,
        round_id=decision.round_id,
        commands=(
            TransitionCommand(
                command="git status --short",
                phase="status",
                required=True,
                expected_exit_codes=(0,),
                execution_surface="user_local",
                operations=("repository_observation",),
                command_id="status.git_status",
            ),
        ),
    )
    errors = validate_command_plan(plan)
    assert any("user_local_requires_machine_specific_execution" in err for err in errors)


def test_legacy_local_remains_readable_but_not_authoring_default() -> None:
    """G2-1: explicit historical ``local`` remains readable through the legacy
    adapter, but is not the authoring default. Current structured plans that
    explicitly select ``local`` must fail closed at the shared validator."""

    from reverse_agent.control_plane.command_authority import validate_command_plan

    decision = _decision()
    raw = _structured_command("status.git_status")
    raw["execution_surface"] = "local"
    contract = _structured_contract(allowed_commands=[raw])
    plan = build_transition_command_plan(decision, contract)
    assert plan.commands[0].execution_surface == "local"
    errors = validate_command_plan(plan)
    assert any(
        "legacy_local_surface_forbidden_in_current_authoring" in err for err in errors
    )


def test_load_legacy_command_plan_normalizes_missing_surface_to_local(tmp_path) -> None:
    """G2-1: only load_legacy_command_plan may normalize a historical missing
    execution_surface to ``local``. This is the narrow implicit compatibility
    path and must not be removed."""

    from reverse_agent.control_plane.legacy_adapter import load_legacy_command_plan

    gates = tmp_path / "gates"
    gates.mkdir(parents=True)
    (gates / "command_plan.json").write_text(
        json.dumps({
            "schema_version": 1,
            "decision_id": "decision_legacy",
            "round_id": "round_legacy",
            "commands": [
                {
                    "command": "git status --short",
                    "phase": "status",
                    "required": True,
                    "expected_exit_codes": [0],
                    "operations": ["repository_observation"],
                    "command_id": "status.git_status",
                }
            ],
        }),
        encoding="utf-8",
    )
    plan = load_legacy_command_plan(gates / "command_plan.json")
    assert plan.commands[0].execution_surface == "local"


# ---------------------------------------------------------------------------
# #636 canonical operation↔surface compatibility regressions.
#
# A current/new typed Decision may not pass merely because its surface token
# is enum-valid when the declared operation is semantically incompatible.
# The same validator is consumed by State Gate and Decision Preflight so no
# divergence between the two gates is possible (issue requirement 11/12).
# ---------------------------------------------------------------------------


def _command_plan(
    *,
    surface: str,
    operations: tuple[str, ...],
    command: str = "git status --short",
) -> "TransitionCommandPlan":
    from reverse_agent.control_plane.models import TransitionCommandPlan

    return TransitionCommandPlan(
        decision_id="decision_compat",
        round_id="round_compat",
        commands=(
            TransitionCommand(
                command=command,
                phase="status",
                required=True,
                expected_exit_codes=(0,),
                execution_surface=surface,
                operations=operations,
                command_id="status.git_status",
            ),
        ),
    )


def test_compat_trusted_worker_source_edit_is_admissible() -> None:
    """trusted_worker + checkout/source-edit must PASS (#636 regression 3)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="trusted_worker", operations=("source_edit",))
    assert validate_command_plan(plan) == ()


def test_compat_github_control_plane_source_edit_fails_closed() -> None:
    """#636 shape: github_control_plane + checkout/source-edit must BLOCK
    before implementation (regression 4)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="github_control_plane", operations=("source_edit",))
    errors = validate_command_plan(plan)
    assert any("operation_surface_incompatible:source_edit:github_control_plane" in e for e in errors)
    plan2 = _command_plan(surface="github_control_plane", operations=("commit",))
    errors2 = validate_command_plan(plan2)
    assert any("operation_surface_incompatible:commit:github_control_plane" in e for e in errors2)


def test_compat_ci_only_source_edit_fails_closed() -> None:
    """ci_only + checkout/source-edit must BLOCK (regression 5)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="ci_only", operations=("source_edit",))
    errors = validate_command_plan(plan)
    assert any("operation_surface_incompatible:source_edit:ci_only" in e for e in errors)


def test_compat_remote_observation_mutation_fails_closed() -> None:
    """remote_observation + mutation must BLOCK (regression 6)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="remote_observation", operations=("commit",))
    errors = validate_command_plan(plan)
    assert any("operation_surface_incompatible:commit:remote_observation" in e for e in errors)
    plan2 = _command_plan(surface="remote_observation", operations=("source_edit",))
    errors2 = validate_command_plan(plan2)
    assert any("operation_surface_incompatible:source_edit:remote_observation" in e for e in errors2)


def test_compat_user_local_with_machine_specific_passes() -> None:
    """Legal machine-specific user_local must PASS (regression 8)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(
        surface="user_local",
        operations=("machine_specific_execution", "repository_observation"),
    )
    assert validate_command_plan(plan) == ()


def test_compat_unknown_surface_fails_closed() -> None:
    """Unknown surface must BLOCK (regression 9)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="not_a_surface", operations=("repository_observation",))
    errors = validate_command_plan(plan)
    assert any("invalid_execution_surface:not_a_surface" in e for e in errors)


def test_compat_636_shape_is_enum_valid_but_semantically_invalid() -> None:
    """#636 regression 10: an enum-valid surface token plus a semantically
    incompatible operation must be rejected by the exact shared plan
    validator State Gate / Decision Preflight both consume."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="github_control_plane", operations=("source_edit", "commit"))
    errors = validate_command_plan(plan)
    for expected in (
        "operation_surface_incompatible:source_edit:github_control_plane",
        "operation_surface_incompatible:commit:github_control_plane",
    ):
        assert any(expected in e for e in errors)


def test_compat_github_native_ops_require_control_plane() -> None:
    """github-native operations must not be declared on subprocess surfaces."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for surface in ("trusted_worker", "ci_only", "remote_observation", "user_local"):
        plan = _command_plan(surface=surface, operations=("draft_pr",))
        errors = validate_command_plan(plan)
        assert any("operation_surface_incompatible:draft_pr" in e for e in errors)
        plan2 = _command_plan(surface=surface, operations=("push",))
        errors2 = validate_command_plan(plan2)
        assert any("operation_surface_incompatible:push" in e for e in errors2)


# ---------------------------------------------------------------------------
# #637 closure: capability-controlled operation vocabulary completeness.
#
# Every operation a CapabilityPolicy can legally ALLOW must have an explicit
# admissible-surface entry. Otherwise enabling the flag leaves a semantically
# dead capability that a fresh typed Decision would see rejected as
# ``unknown_operation``. The invariant is machine-checked, never comment-
# maintained: adding a capability flag without a registry entry fails.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operation",
    [
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
    ],
)
def test_compat_every_capability_operation_has_registry_entry(operation: str) -> None:
    """Every grantable capability operation must be in the compatibility
    registry so it can never surface as ``unknown_operation`` when the
    capability is legitimately allowed (#637 closure)."""
    from reverse_agent.control_plane.command_authority import (
        OPERATION_SURFACE_ADMISSIBILITY,
    )

    assert operation in OPERATION_SURFACE_ADMISSIBILITY
    assert OPERATION_SURFACE_ADMISSIBILITY[operation]


def test_compat_capability_operation_coverage_invariant_is_machine_checked() -> None:
    """capability_operation_coverage() must be empty. If a new capability flag
    is added to CAPABILITY_OPERATION_MAPPING without a registry entry, this
    test immediately FAILS instead of waiting for a real Decision."""
    from reverse_agent.control_plane.command_authority import capability_operation_coverage

    assert capability_operation_coverage() == ()


def test_compat_positive_github_native_capability_operations_pass() -> None:
    """Capability-enabled GitHub-native operations are admissible ONLY on
    github_control_plane (#637 positive representative case)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for operation in (
        "runner_dispatch",
        "direct_push_main",
        "force_push",
        "tag_or_release",
        "merge",
    ):
        plan = _command_plan(surface="github_control_plane", operations=(operation,))
        assert validate_command_plan(plan) == (), operation


def test_compat_positive_trusted_worker_capability_operations_pass() -> None:
    """Capability-enabled checked-out-repository subprocess operations are
    admissible on trusted_worker (#637 positive representative case)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for operation in (
        "rebase",
        "destructive",
        "external_reverse_tool_invocation",
        "unknown_binary_execution",
        "bmad_installation",
        "model_api_invocation",
    ):
        plan = _command_plan(surface="trusted_worker", operations=(operation,))
        assert validate_command_plan(plan) == (), operation


def test_compat_positive_ci_only_install_and_model_api_pass() -> None:
    """bmad_installation and model_api_invocation are network-capable
    subprocess/CI operations (#637 positive representative case)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="ci_only", operations=("bmad_installation",))
    assert validate_command_plan(plan) == ()
    plan2 = _command_plan(surface="ci_only", operations=("model_api_invocation",))
    assert validate_command_plan(plan2) == ()


def test_compat_positive_user_local_machine_specific_capability_passes() -> None:
    """user_local may carry a capability operation ONLY when the explicit
    machine-specific declaration is present (#637 positive representative
    case; issue requirement 15)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(
        surface="user_local",
        operations=("machine_specific_execution", "model_api_invocation"),
    )
    assert validate_command_plan(plan) == ()
    missing = _command_plan(
        surface="user_local",
        operations=("model_api_invocation",),
    )
    errors = validate_command_plan(missing)
    assert any("user_local_requires_machine_specific_execution" in e for e in errors)


def test_compat_capability_true_does_not_allow_cross_surface() -> None:
    """A capability flag being True does NOT mean the operation can run on
    any surface. GitHub-native capability operations must never be declared
    on subprocess/CI/read-only surfaces even when the capability allows them
    (#637 negative cross-surface)."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for surface in ("trusted_worker", "ci_only", "remote_observation", "user_local"):
        for operation in (
            "runner_dispatch",
            "direct_push_main",
            "force_push",
            "tag_or_release",
            "merge",
        ):
            plan = _command_plan(surface=surface, operations=(operation,))
            errors = validate_command_plan(plan)
            assert any(
                f"operation_surface_incompatible:{operation}:{surface}" in e for e in errors
            ), (operation, surface)


def test_compat_subprocess_capability_operations_not_on_github_or_readonly() -> None:
    """Checked-out-repository subprocess capability operations must never be
    declared on github_control_plane or remote_observation."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for surface in ("github_control_plane", "remote_observation"):
        for operation in (
            "rebase",
            "destructive",
            "external_reverse_tool_invocation",
            "unknown_binary_execution",
        ):
            plan = _command_plan(surface=surface, operations=(operation,))
            errors = validate_command_plan(plan)
            assert any(
                f"operation_surface_incompatible:{operation}:{surface}" in e for e in errors
            ), (operation, surface)


def test_compat_model_api_invocation_not_on_github_control_plane() -> None:
    """model_api_invocation is an external API call, not a GitHub-native
    mutation; github_control_plane must reject it."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    plan = _command_plan(surface="github_control_plane", operations=("model_api_invocation",))
    errors = validate_command_plan(plan)
    assert any(
        "operation_surface_incompatible:model_api_invocation:github_control_plane" in e
        for e in errors
    )


def test_compat_truly_unsupported_operation_stays_unknown() -> None:
    """Truly unsupported operations must remain fail-closed as
    ``unknown_operation`` even on an otherwise admissible surface."""
    from reverse_agent.control_plane.command_authority import validate_command_plan

    for operation in (
        "workflow_dispatch_trigger",
        "secret_change",
        "database_creation",
        "sample_solving",
    ):
        plan = _command_plan(surface="trusted_worker", operations=(operation,))
        errors = validate_command_plan(plan)
        assert any(f"unknown_operation:{operation}:trusted_worker" in e for e in errors), operation


def test_compat_capability_policy_and_compatibility_both_required() -> None:
    """Neither layer alone may authorize execution: the capability policy must
    allow the operation AND the operation must be compatible with the declared
    surface. A plan that passes one layer and fails the other must BLOCK."""
    from reverse_agent.control_plane.command_authority import validate_command_plan
    from reverse_agent.control_plane.models import CapabilityPolicy
    from reverse_agent.control_plane.transition import _capability_forbidden_operations

    # Capability allows force_push, so policy enforcement would permit it...
    policy = CapabilityPolicy(force_push_allowed=True)
    forbidden = _capability_forbidden_operations(policy)
    assert "force_push" not in forbidden
    # ...but the declared surface is wrong, so the shared validator must still
    # reject the plan before any execution (operation known, surface wrong).
    plan = _command_plan(surface="trusted_worker", operations=("force_push",))
    errors = validate_command_plan(plan)
    assert any("operation_surface_incompatible:force_push:trusted_worker" in e for e in errors)

    # Conversely a legal surface does not override a denied capability flag.
    policy_denied = CapabilityPolicy(force_push_allowed=False)
    assert "force_push" in _capability_forbidden_operations(policy_denied)
    plan_ok_surface = _command_plan(surface="github_control_plane", operations=("force_push",))
    assert validate_command_plan(plan_ok_surface) == ()
    denied = sorted(set(_capability_forbidden_operations(policy_denied)).intersection(
        plan_ok_surface.commands[0].operations
    ))
    assert "force_push" in denied


def test_command_id_and_surface_must_match_record() -> None:
    """An execution record must match the plan entry by command_id + surface."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("status.git_status")],
    )
    plan = build_transition_command_plan(decision, contract)
    matching = plan.find_command("status.git_status", "local")
    assert matching is not None
    assert matching.command == "git status --short"


def test_bootstrap_command_gets_bootstrap_authority_origin() -> None:
    """Bootstrap exception commands carry authority_origin=bootstrap_exception."""

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("status.git_status")],
    )
    contract["bootstrap_exception_commands"] = ["git rev-parse HEAD"]
    plan = build_transition_command_plan(decision, contract)
    bootstrap_cmd = next(cmd for cmd in plan.commands if cmd.bootstrap_exception)
    assert bootstrap_cmd.authority_origin == "bootstrap_exception"


# --- Phase A.2: strict execution record schema --------------------------


def test_execution_record_rejects_missing_required_fields() -> None:
    """Strict execution record must reject missing required fields."""

    with pytest.raises(ValueError, match="missing_field:command_id"):
        ExecutionRecord(
            command_id="",
            command="git status --short",
            execution_surface="local",
            operations=("repository_observation",),
            mutated_paths=(),
            exit_code=0,
            started_at="2026-07-21T00:00:00Z",
            observed_at="2026-07-21T00:00:01Z",
            head_before="a" * 40,
            head_after="b" * 40,
            stdout_digest="sha256:abc",
            stderr_digest="sha256:def",
            authority_origin="normal_plan",
        )


def test_execution_record_rejects_missing_head_binding() -> None:
    with pytest.raises(ValueError, match="missing_field:head_before"):
        ExecutionRecord(
            command_id="status.git_status",
            command="git status --short",
            execution_surface="local",
            operations=("repository_observation",),
            mutated_paths=(),
            exit_code=0,
            started_at="2026-07-21T00:00:00Z",
            observed_at="2026-07-21T00:00:01Z",
            head_before="",
            head_after="b" * 40,
            stdout_digest="sha256:abc",
            stderr_digest="sha256:def",
            authority_origin="normal_plan",
        )


def test_execution_record_rejects_missing_digest() -> None:
    with pytest.raises(ValueError, match="missing_field:stdout_digest"):
        ExecutionRecord(
            command_id="status.git_status",
            command="git status --short",
            execution_surface="local",
            operations=("repository_observation",),
            mutated_paths=(),
            exit_code=0,
            started_at="2026-07-21T00:00:00Z",
            observed_at="2026-07-21T00:00:01Z",
            head_before="a" * 40,
            head_after="b" * 40,
            stdout_digest="",
            stderr_digest="sha256:def",
            authority_origin="normal_plan",
        )


def test_execution_record_rejects_invalid_authority_origin() -> None:
    with pytest.raises(ValueError, match="invalid_authority_origin"):
        ExecutionRecord(
            command_id="status.git_status",
            command="git status --short",
            execution_surface="local",
            operations=("repository_observation",),
            mutated_paths=(),
            exit_code=0,
            started_at="2026-07-21T00:00:00Z",
            observed_at="2026-07-21T00:00:01Z",
            head_before="a" * 40,
            head_after="b" * 40,
            stdout_digest="sha256:abc",
            stderr_digest="sha256:def",
            authority_origin="caller_supplied",
        )


# --- Phase A.3: bootstrap lifecycle -------------------------------------


def test_bootstrap_state_defaults_to_open(tmp_path) -> None:
    """Without a persisted state file, bootstrap is BOOTSTRAP_OPEN."""

    state = load_bootstrap_state(tmp_path / "missing.json")
    assert state.status == "BOOTSTRAP_OPEN"
    assert state.is_open is True
    assert state.is_expired is False


def test_bootstrap_state_can_be_expired(tmp_path) -> None:
    state_path = tmp_path / "bootstrap_state.json"
    persist_bootstrap_state(state_path, status="BOOTSTRAP_EXPIRED")
    loaded = load_bootstrap_state(state_path)
    assert loaded.status == "BOOTSTRAP_EXPIRED"
    assert loaded.is_expired is True


def test_bootstrap_state_rejects_invalid_status(tmp_path) -> None:
    state_path = tmp_path / "bootstrap_state.json"
    with pytest.raises(ValueError, match="invalid_bootstrap_status"):
        persist_bootstrap_state(state_path, status="BOOTSTRAP_UNKNOWN")


def test_expired_bootstrap_rejects_new_bootstrap_records(tmp_path) -> None:
    """Once BOOTSTRAP_EXPIRED, new bootstrap records must be rejected."""

    state_path = tmp_path / "bootstrap_state.json"
    persist_bootstrap_state(state_path, status="BOOTSTRAP_EXPIRED")
    state = load_bootstrap_state(state_path)
    assert state.is_expired is True
    # A record claiming bootstrap_exception after expiry must be flagged.
    record = ExecutionRecord(
        command_id="bootstrap.cmd",
        command="git rev-parse HEAD",
        execution_surface="local",
        operations=(),
        mutated_paths=(),
        exit_code=0,
        started_at="2026-07-21T00:00:00Z",
        observed_at="2026-07-21T00:00:01Z",
        head_before="a" * 40,
        head_after="b" * 40,
        stdout_digest="sha256:abc",
        stderr_digest="sha256:def",
        authority_origin="bootstrap_exception",
    )
    assert state.rejects_expired_bootstrap_record(record) is True


def test_open_bootstrap_accepts_new_bootstrap_records(tmp_path) -> None:
    state_path = tmp_path / "bootstrap_state.json"
    persist_bootstrap_state(state_path, status="BOOTSTRAP_OPEN")
    state = load_bootstrap_state(state_path)
    record = ExecutionRecord(
        command_id="bootstrap.cmd",
        command="git rev-parse HEAD",
        execution_surface="local",
        operations=(),
        mutated_paths=(),
        exit_code=0,
        started_at="2026-07-21T00:00:00Z",
        observed_at="2026-07-21T00:00:01Z",
        head_before="a" * 40,
        head_after="b" * 40,
        stdout_digest="sha256:abc",
        stderr_digest="sha256:def",
        authority_origin="bootstrap_exception",
    )
    assert state.rejects_expired_bootstrap_record(record) is False


# --- Phase C: plan-driven operation/capability anti-omission ------------


def _envelope(
    command: str = "git status --short",
    *,
    execution_surface: str = "local",
    operations: tuple[str, ...] = ("repository_observation",),
    exit_code: int | None = 0,
    mutated_paths: tuple[str, ...] = (),
) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        command=command,
        execution_surface=execution_surface,
        operations=operations,
        exit_code=exit_code,
        mutated_paths=mutated_paths,
    )


def test_plan_operations_must_be_covered_by_envelope() -> None:
    """Phase C/F2: envelope omitting plan-declared operations must block.

    Plan declares ``repository_observation``; envelope with empty operations
    cannot bypass the operation check by claiming nothing happened.
    """

    from reverse_agent.control_plane.command_authority import reconcile_command
    from reverse_agent.control_plane.legacy_adapter import build_transition_command_plan

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("status.git_status", operations=("repository_observation",))],
    )
    plan = build_transition_command_plan(decision, contract)
    envelope = _envelope(operations=())
    errors = reconcile_command(plan, envelope)
    assert any("operations_under_reported" in err for err in errors)


def test_plan_network_access_enforces_policy_without_envelope_operations() -> None:
    """Phase C/F2: plan network_access=true triggers network policy check.

    Even when envelope omits operations, plan-declared network_access must
    still be subject to capability_policy enforcement.
    """

    from reverse_agent.control_plane.transition import _plan_network_policy_violations
    from reverse_agent.control_plane.legacy_adapter import build_transition_command_plan
    from reverse_agent.control_plane.models import CapabilityPolicy

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command(
            "ci.install",
            command="python -m pip install -e .[test]",
            network_access=True,
            operations=("dependency_install",),
            required_evidence_source="exact_head_ci",
        )],
    )
    plan = build_transition_command_plan(decision, contract)
    # Envelope with no operations — must still trigger plan-driven network check.
    envelope = _envelope(
        command="python -m pip install -e .[test]",
        execution_surface="ci_only",
        operations=(),
    )
    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        ci_network_exceptions=(),  # deny
    )
    violations = _plan_network_policy_violations(plan, (envelope,), policy)
    assert violations, "plan network_access=true must trigger network policy even without envelope operations"


def test_plan_network_false_envelope_network_operation_blocks() -> None:
    """Phase C/F2: envelope claiming network when plan denies must block."""

    from reverse_agent.control_plane.transition import _envelope_network_violations
    from reverse_agent.control_plane.models import CapabilityPolicy

    envelope = _envelope(operations=("network_access",))
    policy = CapabilityPolicy(
        network_access_default_allowed=False,
        local_network_exceptions=(),
    )
    violations = _envelope_network_violations((envelope,), policy)
    assert violations, "envelope network operation must be blocked when policy denies"


def test_unknown_envelope_operation_fails_closed() -> None:
    """Phase C: unknown operations must fail closed."""

    from reverse_agent.control_plane.transition import _unknown_operation_violations
    from reverse_agent.control_plane.legacy_adapter import build_transition_command_plan

    decision = _decision()
    contract = _structured_contract(
        allowed_commands=[_structured_command("status.git_status", operations=("repository_observation",))],
    )
    plan = build_transition_command_plan(decision, contract)
    # Envelope claims an operation not declared by the plan.
    envelope = _envelope(operations=("repository_observation", "data_exfiltration"))
    violations = _unknown_operation_violations(plan, (envelope,))
    assert violations, "unknown operation must fail closed"


# --- Phase E: path contract separation (4-group) -------------------------


def _contract_with_path_contract(
    *,
    reference_paths: list[str] | None = None,
    generated_artifact_paths: list[str] | None = None,
    allowed_mutated_paths: list[str] | None = None,
    forbidden_mutated_paths: list[str] | None = None,
    path_risk_floor: list | None = None,
) -> dict:
    base = _structured_contract(
        allowed_commands=[_structured_command("status.git_status")],
    )
    if reference_paths is not None:
        base["reference_paths"] = reference_paths
    if generated_artifact_paths is not None:
        base["generated_artifact_paths"] = generated_artifact_paths
    if allowed_mutated_paths is not None:
        base["allowed_mutated_paths"] = allowed_mutated_paths
    if forbidden_mutated_paths is not None:
        base["forbidden_mutated_paths"] = forbidden_mutated_paths
    if path_risk_floor is not None:
        base["path_risk_floor"] = path_risk_floor
    return base


def test_load_transition_scope_returns_generated_artifact_paths() -> None:
    """Phase E: generated_artifact_paths must be loaded as a separate group."""

    from reverse_agent.control_plane.legacy_adapter import load_transition_scope

    decision = _decision()
    contract = _contract_with_path_contract(
        reference_paths=["docs/roadmap/example.md"],
        generated_artifact_paths=[
            "project_state/gates/command_plan.json",
            "project_state/gates/transition_preflight_result.json",
        ],
        allowed_mutated_paths=["reverse_agent/example/**"],
        forbidden_mutated_paths=["frontend/**"],
    )
    scope = load_transition_scope(decision, contract)
    assert scope["generated_artifact_paths"] == (
        "project_state/gates/command_plan.json",
        "project_state/gates/transition_preflight_result.json",
    )
    # Reference paths must remain separate from generated artifact paths.
    assert scope["reference_paths"] == ("docs/roadmap/example.md",)


def test_load_transition_scope_rejects_reference_generated_overlap() -> None:
    """Phase E: reference_paths and generated_artifact_paths must not overlap."""

    from reverse_agent.control_plane.legacy_adapter import load_transition_scope

    decision = _decision()
    contract = _contract_with_path_contract(
        reference_paths=["docs/roadmap/example.md"],
        generated_artifact_paths=["docs/roadmap/example.md"],
    )
    with pytest.raises(ValueError, match="reference_generated_path_conflict"):
        load_transition_scope(decision, contract)


def test_load_transition_scope_rejects_generated_forbidden_overlap() -> None:
    """Phase E: generated_artifact_paths and forbidden_mutated_paths must not overlap."""

    from reverse_agent.control_plane.legacy_adapter import load_transition_scope

    decision = _decision()
    contract = _contract_with_path_contract(
        generated_artifact_paths=["project_state/gates/command_plan.json"],
        forbidden_mutated_paths=["project_state/gates/command_plan.json"],
    )
    with pytest.raises(ValueError, match="generated_forbidden_path_conflict"):
        load_transition_scope(decision, contract)


def test_load_transition_scope_allows_generated_allowed_overlap() -> None:
    """Phase E v2: generated_artifact_paths may overlap with allowed_mutated_paths.

    The attestation policy seal round replaces the global generated-artifact
    exemption with command-bound mutation grants. A path may be both an
    authorized mutable path AND a generated artifact bound to a specific
    generator command via ``produced_artifacts``. The scope loader must accept
    this overlap; binding enforcement happens at execution-record validation.
    """

    from reverse_agent.control_plane.legacy_adapter import load_transition_scope

    decision = _decision()
    contract = _contract_with_path_contract(
        generated_artifact_paths=["project_state/gates/command_plan.json"],
        allowed_mutated_paths=["project_state/gates/command_plan.json"],
    )
    scope = load_transition_scope(decision, contract)
    assert "project_state/gates/command_plan.json" in scope["generated_artifact_paths"]
    assert "project_state/gates/command_plan.json" in scope["allowed_paths"]


def test_path_risk_applies_to_all_observed_paths_including_allowed() -> None:
    """Phase E/F5: path risk floor must apply to ALL observed paths, not just outside_scope.

    Modifying a path that is also in the risk floor (e.g. workflow file) and is
    NOT explicitly authorized by the active Decision must still be flagged.
    """

    from reverse_agent.control_plane.transition import _path_risk_floor_violations
    from reverse_agent.control_plane.models import PathRiskFloor

    floor = PathRiskFloor(
        entries=(
            (".github/workflows/**", "R2"),
            ("pyproject.toml", "R2"),
        )
    )
    observed = (
        "reverse_agent/example.py",  # not in floor
        ".github/workflows/ci.yml",  # R2 sensitive
        "pyproject.toml",            # R2 sensitive
    )
    violations = _path_risk_floor_violations(observed, floor, minimum="R2")
    assert ".github/workflows/ci.yml:R2" in violations
    assert "pyproject.toml:R2" in violations


def test_path_risk_floor_respects_explicit_decision_authorization() -> None:
    """F9: R2 paths explicitly authorized by active APPROVED Decision must NOT auto-block.

    A path that is in the risk floor AND in the Decision's authorized_risk_paths
    with risk <= authorized_risk_tier is authorized, not a violation.
    """

    from reverse_agent.control_plane.transition import _path_risk_floor_violations
    from reverse_agent.control_plane.models import PathRiskFloor

    floor = PathRiskFloor(
        entries=(
            ("project_state/gates/**", "R2"),
            ("config/secrets/**", "R3"),
        )
    )
    observed = (
        "project_state/gates/command_plan.json",  # R2, authorized
        "project_state/gates/execution_log.json",  # R2, authorized
        "config/secrets/api.key",                  # R3, exceeds authorized tier
    )
    violations = _path_risk_floor_violations(
        observed,
        floor,
        minimum="R2",
        authorized_risk_paths=("project_state/gates/**",),
        authorized_risk_tier="R2",
    )
    # Authorized R2 paths must NOT appear as violations.
    assert "project_state/gates/command_plan.json:R2" not in violations
    assert "project_state/gates/execution_log.json:R2" not in violations
    # R3 path exceeds authorized tier (R2) -> violation.
    assert "config/secrets/api.key:R3" in violations


def test_path_risk_floor_blocks_unauthorized_r2_path() -> None:
    """F9: R2 path outside authorized_risk_paths must still block."""

    from reverse_agent.control_plane.transition import _path_risk_floor_violations
    from reverse_agent.control_plane.models import PathRiskFloor

    floor = PathRiskFloor(
        entries=(("project_state/gates/**", "R2"),),
    )
    observed = ("project_state/gates/startup_snapshot.json",)
    violations = _path_risk_floor_violations(
        observed,
        floor,
        minimum="R2",
        authorized_risk_paths=("project_state/gates/command_plan.json",),
        authorized_risk_tier="R2",
    )
    # startup_snapshot.json is R2 but NOT in authorized_risk_paths -> violation.


# ---------------------------------------------------------------------------
# P1 closure (review finding 3921389194): execution-log loading must preserve
# and validate the explicit per-record execution_surface instead of rewriting
# every non-CI record to ``local``.
# ---------------------------------------------------------------------------


def _write_log(
    tmp_path: Path,
    entries: list[dict],
    *,
    log_marker: dict | None = None,
    name: str = "execution_log.json",
) -> Path:
    path = tmp_path / name
    payload: dict = {"schema_version": 1, "commands": entries}
    if log_marker:
        payload.update(log_marker)
    path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    return path


def _log_entry(
    command: str,
    *,
    phase: str = "status",
    surface: str | None = None,
    command_id: str | None = None,
) -> dict:
    entry: dict = {"command": command, "phase": phase, "exit_code": 0}
    if surface is not None:
        entry["execution_surface"] = surface
    if command_id is not None:
        entry["command_id"] = command_id
    return entry


def test_execution_log_preserves_explicit_trusted_worker_surface(tmp_path) -> None:
    """P1-3921389194: an explicit ``trusted_worker`` surface must be preserved."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    path = _write_log(tmp_path, [_log_entry("git status --short", surface="trusted_worker")])
    envelopes = load_execution_envelopes_from_log(path)
    assert envelopes[0].execution_surface == "trusted_worker"


def test_execution_log_preserves_explicit_user_local_surface(tmp_path) -> None:
    """P1-3921389194: an explicit ``user_local`` surface must be preserved."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    path = _write_log(tmp_path, [_log_entry("git status --short", surface="user_local")])
    envelopes = load_execution_envelopes_from_log(path)
    assert envelopes[0].execution_surface == "user_local"


def test_execution_log_preserves_explicit_ci_only_surface(tmp_path) -> None:
    """P1-3921389194: an explicit ``ci_only`` surface must be preserved."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    path = _write_log(tmp_path, [_log_entry("git status --short", surface="ci_only")])
    envelopes = load_execution_envelopes_from_log(path)
    assert envelopes[0].execution_surface == "ci_only"


def test_execution_log_unknown_surface_fails_closed(tmp_path) -> None:
    """P1-3921389194: an unknown explicit surface must fail closed."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    path = _write_log(tmp_path, [_log_entry("git status --short", surface="not_a_surface")])
    with pytest.raises(ValueError, match="invalid_execution_surface"):
        load_execution_envelopes_from_log(path)


def test_execution_log_structured_missing_surface_fails_closed(tmp_path) -> None:
    """P1-3921389194: current/new structured evidence without an explicit
    execution_surface must fail closed instead of defaulting to ``local``."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    path = _write_log(tmp_path, [_log_entry("git status --short", command_id="status.git_status")])
    with pytest.raises(ValueError, match="missing_execution_surface"):
        load_execution_envelopes_from_log(path)


def test_execution_log_historical_legacy_missing_surface_explicit_compat_only(tmp_path) -> None:
    """P1-3921389194: historical legacy evidence without a surface is readable
    ONLY through the explicit legacy compatibility marker. Without the marker a
    missing surface fails closed; with the marker the narrow phase-derived
    legacy mapping (ci_* -> ci_only, else local) applies."""
    from reverse_agent.control_plane.legacy_adapter import load_execution_envelopes_from_log

    no_marker = _write_log(tmp_path, [_log_entry("git status --short")], name="no_marker.json")
    with pytest.raises(ValueError, match="missing_execution_surface"):
        load_execution_envelopes_from_log(no_marker)

    legacy_ci = _write_log(
        tmp_path,
        [_log_entry("run ci", phase="ci_stage")],
        log_marker={"legacy_compatibility": True},
        name="legacy_ci.json",
    )
    legacy_local = _write_log(
        tmp_path,
        [_log_entry("git status --short")],
        log_marker={"legacy_compatibility": True},
        name="legacy_local.json",
    )
    envelops_ci = load_execution_envelopes_from_log(legacy_ci)
    envelops_local = load_execution_envelopes_from_log(legacy_local)
    assert envelops_ci[0].execution_surface == "ci_only"
    assert envelops_local[0].execution_surface == "local"
