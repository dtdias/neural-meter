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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
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
    assert c.execute("PRAGMA user_version").fetchone()[0] == 5
    assert c.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1
    assert db.get_model_limits(c, "chatgpt") == {}
    c.close()


def _make_v3_quota_db():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA_V1)
    c.executemany("INSERT INTO provider_state (provider) VALUES (?)", [(p,) for p in db.PROVIDERS])
    c.execute("INSERT INTO usage_events (provider, model_name) VALUES ('claude', 'kept')")
    c.executescript(db.SCHEMA_V2)
    c.executescript(db.SCHEMA_V3)
    c.execute(
        "INSERT INTO quota_snapshots VALUES ('claude', '5h', '5h', '', 72, NULL, '2026-11-13')"
    )
    c.execute("PRAGMA user_version = 3")
    c.commit()
    return c


@pytest.mark.parametrize("fail_after", range(1, 9))
def test_migrate_rolls_back_every_ddl_interruption_then_retries(monkeypatch, fail_after):
    import sqlite3

    c = sqlite3.connect(":memory:")
    original = db._execute_migration_statement
    calls = 0

    def interrupt(conn, statement):
        nonlocal calls
        original(conn, statement)
        calls += 1
        if calls == fail_after:
            raise sqlite3.OperationalError("simulated interruption")

    monkeypatch.setattr(db, "_execute_migration_statement", interrupt)
    with pytest.raises(sqlite3.OperationalError, match="simulated interruption"):
        db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    assert not c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='usage_events'"
    ).fetchone()

    monkeypatch.setattr(db, "_execute_migration_statement", original)
    db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c.close()


@pytest.mark.parametrize("partial_state", ["rename_only", "rename_and_create"])
def test_migrate_recovers_committed_partial_v4_without_data_loss(partial_state):
    c = _make_v3_quota_db()
    if partial_state == "rename_only":
        c.execute("ALTER TABLE quota_snapshots RENAME TO quota_snapshots_unscoped")
    else:
        c.executescript(db.SCHEMA_V4)
    c.commit()  # Reproduces legacy executescript implicit commit before user_version.

    db.migrate(c)

    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert c.execute("SELECT model_name FROM usage_events").fetchone()[0] == "kept"
    assert c.execute("SELECT COUNT(*) FROM provider_state").fetchone()[0] == 2
    assert (
        c.execute(
            "SELECT percent FROM quota_snapshots_unscoped WHERE provider='claude'"
        ).fetchone()[0]
        == 72
    )
    assert "account_hash" in db._table_columns(c, "quota_snapshots")
    c.close()


