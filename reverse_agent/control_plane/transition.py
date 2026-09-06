"""Independent preflight validation for control-plane transition rounds."""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any, Iterable

from .command_authority import (
    authorize_command,
    canonical_command,
    capability_forbidden_operations_for_policy,
    validate_command_plan,
)
from .execution_reconciliation import reconcile_executions
from .models import (
    CapabilityPolicy,
    ExecutionEnvelope,
    PathRiskFloor,
    TransitionAuthority,
    TransitionPreflightResult,
)


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _path_matches(path: str, pattern: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    candidate = pattern.replace("\\", "/").lstrip("./")
    if candidate.endswith("/**"):
        return normalized.startswith(candidate[:-3].rstrip("/") + "/")
    return normalized == candidate or fnmatch(normalized, candidate)


def _paths_within_scope(paths: Iterable[str], patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path for path in paths
        if not any(_path_matches(path, pattern) for pattern in patterns)
    )


def _paths_in_forbidden(paths: Iterable[str], patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path for path in paths
        if any(_path_matches(path, pattern) for pattern in patterns)
    )


_GLOB_META_CHARS = frozenset("*?[")


def _split_pattern_segments(pattern: str) -> tuple[str, ...]:
    """Split a normalized path pattern into ``/``-separated segments.

    Leading ``./`` and redundant slashes are stripped.  ``**`` is preserved as
    a zero-or-more path-segment wildcard; ``*``, ``?`` and character classes
    apply within a single segment.
    """

    normalized = pattern.replace("\\", "/").lstrip("./")
    return tuple(segment for segment in normalized.split("/") if segment)


def _segment_has_glob_meta(segment: str) -> bool:
    return any(char in segment for char in _GLOB_META_CHARS)


def _segment_may_match(left: str, right: str) -> bool:
    """Return whether two single-segment patterns may match one segment.

    This is an authority check: it must never produce a false negative.  An
    exact/literal segment only matches itself; a literal-vs-glob comparison is
    decided with ``fnmatch``; glob-vs-glob is conservatively treated as
    may-match unless it is provably disjoint.
    """

    if left == right:
        return True
    left_meta = _segment_has_glob_meta(left)
    right_meta = _segment_has_glob_meta(right)
    if not left_meta and not right_meta:
        return False
    if not left_meta:
        return fnmatch(left, right)
    if not right_meta:
        return fnmatch(right, left)
    # Two glob segments can always produce a concrete string (e.g. all ``a``,
    # or one of the two literals embedded in either pattern).  Conservatively
    # treat as may-match; the only provably-disjoint glob pair would require a
    # full language-intersection proof, which is intentionally NOT attempted.
    return True


def _patterns_may_intersect(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """Return whether two segment tuples may match a common path.

    ``**`` matches zero or more whole segments.  A conservative existential
    search is used: any assignment that satisfies both patterns means the
    grants may overlap, and an inability to find one is treated as disjoint
    only when both patterns are fully consumed or provably mismatched.  This
    is an authority check, so any residual uncertainty keeps the may-intersect
    result (failing closed in the caller).
    """

    left = tuple(segment for segment in left if segment)
    right = tuple(segment for segment in right if segment)
    if not left and not right:
        return True
    if not left:
        return all(segment == "**" for segment in right)
    if not right:
        return all(segment == "**" for segment in left)
    if left[0] == "**" or right[0] == "**":
        if left[0] == "**":
            return _patterns_may_intersect(left[1:], right) or _patterns_may_intersect(left, right[1:])
        return _patterns_may_intersect(left, right[1:]) or _patterns_may_intersect(left[1:], right)
    if not _segment_may_match(left[0], right[0]):
        return False
    return _patterns_may_intersect(left[1:], right[1:])


def _patterns_may_intersect_text(left: str, right: str) -> bool:
    """Return whether two path patterns may match a common concrete path."""

    return _patterns_may_intersect(
        _split_pattern_segments(left),
        _split_pattern_segments(right),
    )


def _path_grant_conflicts(
    writable_patterns: tuple[str, ...],
    reference_patterns: tuple[str, ...],
) -> tuple[str, ...]:
    """Return writable/reference grant overlaps using shared path semantics.

    P1 closure (review finding 3921697391): a conflict exists whenever the two
    pattern languages MAY intersect, not merely when one pattern string matches
    the other.  ``docs/*.md`` and ``docs/example.*`` share ``docs/example.md``
    even though neither ``_path_matches`` orientation holds.  This is an
    authority check, so it fails closed: unless the patterns are provably
    disjoint, they are reported as a conflict.
    """

    conflicts: list[str] = []
    for writable in writable_patterns:
        for reference in reference_patterns:
            if _patterns_may_intersect_text(writable, reference):
                conflicts.append(f"{writable}->{reference}")
    return tuple(dict.fromkeys(conflicts))


def _capability_forbidden_operations(policy: CapabilityPolicy) -> tuple[str, ...]:
    """Map capability flags to forbidden operations.

    Each flag that is ``False`` must add its corresponding operation to the
    forbidden set so the machine gate stays in sync with the Decision.

    The flag->operation vocabulary lives in the single canonical
    ``CAPABILITY_OPERATION_MAPPING`` (command_authority.py) shared with the
    operation-surface compatibility registry, so the vocabulary cannot drift
    between capability-policy enforcement and registry completeness.
    """

    return capability_forbidden_operations_for_policy(policy)


def _network_exceptions_for_surface(policy: CapabilityPolicy, surface: str) -> tuple[str, ...]:
    """Map an execution surface to its declared network exception list.

    Defaults remain deny: an unknown surface or a surface without a declared
    exception list yields an empty (deny) tuple. ``remote_observation`` is
    read-only and never carries network exceptions.
    """

    mapping = {
        "local": policy.local_network_exceptions,
        "ci_only": policy.ci_network_exceptions,
        "trusted_worker": policy.trusted_worker_network_exceptions,
        "github_control_plane": policy.github_control_plane_network_exceptions,
        "user_local": policy.user_local_network_exceptions,
        "remote_observation": (),
    }
    return mapping.get(surface, ())


def _envelope_network_violations(
    envelopes: tuple[ExecutionEnvelope, ...],
    policy: CapabilityPolicy,
) -> tuple[str, ...]:
    """Check that network access honors the capability policy.

    Network operations must be allowed only through the exception list bound
    to the exact execution surface of the envelope (``local`` maps to
    ``local_network_exceptions``, ``ci_only`` to ``ci_network_exceptions``,
    ``trusted_worker`` to ``trusted_worker_network_exceptions``,
    ``github_control_plane`` to ``github_control_plane_network_exceptions``,
    and ``user_local`` to ``user_local_network_exceptions``).
    """

    violations: list[str] = []
    network_operations = {"network_access", "network", "package_install", "dependency_install"}
    for envelope in envelopes:
        if not any(op in network_operations for op in envelope.operations):
            continue
        allowed_exceptions = _network_exceptions_for_surface(policy, envelope.execution_surface)
        requested = canonical_command(envelope.command)
        if requested in {canonical_command(item) for item in allowed_exceptions}:
            continue
        violations.append(
            f"network_access_violation:{envelope.execution_surface}:{requested}"
        )
    return tuple(dict.fromkeys(violations))


def _path_risk_floor_violations(
    observed_paths: tuple[str, ...],
    path_risk_floor: PathRiskFloor | None,
    *,
    minimum: str = "R2",
    authorized_risk_paths: tuple[str, ...] = (),
    authorized_risk_tier: str = "",
) -> tuple[str, ...]:
    """Return paths that violate the risk floor for sensitive locations.

    F9: a path is a violation only when it is NOT explicitly authorized by the
    active APPROVED Decision, OR its floor risk exceeds the authorized tier.
    Paths in ``authorized_risk_paths`` with floor risk <= ``authorized_risk_tier``
    are treated as authorized and do not appear as violations.
    """

    if path_risk_floor is None or not path_risk_floor.entries:
        return ()
    floor_rank = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
    minimum_rank = floor_rank.get(minimum, 2)
    authorized_rank = floor_rank.get(authorized_risk_tier, -1) if authorized_risk_tier else -1
    violations: list[str] = []
    for path in observed_paths:
        risk = path_risk_floor.risk_for_path(path)
        if risk is None:
            continue
        if floor_rank.get(risk, 0) < minimum_rank:
            continue
        # F9: a path explicitly authorized by the active Decision at a tier
        # >= the floor risk is not a violation.
        if authorized_risk_paths and _path_matches_any(path, authorized_risk_paths):
            if authorized_rank >= 0 and floor_rank.get(risk, 0) <= authorized_rank:
                continue
        violations.append(f"{path}:{risk}")
    return tuple(dict.fromkeys(violations))


def _path_matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    """Return True if path matches any of the provided glob patterns."""

    return any(_path_matches(path, pattern) for pattern in patterns)


def _plan_network_policy_violations(
    plan: Any,
    envelopes: tuple[ExecutionEnvelope, ...],
    policy: CapabilityPolicy,
) -> tuple[str, ...]:
    """Phase C/F2: plan-driven network_access enforcement.

    When a plan entry declares ``network_access=True``, the capability policy
    must be enforced regardless of whether the matching envelope reports
    network operations. This prevents envelopes from bypassing the network
    policy by simply omitting the ``network_access`` operation.
    """

    violations: list[str] = []
    envelope_by_surface: dict[str, list[ExecutionEnvelope]] = {}
    for env in envelopes:
        envelope_by_surface.setdefault(env.execution_surface, []).append(env)
    for cmd in plan.commands:
        if not cmd.network_access:
            continue
        if cmd.authority_origin == "bootstrap_exception":
            continue
        surface = cmd.execution_surface
        allowed_exceptions = _network_exceptions_for_surface(policy, surface)
        requested = canonical_command(cmd.command)
        if requested in {canonical_command(item) for item in allowed_exceptions}:
            continue
        if policy.network_access_default_allowed:
            continue
        # The plan declares network access and the policy does not allow it;
        # this is a violation regardless of what the envelope says.
        violations.append(
            f"plan_network_access_denied:{cmd.command_id or requested}:{surface}"
        )
    return tuple(dict.fromkeys(violations))


def _unknown_operation_violations(
    plan: Any,
    envelopes: tuple[ExecutionEnvelope, ...],
) -> tuple[str, ...]:
    """Phase C: unknown operations in envelopes must fail closed.

    Each operation in an envelope must be declared by the matching plan
    entry (or be a network-access-related operation that the plan's
    ``network_access`` flag covers). Operations that the plan never
    declared are suspicious and must block reconciliation.
    """

    plan_operations_by_command: dict[str, set[str]] = {}
    plan_network_by_command: dict[str, bool] = {}
    for cmd in plan.commands:
        key = canonical_command(cmd.command)
        plan_operations_by_command.setdefault(key, set()).update(cmd.operations)
        plan_network_by_command[key] = cmd.network_access
    network_aliases = {"network_access", "network", "package_install", "dependency_install"}
    violations: list[str] = []
    for envelope in envelopes:
        requested = canonical_command(envelope.command)
        declared = plan_operations_by_command.get(requested, set())
        for operation in envelope.operations:
            if operation in declared:
                continue
            if operation in network_aliases and plan_network_by_command.get(requested, False):
                continue
            violations.append(f"unknown_operation:{requested}:{operation}")
    return tuple(dict.fromkeys(violations))


def _reference_path_write_violations(
    observed_paths: tuple[str, ...],
    reference_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Ensure reference (read-only) paths are not mutated."""

    violations: list[str] = []
    for path in observed_paths:
        for pattern in reference_paths:
            if _path_matches(path, pattern):
                violations.append(f"{path}:reference_path_mutated")
                break
    return tuple(dict.fromkeys(violations))


def _required_command_coverage_missing(
    plan: Any,
    envelopes: tuple[ExecutionEnvelope, ...],
) -> tuple[str, ...]:
    """Validate required evidence using stable, coherent command identity.

    P1 closure (review finding 3921389187): a required command is observed
    only when a single execution envelope carries the exact
    ``(command_id, execution_surface, canonical_command)`` triple authorized
    by the plan entry.  The canonical executed command must belong to the
    SAME plan entry that owns ``command_id`` + ``execution_surface``; mixing
    a required command's ID/surface with the text of a different optional
    command must never satisfy coverage.  ``reconcile_executions`` still
    validates command text, exit codes, operations and other execution
    details independently, but coverage can no longer be assembled from two
    separate validations.  Command-plan ``command_id`` uniqueness continues to
    fail closed in ``validate_command_plan``.
    """

    required_commands = [
        cmd for cmd in plan.commands
        if cmd.required
        and not cmd.diagnostic_only
        and cmd.authority_origin != "bootstrap_exception"
    ]
    plan_command_by_identity = {
        (cmd.command_id, cmd.execution_surface): canonical_command(cmd.command)
        for cmd in plan.commands
        if cmd.command_id and cmd.execution_surface
    }
    observed_identities = {
        (env.command_id, env.execution_surface, canonical_command(env.command))
        for env in envelopes
        if env.command_id and env.execution_surface and canonical_command(env.command)
    }
    missing: list[str] = []
    for cmd in required_commands:
        if cmd.required_evidence_source in {
            "local_command_evidence",
            "repository_state_attestation",
        }:
            requested = canonical_command(cmd.command)
            expected = plan_command_by_identity.get(
                (cmd.command_id, cmd.execution_surface)
            )
            identity = (cmd.command_id, cmd.execution_surface, requested)
            if (
                not cmd.command_id
                or not cmd.execution_surface
                or not requested
                or expected != requested
                or identity not in observed_identities
            ):
                missing.append(cmd.command_id or cmd.command)
        elif cmd.required_evidence_source == "ci_check_attestation":
            # Local provenance cannot satisfy CI evidence; flag as missing
            # until an external CI observation is provided. The post gate
            # currently only sees local envelopes, so required CI commands
            # always fail closed from a local-only log.
            missing.append(cmd.command_id or cmd.command)
    return tuple(dict.fromkeys(missing))


def validate_transition(
    authority: TransitionAuthority,
    envelopes: tuple[ExecutionEnvelope, ...] = (),
    *,
    mode: str = "pre",
) -> TransitionPreflightResult:
    """Validate transition authority without consulting legacy acceptance state.

    ``mode='pre'``: pre-execution authorization. The execution_log is NOT
    consulted as completion evidence; the gate only verifies plan identity,
    path contract, capability policy and bootstrap state.

    ``mode='post'``: post-execution reconciliation. Validates required command
    coverage in addition to the pre-execution checks. Returns
    ``POST_EXECUTION_RECONCILED`` on success.
    """

    decision = authority.decision
    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "decision_identity",
        decision.decision_id == authority.expected_decision_id,
        f"observed={decision.decision_id} expected={authority.expected_decision_id}",
    ))
    checks.append(_check(
        "round_identity",
        decision.round_id == authority.expected_round_id,
        f"observed={decision.round_id} expected={authority.expected_round_id}",
    ))
    checks.append(_check("decision_approved", decision.status == "APPROVED", f"status={decision.status}"))
    missing_skills = tuple(skill for skill in decision.skill_profiles if skill not in authority.active_skills)
    checks.append(_check("active_skills", not missing_skills, f"missing={list(missing_skills)}"))
    checks.append(_check(
        "legal_mainline",
        decision.mainline in authority.legal_mainlines,
        f"mainline={decision.mainline}",
    ))
    branch_ok = authority.actual_branch == authority.expected_branch
    checks.append(_check(
        "branch_identity",
        branch_ok,
        f"observed={authority.actual_branch} expected={authority.expected_branch}",
    ))
    checks.append(_check(
        "base_ancestry",
        bool(authority.base_sha) and authority.merge_base_sha == authority.base_sha,
        f"base={authority.base_sha} merge_base={authority.merge_base_sha}",
    ))
    checks.append(_check(
        "decision_ancestry",
        bool(authority.decision_commit_sha) and authority.decision_is_ancestor,
        f"decision_commit={authority.decision_commit_sha}",
    ))
    plan_identity_ok = (
        authority.command_plan.decision_id == decision.decision_id
        and authority.command_plan.round_id == decision.round_id
    )
    checks.append(_check(
        "command_plan_identity",
        plan_identity_ok,
        f"plan={authority.command_plan.decision_id}/{authority.command_plan.round_id}",
    ))
    plan_errors = validate_command_plan(authority.command_plan)
    checks.append(_check("command_plan_contract", not plan_errors, f"errors={list(plan_errors)}"))

    # P1 closure: read-only path classes are incompatible with every writable
    # grant, not merely observed mutations.  Validate this before the pre-mode
    # return so direct/shared transition callers fail closed before execution.
    command_granted_paths: tuple[str, ...] = ()
    for entry in authority.command_plan.commands:
        command_granted_paths = (
            *command_granted_paths,
            *entry.produced_artifacts,
            *entry.allowed_mutated_paths,
        )
    # P1 closure (review finding 3921389199): ``bootstrap_exception_files`` are
    # a pre-preflight writable grant and must participate in the reference/write
    # disjointness check exactly like every other writable grant.
    writable_grants = tuple(dict.fromkeys((
        *authority.allowed_paths,
        *command_granted_paths,
        *authority.runner_managed_artifact_paths,
        *authority.bootstrap_exception_files,
    )))
    reference_write_conflicts = _path_grant_conflicts(
        writable_grants,
        authority.reference_paths,
    )
    checks.append(_check(
        "reference_write_grants_disjoint",
        not reference_write_conflicts,
        f"conflicts={list(reference_write_conflicts)}",
    ))

    mutated_paths = tuple(dict.fromkeys(
        (*authority.observed_paths, *(path for envelope in envelopes for path in envelope.mutated_paths))
    ))
    # Phase D: remove global generated-artifact exemption. ``generated_artifact_paths``
    # is inventory only (rule #2). A path is authorized for write only if it is
    # in the decision-level ``allowed_paths`` OR in some command's grant
    # (``produced_artifacts ∪ allowed_mutated_paths``). Per-command binding is
    # enforced separately by ``mutation_grants_enforced`` (rule #1).
    # F9/F4: runner-managed artifact paths (executor provenance) are also
    # authorized for write by the trusted execution context itself.
    effective_allowed_scope = tuple(dict.fromkeys((
        *authority.allowed_paths,
        *command_granted_paths,
        *authority.runner_managed_artifact_paths,
    )))
    outside_scope = _paths_within_scope(mutated_paths, effective_allowed_scope)
    # F9/F4: runner-managed artifact paths (executor provenance) are exempt
    # from the forbidden_paths check because they are written by the trusted
    # execution context itself, not by subprocess mutations. Without this
    # exemption, runner-managed .bin evidence files would always be flagged
    # as forbidden binary mutations, blocking the preflight even though the
    # trusted runner is the sole legitimate writer of those paths.
    non_runner_managed_mutated = tuple(
        path for path in mutated_paths
        if not any(_path_matches(path, pattern) for pattern in authority.runner_managed_artifact_paths)
    )
    forbidden_paths = _paths_in_forbidden(non_runner_managed_mutated, authority.forbidden_paths)
    checks.append(_check("allowed_path_scope", not outside_scope, f"outside={list(outside_scope)}"))
    checks.append(_check("forbidden_paths", not forbidden_paths, f"forbidden={list(forbidden_paths)}"))

    # Reference paths must remain read-only even when they appear in the Decision.
    # F10: the read-only check must cover ALL observed mutated paths, not just
    # ``outside_scope``. A reference path mistakenly placed in allowed scope
    # must still be flagged.
    reference_violations = _reference_path_write_violations(mutated_paths, authority.reference_paths)
    checks.append(_check(
        "reference_paths_read_only",
        not reference_violations,
        f"violations={list(reference_violations)}",
    ))

    # Phase D: command-bound mutation grants. Each envelope's mutated paths
    # must be backed by its command's ``produced_artifacts ∪ allowed_mutated_paths``
    # (rule #1). ``generated_artifact_paths`` does NOT grant write permission.
    from .command_authority import validate_mutation_grants
    # Pre-execution envelopes describe requested paths and have no observed
    # exit code or trusted command ID yet.  They are authorization input, not
    # mutation evidence.  Enforce command-local grants only for executions
    # that have actually produced a result; post-execution records still fail
    # closed on missing/unknown/duplicate command IDs with no string fallback.
    executed_envelopes = (
        envelopes
        if mode == "post"
        else tuple(envelope for envelope in envelopes if envelope.exit_code is not None)
    )
    mutation_violations = validate_mutation_grants(
        authority.command_plan,
        executed_envelopes,
        generated_artifact_paths=authority.generated_artifact_paths,
    )
    checks.append(_check(
        "mutation_grants_enforced",
        not mutation_violations,
        f"violations={list(mutation_violations)}",
    ))

    # Capability policy mapping.
    if authority.capability_policy is not None:
        capability_forbidden = _capability_forbidden_operations(authority.capability_policy)
        all_forbidden = tuple(dict.fromkeys((*authority.forbidden_operations, *capability_forbidden)))
        operations = tuple(operation for envelope in envelopes for operation in envelope.operations)
        forbidden_operations = tuple(
            operation for operation in operations if operation in all_forbidden
        )
        checks.append(_check(
            "capability_policy_enforced",
            not forbidden_operations,
            f"forbidden={list(forbidden_operations)}",
        ))
        network_violations = _envelope_network_violations(envelopes, authority.capability_policy)
        checks.append(_check(
            "network_policy_enforced",
            not network_violations,
            f"violations={list(network_violations)}",
        ))
    else:
        operations = tuple(operation for envelope in envelopes for operation in envelope.operations)
        forbidden_operations = tuple(
            operation for operation in operations if operation in authority.forbidden_operations
        )
        checks.append(_check(
            "forbidden_operations",
            not forbidden_operations,
            f"forbidden={list(forbidden_operations)}",
        ))

    # Path risk floor enforcement. Phase E/F5: path risk must apply to ALL
    # observed paths, not just outside_scope. Allowed scope authorizes writes
    # but does not lower the risk classification; an allowed path that is also
    # in the risk floor (e.g. .github/workflows/**) must still be flagged so
    # the runtime can route to Trust Authorization.
    # Phase D: generated_artifact_paths no longer exempt — all mutated paths
    # are subject to the risk floor (rule #2: no auto-exemption).
    if authority.path_risk_floor is not None and authority.path_risk_floor.entries:
        floor_violations = _path_risk_floor_violations(
            mutated_paths,
            authority.path_risk_floor,
            minimum="R2",
            authorized_risk_paths=authority.authorized_risk_paths,
            authorized_risk_tier=authority.authorized_risk_tier,
        )
        checks.append(_check(
            "path_risk_floor_enforced",
            not floor_violations,
            f"violations={list(floor_violations)}",
        ))

    # Real execution reconciliation. In pre mode, do NOT consume execution_log
    # as completion evidence; pass empty envelopes so the pre-execution gate
    # cannot pass based on stale local provenance.
    effective_envelopes = envelopes if mode == "post" else ()
    reconciliation = reconcile_executions(authority.command_plan, effective_envelopes)
    if mode == "post":
        checks.append(_check(
            "execution_reconciliation",
            reconciliation.status != "BLOCKED",
            f"status={reconciliation.status} reasons={list(reconciliation.blocking_reasons)}",
        ))
        checks.append(_check(
            "execution_evidence_present",
            not reconciliation.missing_evidence,
            f"missing_evidence={reconciliation.missing_evidence}",
        ))
        # Phase B/F1: required command coverage. A subset of valid records is
        # not completion. Bootstrap-only records cannot produce a post-
        # execution reconcile.
        missing_coverage = _required_command_coverage_missing(authority.command_plan, effective_envelopes)
        checks.append(_check(
            "required_command_coverage",
            not missing_coverage,
            f"missing={list(missing_coverage)}",
        ))
        # Phase C/F2: plan-driven network_access enforcement. The capability
        # policy must be applied to every plan entry that declares
        # ``network_access=True`` regardless of envelope-reported operations.
        if authority.capability_policy is not None:
            plan_network_violations = _plan_network_policy_violations(
                authority.command_plan,
                effective_envelopes,
                authority.capability_policy,
            )
            checks.append(_check(
                "plan_network_policy_enforced",
                not plan_network_violations,
                f"violations={list(plan_network_violations)}",
            ))
        # Phase C: unknown operations in envelopes must fail closed.
        unknown_operations = _unknown_operation_violations(
            authority.command_plan,
            effective_envelopes,
        )
        checks.append(_check(
            "envelope_operations_declared",
            not unknown_operations,
            f"violations={list(unknown_operations)}",
        ))
    else:
        # Pre mode: no execution evidence required, but no stale log may
        # accidentally turn the gate green either.
        checks.append(_check(
            "execution_evidence_present",
            True,
            "pre_execution_mode_no_evidence_required",
        ))

    blocking = tuple(
        f"{check['name']}: {check['detail']}"
        for check in checks
        if check["status"] == "FAIL"
    )
    if not blocking:
        gate_status = "POST_EXECUTION_RECONCILED" if mode == "post" else "PASSED"
    else:
        gate_status = "BLOCKED"
    return TransitionPreflightResult(
        decision_id=decision.decision_id,
        round_id=decision.round_id,
        gate_status=gate_status,
        checks=tuple(checks),
        blocking_reasons=blocking,
    )
