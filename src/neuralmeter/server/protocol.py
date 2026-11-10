"""Protocolo WS v1 (puro, sem I/O): parsing e validação de mensagens da extensão.

Campos de texto (`text`, `prompt`) nunca são lidos nem persistidos: chaves não
reconhecidas são simplesmente ignoradas.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

MAX_PAYLOAD_BYTES = 65536
PROVIDERS = ("claude", "chatgpt")
SCOPES = ("5h", "weekly")
MAX_MODEL_LEN = 100
_TS_FUTURE_TOLERANCE = timedelta(seconds=60)
_TS_MAX_AGE = timedelta(hours=24)


class ProtocolError(Exception):
    """Mensagem inválida; `str(exc)` é o motivo curto enviado ao cliente."""


@dataclass(frozen=True)
class Hello:
    v: int = 1
    client: str = ""
    version: str = ""


@dataclass(frozen=True)
class Status:
    tabs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    provider: str
    model: str
    conversation_id: str | None
    prompt_chars: int
    context_chars: int
    ts: datetime


@dataclass(frozen=True)
class Limit:
    provider: str
    scope: str
    reset_at: datetime
    rate_limited: bool | None


@dataclass(frozen=True)
class Ping:
    pass


def _provider(d: dict) -> str:
    p = d.get("provider")
    if p not in PROVIDERS:
        raise ProtocolError("invalid provider")
    return p


def _chars(d: dict, key: str, default: int | None = None) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ProtocolError(f"invalid {key}")
    return v


def _epoch_ms(v, key: str) -> datetime:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ProtocolError(f"invalid {key}")
    try:
        return datetime.fromtimestamp(v / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise ProtocolError(f"invalid {key}") from None


def _ts(d: dict, now: datetime) -> datetime:
    v = d.get("ts")
    if v is None:
        return now
    try:
        ts = _epoch_ms(v, "ts")
    except ProtocolError:
        return now
    if ts > now + _TS_FUTURE_TOLERANCE or ts < now - _TS_MAX_AGE:
        return now
    return ts


def parse_message(raw: str | bytes, now: datetime):
    """Valida e converte um frame JSON em dataclass. Levanta ProtocolError."""
    size = len(raw.encode() if isinstance(raw, str) else raw)
    if size > MAX_PAYLOAD_BYTES:
        raise ProtocolError("payload too large")
    try:
        d = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ProtocolError("invalid json") from None
    if not isinstance(d, dict):
        raise ProtocolError("not an object")
    t = d.get("type")
    if t == "ping":
        return Ping()
    if t == "hello":
        v = d.get("v", 1)
        return Hello(
            v=v if isinstance(v, int) and not isinstance(v, bool) else 1,
            client=str(d.get("client", ""))[:100],
            version=str(d.get("version", ""))[:50],
        )
    if t == "status":
        tabs = d.get("tabs", {})
        return Status(tabs=tabs if isinstance(tabs, dict) else {})
    if t == "usage":
        provider = _provider(d)
        model = d.get("model")
        if not isinstance(model, str) or not model or len(model) > MAX_MODEL_LEN:
            raise ProtocolError("invalid model")
        conv = d.get("conversation_id")
        if conv is not None and not isinstance(conv, str):
            raise ProtocolError("invalid conversation_id")
        return Usage(
            provider=provider,
            model=model,
            conversation_id=conv,
            prompt_chars=_chars(d, "prompt_chars"),
            context_chars=_chars(d, "context_chars", 0),
            ts=_ts(d, now),
        )
    if t == "limit":
        provider = _provider(d)
        scope = d.get("scope")
        if scope not in SCOPES:
            raise ProtocolError("invalid scope")
        rl = d.get("rate_limited")
        if rl is not None and not isinstance(rl, bool):
            raise ProtocolError("invalid rate_limited")
        return Limit(
            provider=provider,
            scope=scope,
            reset_at=_epoch_ms(d.get("reset_at"), "reset_at"),
            rate_limited=rl,
        )
    raise ProtocolError("unknown type")


def encode_reply(type: str, **fields) -> str:
    """Serializa resposta App→Ext em JSON compacto."""
    return json.dumps({"type": type, **fields}, separators=(",", ":"))
