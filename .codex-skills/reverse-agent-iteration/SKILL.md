---
name: reverse-agent-iteration
description: Use when working in the reverse-agent repository on iterative reverse-engineering challenge solving, including continuing an unfinished RE plan, reading harness artifacts, comparing solve_reports results, identifying the current best candidate and stall stage, planning the next solver iteration, running validation, or updating project progress. This is the generic workflow layer and must not encode sample-specific anchors, flags, or thresholds.
metadata:
  short-description: Iterate reverse-agent RE challenge runs
---

# Reverse Agent Iteration

Use this skill for reverse-agent challenge iteration work. It provides the generic loop; sample-specific facts belong in a separate sample skill.

## Start Every Iteration

1. Confirm the workspace is the `reverse-agent` repository.
2. Read `AGENT_GUIDE_FOR_AI.md` for current project conventions.
3. Read the tail of `PROJECT_PROGRESS_LOG.txt` for the latest handoff.
4. Inspect the newest relevant `solve_reports/harness_runs/*` directory before relying on memory.
5. Identify the latest complete artifacts: summary files, validation JSON, compare/runtime evidence, and any strategy-specific result JSON.

## Artifact Triage

Extract these facts from artifacts:

- Current best candidate and its source stage.
- Runtime validation result, if available.
- Offline metrics versus runtime metrics.
- Whether compare/runtime semantics agree.
- The latest stall stage: instrumentation, candidate generation, gate/filtering, refine, SMT, validation, model fallback, or reporting/logging.
- Whether the latest change improved the baseline, only changed diagnostics, or regressed.

Prefer structured JSON over markdown summaries. If a command or harness run fails after producing strategy artifacts, inspect whether the artifacts are still complete before treating the run as unusable.

## Choosing Next Work

Map the stall stage to the next action:

- `instrumentation`: improve runtime evidence capture or artifact fields.
- `candidate generation`: adjust bounded candidate sources or provenance, not downstream gates first.
- `gate/filtering`: add diagnostics or refine acceptance bands before expanding search.
- `refine`: improve handoff quality or anchor/context selection.
- `SMT`: adjust variable positions/values/objective within the existing solver path.
- `validation`: fix compare/runtime consistency, explicit validation candidates, or output paths.
- `reporting/logging`: preserve solver behavior and repair observability.

Keep the default bias toward bounded, evidence-driven iteration. Do not expand to blind brute force unless the latest evidence specifically invalidates all structured routes.

## Implementation Discipline

- Preserve user or previous-agent edits; inspect dirty state before editing tracked files.
- Keep generic framework changes sample-neutral.
- Put challenge-specific anchors, thresholds, and candidate facts in a sample skill or profile-specific code path.
- After code changes, run the narrow relevant tests first, then broader tests when feasible.
- After a meaningful solver iteration, run a real harness regression when the target sample is available.
- Update `PROJECT_PROGRESS_LOG.txt` with the run name, artifact path, baseline comparison, stall stage, and next default direction.

## Response Shape

When giving a plan, include:

- Current state from artifacts.
- The next bottleneck and why.
- Generic framework change versus sample-specific change.
- Tests and acceptance criteria.

When reporting execution, include:

- Files changed.
- Tests run and results.
- Harness run name and whether artifacts were complete.
- Whether the best runtime candidate improved.
