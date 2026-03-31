"""
conftest.py — set required env vars BEFORE agent.py is imported.
agent.py reads config at module level so env must be set first.
"""
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")
os.environ.setdefault("CONFIDENCE_THRESHOLD", "0.60")
os.environ.setdefault("LISTENER_URL", "http://localhost:8081")
os.environ.setdefault("ANALYSIS_LOG_PATH", "/tmp/test-analyses.jsonl")
os.environ.setdefault("DRY_RUN", "true")
