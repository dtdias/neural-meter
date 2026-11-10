"""Aplica mensagens validadas no SQLite e produz resposta + notificação de UI."""

import sqlite3

from neuralmeter.db import insert_usage_event
from neuralmeter.engine import (
    sync_chatgpt_official_reset,
    sync_claude_official_reset,
    sync_weekly_reset,
)
from neuralmeter.server.protocol import Hello, Limit, Ping, Status, Usage


def handle_message(conn: sqlite3.Connection, msg) -> tuple[dict, tuple[str, dict] | None]:
    """Retorna (reply_dict, update|None); update = (kind, payload) p/ a UI."""
    if isinstance(msg, Ping):
        return {"type": "pong"}, None
    if isinstance(msg, Usage):
        insert_usage_event(
            conn,
            msg.provider,
            msg.model,
            conversation_id=msg.conversation_id,
            prompt_chars=msg.prompt_chars,
            context_chars=msg.context_chars,
            weight_units=1.0,
            created_at=msg.ts,
        )
        update = ("usage", {"provider": msg.provider, "model": msg.model})
        return {"type": "ack", "ref": "usage"}, update
    if isinstance(msg, Limit):
        if msg.scope == "weekly":
            sync_weekly_reset(conn, msg.provider, msg.reset_at, msg.rate_limited)
        elif msg.provider == "claude":
            sync_claude_official_reset(conn, msg.reset_at, msg.rate_limited)
        else:
            sync_chatgpt_official_reset(conn, msg.reset_at, msg.rate_limited)
        update = (
            "limit",
            {
                "provider": msg.provider,
                "scope": msg.scope,
                "reset_at": msg.reset_at,
                "rate_limited": msg.rate_limited,
            },
        )
        return {"type": "ack", "ref": "limit"}, update
    if isinstance(msg, Hello):
        return {"type": "ack", "ref": "hello"}, (
            "status",
            {"client": msg.client, "version": msg.version},
        )
    if isinstance(msg, Status):
        return {"type": "ack", "ref": "status"}, ("status", {"tabs": msg.tabs})
    raise TypeError(f"unsupported message: {type(msg).__name__}")
