"""Aplica mensagens validadas no SQLite e produz resposta + notificação de UI."""

import sqlite3
from datetime import UTC, datetime

from neuralmeter.db import (
    get_provider_state,
    insert_usage_event,
    observe_quota_identity,
    put_quota_snapshot,
)
from neuralmeter.engine import (
    apply_block_calibration,
    is_new_block,
    sync_chatgpt_official_reset,
    sync_claude_official_reset,
    sync_weekly_reset,
)
from neuralmeter.engine.weight import DEFAULT_WEIGHT_PARAMS, WeightParams, weight_for_usage
from neuralmeter.privacy import PrivacySettings, hash_conversation_id
from neuralmeter.server.protocol import (
    Hello,
    Limit,
    Ping,
    Quota,
    QuotaObservation,
    Status,
    Usage,
)


def handle_message(
    conn: sqlite3.Connection,
    msg,
    weight_params: WeightParams = DEFAULT_WEIGHT_PARAMS,
    privacy: PrivacySettings | None = None,
) -> tuple[dict, tuple[str, dict] | None]:
    """Retorna (reply_dict, update|None); update = (kind, payload) p/ a UI.

    Peso Claude por contexto (RF01.3): PREMISSA em progress.md Decisoes (2026-11-10)."""
    if isinstance(msg, Ping):
        return {"type": "pong"}, None
    if isinstance(msg, Quota):
        accepted = put_quota_snapshot(
            conn,
            msg.provider,
            msg.account_hash,
            msg.window,
            msg.model,
            msg.percent,
            msg.reset_at,
            msg.observed_at,
        )
        return {"type": "ack", "ref": "quota", "accepted": accepted}, (
            "quota",
            {"provider": msg.provider},
        ) if accepted else None
    if isinstance(msg, QuotaObservation):
        accepted = observe_quota_identity(conn, msg.provider, msg.account_hash, msg.observed_at)
        return {"type": "ack", "ref": "quota_observation", "accepted": accepted}, (
            "quota",
            {"provider": msg.provider},
        ) if accepted else None
    if isinstance(msg, Usage):
        weight = weight_for_usage(msg.provider, msg.prompt_chars, msg.context_chars, weight_params)
        conv_id = msg.conversation_id
        if privacy is not None and privacy.hash_conversation_ids:
            conv_id = hash_conversation_id(conv_id, privacy.salt)
        insert_usage_event(
            conn,
            msg.provider,
            msg.model,
            conversation_id=conv_id,
            prompt_chars=msg.prompt_chars,
            context_chars=msg.context_chars,
            weight_units=weight,
            created_at=msg.ts,
        )
        update = ("usage", {"provider": msg.provider, "model": msg.model, "weight": weight})
        return {"type": "ack", "ref": "usage"}, update
    if isinstance(msg, Limit):
        prev_state = get_provider_state(conn, msg.provider)
        if msg.scope == "weekly":
            sync_weekly_reset(conn, msg.provider, msg.reset_at, msg.rate_limited)
        elif msg.provider == "claude":
            sync_claude_official_reset(conn, msg.reset_at, msg.rate_limited)
        else:
            sync_chatgpt_official_reset(conn, msg.reset_at, msg.rate_limited)
        calibrated = None
        if msg.rate_limited and is_new_block(prev_state, msg.scope, msg.reset_at):
            calibrated = apply_block_calibration(
                conn, msg.provider, msg.scope, msg.model, prev_state, datetime.now(UTC)
            )
        update = (
            "limit",
            {
                "provider": msg.provider,
                "scope": msg.scope,
                "reset_at": msg.reset_at,
                "rate_limited": msg.rate_limited,
                "calibrated": calibrated,
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
