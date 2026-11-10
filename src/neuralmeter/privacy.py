"""Privacidade (RNF03): hash de conversation_id e retenção do histórico. Sem Qt."""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from neuralmeter.db import purge_usage_events_before, to_db_ts

MIN_RETENTION_DAYS = 8  # janela semanal (7d) precisa de histórico
MAX_RETENTION_DAYS = 3650
DEFAULT_RETENTION_DAYS = 30


@dataclass(frozen=True)
class PrivacySettings:
    hash_conversation_ids: bool = True
    retention_days: int = DEFAULT_RETENTION_DAYS
    salt: str = ""


def clamp_retention_days(days) -> int:
    if isinstance(days, bool) or not isinstance(days, int):
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))


def hash_conversation_id(conv_id: str | None, salt: str) -> str | None:
    """sha256(salt + id)[:16] em hex; id ausente/vazio -> None."""
    if not conv_id:
        return None
    return hashlib.sha256((salt + conv_id).encode()).hexdigest()[:16]


def apply_retention(conn: sqlite3.Connection, now: datetime, days: int) -> int:
    """Apaga usage_events mais antigos que now-days (days em [8, 3650]). Retorna removidos."""
    cutoff = to_db_ts(now - timedelta(days=clamp_retention_days(days)))
    return purge_usage_events_before(conn, cutoff)
