"""Secure, loopback-only dashboard and owned read-only CLI source workers."""

from __future__ import annotations

import hmac
import json
import math
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from neuralmeter.config import get_data_dir
from neuralmeter.ui.settings import load_settings, save_settings

SOURCES = ("claude", "codex", "antigravity_cli")
CONSENT_KEYS = {
    "claude": "claude_cli_enabled",
    "codex": "codex_cli_enabled",
    "antigravity_cli": "antigravity_cli_usage_enabled",
}
_KNOWN_MODELS = {"Modelo não identificado"}
_STATUS_LABELS = {
    "available": "Dados disponíveis",
    "partial": "Cobertura parcial",
    "no_local_data": "Nenhum histórico local",
    "unavailable": "Fonte indisponível",
    "consent_required": "Autorização necessária",
    "cli_not_installed": "CLI não detectada",
    "not_authenticated": "Sessão não autenticada",
    "unsupported_auth": "Sessão incompatível",
    "quota_unavailable": "Quota indisponível",
    "request_failed": "Falha de consulta",
    "rate_limited": "Aguardando limite de consulta",
    "timeout": "Consulta expirou",
    "not_connected": "Desconectado",
    "cancelled": "Leitura cancelada",
    "unknown": "Desconhecido",
    "authenticated": "Sessão autenticada",
    "not_checked": "Não verificada",
}
_ALLOWED_STATUSES = frozenset(_STATUS_LABELS) | {
    "authenticated",
    "identity_changed",
    "ide_not_found",
    "server_not_found",
    "not_connected",
}
SOURCE_SNAPSHOT_TTL_SECONDS = 120.0
SOURCE_POLL_INTERVAL_SECONDS = 60.0
RATE_LIMIT_FALLBACK_SECONDS = 60.0
RATE_LIMIT_MAX_SECONDS = 3600.0
_SOURCE_DATA_FIELDS = frozenset(
    {"quotas", "models", "periods", "totals", "coverage_partial", "observed_at"}
)
_SOURCE_METADATA_FIELDS = frozenset(
    {"status", "status_label", "consent", "installed", "auth_status"}
)
_SOURCE_TOTAL_FIELDS = (
    "input_tokens",
    "output_tokens",
    "calculated_input_tokens",
    "calculated_output_tokens",
)

_WEB_ASSET_DIR = Path(__file__).parent / "web_assets"
_STATIC_ASSETS = {
    "/": (_WEB_ASSET_DIR / "index.html", "text/html; charset=utf-8", 64 * 1024),
    "/style.css": (_WEB_ASSET_DIR / "style.css", "text/css; charset=utf-8", 64 * 1024),
    "/app.js": (_WEB_ASSET_DIR / "app.js", "text/javascript; charset=utf-8", 64 * 1024),
}
_STATIC_BYTES = {}
for _route, (_asset, _mime, _limit) in _STATIC_ASSETS.items():
    _content = _asset.read_bytes()
    if len(_content) > _limit:
        raise RuntimeError("Bundled dashboard asset exceeds size limit")
    _STATIC_BYTES[_route] = (_content, _mime)
_ICON_FILES = {
    name: _WEB_ASSET_DIR / "icons" / filename
    for name, filename in {
        "mark": "mark.svg",
        "dashboard": "dashboard.svg",
        "usage": "usage.svg",
        "refresh": "refresh.svg",
    }.items()
}
_ICONS = {}
for _icon_name, _icon_path in _ICON_FILES.items():
    _icon = _icon_path.read_bytes()
    if len(_icon) > 16 * 1024 or any(
        bad in _icon.lower()
        for bad in (b"<script", b"javascript:", b"<foreignobject", b"onload=", b'href="http')
    ):
        raise RuntimeError("Bundled SVG failed static safety validation")
    _ICONS[_icon_name] = _icon


def _status_label(status: object) -> str:
    if not isinstance(status, str):
        return "Desconhecido"
    return _STATUS_LABELS.get(status, "Desconhecido")


def _status_code(status: object) -> str:
    return status if isinstance(status, str) and status in _ALLOWED_STATUSES else "unknown"


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) and value.tzinfo else None


