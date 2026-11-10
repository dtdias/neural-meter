"""Tipos e helpers comuns aos motores de janela."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from neuralmeter.db import to_db_ts, update_provider_state

WINDOW_5H = timedelta(hours=5)


@dataclass(frozen=True)
class WindowSnapshot:
    provider: str
    used_units: float
    max_units: float
    percent: float
    remaining_units: float
    window_start: datetime | None
    reset_at: datetime | None
    is_rate_limited: bool
    event_count: int


def weight(event: Mapping) -> float:
    """Peso da mensagem; weight_units NULL conta como 1.0."""
    w = event["weight_units"]
    return 1.0 if w is None else float(w)


def sync_official_reset(
    conn: sqlite3.Connection,
    provider: str,
    reset_at: datetime,
    rate_limited: bool | None = None,
) -> None:
    """Grava o reset oficial reportado pelo servidor (sobrescreve a estimativa)."""
    fields: dict = {"window_5h_reset_at": to_db_ts(reset_at)}
    if rate_limited is not None:
        fields["is_rate_limited"] = int(rate_limited)
    update_provider_state(conn, provider, **fields)


def calc_percent(used: float, max_units: float) -> float:
    """Percentual de uso limitado a [0, 100]. max<=0 -> 100 se used>0, senão 0."""
    if max_units <= 0:
        return 100.0 if used > 0 else 0.0
    return min(100.0, max(0.0, used / max_units * 100.0))
