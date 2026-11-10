from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import connect_db, insert_usage_event, to_db_ts
from neuralmeter.engine import (
    DEFAULT_CHATGPT_LIMITS,
    ModelLimit,
    compute_chatgpt_5h,
    get_chatgpt_5h_snapshot,
    resolve_model_limit,
    sync_chatgpt_official_reset,
)

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
M = timedelta(minutes=1)


def ev(dt, model="gpt-4o", w=1.0):
    return {"created_at": to_db_ts(dt), "model_name": model, "weight_units": w}


def state(**kw):
    base = {"window_5h_max_units": 45.0, "window_5h_reset_at": None, "is_rate_limited": 0}
    base.update(kw)
    return base


def test_no_events():
    r = compute_chatgpt_5h([], state(), NOW)
    assert r.per_model == {}
    assert r.aggregate.percent == 0
    assert r.aggregate.used_units == 0
    assert r.aggregate.max_units == 45.0
    assert r.aggregate.reset_at is None
    assert r.aggregate.window_start is None
    assert r.aggregate.event_count == 0
    assert r.aggregate.provider == "chatgpt"


def test_active_within_window():
    r = compute_chatgpt_5h([ev(NOW - 179 * M)], state(), NOW)
    assert r.per_model["gpt-4o"].event_count == 1


def test_expired_after_window():
    r = compute_chatgpt_5h([ev(NOW - 181 * M)], state(), NOW)
    assert r.per_model == {}
    assert r.aggregate.event_count == 0


def test_o3_five_hour_window():
    r = compute_chatgpt_5h([ev(NOW - 4 * H, "o3")], state(), NOW)
    assert r.per_model["o3"].event_count == 1
    assert r.per_model["o3"].reset_at == NOW + H


def test_individual_expiration():
    events = [ev(NOW - 170 * M), ev(NOW - 100 * M), ev(NOW - 10 * M)]
    r = compute_chatgpt_5h(events, state(), NOW)
    s = r.per_model["gpt-4o"]
    assert s.event_count == 3
    assert s.reset_at == NOW - 170 * M + 3 * H
    assert s.window_start == NOW - 170 * M
    r2 = compute_chatgpt_5h(events, state(), NOW + 15 * M)
    s2 = r2.per_model["gpt-4o"]
    assert s2.event_count == 2
    assert s2.reset_at == NOW - 100 * M + 3 * H


def test_prefix_grouping():
    events = [ev(NOW - M, "gpt-4o-mini"), ev(NOW - M, "gpt-4o"), ev(NOW - M, "O3-MINI")]
    r = compute_chatgpt_5h(events, state(), NOW)
    assert set(r.per_model) == {"gpt-4o", "o3"}
    assert r.per_model["gpt-4o"].event_count == 2
    assert r.per_model["o3"].event_count == 1


def test_resolve_longest_prefix_and_fallback():
    limits = {"gpt-4": ModelLimit(H, 1), "gpt-4o": ModelLimit(2 * H, 2)}
    assert resolve_model_limit("GPT-4o-mini", limits, 9).max_units == 2
    assert resolve_model_limit("gpt-4-turbo", limits, 9).max_units == 1
    assert resolve_model_limit("o3-mini", DEFAULT_CHATGPT_LIMITS, 9).window == 5 * H
    assert resolve_model_limit("zzz", limits, 9) == ModelLimit(3 * H, 9)


def test_unknown_model():
    r = compute_chatgpt_5h([ev(NOW - 170 * M, "Foo-Model")], state(), NOW)
    s = r.per_model["foo-model"]
    assert s.max_units == 45.0
    assert s.reset_at == NOW - 170 * M + 3 * H
    assert compute_chatgpt_5h([ev(NOW - 181 * M, "foo-model")], state(), NOW).per_model == {}


def test_aggregate_is_highest_percent():
    events = [ev(NOW - M, "gpt-4o", 8), ev(NOW - M, "o3", 50), ev(NOW - M, "gpt-5", 1)]
    r = compute_chatgpt_5h(events, state(), NOW)
    assert r.aggregate == r.per_model["o3"]
    assert r.aggregate.percent == 50


def test_limits_override():
    limits = {"gpt-4o": ModelLimit(5 * H, 10)}
    r = compute_chatgpt_5h([ev(NOW - 4 * H, w=2)], state(), NOW, limits)
    s = r.per_model["gpt-4o"]
    assert s.max_units == 10
    assert s.percent == 20
    assert s.reset_at == NOW + H


def test_weights():
    r = compute_chatgpt_5h([ev(NOW - M, w=None), ev(NOW - M, w=2.5)], state(), NOW)
    assert r.per_model["gpt-4o"].used_units == 3.5
    assert r.per_model["gpt-4o"].remaining_units == 80 - 3.5


def test_rate_limited_future_reset():
    st = state(window_5h_reset_at=to_db_ts(NOW + H), is_rate_limited=1)
    r = compute_chatgpt_5h([ev(NOW - M)], st, NOW)
    assert r.aggregate.is_rate_limited
    assert r.aggregate.percent == 100
    assert r.aggregate.remaining_units == 0
    assert r.aggregate.reset_at == NOW + H
    assert not r.per_model["gpt-4o"].is_rate_limited


def test_rate_limited_past_reset_ignored():
    st = state(window_5h_reset_at=to_db_ts(NOW - H), is_rate_limited=1)
    r = compute_chatgpt_5h([ev(NOW - M)], st, NOW)
    assert not r.aggregate.is_rate_limited
    assert r.aggregate.percent < 100


def test_percent_clamped():
    r = compute_chatgpt_5h([ev(NOW - M, w=500)], state(), NOW)
    assert r.per_model["gpt-4o"].percent == 100
    assert r.per_model["gpt-4o"].remaining_units == 0


def test_integration(tmp_path):
    conn = connect_db(tmp_path / "t.db")
    insert_usage_event(conn, "chatgpt", "gpt-4o", weight_units=3.0, created_at=NOW - 2 * H)
    insert_usage_event(conn, "chatgpt", "gpt-4o", weight_units=2.0, created_at=NOW - H)
    insert_usage_event(conn, "claude", "sonnet", weight_units=9.0, created_at=NOW - H)
    r = get_chatgpt_5h_snapshot(conn, NOW)
    assert r.per_model["gpt-4o"].used_units == 5
    assert r.aggregate.reset_at == NOW + H

    sync_chatgpt_official_reset(conn, NOW + 2 * H, rate_limited=True)
    r = get_chatgpt_5h_snapshot(conn, NOW)
    assert r.aggregate.is_rate_limited
    assert r.aggregate.reset_at == NOW + 2 * H
    assert r.aggregate.percent == 100

    r = get_chatgpt_5h_snapshot(conn, NOW, {"gpt-4o": ModelLimit(H + M, 5)})
    assert r.per_model["gpt-4o"].event_count == 1

    with pytest.raises(ValueError):
        sync_chatgpt_official_reset(conn, datetime(2026, 1, 1))
    conn.close()
