#!/bin/bash
# Resolve the directory this script lives in so it works from any checkout,
# worktree, or CI runner — not just one hardcoded machine path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
python3 -m pytest -x \
  --ignore=tests/test_benchmark_real_tempo.py \
  --ignore=mutants \
  tests/test_incident_report.py
