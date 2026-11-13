from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import connect_db, insert_usage_event
from neuralmeter.engine import WeeklyResult, WindowSnapshot
from neuralmeter.ui.view_model import (
    ConnState,
    apply_conn_update,
    build_provider_view,
    conn_label,
    format_countdown,
    level_for_percent,
    load_hud_view,
    remaining_label,
)


def test_remaining_label():
    from dataclasses import replace

    from neuralmeter.ui.view_model import ProviderView

    v = ProviderView("claude", "Claude", 75.0, "yellow", None, 0.0, "green", None, "x", False)
    assert remaining_label(v) == "restam 25%"
    assert remaining_label(replace(v, pct_5h=150.0)) == "restam 0%"
    assert remaining_label(replace(v, pct_5h=-5.0)) == "restam 100%"
    assert remaining_label(replace(v, rate_limited=True)) == "bloq."


NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("pct", "limited", "level"),
    [
        (0, False, "green"),
        (69.9, False, "green"),
        (70, False, "yellow"),
        (89.9, False, "yellow"),
        (90, False, "red"),
        (100, False, "red"),
        (5, True, "red"),
    ],
)
def test_level_for_percent(pct, limited, level):
    assert level_for_percent(pct, limited) == level


@pytest.mark.parametrize(
    ("delta", "text"),
    [
        (None, "—"),
        (timedelta(seconds=-5), "agora"),
        (timedelta(0), "agora"),
        (timedelta(minutes=59), "59m 00s"),
        (timedelta(minutes=5, seconds=7), "05m 07s"),
        (timedelta(hours=3), "3h 00m"),
        (timedelta(hours=3, minutes=5), "3h 05m"),
        (timedelta(days=2, hours=4), "2d 04h"),
    ],
)
def test_format_countdown(delta, text):
    reset = None if delta is None else NOW + delta
    assert format_countdown(reset, NOW) == text


def _snap(provider, pct, reset=None, limited=False):
    return WindowSnapshot(provider, pct, 100, pct, 100 - pct, None, reset, limited, 1)


def test_build_provider_view():
    r5 = NOW + timedelta(hours=1)
    rw = NOW + timedelta(days=3)
    weekly = WeeklyResult(_snap("claude", 91, rw), {}, 3, 100, "opus")
    v = build_provider_view("claude", _snap("claude", 72, r5), weekly)
    assert (v.label, v.pct_5h, v.level_5h, v.reset_5h) == ("Claude", 72, "yellow", r5)
    assert (v.pct_week, v.level_week, v.reset_week) == (91, "red", rw)
    assert v.last_model == "opus"
    assert not v.rate_limited


def test_build_provider_view_empty():
    weekly = WeeklyResult(_snap("chatgpt", 0), {}, 0, 0, None)
    v = build_provider_view("chatgpt", _snap("chatgpt", 0, limited=True), weekly)
    assert v.label == "ChatGPT"
    assert v.last_model == "—"
    assert v.level_5h == "red" and v.rate_limited


def test_fresh_quota_never_hides_local_block_and_reset_source():
    r = NOW + timedelta(hours=1)
    old_blocked = _snap("claude", 8, r, limited=True)
    weekly = WeeklyResult(_snap("claude", 12, r, limited=True), {}, 1, 10, "opus")
    snapshots = {
        "status": "fresh",
        "windows": {
            ("5h", ""): {"percent": 15, "reset_at": None},
            ("weekly", ""): {"percent": 20, "reset_at": None},
        },
    }
    view = build_provider_view("claude", old_blocked, weekly, snapshots)
    assert (view.pct_5h, view.level_5h) == (100, "red")
    assert (view.pct_week, view.level_week) == (100, "red")
    assert view.reset_source_5h == "estimado (bloqueio local)"
    assert view.reset_source_week == "estimado (bloqueio local)"


def test_snapshot_status_fallback_is_explicit():
    weekly = WeeklyResult(_snap("claude", 0), {}, 0, 0, None)
    cases = {
        "stale": "estimado · desatualizado",
        "ambiguous": "estimado · contas ambíguas",
        "unavailable": "estimado · indisponível",
    }
    for status, source in cases.items():
        view = build_provider_view(
            "claude", _snap("claude", 0), weekly, {"status": status, "windows": {}}
        )
        assert view.pct_5h == 0 and view.source_5h == source


