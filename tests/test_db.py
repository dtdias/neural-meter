from datetime import UTC, datetime, timedelta, timezone

import pytest

from neuralmeter import db


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    c = db.connect_db()
    yield c
    c.close()


def test_schema_objects_and_version(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"usage_events", "provider_state", "idx_usage_provider_time"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "model_limits" in names


def test_migrate_v1_to_v2_existing_db(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA_V1)
    raw.execute("INSERT INTO provider_state (provider) VALUES ('claude')")
    raw.execute("INSERT INTO usage_events (provider, model_name) VALUES ('claude', 'm')")
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    c = db.connect_db(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
    assert db.get_model_limits(c, "chatgpt") == {}
    c.close()


def test_set_model_limit_upsert_and_validation(conn):
    db.set_model_limit(conn, "chatgpt", "o3", 5, 30)
    db.set_model_limit(conn, "chatgpt", "o3", 3, 42.5)
    lims = db.get_model_limits(conn, "chatgpt")
    assert list(lims) == ["o3"]
    assert lims["o3"]["window_hours"] == 3.0
    assert lims["o3"]["max_units"] == 42.5
    assert db.get_model_limits(conn, "claude") == {}
    for bad in (0, -1, 100001, float("nan"), "x", True):
        with pytest.raises(ValueError):
            db.set_model_limit(conn, "chatgpt", "o3", 5, bad)
    with pytest.raises(ValueError):
        db.set_model_limit(conn, "other", "o3", 5, 10)


def test_migrate_idempotent(conn):
    db.migrate(conn)
    db.migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM provider_state").fetchone()[0] == 2


def test_seed_defaults(conn):
    for p in db.PROVIDERS:
        st = db.get_provider_state(conn, p)
        assert st["window_5h_max_units"] == 45.0
        assert st["weekly_max_units"] == 500.0
        assert st["plan_tier"] == "pro"
        assert st["is_rate_limited"] == 0


def test_insert_returns_id_and_utc_format(conn):
    t = datetime(2026, 1, 2, 10, 30, 5, tzinfo=timezone(timedelta(hours=3)))
    i = db.insert_usage_event(conn, "claude", "sonnet", prompt_chars=10, created_at=t)
    assert i == 1
    row = conn.execute("SELECT * FROM usage_events WHERE id = ?", (i,)).fetchone()
    assert row["created_at"] == "2026-01-02 07:30:05"
    assert row["prompt_chars"] == 10
    j = db.insert_usage_event(conn, "chatgpt", "gpt-4o")
    assert j == 2
    now = conn.execute("SELECT created_at FROM usage_events WHERE id = 2").fetchone()[0]
    assert datetime.strptime(now, db.TS_FORMAT)


def test_get_events_since_filters_and_orders(conn):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    db.insert_usage_event(conn, "claude", "a", created_at=base + timedelta(hours=2))
    db.insert_usage_event(conn, "claude", "b", created_at=base + timedelta(hours=1))
    db.insert_usage_event(conn, "claude", "a", created_at=base - timedelta(hours=1))
    db.insert_usage_event(conn, "chatgpt", "a", created_at=base + timedelta(hours=3))
    rows = db.get_events_since(conn, "claude", base)
    assert [r["model_name"] for r in rows] == ["b", "a"]
    rows = db.get_events_since(conn, "claude", base, model_name="a")
    assert len(rows) == 1
    assert rows[0]["created_at"] == "2026-01-01 14:00:00"
    assert len(db.get_events_since(conn, "chatgpt", base)) == 1


def test_invalid_provider(conn):
    with pytest.raises(ValueError):
        db.insert_usage_event(conn, "gemini", "x")
    with pytest.raises(ValueError):
        db.get_events_since(conn, "gemini", datetime.now(UTC))
    with pytest.raises(ValueError):
        db.get_provider_state(conn, "gemini")


def test_update_provider_state(conn):
    db.update_provider_state(conn, "claude", is_rate_limited=1, plan_tier="max")
    st = db.get_provider_state(conn, "claude")
    assert st["is_rate_limited"] == 1
    assert st["plan_tier"] == "max"
    assert db.get_provider_state(conn, "chatgpt")["plan_tier"] == "pro"
    with pytest.raises(ValueError):
        db.update_provider_state(conn, "claude", bogus=1)
    with pytest.raises(ValueError):
        db.update_provider_state(conn, "claude", provider="chatgpt")


def test_naive_datetime_rejected(conn):
    naive = datetime(2026, 1, 1)
    with pytest.raises(ValueError):
        db.to_db_ts(naive)
    with pytest.raises(ValueError):
        db.insert_usage_event(conn, "claude", "x", created_at=naive)
    with pytest.raises(ValueError):
        db.get_events_since(conn, "claude", naive)


def test_get_provider_limits_defaults(conn):
    assert db.get_provider_limits(conn, "claude") == {
        "window_5h_max_units": 45.0,
        "weekly_max_units": 500.0,
    }


def test_set_provider_limits_partial(conn):
    db.set_provider_limits(conn, "claude", window_5h_max_units=90)
    assert db.get_provider_limits(conn, "claude") == {
        "window_5h_max_units": 90.0,
        "weekly_max_units": 500.0,
    }
    db.set_provider_limits(conn, "claude", weekly_max_units=1000.5)
    assert db.get_provider_limits(conn, "claude")["weekly_max_units"] == 1000.5
    assert db.get_provider_limits(conn, "chatgpt")["window_5h_max_units"] == 45.0


@pytest.mark.parametrize("bad", [0, -1, 100001, "abc", None, float("nan"), True])
def test_set_provider_limits_invalid(conn, bad):
    if bad is None:  # None = "nao alterar"
        db.set_provider_limits(conn, "claude", window_5h_max_units=None)
        return
    with pytest.raises(ValueError):
        db.set_provider_limits(conn, "claude", window_5h_max_units=bad)
    with pytest.raises(ValueError):
        db.set_provider_limits(conn, "claude", weekly_max_units=bad)
    assert db.get_provider_limits(conn, "claude")["window_5h_max_units"] == 45.0


def test_set_provider_limits_unknown_provider(conn):
    with pytest.raises(ValueError):
        db.set_provider_limits(conn, "gemini", window_5h_max_units=10)
    with pytest.raises(ValueError):
        db.get_provider_limits(conn, "gemini")


def test_claude_engine_reflects_new_limit(conn):
    from neuralmeter.engine import get_claude_5h_snapshot

    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    db.insert_usage_event(conn, "claude", "sonnet", weight_units=9, created_at=now)
    before = get_claude_5h_snapshot(conn, now).percent
    db.set_provider_limits(conn, "claude", window_5h_max_units=90)
    after = get_claude_5h_snapshot(conn, now).percent
    assert before > 0
    assert after == pytest.approx(before / 2)
