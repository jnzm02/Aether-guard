#!/bin/bash
# Run the FULL agent test suite (mirrors the Python job in .github/workflows/ci.yml).
#
# Resolve the directory this script lives in so it works from any checkout,
# worktree, or CI runner — not just one hardcoded machine path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Prefer the repo's dev virtualenv (created by `make setup`) so the pinned deps
# and Python 3.11 are used even when the ambient `python3` is a different version.
PY="python3"
REPO_VENV_PY="$SCRIPT_DIR/../../.venv/bin/python"
[ -x "$REPO_VENV_PY" ] && PY="$REPO_VENV_PY"

# Notes:
#   - tests/conftest.py force-sets the required env vars (ANTHROPIC_API_KEY,
#     DRY_RUN, thresholds, ...) before agent.py is imported.
#   - pytest.ini sets `testpaths = tests` and excludes the mutants/ dir.
#   - test_benchmark_real_tempo.py self-skips unless TEMPO_AVAILABLE=true.
# Extra args are forwarded, e.g. ./run_tests.sh -x -k policy
"$PY" -m pytest tests/ -v --tb=short "$@"
