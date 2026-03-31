"""
conftest.py — set required env vars BEFORE agent.py is imported.
agent.py reads config at module level so env must be set first.
Force-set (not setdefault) so CI environment variables don't bleed
into tests and cause unpredictable threshold/gate behaviour.
"""
import os

os.environ["ANTHROPIC_API_KEY"]    = "sk-ant-test-placeholder"
os.environ["CONFIDENCE_THRESHOLD"] = "0.60"
os.environ["LISTENER_URL"]         = "http://localhost:8081"
os.environ["ANALYSIS_LOG_PATH"]    = "/tmp/test-analyses.jsonl"
os.environ["DRY_RUN"]              = "true"
os.environ["TARGET_CONTAINER"]     = "test-container"
os.environ["REMEDIATION_COOLDOWN_S"] = "300"

