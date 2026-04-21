# Reverse Agent - AI Quickstart Playbook

Use this file as the **first-read startup guide** so a new AI session can begin work immediately.

## Current Project Status (Read First)
- Latest execution log is maintained in `PROJECT_PROGRESS_LOG.txt`.
- Start every new `samplereverse` session by reading `PROJECT_PROGRESS_LOG.txt` first.
- `samplereverse` now runs on the profile + compare-aware strategy path, not the old blind-search-only flow.
- The file records:
  - what has already been completed,
  - current verified findings on `samplereverse.exe`,
  - concrete next tasks for the next iteration.

## 60-Second Startup Checklist
1. Confirm goal: solve reverse challenge input and output one final flag.
2. Confirm input type: local executable path or downloadable URL.
3. Choose analysis mode:
   - `Auto` for default intelligent routing
   - `Static Analysis` for string/logic-driven tasks
   - `Dynamic Debug` for runtime-only/anti-debug/decrypt-at-runtime tasks
4. Use existing pipeline; do not redesign core flow unless requested.
5. Ensure report is generated under `solve_reports\`.

## Project Map (What matters first)
- `app.py`: GUI entrypoint.
- `reverse_agent\gui.py`: user interface (input, analysis mode, model switch).
- `reverse_agent\pipeline.py`: solve workflow orchestration.
- `reverse_agent\dynamic_templates.py`: static/dynamic analysis templates.
- `reverse_agent\models.py`: model backends (Copilot CLI / local OpenAI-compatible API).
- `reverse_agent\tool_runners.py`: IDA / Olly automation entry and artifact normalization.
- `reverse_agent\advanced_solvers.py`: optional `angr` symbolic fallback solver.
- `reverse_agent\profiles\samplereverse.py`: sample-specific profile entry and strategy selection.
- `reverse_agent\strategies\compare_aware_search.py`: compare-aware search and validation driver.
- `reverse_agent\transforms\samplereverse.py`: canonical compare-aware scoring interface.
- `reverse_agent\olly_scripts\collect_evidence.py`: built-in default Olly automation script.
- `reverse_agent\reporter.py`: detailed markdown report writer.

## Samplereverse Current Handoff
- Fixed main line: `L15(prefix8)` only.
- Current strongest runtime-consistent exact2 candidate:
  - `78d540b49c59077041414141414141`
  - runtime prefix: `46006c004464830d311c`
  - metrics: `runtime_ci_exact_wchars=2`, `runtime_ci_distance5=246`
- Secondary exact2 reference:
  - `4a78f0eaeb4f13b041414141414141`
  - runtime prefix: `46004c007e40b92886f5`
  - metrics: `runtime_ci_exact_wchars=2`, `runtime_ci_distance5=471`
- Read these artifacts before doing new work:
  - `solve_reports\tool_artifacts\samplereverse_compare_aware_long4_strata\samplereverse_compare_aware_result.json`
  - `solve_reports\tool_artifacts\samplereverse_compare_aware_long4_strata\samplereverse_compare_aware_compare_1.json`
  - `solve_reports\tool_artifacts\samplereverse_compare_aware_long5_newbest\samplereverse_compare_aware_result.json`
  - `solve_reports\tool_artifacts\samplereverse_compare_aware_pairscan_newbest_exact2\pairscan_summary.json`
- Do not default back to the old `sample_solver` blind search unless the compare-aware main line shows no new progress for two consecutive iterations.

## Fixed Input/Output Contract
- Input: executable file path or downloadable URL.
- Final answer rule: first line must contain one final flag candidate (e.g., `flag{...}` / `ctf{...}`).
- If confidence is low: still return one best candidate, then explain uncertainty briefly.

## Operating Workflow (Do this in order)
1. Resolve input (local file or download URL).
2. Extract printable strings.
3. Detect local candidates (`flag{...}` + token-like short passwords).
4. Resolve analysis mode (`Auto` may switch to static or dynamic based on evidence).
5. Run tool automation (IDA first; Olly in dynamic mode, or static-stage supplement when needed).
6. Merge tool evidence and candidates; optionally add `angr` candidates.
7. Build prompt (adaptive context budget for large evidence sets).
8. Query selected model backend (Copilot timeout triggers one compact-prompt retry).
9. Rank candidates and run runtime validation when enabled.
10. Write report.

## Analysis Mode Guidance

### Static Analysis
Use when challenge can be inferred from constants/strings/control-flow hints.
- Prioritize explicit flag-like patterns first.
- Use validation-related strings/symbols to rank candidates.

### Dynamic Debug
Use when answer depends on runtime behavior.
- Plan breakpoints around entry, compare/check, decode/decrypt, and fail/exit paths.
- Account for anti-debug checks before trusting trace output.
- Prefer values captured immediately before final compare operation.

## Decision Policy (Conflict handling)
1. If local candidate looks valid and is corroborated by model reasoning, prefer it.
2. If model guess conflicts with runtime-evidence candidate, prefer runtime-evidence candidate.
3. Never emit multiple final flags in the first line.
4. Preserve exact flag casing and braces from strongest evidence.
5. If runtime validation is enabled and no candidate validates, return `NOT_FOUND`.

## Prompt and Reasoning Discipline
- Respect selected analysis mode; do not mix modes without reason.
- Do not invent tools/output that are not present in context.
- Keep explanation short, evidence-based, and challenge-scoped.

## Report Requirements
Report must include:
- input and resolved file path
- analysis mode and template used
- model info
- extracted candidate flags
- final selected flag
- beginner-friendly writeup flow (route, evidence, derivation, pitfalls)
- sanitized tool evidence (no local absolute paths)
- address/function context with evidence IDs where available
- candidate confidence ranking table
- candidate validation matrix
- failure diagnostics section (when selected result is `NOT_FOUND` / negative answer)

Do **not** include a dedicated section that dumps system/user prompt text in the final report.

## Safety and Scope
- Stay inside challenge-solving scope.
- Do not request unrelated secrets.
- Avoid destructive environment actions.

## Fast Defaults
- Default GUI analysis mode: `Auto`.
- In `Auto`, prefer `Static Analysis` when strong local candidates already exist.
- In `Auto`, switch to `Dynamic Debug` when runtime/anti-debug signals are strong, especially with custom Olly script configured.
- Runtime validation toggle defaults to enabled; use only in isolated environment.
