---
description: Run the same lint + tests CI runs, for whichever services changed
argument-hint: "[all | go | python] (default: auto-detect changed services)"
---
Run Aether-Guard's checks locally, mirroring `.github/workflows/ci.yml`.

Scope: $ARGUMENTS
(If blank, detect which services changed via `git status` / `git diff --name-only`
and run only the relevant suites. If unsure, run everything.)

Delegate to the **test-runner** subagent, which knows the exact per-service commands
(Go `build`/`vet`/`test -race`; Python `ruff check` + `pytest` with the right
`PYTHONPATH` and `--import-mode`). Report pass/fail per suite with any failing output
quoted verbatim. Do **not** fix failures unless I ask — just surface them.
