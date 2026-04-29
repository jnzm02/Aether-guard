"""
Daily alert summary module for Telegram notifications.

Generates and sends a daily summary of alert metrics.
"""

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("aether-guard.listener")


async def get_alert_stats(alert_queue: list[dict[str, Any]]) -> dict[str, int]:
    """
    Calculate alert statistics from the current queue.

    Args:
        alert_queue: List of alert dictionaries from listener

    Returns:
        Dictionary with alert counts by status
    """
    total = len(alert_queue)
    resolved = sum(1 for a in alert_queue if a.get("status") == "resolved")
    pending = sum(1 for a in alert_queue if not a.get("processed_by_ai", False))
    critical = sum(1 for a in alert_queue if a.get("labels", {}).get("severity") == "critical")

    return {
        "total": total,
        "resolved": resolved,
        "pending": pending,
        "critical": critical,
    }


def format_telegram_message(stats: dict[str, int]) -> str:
    """
    Format alert stats into a Telegram message.

    Args:
        stats: Dictionary with alert counts

    Returns:
        Formatted markdown message for Telegram
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    message = (
        f"📊 *Daily Alert Summary*\n\n"
        f"✅ Resolved: `{stats['resolved']}`\n"
        f"⏳ Pending: `{stats['pending']}`\n"
        f"🔴 Critical: `{stats['critical']}`\n"
        f"📈 Total: `{stats['total']}`\n\n"
        f"🕐 _Generated: {timestamp}_"
    )

    return message


async def send_telegram_message(message: str, bot_token: str, chat_id: str) -> bool:
    """
    Send a message to Telegram.

    Args:
        message: Message text (markdown format)
        bot_token: Telegram bot token
        chat_id: Telegram chat ID

    Returns:
        True if successful, False otherwise
    """
    if not bot_token or not chat_id:
        log.warning("⚠️  Telegram credentials not configured — skipping message")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            log.info("✅ Daily alert summary sent to Telegram")
            return True
    except Exception as exc:
        log.error(f"❌ Failed to send Telegram message: {exc}")
        return False


async def send_daily_summary(
    alert_queue: list[dict[str, Any]], bot_token: str, chat_id: str
) -> None:
    """
    Generate and send daily alert summary to Telegram.

    Args:
        alert_queue: List of alert dictionaries
        bot_token: Telegram bot token
        chat_id: Telegram chat ID
    """
    try:
        stats = await get_alert_stats(alert_queue)
        message = format_telegram_message(stats)
        await send_telegram_message(message, bot_token, chat_id)
    except Exception as exc:
        log.error(f"❌ Error generating daily summary: {exc}")
