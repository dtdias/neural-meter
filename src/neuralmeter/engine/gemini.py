"""Experimental Gemini activity counts; never infer official quota windows."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from neuralmeter.db import get_events_since


@dataclass(frozen=True)
class GeminiActivity:
    messages_5h: int
    messages_7d: int
    estimated_tokens_7d: int
    last_model: str | None


def get_gemini_activity(conn: sqlite3.Connection, now: datetime) -> GeminiActivity:
    """Count locally observed submissions; quota and reset remain explicitly unknown."""
    events = get_events_since(conn, "gemini", now - timedelta(days=7))
    active = []
    for event in events:
        created = datetime.strptime(event["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        if created <= now:
            active.append((event, created))
    five_hour_cutoff = now - timedelta(hours=5)
    messages_5h = sum(created > five_hour_cutoff for _, created in active)
    tokens = sum(
        max(event["prompt_chars"] or 0, event["context_chars"] or 0) // 4 for event, _ in active
    )
    return GeminiActivity(
        messages_5h=messages_5h,
        messages_7d=len(active),
        estimated_tokens_7d=tokens,
        last_model=active[-1][0]["model_name"] if active else None,
    )
