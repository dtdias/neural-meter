from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter import db
from neuralmeter.db import to_db_ts
from neuralmeter.engine import calibrate_ceiling, is_new_block
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import Limit


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    c = db.connect_db()
    yield c
    c.close()


def _events(conn, provider, model, n, age=timedelta(minutes=30)):
    t = datetime.now(UTC) - age
    for i in range(n):
        db.insert_usage_event(conn, provider, model, created_at=t + timedelta(seconds=i))


def _limit(provider, scope, model=None, hours=2):
    return Limit(
        provider=provider,
        scope=scope,
        reset_at=datetime.now(UTC) + timedelta(hours=hours),
        rate_limited=True,
        model=model,
    )


def test_calibrate_ceiling_branches():
    assert calibrate_ceiling(0.5, 45) is None  # used < 1
    assert calibrate_ceiling(50, 45) == 50.0  # sobe
    assert calibrate_ceiling(30, 45) == 30.0  # desce (>= 50%)
    assert calibrate_ceiling(22.5, 45) == 22.5  # exatamente 50%
    assert calibrate_ceiling(10, 45) is None  # parcial
    assert calibrate_ceiling(200000, 45) == 100000.0  # clamp
    assert calibrate_ceiling(30.26, 45) == 30.3  # 1 casa


def test_is_new_block():
    reset = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    free = {"is_rate_limited": 0, "window_5h_reset_at": None, "weekly_reset_at": None}
    assert is_new_block(free, "5h", reset)
    blocked = {
        "is_rate_limited": 1,
        "window_5h_reset_at": to_db_ts(reset),
        "weekly_reset_at": to_db_ts(reset + timedelta(days=1)),
    }
    assert not is_new_block(blocked, "5h", reset)
    assert is_new_block(blocked, "5h", reset + timedelta(hours=5))
    assert not is_new_block(blocked, "weekly", reset + timedelta(days=1))
    assert is_new_block(blocked, "weekly", reset)


def test_claude_5h_calibrates_down_once(conn):
    _events(conn, "claude", "sonnet", 30)
    msg = _limit("claude", "5h")
    _, update = handle_message(conn, msg)
    assert db.get_provider_state(conn, "claude")["window_5h_max_units"] == 30.0
    assert update[1]["calibrated"] == {"target": "window_5h_max_units", "old": 45.0, "new": 30.0}
    _events(conn, "claude", "sonnet", 10)
    _, update2 = handle_message(conn, msg)  # mesmo reset: não é bloqueio novo
    assert db.get_provider_state(conn, "claude")["window_5h_max_units"] == 30.0
    assert update2[1]["calibrated"] is None


def test_partial_usage_keeps_ceiling(conn):
    _events(conn, "claude", "sonnet", 10)
    _, update = handle_message(conn, _limit("claude", "5h"))
    assert db.get_provider_state(conn, "claude")["window_5h_max_units"] == 45.0
    assert update[1]["calibrated"] is None


def test_weekly_raises_ceiling(conn):
    db.set_provider_limits(conn, "claude", weekly_max_units=20)
    _events(conn, "claude", "sonnet", 30, age=timedelta(days=2))
    _, update = handle_message(conn, _limit("claude", "weekly", hours=48))
    assert db.get_provider_state(conn, "claude")["weekly_max_units"] == 30.0
    assert update[1]["calibrated"]["target"] == "weekly_max_units"


def test_chatgpt_model_in_message_writes_model_limits(conn):
    _events(conn, "chatgpt", "o3", 60)
    _, update = handle_message(conn, _limit("chatgpt", "5h", model="o3"))
    lims = db.get_model_limits(conn, "chatgpt")
    assert lims["o3"]["max_units"] == 60.0
    assert lims["o3"]["window_hours"] == 5.0
    assert update[1]["calibrated"] == {"target": "model:o3", "old": 100.0, "new": 60.0}


def test_chatgpt_recent_event_model_fallback(conn):
    _events(conn, "chatgpt", "o3", 60, age=timedelta(minutes=3))
    handle_message(conn, _limit("chatgpt", "5h"))
    assert db.get_model_limits(conn, "chatgpt")["o3"]["max_units"] == 60.0


def test_chatgpt_no_model_no_recent_event_noop(conn):
    _events(conn, "chatgpt", "o3", 60, age=timedelta(minutes=30))
    _, update = handle_message(conn, _limit("chatgpt", "5h"))
    assert db.get_model_limits(conn, "chatgpt") == {}
    assert update[1]["calibrated"] is None


def test_chatgpt_unknown_model_uses_full_name(conn):
    _events(conn, "chatgpt", "mystery-1", 40)
    handle_message(conn, _limit("chatgpt", "5h", model="Mystery-1"))
    assert db.get_model_limits(conn, "chatgpt")["mystery-1"]["max_units"] == 40.0
