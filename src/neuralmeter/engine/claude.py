"""Motor da janela de 5h do Claude (RF01.2)."""

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from neuralmeter.db import (
    from_db_ts,
    get_events_since,
    get_provider_state,
    to_db_ts,
    update_provider_state,
)
from neuralmeter.engine.window import WINDOW_5H, WindowSnapshot, calc_percent

PROVIDER = "claude"
LOOKBACK = timedelta(hours=24)


def _weight(event: Mapping) -> float:
    w = event["weight_units"]
    return 1.0 if w is None else float(w)


def compute_claude_5h(events: Sequence[Mapping], state: Mapping, now: datetime) -> WindowSnapshot:
    """Calcula a janela 5h do Claude. Pura: sem I/O, sem relógio interno."""
    parsed = sorted(
        ((from_db_ts(e["created_at"]), _weight(e)) for e in events),
        key=lambda p: p[0],
    )
    official = from_db_ts(state.get("window_5h_reset_at"))
    official_active = official is not None and official > now

    start: datetime | None = None
    reset_at: datetime | None = None
    used = 0.0
    count = 0

    if official_active:
        start = official - WINDOW_5H
        reset_at = official
        for ts, w in parsed:
            if start <= ts < official:
                used += w
                count += 1
    else:
        w_start: datetime | None = None
        w_used = 0.0
        w_count = 0
        for ts, w in parsed:
            if w_start is None or ts >= w_start + WINDOW_5H:
                w_start, w_used, w_count = ts, 0.0, 0
            w_used += w
            w_count += 1
        if w_start is not None and w_start + WINDOW_5H > now:
            start, reset_at = w_start, w_start + WINDOW_5H
            used, count = w_used, w_count

    max_units = float(state["window_5h_max_units"])
    limited = bool(state.get("is_rate_limited")) and official_active
    if limited:
        percent, remaining = 100.0, 0.0
    else:
        percent = calc_percent(used, max_units)
        remaining = max(0.0, max_units - used)

    return WindowSnapshot(
        provider=PROVIDER,
        used_units=used,
        max_units=max_units,
        percent=percent,
        remaining_units=remaining,
        window_start=start,
        reset_at=reset_at,
        is_rate_limited=limited,
        event_count=count,
    )


def get_claude_5h_snapshot(conn: sqlite3.Connection, now: datetime | None = None) -> WindowSnapshot:
    now = datetime.now(UTC) if now is None else now
    events = get_events_since(conn, PROVIDER, now - LOOKBACK)
    state = get_provider_state(conn, PROVIDER)
    return compute_claude_5h(events, state, now)


def sync_claude_official_reset(
    conn: sqlite3.Connection, reset_at: datetime, rate_limited: bool | None = None
) -> None:
    """Grava o reset oficial reportado pelo servidor (sobrescreve a estimativa)."""
    fields: dict = {"window_5h_reset_at": to_db_ts(reset_at)}
    if rate_limited is not None:
        fields["is_rate_limited"] = int(rate_limited)
    update_provider_state(conn, PROVIDER, **fields)
