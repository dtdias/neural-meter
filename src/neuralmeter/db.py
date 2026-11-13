"""Camada SQLite local do NeuralMeter (schema PRD §6)."""

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from neuralmeter import config

SCHEMA_VERSION = 5
PROVIDERS = ("claude", "chatgpt")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# v2 (aditiva): tetos calibrados por modelo (auto-calibração RF04.2).
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS model_limits (
    provider TEXT NOT NULL,
    model_key TEXT NOT NULL,
    window_hours REAL NOT NULL,
    max_units REAL NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, model_key)
);
"""

SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS quota_snapshots (
    provider TEXT NOT NULL,
    scope TEXT NOT NULL,
    window_name TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    percent REAL NOT NULL CHECK(percent >= 0 AND percent <= 100),
    reset_at TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(provider, scope, window_name, model)
);
"""

SCHEMA_V4 = """
ALTER TABLE quota_snapshots RENAME TO quota_snapshots_unscoped;
CREATE TABLE quota_snapshots (
    provider TEXT NOT NULL,
    account_hash TEXT NOT NULL,
    window_name TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    percent REAL NOT NULL CHECK(percent >= 0 AND percent <= 100),
    reset_at TEXT,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(provider, account_hash, window_name, model)
);
"""

SCHEMA_V5 = """
CREATE TABLE quota_identity_observations (
    provider TEXT NOT NULL,
    account_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(provider, account_hash)
);
"""

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
        if version < 2:
            conn.executescript(SCHEMA_V2)
        if version < 3:
            conn.executescript(SCHEMA_V3)
        if version < 4:
            conn.executescript(SCHEMA_V4)
        if version < 5:
            conn.executescript(SCHEMA_V5)
        if version < SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()


def put_quota_snapshot(
    conn, provider, account_hash, window_name, model, percent, reset_at, observed_at
):
    """Persist provider-reported value; reject stale observations and expired resets."""
    import math

    if (
        provider != "claude"
        or not isinstance(account_hash, str)
        or len(account_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in account_hash)
        or window_name not in ("5h", "weekly", "opus", "sonnet")
    ):
        raise ValueError("invalid quota identity")
    if (window_name in ("5h", "weekly") and model is not None) or (
        window_name in ("opus", "sonnet") and model != window_name
    ):
        raise ValueError("invalid quota model/window combination")
    if (
        not isinstance(percent, (int, float))
        or isinstance(percent, bool)
        or not math.isfinite(percent)
        or not 0 <= percent <= 100
    ):
        raise ValueError("invalid quota percent")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("datetime naive não permitido; use datetime com fuso")
    observed = observed_at.astimezone(UTC).isoformat(sep=" ", timespec="microseconds")
    reset = to_db_ts(reset_at) if reset_at else None
    with _write_lock:
        old = conn.execute(
            "SELECT observed_at, reset_at FROM quota_snapshots "
            "WHERE provider=? AND account_hash=? AND window_name=? AND model=?",
            (provider, account_hash, window_name, model or ""),
        ).fetchone()
        if old and old[0] >= observed:
            return False
        if reset and reset <= observed:
            return False
        if old and old[1] and reset and old[1] > observed and reset < old[1]:
            return False
        if old and old[1] and old[1] > observed:
            if reset is None:
                reset = old[1]
            if reset == old[1]:
                prior = conn.execute(
                    "SELECT percent FROM quota_snapshots WHERE provider=? AND account_hash=? "
                    "AND window_name=? AND model=?",
                    (provider, account_hash, window_name, model or ""),
                ).fetchone()
                if prior and percent < prior[0]:
                    return False
        conn.execute(
            "INSERT INTO quota_snapshots VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(provider,account_hash,window_name,model) DO UPDATE SET "
            "percent=excluded.percent,reset_at=excluded.reset_at,observed_at=excluded.observed_at",
            (provider, account_hash, window_name, model or "", percent, reset, observed),
        )
        conn.commit()
    return True


def observe_quota_identity(conn, provider, account_hash, observed_at):
    """Persist hashed account observation even when response contains no valid quota fields."""
    if (
        provider != "claude"
        or not isinstance(account_hash, str)
        or len(account_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in account_hash)
    ):
        raise ValueError("invalid quota identity")
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("datetime naive não permitido; use datetime com fuso")
    observed = observed_at.astimezone(UTC).isoformat(sep=" ", timespec="microseconds")
    with _write_lock:
        old = conn.execute(
            "SELECT observed_at FROM quota_identity_observations "
            "WHERE provider=? AND account_hash=?",
            (provider, account_hash),
        ).fetchone()
        if old and old[0] >= observed:
            return False
        conn.execute(
            "INSERT INTO quota_identity_observations VALUES(?,?,?) "
            "ON CONFLICT(provider,account_hash) DO UPDATE SET observed_at=excluded.observed_at",
            (provider, account_hash, observed),
        )
        conn.commit()
    return True


