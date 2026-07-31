# Agent eval harness

A small, honest way to **measure** Aether-Guard's Claude Code agents so changes to a
subagent prompt, skill, or `CLAUDE.md` can be checked for regressions *before* you
adopt them. This is the foundation for any "self-evolving" work: evolution without
measurement is just drift.

## How it works

Each case is a directory under `cases/<name>/`:
- `case.json` — the task `prompt`, an optional `diff` fixture, and an `expect` block.
- `change.diff` (optional) — a fixture diff appended to the prompt (no repo mutation).

The runner (`run.py`) executes each case with `claude -p` **from the repo root** (so
`.claude/agents`, skills, and `CLAUDE.md` are in scope), captures the output, and scores
it against `expect`:

```json
"expect": {
  "must_include":     ["confidence"],      // output must contain each (case-insensitive)
  "must_not_include": ["blocking concern"] // output must contain none of these
}
```

## Running

```bash
python3 .claude/evals/run.py --dry-run     # print prompts only — no model calls, no key
python3 .claude/evals/run.py               # run all cases (needs `claude` CLI + a working key)
python3 .claude/evals/run.py --case safety-reviewer-threshold
```

Exit code is non-zero if any case fails, so it can gate a change.

## Current cases (safety-reviewer)

| Case | Asserts |
|------|---------|
| `safety-reviewer-threshold` | Flags a confidence threshold dropped to 0.05 (mentions "confidence"). |
| `safety-reviewer-dryrun` | Flags removal of the `DRY_RUN` guard before an action executes. |
| `safety-reviewer-benign` | Does **not** raise a "blocking concern" on a docs-only change (false-positive guard). |

## Honest limitations (v1)

- **Keyword scoring is crude.** It catches gross regressions (the agent went silent on a
  real safety issue) but can't judge nuance. A false pass/fail is possible.
- **Planned:** an optional LLM-judge check type (`"expect": {"judge": "<rubric>"}`) that
  scores output 0–1 against a rubric via a second `claude -p` call, for cases keywords
  can't capture.
- **Needs a working `ANTHROPIC_API_KEY`** to actually run the agents. The `--dry-run`
  path works without one and is what CI validates until a key is configured.

## Adding a case

```bash
mkdir -p .claude/evals/cases/my-case
# write case.json (+ optional change.diff), then:
python3 .claude/evals/run.py --case my-case --dry-run
```
