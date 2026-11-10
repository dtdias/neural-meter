"""Tipos e helpers comuns aos motores de janela."""

from dataclasses import dataclass
from datetime import datetime, timedelta

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


def calc_percent(used: float, max_units: float) -> float:
    """Percentual de uso limitado a [0, 100]. max<=0 -> 100 se used>0, senão 0."""
    if max_units <= 0:
        return 100.0 if used > 0 else 0.0
    return min(100.0, max(0.0, used / max_units * 100.0))
