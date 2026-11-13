import json
from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.db import connect_db, insert_usage_event
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import ProtocolError, parse_message
from neuralmeter.ui.view_model import load_hud_view


def test_gemini_usage_persists_counts_without_quota_state_or_prompt(tmp_path):
    path = tmp_path / "gemini.db"
    now = datetime(2026, 11, 13, 12, tzinfo=UTC)
    conn = connect_db(path)
    insert_usage_event(conn, "claude", "claude-test", prompt_chars=20, created_at=now)
    prompt_secret = "fixture-only-private-prompt"
    parsed = parse_message(
        json.dumps(
            {
                "type": "usage",
                "provider": "gemini",
                "model": "gemini-unknown",
                "prompt_chars": len(prompt_secret),
                "context_chars": len(prompt_secret),
                "ts": int(now.timestamp() * 1000),
            }
        ),
        now,
    )
    handle_message(conn, parsed)
    row = conn.execute("SELECT * FROM usage_events WHERE provider='gemini'").fetchone()
    assert row["prompt_chars"] == len(prompt_secret)
    assert prompt_secret not in repr(dict(row))
    assert conn.execute("SELECT 1 FROM provider_state WHERE provider='gemini'").fetchone() is None

    view = load_hud_view(conn, now)["gemini"]
    assert view.quota_unknown is True
    assert view.messages_5h == 1
    assert view.messages_7d == 1
    assert view.estimated_tokens_7d == len(prompt_secret) // 4
    assert view.pct_5h == 0 and view.pct_week == 0
    assert view.reset_5h is None and view.reset_week is None
    conn.close()

    reopened = connect_db(path)
    persisted = load_hud_view(reopened, now + timedelta(minutes=1))["gemini"]
    assert persisted.messages_5h == 1 and persisted.messages_7d == 1
    assert persisted.quota_unknown is True
    assert (
        reopened.execute("SELECT COUNT(*) FROM usage_events WHERE provider='claude'").fetchone()[0]
        == 1
    )
    reopened.close()


def test_gemini_limit_messages_rejected_not_guessed():
    now = datetime.now(UTC)
    with pytest.raises(ProtocolError, match="unsupported Gemini quota"):
        parse_message(
            json.dumps(
                {
                    "type": "limit",
                    "provider": "gemini",
                    "scope": "5h",
                    "reset_at": int((now + timedelta(hours=1)).timestamp() * 1000),
                }
            ),
            now,
        )
