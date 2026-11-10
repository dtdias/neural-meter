from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import (
    clear_usage_events,
    connect_db,
    get_provider_state,
    insert_usage_event,
    purge_usage_events_before,
    set_provider_limits,
)
from neuralmeter.privacy import (
    PrivacySettings,
    apply_retention,
    hash_conversation_id,
)
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import parse_message
from neuralmeter.ui.settings import ensure_privacy_salt, parse_privacy_settings

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SENTINEL = "SENTINELA_SEGREDO_XYZ_123"


@pytest.fixture
def conn(tmp_path):
    c = connect_db(tmp_path / "t.db")
    yield c
    c.close()


def test_hash_deterministic_16_hex():
    h = hash_conversation_id("abc", "salt")
    assert h == hash_conversation_id("abc", "salt")
    assert len(h) == 16
    int(h, 16)
    assert h != "abc"


def test_hash_none_and_empty():
    assert hash_conversation_id(None, "s") is None
    assert hash_conversation_id("", "s") is None


def test_hash_salt_changes_result():
    assert hash_conversation_id("abc", "s1") != hash_conversation_id("abc", "s2")


def test_parse_privacy_defaults():
    p = parse_privacy_settings({})
    assert p == PrivacySettings(True, 30, "")
    assert parse_privacy_settings({"privacy": "x"}).retention_days == 30


def test_parse_privacy_values_and_clamp():
    s = {"privacy": {"hash_conversation_ids": False, "retention_days": 2}, "privacy_salt": "ab"}
    p = parse_privacy_settings(s)
    assert p.hash_conversation_ids is False
    assert p.retention_days == 8
    assert p.salt == "ab"
    assert parse_privacy_settings({"privacy": {"retention_days": 99999}}).retention_days == 3650
    assert parse_privacy_settings({"privacy": {"retention_days": "x"}}).retention_days == 30
    assert parse_privacy_settings({"privacy": {"retention_days": True}}).retention_days == 30
    assert parse_privacy_settings(
        {"privacy": {"hash_conversation_ids": "no"}}
    ).hash_conversation_ids


def test_ensure_salt():
    s: dict = {}
    assert ensure_privacy_salt(s) is True
    salt = s["privacy_salt"]
    assert len(salt) == 32
    int(salt, 16)
    assert ensure_privacy_salt(s) is False
    assert s["privacy_salt"] == salt
    s["privacy_salt"] = "curto"
    assert ensure_privacy_salt(s) is True
    assert len(s["privacy_salt"]) == 32


def test_purge_removes_only_old(conn):
    insert_usage_event(conn, "claude", "m", created_at=NOW - timedelta(days=40))
    insert_usage_event(conn, "claude", "m", created_at=NOW - timedelta(days=1))
    cutoff = (NOW - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert purge_usage_events_before(conn, cutoff) == 1
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 1


def test_apply_retention(conn):
    insert_usage_event(conn, "claude", "m", created_at=NOW - timedelta(days=20))
    insert_usage_event(conn, "claude", "m", created_at=NOW - timedelta(days=5))
    assert apply_retention(conn, NOW, 10) == 1
    # days abaixo de 8 e' elevado a 8
    assert apply_retention(conn, NOW, 1) == 0


def test_clear_keeps_provider_state(conn):
    set_provider_limits(conn, "claude", window_5h_max_units=77)
    insert_usage_event(conn, "claude", "m")
    insert_usage_event(conn, "chatgpt", "m")
    assert clear_usage_events(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0] == 0
    assert get_provider_state(conn, "claude")["window_5h_max_units"] == 77


def _usage(conv="conv-123", **extra):
    import json

    d = {
        "type": "usage",
        "provider": "claude",
        "model": "m",
        "conversation_id": conv,
        "prompt_chars": 10,
        "context_chars": 5,
        **extra,
    }
    return parse_message(json.dumps(d), NOW)


def test_handler_hashes_conv_id(conn):
    p = PrivacySettings(True, 30, "salt")
    handle_message(conn, _usage(), privacy=p)
    got = conn.execute("SELECT conversation_id FROM usage_events").fetchone()[0]
    assert got == hash_conversation_id("conv-123", "salt")
    assert got != "conv-123"


def test_handler_without_privacy_stores_raw(conn):
    handle_message(conn, _usage())
    assert conn.execute("SELECT conversation_id FROM usage_events").fetchone()[0] == "conv-123"


def test_handler_privacy_off_stores_raw(conn):
    handle_message(conn, _usage(), privacy=PrivacySettings(False, 30, "s"))
    assert conn.execute("SELECT conversation_id FROM usage_events").fetchone()[0] == "conv-123"


def test_handler_null_conv_stays_null(conn):
    handle_message(conn, _usage(conv=None), privacy=PrivacySettings(True, 30, "s"))
    assert conn.execute("SELECT conversation_id FROM usage_events").fetchone()[0] is None


def test_prompt_text_never_persisted(tmp_path):
    path = tmp_path / "rnf03.db"
    c = connect_db(path)
    msg = _usage(
        prompt=SENTINEL,
        text=SENTINEL,
        content=SENTINEL,
        messages=[{"role": "user", "content": SENTINEL}],
    )
    reply, update = handle_message(c, msg, privacy=PrivacySettings(True, 30, "s"))
    assert reply["type"] == "ack"
    assert SENTINEL not in repr(update)
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        for row in c.execute(f"SELECT * FROM {t}").fetchall():  # noqa: S608
            assert SENTINEL not in repr(tuple(row))
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.commit()
    c.close()
    for f in tmp_path.iterdir():
        assert SENTINEL.encode() not in f.read_bytes()