def test_v4_failure_rolls_back_rename_and_restart_preserves_legacy_data(monkeypatch):
    import sqlite3

    c = _make_v3_quota_db()
    original = db._execute_migration_statement

    def interrupt_after_scoped_table(conn, statement):
        original(conn, statement)
        if statement.lstrip().startswith("CREATE TABLE quota_snapshots ("):
            raise sqlite3.OperationalError("simulated power loss")

    monkeypatch.setattr(db, "_execute_migration_statement", interrupt_after_scoped_table)
    with pytest.raises(sqlite3.OperationalError, match="simulated power loss"):
        db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "scope" in db._table_columns(c, "quota_snapshots")
    assert "quota_snapshots_unscoped" not in {
        r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    monkeypatch.setattr(db, "_execute_migration_statement", original)
    db.migrate(c)
    assert c.execute("SELECT percent FROM quota_snapshots_unscoped").fetchone()[0] == 72
    assert c.execute("SELECT model_name FROM usage_events").fetchone()[0] == "kept"
    c.close()


def _make_legacy_v2_db():
    import sqlite3

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(db.SCHEMA_V1)
    c.executemany("INSERT INTO provider_state (provider) VALUES (?)", [(p,) for p in db.PROVIDERS])
    c.execute("INSERT INTO usage_events (provider, model_name) VALUES ('claude', 'kept-v2')")
    c.executescript(db.SCHEMA_V2)
    c.execute("PRAGMA user_version = 2")
    c.commit()
    return c


def _apply_legacy_v4_stage(c, stage):
    if stage in {
        "v3_done",
        "v4_rename_only",
        "v4_rename_v3_duplicate",
        "v4_done",
        "v5_done",
    }:
        c.executescript(db.SCHEMA_V3)
    if stage in {"v4_rename_only", "v4_rename_v3_duplicate", "v4_done", "v5_done"}:
        c.execute(
            "INSERT INTO quota_snapshots VALUES ('claude', '5h', '5h', '', 67, NULL, '2026-11-13')"
        )
    if stage in {"v4_rename_only", "v4_rename_v3_duplicate"}:
        c.execute("ALTER TABLE quota_snapshots RENAME TO quota_snapshots_unscoped")
    if stage == "v4_rename_v3_duplicate":
        c.executescript(db.SCHEMA_V3)
    if stage in {"v4_done", "v5_done"}:
        c.executescript(db.SCHEMA_V4)
    if stage == "v5_done":
        c.executescript(db.SCHEMA_V5)
        c.execute(
            "INSERT INTO quota_identity_observations VALUES ('claude', ?, '2026-11-13')",
            ("a" * 64,),
        )
    c.commit()


@pytest.mark.parametrize(
    ("start_version", "stage"),
    [
        (2, "v3_done"),
        (2, "v4_rename_only"),
        (2, "v4_rename_v3_duplicate"),
        (2, "v4_done"),
        (2, "v5_done"),
        (3, "v3_done"),
        (3, "v4_rename_only"),
        (3, "v4_rename_v3_duplicate"),
        (3, "v4_done"),
        (3, "v5_done"),
        (4, "v4_done"),
        (4, "v5_done"),
    ],
)
def test_legacy_migration_frontier_recovery_matrix(start_version, stage):
    c = _make_legacy_v2_db()
    _apply_legacy_v4_stage(c, stage)
    c.execute(f"PRAGMA user_version = {start_version}")
    c.commit()

    db.migrate(c)
    db.migrate(c)  # Another restart after successful recovery stays safe.

    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert c.execute("SELECT model_name FROM usage_events").fetchone()[0] == "kept-v2"
    assert c.execute("SELECT COUNT(*) FROM provider_state").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM quota_identity_observations").fetchone()[0] == (
        1 if stage == "v5_done" else 0
    )
    if stage == "v5_done":
        assert (
            c.execute("SELECT account_hash FROM quota_identity_observations").fetchone()[0]
            == "a" * 64
        )
    if stage in {"v4_rename_only", "v4_rename_v3_duplicate", "v4_done", "v5_done"}:
        assert c.execute("SELECT percent FROM quota_snapshots_unscoped").fetchone()[0] == 67
    c.close()


def test_migration_rejects_incompatible_existing_v3_schema_without_data_loss():
    import sqlite3

    c = _make_legacy_v2_db()
    c.execute(
        "CREATE TABLE quota_snapshots (provider TEXT, scope TEXT, window_name TEXT, "
        "model TEXT, percent REAL, reset_at TEXT, observed_at TEXT)"
    )
    c.commit()
    with pytest.raises(sqlite3.DatabaseError, match="incompatible schema"):
        db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c.execute("SELECT model_name FROM usage_events").fetchone()[0] == "kept-v2"
    c.close()


def test_migration_refuses_nonempty_duplicate_legacy_table_without_data_loss():
    import sqlite3

    c = _make_legacy_v2_db()
    c.executescript(db.SCHEMA_V3)
    c.execute(
        "INSERT INTO quota_snapshots VALUES ('claude', '5h', '5h', '', 11, NULL, 'before-rename')"
    )
    c.execute("ALTER TABLE quota_snapshots RENAME TO quota_snapshots_unscoped")
    c.executescript(db.SCHEMA_V3)
    c.execute(
        "INSERT INTO quota_snapshots VALUES ('claude', '5h', '5h', '', 99, NULL, 'duplicate')"
    )
    c.commit()

    with pytest.raises(sqlite3.DatabaseError, match="duplicate legacy.*contain data"):
        db.migrate(c)

    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c.execute("SELECT percent FROM quota_snapshots_unscoped").fetchone()[0] == 11
    assert c.execute("SELECT percent FROM quota_snapshots").fetchone()[0] == 99
    c.close()


