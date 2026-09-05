from __future__ import annotations

from dataclasses import replace

from reverse_agent.architecture.contracts import AuthorizationRequest, ExecutionEnvelope, WorkflowIdentity
from reverse_agent.architecture.risk import AuthorizationStatus, RiskTier
from reverse_agent.control_plane.models import TransitionAuthority, TransitionCommand, TransitionCommandPlan, TransitionDecision
from reverse_agent.trust.authorization import TransitionKernelAuthorizationAdapter


def _authority() -> TransitionAuthority:
    decision = TransitionDecision("decision_x", "round_x", "APPROVED", "engineering_branch", ("reverse-agent-iteration@v2",))
    command = "python -m pytest tests/test_architecture_contracts.py -q"
    plan = TransitionCommandPlan(
        "decision_x",
        "round_x",
        (TransitionCommand(
            command,
            "test",
            True,
            (0,),
            "trusted_worker",
            ("unit_test",),
            command_id="test.unit",
            allowed_mutated_paths=("tests/test_architecture_contracts.py",),
        ),),
    )
    return TransitionAuthority(
        decision=decision,
        command_plan=plan,
        expected_decision_id="decision_x",
        expected_round_id="round_x",
        active_skills=("reverse-agent-iteration@v2",),
        legal_mainlines=("engineering_branch",),
        expected_branch="codex/architecture-spine-v1",
        actual_branch="codex/architecture-spine-v1",
        base_sha="a" * 40,
        merge_base_sha="a" * 40,
        decision_commit_sha="b" * 40,
        decision_is_ancestor=True,
        observed_paths=(),
        allowed_paths=("tests/test_architecture_contracts.py",),
        forbidden_paths=("frontend/**",),
        forbidden_operations=("force_push",),
    )


def _request(*, decision_id: str = "decision_x", tier: RiskTier = RiskTier.R2) -> AuthorizationRequest:
    return AuthorizationRequest(
        workflow_identity=WorkflowIdentity("workflow-x", "owner/repo#1@node"),
        risk_tier=tier,
        envelope=ExecutionEnvelope(("unit_test",), ("tests/test_architecture_contracts.py",)),
        decision_id=decision_id,
        round_id="round_x",
        command="python -m pytest tests/test_architecture_contracts.py -q",
    )


def test_transition_adapter_authorizes_matching_high_risk_request() -> None:
    result = TransitionKernelAuthorizationAdapter(_authority()).authorize(_request())
    assert result.status is AuthorizationStatus.AUTHORIZED


def test_transition_adapter_fails_closed_on_identity_or_kernel_failure() -> None:
    adapter = TransitionKernelAuthorizationAdapter(_authority())
    assert adapter.authorize(_request(decision_id="wrong")).status is AuthorizationStatus.BLOCKED
    broken = replace(_authority(), decision_is_ancestor=False)
    assert TransitionKernelAuthorizationAdapter(broken).authorize(_request()).status is AuthorizationStatus.BLOCKED


def test_transition_adapter_does_not_accept_low_risk_requests() -> None:
    result = TransitionKernelAuthorizationAdapter(_authority()).authorize(_request(tier=RiskTier.R1))
    assert result.status is AuthorizationStatus.BLOCKED
