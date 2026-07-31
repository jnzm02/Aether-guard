#!/usr/bin/env python3
"""Minimal eval harness for Aether-Guard's Claude Code agents.

Each case lives in .claude/evals/cases/<name>/case.json and is scored by running
Claude Code headless (`claude -p`) from the repo root (so .claude/agents, skills,
and CLAUDE.md are in scope) and checking its output.

This is v1: **deterministic keyword checks**. They're crude but stable and need no
extra model calls. Fuzzier rubrics (an LLM judge) are a planned extension — see
README.md. The point is that any change to a subagent prompt / skill can be
*measured* against these cases before you adopt it, so evolution is grounded.

Usage:
  python3 .claude/evals/run.py                 # run all cases (needs `claude` + key)
  python3 .claude/evals/run.py --case <name>   # run one
  python3 .claude/evals/run.py --dry-run       # print prompts only, no model calls
"""
import argparse
import glob
import json
import os
import subprocess
import sys

EVAL_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(EVAL_DIR, "..", ".."))


def load_cases(filter_name=None):
    cases = []
    for cj in sorted(glob.glob(os.path.join(EVAL_DIR, "cases", "*", "case.json"))):
        with open(cj) as f:
            c = json.load(f)
        c["_dir"] = os.path.dirname(cj)
        if filter_name and c["name"] != filter_name:
            continue
        cases.append(c)
    return cases


def build_prompt(case):
    prompt = case["prompt"]
    diff_file = case.get("diff")
    if diff_file:
        with open(os.path.join(case["_dir"], diff_file)) as f:
            prompt += "\n\n--- diff under review ---\n" + f.read()
    return prompt


def run_claude(prompt, model, timeout):
    args = ["claude", "-p", prompt]
    if model:
        args += ["--model", model]
    proc = subprocess.run(
        args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
    )
    return proc.stdout + "\n" + proc.stderr


def score(case, output):
    out = output.lower()
    expect = case.get("expect", {})
    results = []
    for kw in expect.get("must_include", []):
        results.append((f"includes '{kw}'", kw.lower() in out))
    for kw in expect.get("must_not_include", []):
        results.append((f"excludes '{kw}'", kw.lower() not in out))
    return all(ok for _, ok in results), results


def main():
    ap = argparse.ArgumentParser(description="Run Aether-Guard agent evals.")
    ap.add_argument("--case", help="run only the case with this name")
    ap.add_argument("--model", default="claude-sonnet-4-5", help="model for claude -p")
    ap.add_argument("--timeout", type=int, default=300, help="per-case timeout (s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the constructed prompts and exit (no model calls)")
    args = ap.parse_args()

    cases = load_cases(args.case)
    if not cases:
        print("No eval cases found under .claude/evals/cases/", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        for c in cases:
            print(f"=== {c['name']} (dry-run) ===")
            print(build_prompt(c))
            print()
        return

    total = passed = 0
    for c in cases:
        print(f"=== {c['name']} ===")
        try:
            out = run_claude(build_prompt(c), args.model, args.timeout)
        except FileNotFoundError:
            print("  ERROR: `claude` CLI not found on PATH", file=sys.stderr)
            sys.exit(2)
        except subprocess.TimeoutExpired:
            print("  [FAIL] timed out\n")
            total += 1
            continue
        ok, results = score(c, out)
        for label, r in results:
            print(f"  [{'PASS' if r else 'FAIL'}] {label}")
        print(f"  -> {'PASS' if ok else 'FAIL'}\n")
        total += 1
        passed += 1 if ok else 0

    print(f"Score: {passed}/{total} cases passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
