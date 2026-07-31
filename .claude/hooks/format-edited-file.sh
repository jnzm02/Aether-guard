#!/bin/bash
# PostToolUse hook (Edit|Write|MultiEdit): keep edited files CI-clean.
#
#   .go  -> gofmt -w         (auto-format in place; Go is universally gofmt'd)
#   .py  -> ruff check       (report only; if it fails, feed the issues back to
#                             Claude via exit 2 so they get fixed before CI does)
#
# Design choices:
#   - Go is auto-formatted because gofmt is canonical and non-controversial.
#   - Python is NOT auto-reformatted: this repo's CI runs `ruff check` (lint),
#     not `ruff format`, so blindly reformatting would create noisy, unrelated
#     diffs. We only surface lint violations the pipeline would reject anyway.
#   - Any internal error is swallowed (exit 0) so the hook never blocks an edit
#     for reasons unrelated to the code itself.
#
# Input: JSON on stdin from Claude Code. We read tool_input.file_path from it.

input=$(cat)

file=$(printf '%s' "$input" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get("tool_input", {}).get("file_path", ""))
except Exception:
    print("")
' 2>/dev/null)

# Nothing to do if we could not determine a real file.
[ -n "$file" ] || exit 0
[ -f "$file" ] || exit 0

case "$file" in
  *.go)
    if command -v gofmt >/dev/null 2>&1; then
      gofmt -w "$file" 2>/dev/null
    fi
    exit 0
    ;;
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      out=$(ruff check "$file" 2>&1)
      if [ $? -ne 0 ]; then
        echo "ruff found lint issues in $file (these would fail CI):" >&2
        echo "$out" >&2
        # Exit 2 surfaces stderr back to Claude as actionable feedback.
        exit 2
      fi
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
