# Reverse Agent - AI Quickstart Playbook

Use this file as the **first-read startup guide** so a new AI session can begin work immediately.

## 60-Second Startup Checklist
1. Confirm goal: solve reverse challenge input and output one final flag.
2. Confirm input type: local executable path or downloadable URL.
3. Choose analysis mode:
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
- `reverse_agent\reporter.py`: detailed markdown report writer.

## Fixed Input/Output Contract
- Input: executable file path or downloadable URL.
- Final answer rule: first line must contain one final flag candidate (e.g., `flag{...}` / `ctf{...}`).
- If confidence is low: still return one best candidate, then explain uncertainty briefly.

## Operating Workflow (Do this in order)
1. Resolve input (local file or download URL).
2. Extract printable strings.
3. Detect local candidate flags via pattern match.
4. Build prompt with selected analysis template.
5. Query selected model backend.
6. Select final flag (prefer strongest evidence).
7. Write detailed report.

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

Do **not** include a dedicated section that dumps system/user prompt text in the final report.

## Safety and Scope
- Stay inside challenge-solving scope.
- Do not request unrelated secrets.
- Avoid destructive environment actions.

## Fast Defaults
- Default analysis mode: `Static Analysis`.
- Switch to `Dynamic Debug` when:
  - no strong static candidate exists, or
  - challenge behavior indicates runtime decryption/anti-debug gating.
