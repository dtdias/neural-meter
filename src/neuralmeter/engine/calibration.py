"""Auto-calibração do teto ao detectar bloqueio real (RF04.2)."""

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta

from neuralmeter.db import (
    MAX_LIMIT_UNITS,
    get_events_since,
    get_model_limits,
    set_model_limit,
    to_db_ts,
    update_provider_state,
)
from neuralmeter.engine.chatgpt import (
    DEFAULT_CHATGPT_LIMITS,
    ModelLimit,
    get_chatgpt_5h_snapshot,
)
from neuralmeter.engine.claude import get_claude_5h_snapshot
from neuralmeter.engine.weekly import get_weekly_snapshot
from neuralmeter.engine.window import match_model_key

RECENT_MODEL_WINDOW = timedelta(minutes=5)
PARTIAL_TRACKING_RATIO = 0.5
_RESET_FIELD = {"5h": "window_5h_reset_at", "weekly": "weekly_reset_at"}
_MAX_FIELD = {"5h": "window_5h_max_units", "weekly": "weekly_max_units"}


def calibrate_ceiling(used: float, current: float) -> float | None:
    """Novo teto a partir do uso medido no bloqueio; None = não mexer.

    used<1 -> None; used>current -> used (sobe sempre); used>=0.5*current -> used
    (desce); senão None (rastreio provavelmente parcial). Clamp 100000, 1 casa.
    """
    if used < 1:
        return None
    if used > current or used >= PARTIAL_TRACKING_RATIO * current:
        return round(min(float(used), MAX_LIMIT_UNITS), 1)
    return None


def is_new_block(prev_state: Mapping, scope: str, new_reset_at: datetime) -> bool:
    """Bloqueio novo: antes liberado, ou reset anterior do escopo difere do novo."""
    if not prev_state.get("is_rate_limited"):
        return True
    return prev_state.get(_RESET_FIELD[scope]) != to_db_ts(new_reset_at)


def calibrated_chatgpt_limits(conn: sqlite3.Connection) -> dict[str, ModelLimit]:
    """Tetos calibrados do DB como ModelLimit."""
    return {
        key: ModelLimit(timedelta(hours=v["window_hours"]), v["max_units"])
        for key, v in get_model_limits(conn, "chatgpt").items()
    }


def _recent_chatgpt_model(conn: sqlite3.Connection, now: datetime) -> str | None:
    events = get_events_since(conn, "chatgpt", now - RECENT_MODEL_WINDOW)
    for e in reversed(events):
        if e["model_name"]:
            return e["model_name"]
    return None


def _calibrate_chatgpt_5h(
    conn: sqlite3.Connection, model: str | None, prev_state: Mapping, now: datetime
) -> dict | None:
    model = model or _recent_chatgpt_model(conn, now)
    if not model:
        return None
    limits = {**DEFAULT_CHATGPT_LIMITS, **calibrated_chatgpt_limits(conn)}
    key = match_model_key(model, limits)
    if key is not None:
        lim = limits[key]
        store_key, group_key = key.lower(), key
    else:
        fallback = float(prev_state["window_5h_max_units"])
        lim = ModelLimit(timedelta(hours=3), fallback)
        store_key = group_key = model.lower()
    snap = get_chatgpt_5h_snapshot(conn, now, limits=limits).per_model.get(group_key)
    if snap is None:
        return None
    new = calibrate_ceiling(snap.used_units, lim.max_units)
    if new is None or new == lim.max_units:
        return None
    set_model_limit(conn, "chatgpt", store_key, lim.window.total_seconds() / 3600, new)
    return {"target": f"model:{store_key}", "old": lim.max_units, "new": new}


def apply_block_calibration(
    conn: sqlite3.Connection,
    provider: str,
    scope: str,
    model: str | None,
    prev_state: Mapping,
    now: datetime,
) -> dict | None:
    """Ajusta o teto com o uso medido no bloqueio. Devolve {target, old, new} ou None."""
    if scope == "5h" and provider == "chatgpt":
        return _calibrate_chatgpt_5h(conn, model, prev_state, now)
    field = _MAX_FIELD[scope]
    if scope == "weekly":
        used = get_weekly_snapshot(conn, provider, now).aggregate.used_units
    else:
        used = get_claude_5h_snapshot(conn, now).used_units
    current = float(prev_state[field])
    new = calibrate_ceiling(used, current)
    if new is None or new == current:
        return None
    update_provider_state(conn, provider, **{field: new})
    return {"target": field, "old": current, "new": new}