def _finite_percent(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not 0 <= value <= 100:
        return None
    return value


def _quota_window(percent: object, reset_at: object, now: datetime) -> dict:
    reset = reset_at if isinstance(reset_at, datetime) and reset_at.tzinfo else None
    if reset is not None:
        reset = reset.astimezone(UTC)
    valid_percent = _finite_percent(percent)
    expired = reset is not None and reset <= now
    return {
        "percent": None if expired else valid_percent,
        "reset_at": _iso(reset),
        "expired": expired,
    }


def _count(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**15
        else None
    )


def _safe_model(value: object) -> str:
    from neuralmeter.model_catalog import canonical_public_model_id

    return canonical_public_model_id(value) or "Modelo não identificado"


def _empty_usage() -> dict:
    return {
        "status": "unknown",
        "models": [],
        "periods": {},
        "coverage_partial": False,
        "observed_at": None,
        "consent": False,
    }


def _empty_source_payload() -> dict:
    return {
        "quotas": [],
        "models": [],
        "periods": {},
        "totals": dict.fromkeys(_SOURCE_TOTAL_FIELDS),
        "coverage_partial": False,
        "observed_at": None,
    }


def _clear_source_payload(source: dict) -> None:
    """Reset source state to known fields, preserving only connection metadata."""
    metadata = {
        "status": "unknown",
        "status_label": _status_label("unknown"),
        "consent": source.get("consent") is True,
        "installed": source.get("installed")
        if source.get("installed") is None or isinstance(source.get("installed"), bool)
        else None,
        "auth_status": "unknown",
    }
    source.clear()
    source.update(metadata)
    source.update(_empty_source_payload())


def _bounded_retry_delay(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return RATE_LIMIT_FALLBACK_SECONDS
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return RATE_LIMIT_FALLBACK_SECONDS
    if not math.isfinite(seconds) or seconds <= 0:
        return RATE_LIMIT_FALLBACK_SECONDS
    return min(RATE_LIMIT_MAX_SECONDS, max(RATE_LIMIT_FALLBACK_SECONDS, seconds))


def _new_snapshot() -> dict:
    return {
        "sources": {
            source: {
                "status": "unknown",
                "status_label": "Desconhecido",
                "consent": False,
                "installed": None,
                "auth_status": "unknown",
                "observed_at": None,
                "quotas": [],
                "models": [],
                "periods": {},
                "totals": dict.fromkeys(_SOURCE_TOTAL_FIELDS),
                "coverage_partial": False,
            }
            for source in SOURCES
        }
    }


def _claude_dto(value) -> dict:
    now = datetime.now(UTC)
    windows = []
    for title, window in (("5 horas", value.five_hour), ("7 dias", value.seven_day)):
        windows.append(
            {
                "label": title,
                **_quota_window(window.percent, window.reset_at, now),
            }
        )
    return {
        "status": _status_code(value.status),
        "status_label": _status_label(_status_code(value.status)),
        "consent": True,
        "installed": value.status != "cli_not_installed",
        "auth_status": _status_label(value.auth_status),
        "observed_at": _iso(value.observed_at),
        "quotas": windows,
    }


def _codex_dto(value) -> dict:
    now = datetime.now(UTC)
    windows = []
    for item in value.windows[:256]:
        scope = item.scope if item.scope in {"primary", "secondary"} else None
        if scope is None:
            continue
        windows.append(
            {
                "label": "Principal" if scope == "primary" else "Secundária",
                **_quota_window(item.percent, item.reset_at, now),
            }
        )
    return {
        "status": _status_code(value.status),
        "status_label": _status_label(_status_code(value.status)),
        "consent": True,
        "installed": value.status != "cli_not_installed",
        "auth_status": "Sessão identificada"
        if value.status == "available"
        else _status_label(value.status),
        "observed_at": _iso(value.observed_at),
        "quotas": windows,
        "coverage_partial": bool(value.coverage_partial),
    }


@dataclass(frozen=True)
class _SourceRead:
    value: object
    local_identity_before: str | None = None
    local_identity_after: str | None = None
    account_identity: str | None = None
    identity_epoch: int = 0


def _private_fingerprint(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(char not in "0123456789abcdef" for char in value.casefold()):
        return None
    return value.casefold()


def _agy_dto(value: dict) -> dict:
    result = _empty_usage()
    status = _status_code(value.get("status"))
    result.update(
        {
            "status": status,
            "status_label": _status_label(status),
            "consent": True,
            "coverage_partial": bool(value.get("coverage_partial")),
            "observed_at": None,
        }
    )

    def model_rows(rows: object) -> list[dict]:
        if not isinstance(rows, list):
            return []
        safe = []
        for row in rows[:256]:
            if not isinstance(row, dict):
                continue
            safe.append(
                {
                    "model": _safe_model(row.get("model")),
                    "input_tokens": _count(row.get("input_tokens")),
                    "output_tokens": _count(row.get("output_tokens")),
                    "cache_read_tokens": _count(row.get("cache_read_tokens")),
                    "cache_write_tokens": _count(row.get("cache_write_tokens")),
                    "calculated_input_tokens": _count(row.get("calculated_input_tokens")) or 0,
                    "calculated_output_tokens": _count(row.get("calculated_output_tokens")) or 0,
                    "calculated_input_rows": _count(row.get("calculated_input_rows")) or 0,
                    "calculated_output_rows": _count(row.get("calculated_output_rows")) or 0,
                    "coverage_partial": bool(row.get("coverage_partial")),
                    "unknown_timestamp_rows": _count(row.get("unknown_timestamp_rows")) or 0,
                }
            )
        return safe

    result["models"] = model_rows(value.get("models"))
    periods = value.get("periods")
    if isinstance(periods, dict):
        for name in ("today", "last_7_days", "history"):
            period = periods.get(name)
            if isinstance(period, dict):
                result["periods"][name] = {
                    "models": model_rows(period.get("models")),
                    "partial": bool(period.get("partial")),
                    "coverage_partial": bool(period.get("coverage_partial")),
                    "boundary_start_local": period.get("boundary_start_local")
                    if isinstance(period.get("boundary_start_local"), str)
                    else None,
                    "boundary_end_local": period.get("boundary_end_local")
                    if isinstance(period.get("boundary_end_local"), str)
                    else None,
                    "observed_start": period.get("observed_start")
                    if isinstance(period.get("observed_start"), str)
                    else None,
                    "observed_end": period.get("observed_end")
                    if isinstance(period.get("observed_end"), str)
                    else None,
                    "range_is_complete": False,
                }
    result["totals"] = {
        "input_tokens": sum(row["input_tokens"] or 0 for row in result["models"])
        if result["models"] and all(row["input_tokens"] is not None for row in result["models"])
        else None,
        "output_tokens": sum(row["output_tokens"] or 0 for row in result["models"])
        if result["models"] and all(row["output_tokens"] is not None for row in result["models"])
        else None,
        "calculated_input_tokens": sum(row["calculated_input_tokens"] for row in result["models"]),
        "calculated_output_tokens": sum(
            row["calculated_output_tokens"] for row in result["models"]
        ),
    }
    return result


class _OwnedSource:
    """One generation-fenced worker per source; never more than one active reader."""

    def __init__(
        self,
        source: str,
        reader,
        publish,
        interval: float = 60,
        identity_reader=None,
        observe_identity=None,
        monotonic=None,
    ):
        self.source = source
        self.reader = reader
        self.publish = publish
        self.identity_reader = identity_reader
        self.observe_identity = observe_identity
        self.interval = interval
        self.monotonic = monotonic or time.monotonic
        self.condition = threading.Condition()
        self.enabled = False
        self.generation = 0
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.refresh_requested = False
        self.rate_limited_until = 0.0
        self.next_read_at = 0.0
        self.read_inflight = False
        self.thread = threading.Thread(target=self._run, name=f"neuralmeter-{source}", daemon=False)

    def start(self) -> None:
        self.thread.start()

    def set_enabled(self, enabled: bool) -> int:
        with self.condition:
            if self.enabled == enabled:
                return self.generation
            self.generation += 1
            self.enabled = enabled
            if enabled:
                # New generation owns new event; old in-flight reader keeps cancelled event.
                self.cancel_event = threading.Event()
                self.refresh_requested = True
            else:
                self.cancel_event.set()
                self.refresh_requested = False
            generation = self.generation
            self.condition.notify_all()
        return generation

    def refresh(self) -> bool:
        with self.condition:
            if (
                not self.enabled
                or self.read_inflight
                or self.monotonic() < max(self.rate_limited_until, self.next_read_at)
            ):
                return False
            self.refresh_requested = True
            self.condition.notify_all()
            return True

    def current_generation(self) -> int:
        with self.condition:
            return self.generation

    def is_enabled(self) -> bool:
        with self.condition:
            return self.enabled

    def close(self) -> None:
        with self.condition:
            self.generation += 1
            self.enabled = False
            self.stop_event.set()
            self.cancel_event.set()
            self.condition.notify_all()

    def _run(self) -> None:
        while True:
            with self.condition:
                while True:
                    if self.stop_event.is_set():
                        return
                    if not self.enabled or not self.refresh_requested or self.read_inflight:
                        self.condition.wait()
                        continue
                    deadline = max(self.rate_limited_until, self.next_read_at)
                    remaining = deadline - self.monotonic()
                    if remaining > 0:
                        self.condition.wait(timeout=remaining)
                        continue
                    break
                generation = self.generation
                read_cancel_event = self.cancel_event
                self.refresh_requested = False
                self.read_inflight = True
                self.next_read_at = max(self.next_read_at, self.monotonic()) + self.interval
            identity_before: str | None = None
            identity_after: str | None = None
            identity_epoch = 0
            try:
                if self.identity_reader is not None:
                    identity_before = _private_fingerprint(self.identity_reader())
                    identity_epoch = self.observe_identity(self.source, identity_before, generation)
                    if identity_epoch is None:
                        continue
                result = self.reader(read_cancel_event)
                result_status = (
                    result.get("status")
                    if isinstance(result, dict)
                    else getattr(result, "status", None)
                )
                if result_status == "rate_limited":
                    delay = _bounded_retry_delay(getattr(result, "retry_after", None))
                    with self.condition:
                        self.rate_limited_until = max(
                            self.rate_limited_until, self.monotonic() + delay
                        )
                        self.condition.notify_all()
                dto = result
                if self.source == "claude":
                    dto = _claude_dto(result)
                elif self.source == "codex":
                    dto = _codex_dto(result)
                else:
                    dto = _agy_dto(result)
                with self.condition:
                    current = (
                        not self.stop_event.is_set()
                        and self.enabled
                        and self.generation == generation
                        and not read_cancel_event.is_set()
                    )
                if not current:
                    continue
                identity_after = identity_before
                if self.identity_reader is not None:
                    identity_after = _private_fingerprint(self.identity_reader())
                    after_epoch = self.observe_identity(self.source, identity_after, generation)
                    if after_epoch is None:
                        continue
                    if after_epoch != identity_epoch or identity_after != identity_before:
                        continue
                with self.condition:
                    current = (
                        not self.stop_event.is_set()
                        and self.enabled
                        and self.generation == generation
                        and not read_cancel_event.is_set()
                    )
                if current:
                    self.publish(
                        self.source,
                        _SourceRead(
                            dto,
                            identity_before,
                            identity_after,
                            _private_fingerprint(getattr(result, "identity", None)),
                            identity_epoch,
                        ),
                        generation,
                    )
            except Exception:
                with self.condition:
                    should_report = (
                        not self.stop_event.is_set()
                        and self.enabled
                        and self.generation == generation
                        and not read_cancel_event.is_set()
                    )
                if should_report:
                    self.publish(
                        self.source,
                        _SourceRead(
                            {"status": "request_failed", "status_label": "Falha de consulta"},
                            identity_epoch=identity_epoch,
                        ),
                        generation,
                    )
            finally:
                with self.condition:
                    self.read_inflight = False
                    if (
                        self.enabled
                        and self.generation == generation
                        and not self.stop_event.is_set()
                    ):
                        self.refresh_requested = True
                    self.condition.notify_all()


class _SourceService:
    def __init__(self, data_dir: Path, *, monotonic=None):
        self.data_dir = data_dir
        self.monotonic = monotonic or time.monotonic
        self.settings_lock = threading.RLock()
        self.snapshot_lock = threading.RLock()
        self.settings = load_settings(data_dir / "settings.json")
        self.snapshot = _new_snapshot()
        self.generations = {source: 0 for source in SOURCES}
        self.snapshot_observed_mono: dict[str, float | None] = {source: None for source in SOURCES}
        self.identity_epochs = {source: 0 for source in SOURCES}
        self.identity_baselines: dict[str, str | None] = {source: None for source in SOURCES}
        self.identity_initialized = {source: False for source in SOURCES}
        self.account_identities: dict[str, str | None] = {source: None for source in SOURCES}
        self.identity_readers = self._identity_readers()
        self.identity_stop = threading.Event()
        self.workers: dict[str, _OwnedSource] = {}
        self.closed = False
        self.identity_thread: threading.Thread | None = None
        readers = self._readers()
        for source in SOURCES:
            worker = _OwnedSource(
                source,
                readers[source],
                self._publish,
                interval=SOURCE_POLL_INTERVAL_SECONDS,
                identity_reader=self.identity_readers.get(source),
                observe_identity=self._observe_identity,
                monotonic=self.monotonic,
            )
            self.workers[source] = worker
        try:
            for worker in self.workers.values():
                worker.start()
            # Restore literal persisted opt-ins once, without rewriting settings.
            for source, key in CONSENT_KEYS.items():
                if self.settings.get(key) is True:
                    self.set_consent(source, True, persist=False)
            self.identity_thread = threading.Thread(
                target=self._identity_monitor, name="neuralmeter-identity", daemon=False
            )
            self.identity_thread.start()
        except BaseException:
            self.closed = True
            self.identity_stop.set()
            for worker in self.workers.values():
                worker.close()
            for worker in self.workers.values():
                if worker.thread.ident is not None:
                    worker.thread.join()
            if self.identity_thread is not None and self.identity_thread.ident is not None:
                self.identity_thread.join()
            raise

    @staticmethod
    def _readers() -> dict[str, object]:
        from neuralmeter.antigravity_usage import read_antigravity_usage
        from neuralmeter.claude_cli import read_usage
        from neuralmeter.codex_cli import read_codex_quota

        def claude(_cancel):
            return read_usage(timeout=5)

        def codex(cancel):
            return read_codex_quota(cancel_event=cancel)

        def agy(cancel):
            return read_antigravity_usage(
                consent_granted=True,
                cancel_event=cancel,
            )

        return {"claude": claude, "codex": codex, "antigravity_cli": agy}

    @staticmethod
    def _identity_readers() -> dict[str, object]:
        from neuralmeter.claude_cli import credential_identity
        from neuralmeter.codex_cli import codex_auth_metadata_identity

        return {"claude": credential_identity, "codex": codex_auth_metadata_identity}

    def _observe_identity(self, source: str, identity: str | None, generation: int) -> int | None:
        worker = self.workers[source]
        with worker.condition:
            if not worker.enabled or worker.generation != generation:
                return None
        with self.snapshot_lock:
            if self.closed or self.generations[source] != generation:
                return None
            changed = self.identity_initialized[source] and (
                self.identity_baselines[source] != identity
            )
            if not self.identity_initialized[source]:
                self.identity_initialized[source] = True
            if changed:
                self.identity_epochs[source] += 1
                self.identity_baselines[source] = identity
                self.account_identities[source] = None
                current = self.snapshot["sources"][source]
                _clear_source_payload(current)
                current.update(
                    {
                        "status": "unknown",
                        "status_label": "Identidade da sessão alterada",
                        "auth_status": "unknown",
                    }
                )
                self.snapshot_observed_mono[source] = None
            else:
                self.identity_baselines[source] = identity
            return self.identity_epochs[source]

    def _identity_monitor(self) -> None:
        while not self.identity_stop.wait(2.5):
            for source, getter in self.identity_readers.items():
                worker = self.workers[source]
                with worker.condition:
                    enabled, generation = worker.enabled, worker.generation
                if not enabled:
                    continue
                try:
                    identity = _private_fingerprint(getter())
                except Exception:
                    identity = None
                self._observe_identity(source, identity, generation)

    def _publish(self, source: str, result: object, generation: int) -> None:
        read = result if isinstance(result, _SourceRead) else _SourceRead(result)
        dto = read.value
        if not isinstance(dto, dict):
            return
        worker = self.workers[source]
        with worker.condition:
            current_worker = worker.enabled and worker.generation == generation
        if not current_worker:
            return
        with self.snapshot_lock:
            if (
                self.closed
                or self.generations[source] != generation
                or read.identity_epoch != self.identity_epochs[source]
            ):
                return
            if read.account_identity is not None:
                previous_identity = self.account_identities[source]
                if previous_identity is not None and previous_identity != read.account_identity:
                    current = self.snapshot["sources"][source]
                    _clear_source_payload(current)
                    current.update(
                        {
                            "status": "unknown",
                            "status_label": _status_label("unknown"),
                            "auth_status": "unknown",
                        }
                    )
                self.account_identities[source] = read.account_identity
            if dto.get("status") == "identity_changed":
                _clear_source_payload(self.snapshot["sources"][source])
                dto = {
                    **dto,
                    **_empty_source_payload(),
                    "auth_status": "unknown",
                }
                self.snapshot_observed_mono[source] = None
            data_status = dto.get("status") in (
                {"available", "partial"} if source == "antigravity_cli" else {"available"}
            )
            observed_text = dto.get("observed_at")
            if source != "antigravity_cli":
                try:
                    observed_dt = datetime.fromisoformat(observed_text.replace("Z", "+00:00"))
                    data_status = data_status and observed_dt.tzinfo is not None
                except (AttributeError, ValueError):
                    data_status = False
            if not data_status:
                _clear_source_payload(self.snapshot["sources"][source])
                dto = {
                    **dto,
                    **_empty_source_payload(),
                }
                if not isinstance(dto.get("auth_status"), str):
                    dto["auth_status"] = "unknown"
                if source != "antigravity_cli" and dto.get("status") == "available":
                    dto["status"] = "unknown"
                    dto["status_label"] = _status_label("unknown")
                self.snapshot_observed_mono[source] = None
            elif dto.get("status") not in {"unknown", "identity_changed"}:
                self.snapshot_observed_mono[source] = self.monotonic()
            self.snapshot["sources"][source].update(dto)

    def set_consent(self, source: str, enabled: bool, persist: bool = True) -> None:
        if source not in CONSENT_KEYS or type(enabled) is not bool:
            raise ValueError("invalid consent")
        with self.settings_lock:
            if self.closed:
                raise RuntimeError("service closed")
            worker = self.workers[source]
            currently_enabled = self.settings.get(CONSENT_KEYS[source]) is True
            with worker.condition:
                runtime_enabled = worker.enabled
            if currently_enabled == enabled and runtime_enabled == enabled:
                return
            if persist and currently_enabled != enabled:
                self.settings[CONSENT_KEYS[source]] = enabled
                save_settings(self.settings, self.data_dir / "settings.json")
            worker_generation = worker.current_generation()
            changing_runtime = runtime_enabled != enabled
            generation = worker_generation + 1 if changing_runtime else worker_generation
            with self.snapshot_lock:
                self.generations[source] = generation
                if enabled:
                    current = self.snapshot["sources"][source]
                    current["consent"] = True
                    if changing_runtime:
                        _clear_source_payload(current)
                        current.update(
                            {
                                "status": "unknown",
                                "status_label": "Consultando fonte",
                                "auth_status": "unknown",
                            }
                        )
                else:
                    clear = _new_snapshot()["sources"][source]
                    clear.update(
                        {
                            "consent": False,
                            "status": "not_connected",
                            "status_label": "Opt-in desativado",
                        }
                    )
                    self.snapshot["sources"][source] = clear
                    self.snapshot_observed_mono[source] = None
            worker_generation = (
                worker.set_enabled(enabled) if changing_runtime else worker_generation
            )
            if worker_generation != generation:
                raise RuntimeError("collector generation changed unexpectedly")

    def refresh(self, source: str) -> bool:
        if source == "all":
            accepted = [self.workers[name].refresh() for name in SOURCES]
            return any(accepted)
        if source not in SOURCES:
            return False
        return self.workers[source].refresh()

    def get_snapshot(self) -> dict:
        with self.snapshot_lock:
            now = self.monotonic()
            wall_now = datetime.now(UTC)
            for source in SOURCES:
                observed = self.snapshot_observed_mono[source]
                if observed is not None and now - observed > SOURCE_SNAPSHOT_TTL_SECONDS:
                    current = self.snapshot["sources"][source]
                    _clear_source_payload(current)
                    current.update(
                        {
                            "status": "unknown",
                            "status_label": _status_label("unknown"),
                            "auth_status": "unknown",
                        }
                    )
                    self.snapshot_observed_mono[source] = None
                else:
                    current = self.snapshot["sources"][source]
                    for quota in current.get("quotas", []):
                        reset_raw = quota.get("reset_at")
                        try:
                            reset = datetime.fromisoformat(reset_raw.replace("Z", "+00:00"))
                        except (AttributeError, ValueError):
                            reset = None
                        if reset is not None and reset.tzinfo and reset.astimezone(UTC) <= wall_now:
                            quota.update({"percent": None, "expired": True})
            result = json.loads(json.dumps(self.snapshot, ensure_ascii=False))
        with self.settings_lock:
            for source in SOURCES:
                consent = self.settings.get(CONSENT_KEYS[source]) is True
                result["sources"][source]["consent"] = consent
        return result

    def close(self) -> None:
        with self.settings_lock:
            if self.closed:
                return
            self.closed = True
            self.identity_stop.set()
            for worker in self.workers.values():
                worker.close()
        for worker in self.workers.values():
            worker.thread.join()
        if self.identity_thread is not None and self.identity_thread.ident is not None:
            self.identity_thread.join()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False

    def __init__(self, port: int, service: _SourceService):
        self.service = service
        self.csrf = secrets.token_urlsafe(32)
        self.shutdown_requested = threading.Event()
        self._request_slots = threading.BoundedSemaphore(16)
        super().__init__(("127.0.0.1", port), _Handler)
        self.timeout = 1

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _send(self, status: int, content_type: str, body: bytes, cookie: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        if cookie:
            self.send_header(
                "Set-Cookie",
                f"nm_session={self.server.csrf}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200",
            )
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self, *, mutation: bool = False) -> bool:
        expected_host = f"127.0.0.1:{self.server.server_port}"
        expected_origin = f"http://{expected_host}"
        if self.headers.get("Host", "") != expected_host:
            return False
        origin = self.headers.get("Origin")
        if mutation:
            if origin != expected_origin:
                return False
        elif origin is not None and origin != expected_origin:
            return False
        fetch_site = self.headers.get("Sec-Fetch-Site", "same-origin")
        if mutation and fetch_site != "same-origin":
            return False
        if fetch_site == "cross-site":
            return False
        return fetch_site != "none" or (not mutation and origin is None)

    def _session(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        return any(part.strip() == f"nm_session={self.server.csrf}" for part in cookie.split(";"))

    def _json(self, status: int, payload: dict, cookie: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body, cookie)

    def do_GET(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
        if lengths or transfer_encodings:
            self.close_connection = True
            return self._send(400, "text/plain; charset=utf-8", b"invalid framing")
        if not self._trusted():
            return self._send(403, "text/plain; charset=utf-8", b"forbidden")
        if not self.path.startswith("/") or self.path.startswith("//"):
            return self._send(400, "text/plain; charset=utf-8", b"bad request")
        path = urlsplit(self.path).path
        if path in _STATIC_BYTES:
            body, kind = _STATIC_BYTES[path]
            return self._send(200, kind, body)
        if path.startswith("/assets/icons/"):
            name = path.removeprefix("/assets/icons/").removesuffix(".svg")
            if name in _ICONS and path == f"/assets/icons/{name}.svg":
                return self._send(200, "image/svg+xml; charset=utf-8", _ICONS[name])
        if path == "/api/bootstrap":
            return self._json(200, {"csrf": self.server.csrf}, cookie=True)
        if path == "/api/snapshot":
            return self._json(200, self.server.service.get_snapshot())
        return self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:
        lengths = self.headers.get_all("Content-Length", [])
        transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
        if transfer_encodings or len(lengths) != 1:
            self.close_connection = True
            return self._send(400, "text/plain; charset=utf-8", b"invalid framing")
        raw_length = lengths[0]
        if not raw_length.isascii() or not raw_length.isdecimal() or len(raw_length) > 5:
            self.close_connection = True
            return self._send(400, "text/plain; charset=utf-8", b"invalid framing")
        content_types = self.headers.get_all("Content-Type", [])
        if len(content_types) != 1:
            return self._send(415, "text/plain; charset=utf-8", b"unsupported")
        content_type_parts = [part.strip().lower() for part in content_types[0].split(";")]
        if (
            content_type_parts[0] != "application/json"
            or len(content_type_parts) > 2
            or any(
                part not in {"charset=utf-8", 'charset="utf-8"'} for part in content_type_parts[1:]
            )
        ):
            return self._send(415, "text/plain; charset=utf-8", b"unsupported")
        length = int(raw_length)
        if length < 0 or length > 8192:
            self.close_connection = True
            return self._send(413, "text/plain; charset=utf-8", b"invalid body")
        if not self._trusted(mutation=True) or not self._session():
            self.close_connection = True
            return self._send(403, "text/plain; charset=utf-8", b"forbidden")
        if not hmac.compare_digest(self.headers.get("X-NeuralMeter-CSRF", ""), self.server.csrf):
            self.close_connection = True
            return self._send(403, "text/plain; charset=utf-8", b"forbidden")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self._send(400, "text/plain; charset=utf-8", b"invalid body")
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, OSError, UnicodeDecodeError, RecursionError):
            return self._send(400, "text/plain; charset=utf-8", b"invalid body")
        if not isinstance(payload, dict) or len(payload) > 4:
            return self._send(400, "text/plain; charset=utf-8", b"invalid body")
        path = urlsplit(self.path).path
        if path == "/api/consent":
            source, enabled = payload.get("source"), payload.get("enabled")
            if (
                not isinstance(source, str)
                or source not in CONSENT_KEYS
                or type(enabled) is not bool
                or set(payload) != {"source", "enabled"}
            ):
                return self._send(400, "text/plain; charset=utf-8", b"invalid consent")
            try:
                self.server.service.set_consent(source, enabled)
            except (OSError, RuntimeError):
                return self._send(500, "text/plain; charset=utf-8", b"operation failed")
            return self._json(200, {"ok": True})
        if path == "/api/refresh":
            source = payload.get("source")
            if source not in (*SOURCES, "all") or set(payload) != {"source"}:
                return self._send(400, "text/plain; charset=utf-8", b"invalid source")
            accepted = self.server.service.refresh(source)
            if accepted:
                return self._json(200, {"accepted": True})
            names = SOURCES if source == "all" else (source,)
            reason = (
                "cooldown"
                if any(self.server.service.workers[name].is_enabled() for name in names)
                else "not_opted_in"
            )
            return self._json(200, {"accepted": False, "reason": reason})
        if path == "/api/shutdown":
            if payload or self.path != "/api/shutdown":
                return self._send(400, "text/plain; charset=utf-8", b"invalid request")
            self.server.shutdown_requested.set()
            return self._json(200, {"ok": True})
        return self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_OPTIONS(self) -> None:
        if self.headers.get_all("Content-Length", []) or self.headers.get_all(
            "Transfer-Encoding", []
        ):
            self.close_connection = True
            return self._send(400, "text/plain; charset=utf-8", b"invalid framing")
        if not self._trusted(mutation=True) or not self._session():
            return self._send(403, "text/plain; charset=utf-8", b"forbidden")
        if not hmac.compare_digest(self.headers.get("X-NeuralMeter-CSRF", ""), self.server.csrf):
            return self._send(403, "text/plain; charset=utf-8", b"forbidden")
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")
        self.end_headers()


class LocalDashboard:
    """Owns server, three source workers, session nonce, and only opened browser URL."""

    def __init__(self, port: int = 8766, data_dir: Path | None = None):
        self.data_dir = data_dir or get_data_dir()
        self.service = _SourceService(self.data_dir)
        try:
            self.server = _Server(port, self.service)
        except OSError:
            self.service.close()
            raise
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="neuralmeter-web", daemon=False
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/"

    def start(self, open_browser: bool = True) -> None:
        self.thread.start()
        if open_browser:
            webbrowser.open(self.url)

    def wait_for_shutdown(self) -> None:
        self.server.shutdown_requested.wait()

    def close(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
            self.thread.join(timeout=5)
        self.server.server_close()
        self.service.close()
