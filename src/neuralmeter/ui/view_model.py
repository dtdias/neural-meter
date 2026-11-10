"""View-model puro do HUD (sem Qt): níveis de cor, countdown, dados por provedor, conexão."""

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from neuralmeter.engine import (
    WeeklyResult,
    WindowSnapshot,
    get_chatgpt_5h_snapshot,
    get_claude_5h_snapshot,
    get_weekly_snapshot,
)

Level = Literal["green", "yellow", "red"]

LEVEL_COLORS: dict[str, str] = {
    "green": "#22c55e",
    "yellow": "#eab308",
    "red": "#ef4444",
}

PROVIDER_LABELS: dict[str, str] = {"claude": "Claude", "chatgpt": "ChatGPT"}
NO_DATA = "—"


def level_for_percent(pct: float, rate_limited: bool = False) -> Level:
    """verde <70, amarelo 70–89.99, vermelho >=90 ou bloqueado."""
    if rate_limited or pct >= 90:
        return "red"
    if pct >= 70:
        return "yellow"
    return "green"


def format_countdown(reset_at: datetime | None, now: datetime) -> str:
    """None -> '—'; <=0 -> 'agora'; <1h 'MMm SSs'; <24h 'Hh MMm'; >=24h 'Dd HHh'."""
    if reset_at is None:
        return NO_DATA
    total = int((reset_at - now).total_seconds())
    if total <= 0:
        return "agora"
    if total < 3600:
        return f"{total // 60:02d}m {total % 60:02d}s"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60:02d}m"
    return f"{total // 86400}d {(total % 86400) // 3600:02d}h"


@dataclass(frozen=True)
class ProviderView:
    provider: str
    label: str
    pct_5h: float
    level_5h: Level
    reset_5h: datetime | None
    pct_week: float
    level_week: Level
    reset_week: datetime | None
    last_model: str
    rate_limited: bool


def build_provider_view(
    provider: str, snap_5h: WindowSnapshot, weekly: WeeklyResult
) -> ProviderView:
    week = weekly.aggregate
    return ProviderView(
        provider=provider,
        label=PROVIDER_LABELS.get(provider, provider),
        pct_5h=snap_5h.percent,
        level_5h=level_for_percent(snap_5h.percent, snap_5h.is_rate_limited),
        reset_5h=snap_5h.reset_at,
        pct_week=week.percent,
        level_week=level_for_percent(week.percent, week.is_rate_limited),
        reset_week=week.reset_at,
        last_model=weekly.last_model or NO_DATA,
        rate_limited=snap_5h.is_rate_limited or week.is_rate_limited,
    )


def load_hud_view(conn: sqlite3.Connection, now: datetime) -> dict[str, ProviderView]:
    claude_5h = get_claude_5h_snapshot(conn, now)
    chatgpt_5h = get_chatgpt_5h_snapshot(conn, now).aggregate
    return {
        "claude": build_provider_view(
            "claude", claude_5h, get_weekly_snapshot(conn, "claude", now)
        ),
        "chatgpt": build_provider_view(
            "chatgpt", chatgpt_5h, get_weekly_snapshot(conn, "chatgpt", now)
        ),
    }


@dataclass(frozen=True)
class ConnState:
    clients: int = 0
    tabs: dict[str, bool] = field(default_factory=lambda: {"claude": False, "chatgpt": False})


def apply_conn_update(state: ConnState, kind: str, payload: dict) -> ConnState:
    if kind == "conn":
        clients = max(0, int(payload.get("clients", 0)))
        tabs = {"claude": False, "chatgpt": False} if clients == 0 else dict(state.tabs)
        return ConnState(clients=clients, tabs=tabs)
    if kind == "status":
        raw = payload.get("tabs")
        if isinstance(raw, dict):
            tabs = {k: bool(raw.get(k, state.tabs.get(k, False))) for k in ("claude", "chatgpt")}
            return ConnState(clients=state.clients, tabs=tabs)
    return state


def conn_label(state: ConnState) -> str:
    if state.clients <= 0:
        return "offline"
    if any(state.tabs.values()):
        return "online"
    return "aguardando aba"
