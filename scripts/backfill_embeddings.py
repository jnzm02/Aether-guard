#!/usr/bin/env python3
"""
Aether-Guard — Backfill Embeddings for Existing Incidents (Priority 8)

Generates embeddings for all incidents in Postgres that don't have embeddings yet.

Usage:
    python scripts/backfill_embeddings.py [--batch-size=100] [--dry-run] [--limit=N]

Environment variables:
    VOYAGE_API_KEY: Required. Voyage AI API key
    POSTGRES_URL: Required. Postgres connection string
    SIMILARITY_MIN_CONFIDENCE: Optional. Only backfill incidents >= this confidence (default 0.7)

Rate limiting:
    Voyage AI free tier: 100 requests/minute
    This script respects rate limits with 0.6s delay between requests (max 100/min)
"""

import asyncio
import argparse
import logging
import os
import sys
from datetime import datetime

# Add services/agent to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "agent"))

from incident_storage import get_storage
from embedding import generate_embedding, compose_embedding_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("backfill-embeddings")


async def backfill_embeddings(
    batch_size: int = 100,
    dry_run: bool = False,
    limit: int | None = None,
    rate_limit_delay: float = 0.6,
) -> dict:
    """
    Backfill embeddings for incidents without embeddings.

    Args:
        batch_size: Process this many incidents at a time (for progress reporting)
        dry_run: If True, don't actually update the database
        limit: Max incidents to process (None = all)
        rate_limit_delay: Delay between API calls in seconds (default 0.6s = 100/min)

    Returns:
        Dict with {success: int, failed: int, skipped: int, total: int}
    """
    log.info("Starting embedding backfill (dry_run=%s, limit=%s)", dry_run, limit)

    # Validate env vars
    if not os.getenv("VOYAGE_API_KEY"):
        log.error("VOYAGE_API_KEY not set — cannot generate embeddings")
        return {"error": "VOYAGE_API_KEY not set"}

    if not os.getenv("POSTGRES_URL"):
        log.error("POSTGRES_URL not set — cannot connect to database")
        return {"error": "POSTGRES_URL not set"}

    stats = {"success": 0, "failed": 0, "skipped": 0, "total": 0}

    async with get_storage() as storage:
        # Fetch all incidents without embeddings
        incidents = await storage.get_all_without_embeddings(limit=limit)
        stats["total"] = len(incidents)

        if stats["total"] == 0:
            log.info("✓ No incidents need embeddings — backfill complete")
            return stats

        log.info("Found %d incidents without embeddings", stats["total"])

        start_time = datetime.now()

        for i, incident in enumerate(incidents, 1):
            incident_id = incident["incident_id"]

            try:
                # Compose embedding text from incident fields
                text = compose_embedding_text(incident)

                # Generate embedding via Voyage AI
                embedding = await generate_embedding(text)

                # Update database (unless dry_run)
                if not dry_run:
                    success = await storage.update_embedding(incident_id, embedding)
                    if success:
                        stats["success"] += 1
                        log.info(
                            "✓ [%d/%d] %s (confidence=%.2f, pattern=%s)",
                            i, stats["total"], incident_id[:12],
                            incident.get("confidence", 0.0),
                            incident.get("matched_pattern", "unknown")
                        )
                    else:
                        stats["failed"] += 1
                        log.warning("✗ [%d/%d] %s - database update failed", i, stats["total"], incident_id[:12])
                else:
                    stats["success"] += 1
                    log.info(
                        "✓ [%d/%d] %s (DRY RUN - would update)",
                        i, stats["total"], incident_id[:12]
                    )

                # Rate limit: Wait between requests to respect API limits
                # Voyage AI free tier: 100 requests/minute
                if i < stats["total"]:  # Don't wait after last request
                    await asyncio.sleep(rate_limit_delay)

            except Exception as exc:
                stats["failed"] += 1
                log.error("✗ [%d/%d] %s - %s", i, stats["total"], incident_id[:12], exc)

        elapsed = (datetime.now() - start_time).total_seconds()
        rate = stats["success"] / elapsed if elapsed > 0 else 0

        log.info("")
        log.info("Backfill complete:")
        log.info("  Total incidents:  %d", stats["total"])
        log.info("  Success:          %d", stats["success"])
        log.info("  Failed:           %d", stats["failed"])
        log.info("  Elapsed time:     %.1fs", elapsed)
        log.info("  Rate:             %.1f embeddings/sec", rate)

        if stats["failed"] > 0:
            log.warning("⚠ %d incidents failed — check logs above for errors", stats["failed"])

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Backfill embeddings for existing incidents in Postgres"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Process this many incidents at a time (for progress reporting)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate embeddings but don't update the database"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max incidents to process (for testing)"
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=0.6,
        help="Delay between API calls in seconds (default 0.6 = 100/min)"
    )

    args = parser.parse_args()

    stats = await backfill_embeddings(
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        limit=args.limit,
        rate_limit_delay=args.rate_limit_delay,
    )

    # Exit with error code if any failures
    if "error" in stats:
        sys.exit(1)
    elif stats.get("failed", 0) > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
