"""Motor da janela deslizante do ChatGPT, por modelo (RF01.4)."""

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from neuralmeter.db import from_db_ts, get_events_since, get_provider_state
from neuralmeter.engine.window import (
    WindowSnapshot,
    calc_percent,
    sync_official_reset,
    weight,
)

PROVIDER = "chatgpt"
LOOKBACK = timedelta(hours=24)
DEFAULT_WINDOW = timedelta(hours=3)


@dataclass(frozen=True)
class ModelLimit:
    window: timedelta
    max_units: float


@dataclass(frozen=True)
class ChatGPT5hResult:
    aggregate: WindowSnapshot
    per_model: dict[str, WindowSnapshot]


# PREMISSA: valores estimados (sem captura real); calibração ajusta.
DEFAULT_CHATGPT_LIMITS: dict[str, ModelLimit] = {
    "gpt-4o": ModelLimit(timedelta(hours=3), 80),
    "gpt-4.1": ModelLimit(timedelta(hours=3), 80),
    "gpt-5": ModelLimit(timedelta(hours=3), 160),
    "o4-mini": ModelLimit(timedelta(hours=3), 150),
    "o3": ModelLimit(timedelta(hours=5), 100),
}


def _match_key(model_name: str, limits: Mapping[str, ModelLimit]) -> str | None:
    name = model_name.lower()
    best: str | None = None
    for key in limits:
        k = key.lower()
        if name.startswith(k) and (best is None or len(k) > len(best)):
            best = key
    return best


def resolve_model_limit(
    model_name: str, limits: Mapping[str, ModelLimit], fallback_max: float
) -> ModelLimit:
    """Limite do modelo pelo prefixo mais longo (sem diferenciar maiúsculas)."""
    key = _match_key(model_name, limits)
    if key is None:
        return ModelLimit(DEFAULT_WINDOW, fallback_max)
    return limits[key]


def compute_chatgpt_5h(
    events: Sequence[Mapping],
    state: Mapping,
    now: datetime,
    limits: Mapping[str, ModelLimit] | None = None,
) -> ChatGPT5hResult:
    """Calcula uso por modelo e agregado. Pura: sem I/O, sem relógio interno."""
    limits = DEFAULT_CHATGPT_LIMITS if limits is None else limits
    fallback_max = float(state["window_5h_max_units"])

    groups: dict[str, tuple[ModelLimit, list[tuple[datetime, float]]]] = {}
    for e in events:
        name = e["model_name"]
        key = _match_key(name, limits)
        group_key = key if key is not None else name.lower()
        lim = limits[key] if key is not None else ModelLimit(DEFAULT_WINDOW, fallback_max)
        ts = from_db_ts(e["created_at"])
        if ts <= now < ts + lim.window:
            groups.setdefault(group_key, (lim, []))[1].append((ts, weight(e)))

    per_model: dict[str, WindowSnapshot] = {}
    for gk, (lim, items) in groups.items():
        oldest = min(ts for ts, _ in items)
        used = sum(w for _, w in items)
        per_model[gk] = WindowSnapshot(
            provider=PROVIDER,
            used_units=used,
            max_units=lim.max_units,
            percent=calc_percent(used, lim.max_units),
            remaining_units=max(0.0, lim.max_units - used),
            window_start=oldest,
            reset_at=oldest + lim.window,
            is_rate_limited=False,
            event_count=len(items),
        )

    if per_model:
        aggregate = max(per_model.values(), key=lambda s: (s.percent, s.used_units))
    else:
        aggregate = WindowSnapshot(
            provider=PROVIDER,
            used_units=0.0,
            max_units=fallback_max,
            percent=0.0,
            remaining_units=fallback_max,
            window_start=None,
            reset_at=None,
            is_rate_limited=False,
            event_count=0,
        )

    official = from_db_ts(state.get("window_5h_reset_at"))
    if state.get("is_rate_limited") and official is not None and official > now:
        aggregate = replace(
            aggregate,
            percent=100.0,
            remaining_units=0.0,
            is_rate_limited=True,
            reset_at=official,
        )

    return ChatGPT5hResult(aggregate=aggregate, per_model=per_model)


def get_chatgpt_5h_snapshot(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    limits: Mapping[str, ModelLimit] | None = None,
) -> ChatGPT5hResult:
    now = datetime.now(UTC) if now is None else now
    events = get_events_since(conn, PROVIDER, now - LOOKBACK)
    state = get_provider_state(conn, PROVIDER)
    return compute_chatgpt_5h(events, state, now, limits)


def sync_chatgpt_official_reset(
    conn: sqlite3.Connection, reset_at: datetime, rate_limited: bool | None = None
) -> None:
    """Grava o reset oficial reportado pelo servidor."""
    sync_official_reset(conn, PROVIDER, reset_at, rate_limited)
