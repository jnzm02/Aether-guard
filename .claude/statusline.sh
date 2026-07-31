#!/bin/bash
# Claude Code status line for Aether-Guard.
# Reads the session JSON on stdin and prints one line:
#   📁 <dir>   <branch>±<dirty>   🧠 <model>   +added/-removed
#
# Robust to the repo path containing a space. Never errors out — worst case it
# prints a minimal line, so the status bar always renders.
input=$(cat)

printf '%s' "$input" | python3 -c '
import sys, json, os, subprocess

try:
    d = json.load(sys.stdin)
except Exception:
    print("aether-guard")
    sys.exit(0)

model = ((d.get("model") or {}).get("display_name")) or "?"
ws = d.get("workspace") or {}
cur = ws.get("current_dir") or d.get("cwd") or os.getcwd()
base = os.path.basename(cur.rstrip("/")) or cur

def git(*args):
    try:
        return subprocess.check_output(
            ["git", "-C", cur, *args],
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return ""

branch = git("rev-parse", "--abbrev-ref", "HEAD")
dirty = "*" if git("status", "--porcelain") else ""

cost = d.get("cost") or {}
added = cost.get("total_lines_added") or 0
removed = cost.get("total_lines_removed") or 0

parts = ["\U0001F4C1 " + base]
if branch:
    parts.append("⎇ " + branch + dirty)
parts.append("\U0001F9E0 " + model)
if added or removed:
    parts.append("+%d/-%d" % (added, removed))

print("   ".join(parts))
'