def get_quota_snapshots(conn, now):
    """Return explicit fresh/stale/ambiguous state; legacy unscoped data stays quarantined."""
    rows = conn.execute("SELECT * FROM quota_snapshots WHERE provider='claude'").fetchall()
    identities = conn.execute(
        "SELECT account_hash, observed_at FROM quota_identity_observations WHERE provider='claude'"
    ).fetchall()
    active_identities = {
        row["account_hash"]
        for row in identities
        if (observed := datetime.fromisoformat(row["observed_at"]).replace(tzinfo=UTC)) <= now
        and (now - observed).total_seconds() <= 600
    }
    if len(active_identities) > 1:
        return {
            "status": "ambiguous",
            "windows": {},
            "stale_windows": {},
            "account_hash": None,
            "identity_confirmed": False,
        }
    active_account = next(iter(active_identities), None)
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["account_hash"], []).append(dict(row))
    fresh_accounts: dict[str, dict] = {}
    stale_accounts: dict[str, dict] = {}
    for account_hash, account_rows in grouped.items():
        fresh = {}
        stale = {}
        for item in account_rows:
            observed = datetime.fromisoformat(item["observed_at"]).replace(tzinfo=UTC)
            expired = bool(item["reset_at"] and from_db_ts(item["reset_at"]) <= now)
            key = (item["window_name"], item["model"])
            if observed <= now and (now - observed).total_seconds() <= 600 and not expired:
                fresh[key] = item
            else:
                stale[key] = item
        if fresh:
            fresh_accounts[account_hash] = fresh
        if stale:
            stale_accounts[account_hash] = stale
    if active_account and active_account in fresh_accounts:
        account_hash, values = active_account, fresh_accounts[active_account]
        return {
            "status": "fresh",
            "windows": values,
            "stale_windows": stale_accounts.get(account_hash, {}),
            "account_hash": account_hash,
            "identity_confirmed": True,
        }
    if active_account and active_account in stale_accounts:
        return {
            "status": "stale",
            "windows": {},
            "stale_windows": stale_accounts[active_account],
            "account_hash": active_account,
            "identity_confirmed": True,
        }
    if not active_account and len(stale_accounts) > 1:
        return {
            "status": "ambiguous",
            "windows": {},
            "stale_windows": {},
            "account_hash": None,
            "identity_confirmed": False,
        }
    if not active_account and stale_accounts:
        latest_hash, values = max(
            stale_accounts.items(),
            key=lambda pair: max(item["observed_at"] for item in pair[1].values()),
        )
        return {
            "status": "stale",
            "windows": {},
            "stale_windows": values,
            "account_hash": latest_hash,
            "identity_confirmed": False,
        }
    return {
        "status": "unavailable",
        "windows": {},
        "stale_windows": {},
        "account_hash": active_account,
        "identity_confirmed": active_account is not None,
    }


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


def purge_usage_events_before(conn: sqlite3.Connection, cutoff_ts: str) -> int:
    """Apaga eventos com created_at < cutoff_ts ('YYYY-MM-DD HH:MM:SS' UTC). Retorna removidos."""
    with _write_lock:
        cur = conn.execute("DELETE FROM usage_events WHERE created_at < ?", (cutoff_ts,))
        conn.commit()
        return cur.rowcount


def clear_usage_events(conn: sqlite3.Connection) -> int:
    """Apaga todo o histórico de usage_events (mantém provider_state/model_limits)."""
    with _write_lock:
        cur = conn.execute("DELETE FROM usage_events")
        conn.commit()
        return cur.rowcount


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


MAX_LIMIT_UNITS = 100000.0


def get_provider_limits(conn: sqlite3.Connection, provider: str) -> dict[str, float]:
    """Tetos manuais do provedor (5h e semanal) em unidades."""
    state = get_provider_state(conn, provider)
    return {
        "window_5h_max_units": float(state["window_5h_max_units"]),
        "weekly_max_units": float(state["weekly_max_units"]),
    }


def _check_limit(name: str, value) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} inválido: {value!r}")
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} inválido: {value!r}") from exc
    if not (0 < num <= MAX_LIMIT_UNITS):  # também rejeita NaN
        raise ValueError(f"{name} fora do intervalo (0, {MAX_LIMIT_UNITS:g}]: {value!r}")
    return num


def set_provider_limits(
    conn: sqlite3.Connection,
    provider: str,
    *,
    window_5h_max_units: float | None = None,
    weekly_max_units: float | None = None,
) -> None:
    """Atualiza só os tetos informados. Valor <=0, >100000 ou não numérico -> ValueError."""
    _check_provider(provider)
    fields: dict[str, float] = {}
    if window_5h_max_units is not None:
        fields["window_5h_max_units"] = _check_limit("window_5h_max_units", window_5h_max_units)
    if weekly_max_units is not None:
        fields["weekly_max_units"] = _check_limit("weekly_max_units", weekly_max_units)
    if fields:
        update_provider_state(conn, provider, **fields)


def get_model_limits(conn: sqlite3.Connection, provider: str) -> dict[str, dict]:
    """Tetos calibrados por modelo: {model_key: {window_hours, max_units, updated_at}}."""
    _check_provider(provider)
    rows = conn.execute(
        "SELECT model_key, window_hours, max_units, updated_at FROM model_limits "
        "WHERE provider = ?",
        (provider,),
    ).fetchall()
    return {
        r["model_key"]: {
            "window_hours": float(r["window_hours"]),
            "max_units": float(r["max_units"]),
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


def set_model_limit(
    conn: sqlite3.Connection,
    provider: str,
    model_key: str,
    window_hours: float,
    max_units: float,
) -> None:
    """Upsert do teto calibrado de um modelo. max_units fora de (0, 100000] -> ValueError."""
    _check_provider(provider)
    if not isinstance(model_key, str) or not model_key:
        raise ValueError(f"model_key inválido: {model_key!r}")
    hours = _check_limit("window_hours", window_hours)
    units = _check_limit("max_units", max_units)
    with _write_lock:
        conn.execute(
            "INSERT INTO model_limits (provider, model_key, window_hours, max_units, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(provider, model_key) DO UPDATE SET "
            "window_hours = excluded.window_hours, max_units = excluded.max_units, "
            "updated_at = excluded.updated_at",
            (provider, model_key, hours, units, utc_now_str()),
        )
        conn.commit()


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
