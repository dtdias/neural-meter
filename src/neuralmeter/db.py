"""Camada SQLite local do NeuralMeter (schema PRD §6)."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from neuralmeter import config

SCHEMA_VERSION = 1
PROVIDERS = ("claude", "chatgpt")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,              -- 'claude' | 'chatgpt'
    model_name TEXT NOT NULL,            -- ex: 'claude-3-7-sonnet', 'gpt-4o', 'o3-mini'
    conversation_id TEXT,                -- ID da conversa para calcular acúmulo de contexto
    prompt_chars INTEGER DEFAULT 0,      -- Tamanho da mensagem enviada
    context_chars INTEGER DEFAULT 0,     -- Tamanho acumulado estimado da thread
    weight_units REAL DEFAULT 1.0,       -- Peso calculado da interação na cota
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Tabela de estado atual e limites sincronizados por provedor
CREATE TABLE IF NOT EXISTS provider_state (
    provider TEXT PRIMARY KEY,           -- 'claude' | 'chatgpt'
    plan_tier TEXT DEFAULT 'pro',        -- 'free' | 'pro' | 'plus' | 'max'
    window_5h_max_units REAL DEFAULT 45.0,
    window_5h_reset_at DATETIME,         -- Timestamp oficial caso reportado pela API
    weekly_max_units REAL DEFAULT 500.0,
    weekly_reset_at DATETIME,
    is_rate_limited INTEGER DEFAULT 0,   -- 0 = Liberado, 1 = Bloqueado no momento
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Índices para consultas rápidas de janelas de tempo
CREATE INDEX IF NOT EXISTS idx_usage_provider_time
ON usage_events (provider, created_at);
"""

STATE_FIELDS = frozenset(
    {
        "plan_tier",
        "window_5h_max_units",
        "window_5h_reset_at",
        "weekly_max_units",
        "weekly_reset_at",
        "is_rate_limited",
    }
)

_write_lock = threading.Lock()


def to_db_ts(dt: datetime) -> str:
    """datetime com fuso -> 'YYYY-MM-DD HH:MM:SS' em UTC. Naive -> ValueError."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("datetime naive não permitido; use datetime com fuso")
    return dt.astimezone(UTC).strftime(TS_FORMAT)


def from_db_ts(s: str | None) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC) -> datetime com fuso UTC. None -> None."""
    if s is None:
        return None
    return datetime.strptime(s, TS_FORMAT).replace(tzinfo=UTC)


def utc_now_str() -> str:
    return to_db_ts(datetime.now(UTC))


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"provider inválido: {provider!r}; esperado um de {PROVIDERS}")


def migrate(conn: sqlite3.Connection) -> None:
    """Aplica migrações pendentes via PRAGMA user_version. Idempotente."""
    with _write_lock:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.executescript(SCHEMA_V1)
            conn.executemany(
                "INSERT OR IGNORE INTO provider_state (provider) VALUES (?)",
                [(p,) for p in PROVIDERS],
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()


def connect_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Abre (e migra) o banco local. Padrão: config.get_db_path()."""
    db_path = config.get_db_path() if path is None else path
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def insert_usage_event(
    conn: sqlite3.Connection,
    provider: str,
    model_name: str,
    *,
    conversation_id: str | None = None,
    prompt_chars: int = 0,
    context_chars: int = 0,
    weight_units: float = 1.0,
    created_at: datetime | None = None,
) -> int:
    """Grava um evento de mensagem (só contagens, nunca o texto). Retorna o id."""
    _check_provider(provider)
    ts = utc_now_str() if created_at is None else to_db_ts(created_at)
    with _write_lock:
        cur = conn.execute(
            "INSERT INTO usage_events (provider, model_name, conversation_id, prompt_chars, "
            "context_chars, weight_units, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, model_name, conversation_id, prompt_chars, context_chars, weight_units, ts),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_events_since(
    conn: sqlite3.Connection,
    provider: str,
    since: datetime,
    model_name: str | None = None,
) -> list[sqlite3.Row]:
    """Eventos do provedor com created_at >= since, ordem ASC."""
    _check_provider(provider)
    sql = "SELECT * FROM usage_events WHERE provider = ? AND created_at >= ?"
    params: list = [provider, to_db_ts(since)]
    if model_name is not None:
        sql += " AND model_name = ?"
        params.append(model_name)
    sql += " ORDER BY created_at ASC, id ASC"
    return conn.execute(sql, params).fetchall()


def get_provider_state(conn: sqlite3.Connection, provider: str) -> dict:
    _check_provider(provider)
    row = conn.execute("SELECT * FROM provider_state WHERE provider = ?", (provider,)).fetchone()
    if row is None:
        raise KeyError(provider)
    return dict(row)


def update_provider_state(conn: sqlite3.Connection, provider: str, /, **fields) -> None:
    """Atualiza colunas permitidas de provider_state e updated_at."""
    _check_provider(provider)
    unknown = set(fields) - STATE_FIELDS
    if unknown:
        raise ValueError(f"colunas inválidas: {sorted(unknown)}")
    cols = [*fields, "updated_at"]
    values = [*fields.values(), utc_now_str()]
    assignments = ", ".join(f"{c} = ?" for c in cols)
    with _write_lock:
        conn.execute(
            f"UPDATE provider_state SET {assignments} WHERE provider = ?",  # noqa: S608
            [*values, provider],
        )
        conn.commit()
