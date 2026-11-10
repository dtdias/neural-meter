from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import connect_db, from_db_ts, get_provider_state, insert_usage_event, to_db_ts
from neuralmeter.engine import (
    WINDOW_7D,
    WeeklyQuota,
    compute_weekly,
    estimate_tokens,
    get_weekly_snapshot,
    match_model_key,
    sync_weekly_reset,
)

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
H = timedelta(hours=1)
D = timedelta(days=1)


def ev(dt, model="gpt-4o", w=1.0, prompt=0, ctx=0):
    return {
        "created_at": to_db_ts(dt),
        "model_name": model,
        "weight_units": w,
        "prompt_chars": prompt,
        "context_chars": ctx,
    }


def state(**kw):
    base = {"weekly_max_units": 500.0, "weekly_reset_at": None, "is_rate_limited": 0}
    base.update(kw)
    return base


def test_window_boundaries():
    assert WINDOW_7D == 7 * D
    r = compute_weekly("claude", [ev(NOW - 6 * D - 23 * H)], state(), NOW)
    assert r.event_count == 1
    r = compute_weekly("claude", [ev(NOW - 7 * D - H)], state(), NOW)
    assert r.event_count == 0
    r = compute_weekly("claude", [ev(NOW + H)], state(), NOW)
    assert r.event_count == 0


def test_sum_weight_null_and_percent_cap():
    evs = [ev(NOW - H, w=2.5), ev(NOW - 2 * H, w=None)]
    r = compute_weekly("claude", evs, state(), NOW)
    assert r.aggregate.used_units == 3.5
    assert r.aggregate.max_units == 500.0
    assert r.aggregate.percent == pytest.approx(0.7)
    r = compute_weekly("claude", evs, state(weekly_max_units=2.0), NOW)
    assert r.aggregate.percent == 100.0
    assert r.aggregate.remaining_units == 0.0


def test_reset_estimated_official_and_past():
    oldest = NOW - 2 * D
    evs = [ev(NOW - H), ev(oldest)]
    r = compute_weekly("claude", evs, state(), NOW)
    assert r.aggregate.window_start == oldest
    assert r.aggregate.reset_at == oldest + 7 * D
    fut = NOW + 3 * D
    r = compute_weekly("claude", evs, state(weekly_reset_at=to_db_ts(fut)), NOW)
    assert r.aggregate.reset_at == fut
    r = compute_weekly("claude", evs, state(weekly_reset_at=to_db_ts(NOW - H)), NOW)
    assert r.aggregate.reset_at == oldest + 7 * D


def test_rate_limited():
    fut = NOW + 2 * D
    r = compute_weekly(
        "claude",
        [ev(NOW - H)],
        state(is_rate_limited=1, weekly_reset_at=to_db_ts(fut)),
        NOW,
    )
    assert r.aggregate.percent == 100.0
    assert r.aggregate.remaining_units == 0.0
    assert r.aggregate.is_rate_limited is True
    assert r.aggregate.reset_at == fut
    r = compute_weekly("claude", [ev(NOW - H)], state(is_rate_limited=1), NOW)
    assert r.aggregate.is_rate_limited is False
    assert r.aggregate.percent == pytest.approx(0.2)
    r = compute_weekly(
        "claude",
        [ev(NOW - H)],
        state(is_rate_limited=1, weekly_reset_at=to_db_ts(NOW - H)),
        NOW,
    )
    assert r.aggregate.is_rate_limited is False


def test_per_model():
    evs = [
        ev(NOW - 3 * D, "o3-2025", w=10),
        ev(NOW - 2 * D, "o4-mini-high", w=7),
        ev(NOW - D, "gpt-4o", w=1),
    ]
    r = compute_weekly("chatgpt", evs, state(), NOW)
    assert set(r.per_model) == {"o3", "o4-mini-high"}
    o3 = r.per_model["o3"]
    assert o3.used_units == 10 and o3.max_units == 100 and o3.percent == 10
    assert o3.reset_at == NOW - 3 * D + 7 * D
    assert r.per_model["o4-mini-high"].used_units == 7
    assert r.per_model["o4-mini-high"].percent == 1.0
    assert r.aggregate.used_units == 18
    assert r.aggregate.event_count == 3


def test_claude_per_model_empty():
    r = compute_weekly("claude", [ev(NOW - H, "claude-sonnet-4")], state(), NOW)
    assert r.per_model == {}


def test_custom_limits():
    lim = {"claude": {"claude-opus": WeeklyQuota(10)}}
    r = compute_weekly("claude", [ev(NOW - H, "claude-opus-4", w=5)], state(), NOW, lim)
    assert r.per_model["claude-opus"].percent == 50.0


def test_tokens_and_last_model():
    evs = [
        ev(NOW - 2 * H, "a", prompt=400, ctx=100),
        ev(NOW - H, "b", prompt=10, ctx=4000),
    ]
    assert estimate_tokens(evs) == 100 + 1000
    r = compute_weekly("claude", evs, state(), NOW)
    assert r.est_tokens == 1100
    assert r.last_model == "b"
    assert estimate_tokens([{"prompt_chars": None, "context_chars": None}]) == 0


def test_empty():
    r = compute_weekly("claude", [], state(), NOW)
    assert r.aggregate.percent == 0
    assert r.aggregate.reset_at is None
    assert r.aggregate.window_start is None
    assert r.est_tokens == 0
    assert r.last_model is None
    assert r.per_model == {}


def test_match_model_key():
    assert match_model_key("O4-mini-high-x", ["o4-mini", "o4-mini-high"]) == "o4-mini-high"
    assert match_model_key("foo", ["o3"]) is None


def test_integration(tmp_path):
    conn = connect_db(tmp_path / "t.db")
    insert_usage_event(
        conn, "chatgpt", "o3", prompt_chars=800, weight_units=2.0, created_at=NOW - D
    )
    insert_usage_event(conn, "chatgpt", "gpt-4o", created_at=NOW - 8 * D)
    r = get_weekly_snapshot(conn, "chatgpt", NOW)
    assert r.event_count == 1
    assert r.est_tokens == 200
    assert r.per_model["o3"].used_units == 2.0
    assert r.last_model == "o3"

    fut = NOW + D
    sync_weekly_reset(conn, "chatgpt", fut, rate_limited=True)
    assert from_db_ts(get_provider_state(conn, "chatgpt")["weekly_reset_at"]) == fut
    r = get_weekly_snapshot(conn, "chatgpt", NOW)
    assert r.aggregate.is_rate_limited and r.aggregate.percent == 100.0
    assert r.aggregate.reset_at == fut

    with pytest.raises(ValueError):
        get_weekly_snapshot(conn, "bogus", NOW)
    conn.close()