def test_missing_aggregate_stays_estimated_while_model_windows_remain_distinct():
    weekly = WeeklyResult(_snap("claude", 0), {}, 0, 0, None)
    quota = {
        "status": "fresh",
        "windows": {
            ("opus", "opus"): {"percent": 33, "observed_at": NOW.isoformat()},
            ("sonnet", "sonnet"): {"percent": 44, "observed_at": NOW.isoformat()},
        },
    }
    view = build_provider_view("claude", _snap("claude", 0), weekly, quota)
    assert view.pct_5h == 0 and view.source_5h == "estimado · campo ausente"
    assert "opus: 33% oficial" in view.model_quota
    assert "sonnet: 44% oficial" in view.model_quota


def test_official_percent_without_reset_keeps_reset_estimated_and_labeled():
    local_reset = NOW + timedelta(hours=2)
    weekly = WeeklyResult(_snap("claude", 0), {}, 0, 0, None)
    view = build_provider_view(
        "claude",
        _snap("claude", 35, local_reset),
        weekly,
        {"status": "fresh", "windows": {("5h", ""): {"percent": 40, "reset_at": None}}},
    )
    assert view.pct_5h == 40 and view.source_5h == "oficial"
    assert view.reset_5h == local_reset
    assert view.reset_source_5h == "não informado pela fonte"


def test_load_hud_view(tmp_data_dir):
    conn = connect_db()
    t = NOW - timedelta(minutes=10)
    insert_usage_event(conn, "claude", "sonnet-4", prompt_chars=400, created_at=t)
    insert_usage_event(conn, "chatgpt", "gpt-4o", prompt_chars=100, created_at=t)
    views = load_hud_view(conn, NOW)
    assert set(views) == {"claude", "chatgpt"}
    assert views["claude"].pct_5h > 0 and views["claude"].last_model == "sonnet-4"
    assert views["chatgpt"].pct_5h > 0 and views["chatgpt"].last_model == "gpt-4o"
    assert views["claude"].pct_week > 0


def test_load_hud_view_empty(tmp_data_dir):
    views = load_hud_view(connect_db(), NOW)
    assert views["claude"].pct_5h == 0
    assert views["claude"].last_model == "—"
    assert views["claude"].reset_5h is None


def test_apply_conn_update_transitions():
    s = ConnState()
    assert conn_label(s) == "offline"
    s = apply_conn_update(s, "conn", {"clients": 1})
    assert s.clients == 1 and conn_label(s) == "aguardando aba"
    s = apply_conn_update(
        s, "status", {"tabs": {"claude": True, "chatgpt": False, "claude_count": 2}}
    )
    assert s.tabs == {"claude": True, "chatgpt": False} and conn_label(s) == "online"
    assert s.claude_tabs == 2
    s = apply_conn_update(s, "conn", {"clients": 2})
    assert s.tabs["claude"] and conn_label(s) == "online"
    s = apply_conn_update(s, "conn", {"clients": 0})
    assert s.tabs == {"claude": False, "chatgpt": False} and conn_label(s) == "offline"
    assert s.claude_tabs is None
    assert apply_conn_update(s, "usage", {}) == s
    assert apply_conn_update(s, "status", {}) == s


def test_load_hud_view_chatgpt_override(tmp_data_dir):
    from neuralmeter.engine import DEFAULT_CHATGPT_LIMITS, ModelLimit

    conn = connect_db()
    insert_usage_event(conn, "chatgpt", "gpt-5", created_at=NOW - timedelta(minutes=10))
    base = load_hud_view(conn, NOW)["chatgpt"].pct_5h
    limits = {**DEFAULT_CHATGPT_LIMITS, "gpt-5": ModelLimit(timedelta(hours=3), 16)}
    over = load_hud_view(conn, NOW, chatgpt_limits=limits)["chatgpt"].pct_5h
    assert base > 0
    assert over == pytest.approx(base * 10)
