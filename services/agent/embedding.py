"""
Aether-Guard — Voyage AI Embedding Integration (Priority 8)

Generates semantic embeddings for incident reports to enable RAG-based
similarity search across historical incidents.

Environment variables:
  VOYAGE_API_KEY: Required. Voyage AI API key from https://www.voyageai.com/
  VOYAGE_MODEL: Optional. Defaults to "voyage-3" (Anthropic-recommended, 1024-dim)
"""

import logging
import os
from typing import List

import httpx

log = logging.getLogger("aether-guard.agent.embedding")

# Configuration
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
VOYAGE_MODEL = os.getenv("VOYAGE_MODEL", "voyage-3")
VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage-3 produces 1024-dimensional embeddings (normalized to unit length)
EMBEDDING_DIM = 1024


async def generate_embedding(text: str) -> List[float]:
    """
    Generate embedding vector via Voyage AI API.

    Args:
        text: Input text to embed (recommended <4096 tokens for voyage-3)

    Returns:
        1024-dimensional embedding vector (normalized to unit length)

    Raises:
        ValueError: If VOYAGE_API_KEY not set
        httpx.HTTPStatusError: If API request fails
    """
    if not VOYAGE_API_KEY:
        raise ValueError(
            "VOYAGE_API_KEY is not set. "
            "Export it before starting the agent or skip embedding generation."
        )

    log.debug("Generating embedding for text (length=%d chars)", len(text))

    async with httpx.AsyncClient() as client:
        response = await client.post(
            VOYAGE_API_URL,
            headers={
                "Authorization": f"Bearer {VOYAGE_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "input": text,
                "model": VOYAGE_MODEL,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        # Extract embedding from response: {"data": [{"embedding": [...], "index": 0}], ...}
        embedding = data["data"][0]["embedding"]

        if len(embedding) != EMBEDDING_DIM:
            log.warning(
                "Unexpected embedding dimension: got %d, expected %d",
                len(embedding), EMBEDDING_DIM
            )

        log.debug("Embedding generated successfully (dim=%d)", len(embedding))
        return embedding


def compose_embedding_text(report) -> str:
    """
    Compose text for embedding from IncidentReport fields.

    Includes: trigger (alert type), matched_pattern (RCA method), root_cause,
    reasoning, action_taken, and outcome. This provides rich semantic context
    for similarity matching.

    Args:
        report: IncidentReport instance or dict with these keys

    Returns:
        Formatted text suitable for embedding (labeled fields for clarity)
    """
    # Support both IncidentReport dataclass and dict
    if hasattr(report, 'trigger'):
        # IncidentReport dataclass
        trigger = report.trigger
        pattern = report.matched_pattern
        root_cause = report.root_cause
        reasoning = report.reasoning
        action = report.action_taken
        outcome = report.outcome
    else:
        # Dict (e.g., from Postgres row)
        trigger = report.get('trigger', 'unknown')
        pattern = report.get('matched_pattern', 'unknown')
        root_cause = report.get('root_cause', 'unknown')
        reasoning = report.get('reasoning', '')
        action = report.get('action_taken', 'IGNORE')
        outcome = report.get('outcome', 'unknown')

    # Labeled format for semantic clarity (LLMs respond better to structured text)
    text = f"""Alert: {trigger}
Pattern: {pattern}
Root Cause: {root_cause}
Reasoning: {reasoning}
Action: {action}
Outcome: {outcome}"""

    return text.strip()
