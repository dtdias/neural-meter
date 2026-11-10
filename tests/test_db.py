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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


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
