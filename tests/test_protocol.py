import json
from datetime import UTC, datetime, timedelta

import pytest

from neuralmeter.server.protocol import (
    Limit,
    Ping,
    ProtocolError,
    Usage,
    encode_reply,
    parse_message,
)

NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def ms(dt):
    return int(dt.timestamp() * 1000)


def usage(**kw):
    d = {"type": "usage", "provider": "claude", "model": "sonnet", "prompt_chars": 10}
    d.update(kw)
    return json.dumps(d)


def test_usage_valid():
    m = parse_message(usage(context_chars=50, conversation_id="c1", ts=ms(NOW)), NOW)
    assert isinstance(m, Usage)
    assert (m.provider, m.model, m.prompt_chars, m.context_chars) == ("claude", "sonnet", 10, 50)
    assert m.conversation_id == "c1"
    assert m.ts == NOW


def test_usage_bytes():
    assert isinstance(parse_message(usage().encode(), NOW), Usage)


@pytest.mark.parametrize(
    "ts",
    [None, ms(NOW + timedelta(minutes=5)), ms(NOW - timedelta(days=2)), "abc"],
)
def test_ts_fallback_now(ts):
    raw = usage() if ts is None else usage(ts=ts)
    assert parse_message(raw, NOW).ts == NOW


def test_ts_recent_kept():
    t = NOW - timedelta(minutes=3)
    assert parse_message(usage(ts=ms(t)), NOW).ts == t


@pytest.mark.parametrize(
    "raw",
    [
        "{bad",
        "[1]",
        '"x"',
        json.dumps({"type": "nope"}),
        json.dumps({}),
        usage(provider="gemini"),
        usage(prompt_chars=-1),
        usage(prompt_chars="3"),
        usage(prompt_chars=True),
        usage(model=""),
        usage(model="x" * 101),
        usage(model=5),
        json.dumps({"type": "limit", "provider": "claude", "scope": "1h", "reset_at": 1}),
        json.dumps({"type": "limit", "provider": "claude", "scope": "5h"}),
        "x" * 70000,
    ],
)
def test_invalid(raw):
    with pytest.raises(ProtocolError):
        parse_message(raw, NOW)


def test_limit_utc():
    raw = json.dumps(
        {
            "type": "limit",
            "provider": "chatgpt",
            "scope": "weekly",
            "reset_at": ms(NOW),
            "rate_limited": True,
        }
    )
    m = parse_message(raw, NOW)
    assert isinstance(m, Limit)
    assert m.reset_at == NOW and m.reset_at.tzinfo is not None
    assert m.rate_limited is True


def test_limit_model_optional():
    base = {"type": "limit", "provider": "chatgpt", "scope": "5h", "reset_at": ms(NOW)}
    assert parse_message(json.dumps(base), NOW).model is None
    assert parse_message(json.dumps({**base, "model": "o3"}), NOW).model == "o3"
    for bad in (5, "", "x" * 101, ["o3"], True):
        with pytest.raises(ProtocolError):
            parse_message(json.dumps({**base, "model": bad}), NOW)


def test_extra_keys_and_text_ignored():
    m = parse_message(usage(prompt="secret", text="secret", extra=1), NOW)
    assert isinstance(m, Usage)
    assert "secret" not in repr(m)


def test_ping_and_reply():
    assert isinstance(parse_message('{"type":"ping"}', NOW), Ping)
    assert encode_reply("ack", ref="usage") == '{"type":"ack","ref":"usage"}'
