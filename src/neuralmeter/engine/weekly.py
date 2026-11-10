"""Motor semanal de 7 dias (RF02): agregado + cotas por modelo, tokens estimados."""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from neuralmeter.db import (
    from_db_ts,
    get_events_since,
    get_provider_state,
    to_db_ts,
    update_provider_state,
)
from neuralmeter.engine.window import (
    WINDOW_7D,
    WindowSnapshot,
    calc_percent,
    match_model_key,
    weight,
)


@dataclass(frozen=True)
class WeeklyQuota:
    max_units: float


@dataclass(frozen=True)
class WeeklyResult:
    aggregate: WindowSnapshot
    per_model: dict[str, WindowSnapshot]
    event_count: int
    est_tokens: int
    last_model: str | None


# PREMISSA: valores estimados (sem captura real); calibração/overrides ajustam.
DEFAULT_WEEKLY_LIMITS: dict[str, dict[str, WeeklyQuota]] = {
    "chatgpt": {"o3": WeeklyQuota(100), "o4-mini-high": WeeklyQuota(700)},
    "claude": {},
}


def estimate_tokens(events: Sequence[Mapping]) -> int:
    """Σ max(prompt_chars, context_chars) // 4 (heurística ~4 chars/token)."""
    return sum(max(e["prompt_chars"] or 0, e["context_chars"] or 0) // 4 for e in events)


def compute_weekly(
    provider: str,
    events: Sequence[Mapping],
    state: Mapping,
    now: datetime,
    limits: Mapping[str, Mapping[str, WeeklyQuota]] | None = None,
) -> WeeklyResult:
    """Uso dos últimos 7 dias. Pura: sem I/O, sem relógio interno."""
    limits = DEFAULT_WEEKLY_LIMITS if limits is None else limits
    quotas = limits.get(provider, {})
    max_units = float(state["weekly_max_units"])

    active: list[tuple[datetime, Mapping]] = []
    for e in events:
        ts = from_db_ts(e["created_at"])
        if now - WINDOW_7D < ts <= now:
            active.append((ts, e))
    active.sort(key=lambda x: x[0])

    used = sum(weight(e) for _, e in active)
    oldest = active[0][0] if active else None
    official = from_db_ts(state.get("weekly_reset_at"))
    official_future = official is not None and official > now

    if official_future:
        reset_at = official
    elif oldest is not None:
        reset_at = oldest + WINDOW_7D
    else:
        reset_at = None

    aggregate = WindowSnapshot(
        provider=provider,
        used_units=used,
        max_units=max_units,
        percent=calc_percent(used, max_units),
        remaining_units=max(0.0, max_units - used),
        window_start=oldest,
        reset_at=reset_at,
        is_rate_limited=False,
        event_count=len(active),
    )
    if state.get("is_rate_limited") and official_future:
        aggregate = replace(
            aggregate,
            percent=100.0,
            remaining_units=0.0,
            is_rate_limited=True,
            reset_at=official,
        )

    groups: dict[str, list[tuple[datetime, Mapping]]] = {}
    for ts, e in active:
        key = match_model_key(e["model_name"], quotas)
        if key is not None:
            groups.setdefault(key, []).append((ts, e))

    per_model: dict[str, WindowSnapshot] = {}
    for key, items in groups.items():
        g_used = sum(weight(e) for _, e in items)
        g_oldest = items[0][0]
        g_max = quotas[key].max_units
        per_model[key] = WindowSnapshot(
            provider=provider,
            used_units=g_used,
            max_units=g_max,
            percent=calc_percent(g_used, g_max),
            remaining_units=max(0.0, g_max - g_used),
            window_start=g_oldest,
            reset_at=g_oldest + WINDOW_7D,
            is_rate_limited=False,
            event_count=len(items),
        )

    return WeeklyResult(
        aggregate=aggregate,
        per_model=per_model,
        event_count=len(active),
        est_tokens=estimate_tokens([e for _, e in active]),
        last_model=active[-1][1]["model_name"] if active else None,
    )


def get_weekly_snapshot(
    conn: sqlite3.Connection,
    provider: str,
    now: datetime | None = None,
    limits: Mapping[str, Mapping[str, WeeklyQuota]] | None = None,
) -> WeeklyResult:
    now = datetime.now(UTC) if now is None else now
    state = get_provider_state(conn, provider)  # valida provider
    events = get_events_since(conn, provider, now - WINDOW_7D)
    return compute_weekly(provider, events, state, now, limits)


def sync_weekly_reset(
    conn: sqlite3.Connection,
    provider: str,
    reset_at: datetime,
    rate_limited: bool | None = None,
) -> None:
    """Grava o reset semanal oficial reportado pelo servidor."""
    fields: dict = {"weekly_reset_at": to_db_ts(reset_at)}
    if rate_limited is not None:
        fields["is_rate_limited"] = int(rate_limited)
    update_provider_state(conn, provider, **fields)
