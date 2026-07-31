#!/usr/bin/env bash
# Bootstrap the local Python dev environment for Aether-Guard.
#
# Creates a Python 3.11 virtualenv at .venv and installs the agent + listener
# runtime and dev dependencies (plus the CI-pinned ruff) so that `make test-py`,
# `make lint-py`, the .claude ruff hook, and the test-runner subagent all work
# end-to-end locally — without depending on whatever `python3` happens to be.
#
# Idempotent: re-run any time to refresh dependencies.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV="${VENV:-.venv}"
RUFF_VERSION="0.4.4"   # keep in sync with .github/workflows/ci.yml

# ── Find a Python 3.11 interpreter (project + CI target 3.11) ──────────────────
PY=""
for cand in python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [ "$ver" = "3.11" ]; then PY="$cand"; break; fi
  fi
done

if [ -z "$PY" ]; then
  echo "ERROR: Python 3.11 not found." >&2
  echo "The agent/listener deps are pinned against 3.11 (matches CI)." >&2
  echo "Install it, e.g.:  brew install python@3.11    then re-run 'make setup'." >&2
  exit 1
fi

echo "==> Using $("$PY" --version) at $(command -v "$PY")"

# ── Create / reuse the virtualenv ─────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
  echo "==> Created virtualenv at $VENV"
else
  echo "==> Reusing existing virtualenv at $VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip

echo "==> Installing agent dependencies"
pip install --quiet -r services/agent/requirements.txt -r services/agent/requirements-dev.txt

echo "==> Installing listener dependencies"
pip install --quiet -r services/listener/requirements.txt -r services/listener/requirements-dev.txt

echo "==> Installing ruff==$RUFF_VERSION (matches CI)"
pip install --quiet "ruff==$RUFF_VERSION"

echo
echo "✅  Dev environment ready."
echo "    Activate:      source $VENV/bin/activate"
echo "    Python tests:  make test-py"
echo "    Lint:          make lint-py"
