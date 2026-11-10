from datetime import UTC, datetime

import pytest

from neuralmeter import db
from neuralmeter.engine import get_claude_5h_snapshot
from neuralmeter.engine.weight import (
    WeightParams,
    compute_claude_weight,
    weight_for_usage,
)
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import Usage


@pytest.mark.parametrize(
    ("prompt", "ctx", "expected"),
    [
        (0, 0, 1.0),
        (100, 8000, 1.0),
        (0, 16000, 2.0),
        (0, 12000, 1.5),
        (0, 10_000_000, 50.0),
        (None, None, 1.0),
        (None, 16000, 2.0),
        (16000, None, 2.0),
        (16000, 100, 2.0),
    ],
)
def test_compute_claude_weight(prompt, ctx, expected):
    assert compute_claude_weight(prompt, ctx) == expected


def test_custom_params():
    p = WeightParams(base_chars=0, step_chars=1000, max_weight=50.0)
    assert compute_claude_weight(0, 3000, p) == 4.0


def test_chatgpt_always_one():
    assert weight_for_usage("chatgpt", 100_000, 100_000) == 1.0
    assert weight_for_usage("claude", 0, 16000) == 2.0


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    c = db.connect_db()
    yield c
    c.close()


def _usage(provider, ctx):
    return Usage(
        provider=provider,
        model="m",
        conversation_id="c",
        prompt_chars=10,
        context_chars=ctx,
        ts=datetime.now(UTC),
    )


def test_handler_writes_weight_and_payload(conn):
    _, upd = handle_message(conn, _usage("claude", 16000))
    assert upd[1]["weight"] == 2.0
    _, upd2 = handle_message(conn, _usage("chatgpt", 16000))
    assert upd2[1]["weight"] == 1.0
    rows = conn.execute("SELECT provider, weight_units FROM usage_events ORDER BY id").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("claude", 2.0), ("chatgpt", 1.0)]


def test_handler_custom_params(conn):
    p = WeightParams(base_chars=0, step_chars=1000, max_weight=50.0)
    _, upd = handle_message(conn, _usage("claude", 3000), p)
    assert upd[1]["weight"] == 4.0


def test_engine_consumes_weight(conn):
    handle_message(conn, _usage("claude", 16000))
    handle_message(conn, _usage("claude", 16000))
    assert get_claude_5h_snapshot(conn).used_units == 4.0
