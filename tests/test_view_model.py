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
)

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
    s = apply_conn_update(s, "status", {"tabs": {"claude": True, "chatgpt": False}})
    assert s.tabs == {"claude": True, "chatgpt": False} and conn_label(s) == "online"
    s = apply_conn_update(s, "conn", {"clients": 2})
    assert s.tabs["claude"] and conn_label(s) == "online"
    s = apply_conn_update(s, "conn", {"clients": 0})
    assert s.tabs == {"claude": False, "chatgpt": False} and conn_label(s) == "offline"
    assert apply_conn_update(s, "usage", {}) == s
    assert apply_conn_update(s, "status", {}) == s
