from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import (
    connect_db,
    from_db_ts,
    insert_usage_event,
    to_db_ts,
)
from neuralmeter.engine import (
    calc_percent,
    compute_claude_5h,
    get_claude_5h_snapshot,
    sync_claude_official_reset,
)

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)


def ev(dt, w=1.0):
    return {"created_at": to_db_ts(dt), "weight_units": w}


def state(**kw):
    base = {
        "window_5h_max_units": 45.0,
        "window_5h_reset_at": None,
        "is_rate_limited": 0,
    }
    base.update(kw)
    return base


def test_from_db_ts_roundtrip():
    assert from_db_ts(None) is None
    assert from_db_ts(to_db_ts(NOW)) == NOW
    assert from_db_ts(to_db_ts(NOW)).tzinfo is not None


def test_no_events():
    s = compute_claude_5h([], state(), NOW)
    assert s.used_units == 0
    assert s.percent == 0
    assert s.reset_at is None
    assert s.window_start is None
    assert s.event_count == 0


def test_single_event():
    s = compute_claude_5h([ev(NOW - H)], state(), NOW)
    assert s.used_units == 1
    assert s.reset_at == NOW - H + 5 * H
    assert s.percent == pytest.approx(2.2222, abs=1e-3)
    assert s.remaining_units == 44


def test_chain_event_in_expired_window():
    s = compute_claude_5h([ev(NOW - 6 * H), ev(NOW - 4 * H)], state(), NOW)
    assert s.used_units == 0
    assert s.reset_at is None


def test_chain_new_window():
    s = compute_claude_5h([ev(NOW - 6 * H), ev(NOW - H / 2)], state(), NOW)
    assert s.used_units == 1
    assert s.window_start == NOW - H / 2
    assert s.reset_at == NOW - H / 2 + 5 * H


def test_event_at_boundary_opens_new_window():
    start = NOW - 6 * H
    s = compute_claude_5h([ev(start), ev(start + 5 * H)], state(), NOW)
    assert s.window_start == start + 5 * H
    assert s.used_units == 1


def test_official_reset_overrides():
    events = [ev(NOW - 4 * H), ev(NOW - 3 * H, 2), ev(NOW - H)]
    s = compute_claude_5h(events, state(window_5h_reset_at=to_db_ts(NOW + 2 * H)), NOW)
    assert s.window_start == NOW - 3 * H
    assert s.reset_at == NOW + 2 * H
    assert s.used_units == 3
    assert s.event_count == 2


def test_official_reset_past_ignored():
    s = compute_claude_5h([ev(NOW - H)], state(window_5h_reset_at=to_db_ts(NOW - H)), NOW)
    assert s.reset_at == NOW + 4 * H
    assert s.used_units == 1


def test_rate_limited_future_reset():
    st = state(window_5h_reset_at=to_db_ts(NOW + H), is_rate_limited=1)
    s = compute_claude_5h([ev(NOW - H)], st, NOW)
    assert s.is_rate_limited
    assert s.percent == 100
    assert s.remaining_units == 0


def test_rate_limited_past_reset_not_blocked():
    st = state(window_5h_reset_at=to_db_ts(NOW - H), is_rate_limited=1)
    s = compute_claude_5h([ev(NOW - 30 * H / 60)], st, NOW)
    assert not s.is_rate_limited
    assert s.percent < 100


def test_clamp_over_max():
    s = compute_claude_5h([ev(NOW - H, 100)], state(), NOW)
    assert s.percent == 100
    assert s.remaining_units == 0


def test_calc_percent_zero_max():
    assert calc_percent(1, 0) == 100
    assert calc_percent(0, 0) == 0


def test_integration(tmp_path):
    conn = connect_db(tmp_path / "t.db")
    insert_usage_event(conn, "claude", "sonnet", weight_units=3.0, created_at=NOW - 2 * H)
    insert_usage_event(conn, "claude", "sonnet", weight_units=2.0, created_at=NOW - H)
    insert_usage_event(conn, "chatgpt", "gpt-4o", weight_units=9.0, created_at=NOW - H)
    s = get_claude_5h_snapshot(conn, NOW)
    assert s.used_units == 5
    assert s.reset_at == NOW + 3 * H

    sync_claude_official_reset(conn, NOW + H, rate_limited=True)
    s = get_claude_5h_snapshot(conn, NOW)
    assert s.window_start == NOW - 4 * H
    assert s.reset_at == NOW + H
    assert s.is_rate_limited
    assert s.percent == 100

    with pytest.raises(ValueError):
        sync_claude_official_reset(conn, datetime(2026, 1, 1))
    conn.close()
