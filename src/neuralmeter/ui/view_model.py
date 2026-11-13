"""View-model puro do HUD (sem Qt): níveis de cor, countdown, dados por provedor, conexão."""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal

from neuralmeter.engine import (
    ModelLimit,
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
    source_5h: str = "estimado"
    source_week: str = "estimado"
    fresh_quota: bool = False
    model_quota: str = ""
    reset_source_5h: str = "estimado"
    reset_source_week: str = "estimado"
    estimated_pct_5h: float | None = None
    estimated_pct_week: float | None = None
    estimated_reset_5h: datetime | None = None
    estimated_reset_week: datetime | None = None
    estimated_blocked_5h: bool = False
    estimated_blocked_week: bool = False
    estimated_rate_limited: bool = False
    official_until_5h: datetime | None = None
    official_until_week: datetime | None = None
    quota_identity: str | None = None
    model_quotas: tuple["ModelQuotaView", ...] = ()
    renewal_boundary_5h: datetime | None = None
    identity_state: str = "unconfirmed"
    quota_state: str = "unavailable"
    quota_fresh_5h: bool = False
    quota_fresh_week: bool = False
    official_reset_5h: datetime | None = None
    official_reset_week: datetime | None = None
    quota_observed_at_5h: datetime | None = None


@dataclass(frozen=True)
class ModelQuotaView:
    model: str
    percent: float
    reset_at: datetime | None
    observed_at: datetime
    source: str
    is_fresh: bool = True

    @property
    def valid_until(self) -> datetime:
        deadline = self.observed_at + timedelta(minutes=10)
        return min(deadline, self.reset_at) if self.reset_at else deadline


def resolve_model_quotas(
    snapshots: tuple[ModelQuotaView, ...], now: datetime
) -> tuple[ModelQuotaView, ...]:
    """Normalize TTL/reset expiry per model without refreshing observation time."""
    resolved = []
    for snapshot in snapshots:
        source = snapshot.source
        is_fresh = snapshot.is_fresh and now < snapshot.valid_until
        if snapshot.is_fresh and not is_fresh:
            source = "snapshot desatualizado · origem oficial"
        resolved.append(replace(snapshot, source=source, is_fresh=is_fresh))
    return tuple(resolved)


def model_quota_label(snapshots: tuple[ModelQuotaView, ...]) -> str:
    return " · ".join(f"{item.model}: {item.percent:g}% {item.source}" for item in snapshots)


def remaining_label(view: "ProviderView") -> str:
    """Texto da pilula: 'bloq.' se bloqueado; senao 'restam N%' da janela 5h."""
    if view.rate_limited:
        return "bloq."
    return f"restam {max(0, min(100, 100 - round(view.pct_5h)))}%"


def build_provider_view(
    provider: str,
    snap_5h: WindowSnapshot,
    weekly: WeeklyResult,
    quota: Mapping | None = None,
    now: datetime | None = None,
) -> ProviderView:
    week = weekly.aggregate
    state = quota.get("status", "unavailable") if quota else "unavailable"
    snapshots = quota.get("windows", {}) if quota else {}
    stale_snapshots = quota.get("stale_windows", {}) if quota else {}
    fresh_5h = snapshots.get(("5h", "")) if state == "fresh" else None
    fresh_week = snapshots.get(("weekly", "")) if state == "fresh" else None
    model_rows = []
    for name in ("opus", "sonnet"):
        row = snapshots.get((name, name)) if state == "fresh" else None
        is_fresh = row is not None
        source = "oficial"
        if row is None:
            row = stale_snapshots.get((name, name))
            source = "snapshot desatualizado · origem oficial"
        if row is not None:
            model_rows.append(
                ModelQuotaView(
                    model=name,
                    percent=row["percent"],
                    reset_at=(
                        datetime.fromisoformat(row["reset_at"]).replace(tzinfo=UTC)
                        if row.get("reset_at")
                        else None
                    ),
                    observed_at=datetime.fromisoformat(row["observed_at"]).replace(tzinfo=UTC),
                    source=source,
                    is_fresh=is_fresh,
                )
            )
    model_quotas = tuple(model_rows)
    estimated_pct_5h = 100 if snap_5h.is_rate_limited else snap_5h.percent
    estimated_pct_week = 100 if week.is_rate_limited else week.percent
    pct_5h = fresh_5h["percent"] if fresh_5h else estimated_pct_5h
    pct_week = fresh_week["percent"] if fresh_week else estimated_pct_week
    if snap_5h.is_rate_limited:
        pct_5h = 100
    if week.is_rate_limited:
        pct_week = 100
    reset_5h = (
        snap_5h.reset_at
        if snap_5h.is_rate_limited
        else datetime.fromisoformat(fresh_5h["reset_at"]).replace(tzinfo=UTC)
        if fresh_5h and fresh_5h.get("reset_at")
        else snap_5h.reset_at
    )
    reset_week = (
        week.reset_at
        if week.is_rate_limited
        else datetime.fromisoformat(fresh_week["reset_at"]).replace(tzinfo=UTC)
        if fresh_week and fresh_week.get("reset_at")
        else week.reset_at
    )
    return ProviderView(
        provider=provider,
        label=PROVIDER_LABELS.get(provider, provider),
        pct_5h=pct_5h,
        level_5h=level_for_percent(
            pct_5h, snap_5h.is_rate_limited or bool(fresh_5h and pct_5h >= 100)
        ),
        reset_5h=reset_5h,
        pct_week=pct_week,
        level_week=level_for_percent(
            pct_week, week.is_rate_limited or bool(fresh_week and pct_week >= 100)
        ),
        reset_week=reset_week,
        last_model=weekly.last_model or NO_DATA,
        rate_limited=snap_5h.is_rate_limited
        or week.is_rate_limited
        or bool(fresh_5h and pct_5h >= 100)
        or bool(fresh_week and pct_week >= 100),
        source_5h="oficial"
        if fresh_5h
        else _fallback_source(state, snapshots, stale_snapshots, "5h"),
        source_week="oficial"
        if fresh_week
        else _fallback_source(state, snapshots, stale_snapshots, "weekly"),
        fresh_quota=bool(fresh_5h or fresh_week),
        model_quota=model_quota_label(model_quotas),
        reset_source_5h="estimado (bloqueio local)"
        if snap_5h.is_rate_limited
        else "oficial"
        if fresh_5h and fresh_5h.get("reset_at")
        else ("não informado pela fonte" if fresh_5h else "estimado"),
        reset_source_week="estimado (bloqueio local)"
        if week.is_rate_limited
        else "oficial"
        if fresh_week and fresh_week.get("reset_at")
        else ("não informado pela fonte" if fresh_week else "estimado"),
        estimated_pct_5h=estimated_pct_5h,
        estimated_pct_week=estimated_pct_week,
        estimated_reset_5h=snap_5h.reset_at,
        estimated_reset_week=week.reset_at,
        estimated_blocked_5h=snap_5h.is_rate_limited,
        estimated_blocked_week=week.is_rate_limited,
        estimated_rate_limited=snap_5h.is_rate_limited or week.is_rate_limited,
        official_until_5h=_quota_valid_until(fresh_5h),
        official_until_week=_quota_valid_until(fresh_week),
        quota_identity=(
            quota.get("account_hash") if quota and quota.get("identity_confirmed") else None
        ),
        model_quotas=model_quotas,
        renewal_boundary_5h=(
            _confirmed_reset_boundary(fresh_5h or stale_snapshots.get(("5h", "")), now)
            if quota and quota.get("identity_confirmed") and now is not None
            else None
        ),
        identity_state=(
            "ambiguous"
            if quota and quota.get("status") == "ambiguous"
            else "confirmed"
            if quota and quota.get("identity_confirmed")
            else "unconfirmed"
        ),
        quota_state=state,
        quota_fresh_5h=fresh_5h is not None,
        quota_fresh_week=fresh_week is not None,
        official_reset_5h=(
            datetime.fromisoformat(fresh_5h["reset_at"]).replace(tzinfo=UTC)
            if fresh_5h and fresh_5h.get("reset_at")
            else None
        ),
        official_reset_week=(
            datetime.fromisoformat(fresh_week["reset_at"]).replace(tzinfo=UTC)
            if fresh_week and fresh_week.get("reset_at")
            else None
        ),
        quota_observed_at_5h=(
            datetime.fromisoformat(fresh_5h["observed_at"]).replace(tzinfo=UTC)
            if fresh_5h and fresh_5h.get("observed_at")
            else None
        ),
    )


def _quota_valid_until(snapshot: Mapping | None) -> datetime | None:
    if not snapshot or not snapshot.get("observed_at"):
        return None
    observed = datetime.fromisoformat(snapshot["observed_at"]).replace(tzinfo=UTC)
    valid_until = observed + timedelta(minutes=10)
    if snapshot.get("reset_at"):
        reset = datetime.fromisoformat(snapshot["reset_at"]).replace(tzinfo=UTC)
        valid_until = min(valid_until, reset)
    return valid_until


def _confirmed_reset_boundary(snapshot: Mapping | None, now: datetime) -> datetime | None:
    if not snapshot or not snapshot.get("reset_at"):
        return None
    reset = datetime.fromisoformat(snapshot["reset_at"]).replace(tzinfo=UTC)
    if reset <= now and _quota_valid_until(snapshot) == reset:
        return reset
    return None


def _fallback_source(state: str, snapshots: Mapping, stale_snapshots: Mapping, window: str) -> str:
    if state == "ambiguous":
        return "estimado · contas ambíguas"
    if state == "stale" or (window, "") in stale_snapshots:
        return "estimado · desatualizado"
    if state == "fresh" and (window, "") not in snapshots:
        return "estimado · campo ausente"
    return "estimado · indisponível"


def load_hud_view(
    conn: sqlite3.Connection,
    now: datetime,
    chatgpt_limits: Mapping[str, ModelLimit] | None = None,
) -> dict[str, ProviderView]:
    claude_5h = get_claude_5h_snapshot(conn, now)
    from neuralmeter.db import get_quota_snapshots

    quota = get_quota_snapshots(conn, now)
    chatgpt_5h = get_chatgpt_5h_snapshot(conn, now, limits=chatgpt_limits).aggregate
    return {
        "claude": build_provider_view(
            "claude", claude_5h, get_weekly_snapshot(conn, "claude", now), quota, now
        ),
        "chatgpt": build_provider_view(
            "chatgpt", chatgpt_5h, get_weekly_snapshot(conn, "chatgpt", now)
        ),
    }


@dataclass(frozen=True)
class ConnState:
    clients: int = 0
    tabs: dict[str, bool] = field(default_factory=lambda: {"claude": False, "chatgpt": False})
    claude_tabs: int | None = None


def apply_conn_update(state: ConnState, kind: str, payload: dict) -> ConnState:
    if kind == "conn":
        clients = max(0, int(payload.get("clients", 0)))
        tabs = {"claude": False, "chatgpt": False} if clients == 0 else dict(state.tabs)
        return ConnState(
            clients=clients, tabs=tabs, claude_tabs=None if clients == 0 else state.claude_tabs
        )
    if kind == "status":
        raw = payload.get("tabs")
        if isinstance(raw, dict):
            tabs = {k: bool(raw.get(k, state.tabs.get(k, False))) for k in ("claude", "chatgpt")}
            raw_count = raw.get("claude_count")
            count = (
                min(100, raw_count)
                if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0
                else (1 if tabs["claude"] else 0)
            )
            return ConnState(clients=state.clients, tabs=tabs, claude_tabs=count)
    return state


def conn_label(state: ConnState) -> str:
    if state.clients <= 0:
        return "offline"
    if any(state.tabs.values()):
        return "online"
    return "aguardando aba"