def test_migration_rejects_incompatible_existing_v5_observations():
    import sqlite3

    c = _make_v3_quota_db()
    c.executescript(db.SCHEMA_V4)
    c.execute("PRAGMA user_version = 4")
    c.execute(
        "CREATE TABLE quota_identity_observations "
        "(provider TEXT, account_hash TEXT, observed_at TEXT)"
    )
    c.commit()
    with pytest.raises(sqlite3.DatabaseError, match="incompatible schema"):
        db.migrate(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 4
    c.close()


def test_quota_snapshot_order_expiry_and_isolation(conn):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    account = "a" * 64
    db.observe_quota_identity(conn, "claude", account, now)
    assert db.put_quota_snapshot(
        conn, "claude", account, "5h", None, 50, now + timedelta(hours=1), now
    )
    assert not db.put_quota_snapshot(
        conn, "claude", account, "5h", None, 99, now + timedelta(hours=1), now
    )
    assert not db.put_quota_snapshot(
        conn,
        "claude",
        account,
        "5h",
        None,
        25,
        now + timedelta(minutes=30),
        now + timedelta(minutes=1),
    )
    assert not db.put_quota_snapshot(
        conn,
        "claude",
        account,
        "5h",
        None,
        25,
        now + timedelta(hours=1),
        now + timedelta(minutes=2),
    )
    result = db.get_quota_snapshots(conn, now)
    assert result["status"] == "fresh"
    assert result["windows"][("5h", "")]["percent"] == 50
    assert db.get_quota_snapshots(conn, now + timedelta(minutes=11))["status"] == "stale"
    with pytest.raises(ValueError):
        db.put_quota_snapshot(conn, "chatgpt", account, "5h", None, 2, None, now)


def test_quota_multi_account_ambiguous_and_model_scope(conn):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    db.observe_quota_identity(conn, "claude", "a" * 64, now)
    db.observe_quota_identity(conn, "claude", "b" * 64, now)
    assert db.put_quota_snapshot(
        conn, "claude", "a" * 64, "weekly", None, 12, now + timedelta(days=1), now
    )
    for account, pct in (("a" * 64, 25), ("b" * 64, 75)):
        assert db.put_quota_snapshot(
            conn, "claude", account, "5h", None, pct, now + timedelta(hours=1), now
        )
    assert db.get_quota_snapshots(conn, now)["status"] == "ambiguous"
    with pytest.raises(ValueError):
        db.put_quota_snapshot(
            conn, "claude", "a" * 64, "5h", "opus", 10, now + timedelta(hours=1), now
        )
    with pytest.raises(ValueError):
        db.put_quota_snapshot(
            conn, "claude", "a" * 64, "opus", "sonnet", 10, now + timedelta(hours=1), now
        )


def test_quota_preserves_model_scoped_windows(conn):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    db.observe_quota_identity(conn, "claude", "d" * 64, now)
    for model, percent in (("opus", 33), ("sonnet", 44)):
        assert db.put_quota_snapshot(
            conn, "claude", "d" * 64, model, model, percent, now + timedelta(days=1), now
        )
    result = db.get_quota_snapshots(conn, now)
    assert result["status"] == "fresh"
    assert result["windows"][("opus", "opus")]["percent"] == 33
    assert result["windows"][("sonnet", "sonnet")]["percent"] == 44


def test_multiple_unconfirmed_stale_accounts_are_not_arbitrarily_selected(conn):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    observed = now - timedelta(minutes=11)
    for account, percent in (("e" * 64, 20), ("f" * 64, 80)):
        db.observe_quota_identity(conn, "claude", account, observed)
        db.put_quota_snapshot(
            conn, "claude", account, "5h", None, percent, now + timedelta(hours=1), observed
        )
    result = db.get_quota_snapshots(conn, now)
    assert result["status"] == "ambiguous"
    assert result["stale_windows"] == {}


def test_quota_reset_expiry_and_unavailable_after_restart(conn):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    account = "c" * 64
    db.observe_quota_identity(conn, "claude", account, now)
    db.put_quota_snapshot(
        conn, "claude", account, "weekly", None, 0, now + timedelta(minutes=2), now
    )
    assert db.get_quota_snapshots(conn, now)["status"] == "fresh"
    assert db.get_quota_snapshots(conn, now + timedelta(minutes=3))["status"] == "stale"
    conn.execute("DELETE FROM quota_snapshots")
    conn.commit()
    assert db.get_quota_snapshots(conn, now)["status"] == "unavailable"


def test_v3_unscoped_quota_quarantined_on_upgrade(tmp_path):
    import sqlite3

    path = tmp_path / "v3.db"
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA_V1 + db.SCHEMA_V2 + db.SCHEMA_V3)
    raw.execute(
        "INSERT INTO quota_snapshots "
        "VALUES('claude','browser','5h','',20,NULL,'2026-11-12 12:00:00')"
    )
    raw.execute("PRAGMA user_version=3")
    raw.commit()
    raw.close()
    conn = db.connect_db(path)
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    assert db.get_quota_snapshots(conn, now)["status"] == "unavailable"
    assert conn.execute("SELECT COUNT(*) FROM quota_snapshots_unscoped").fetchone()[0] == 1
    conn.close()


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
