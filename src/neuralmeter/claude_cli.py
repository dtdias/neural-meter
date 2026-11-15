"""Opt-in, read-only Claude Code CLI quota collector (Windows)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
MAX_CREDENTIAL_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_CLI_STDOUT_BYTES = 16 * 1024
MAX_CLI_STDERR_BYTES = 16 * 1024
_IDENTITY_KEY = secrets.token_bytes(32)


def _run_cli_metadata(executable: str, arguments: list[str], timeout: float) -> bytes | None:
    """Run fixed read-only metadata subcommand with byte-bounded pipe readers."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_API_KEY",
        "ANTHROPIC_BASE_URL",
    ):
        env.pop(name, None)
    try:
        process = subprocess.Popen(
            [executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path.home()),
            shell=False,
            creationflags=flags,
            bufsize=0,
            env=env,
        )
    except OSError:
        return None
    output = bytearray()
    overflow = threading.Event()

    def read_stdout() -> None:
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                return
            if len(output) + len(chunk) > MAX_CLI_STDOUT_BYTES:
                overflow.set()
                process.kill()
                return
            output.extend(chunk)

    def drain_stderr() -> None:
        count = 0
        while True:
            chunk = process.stderr.read(4096)
            if not chunk:
                return
            count += len(chunk)
            if count > MAX_CLI_STDERR_BYTES:
                process.kill()
                return

    stdout_thread = threading.Thread(target=read_stdout, daemon=True)
    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=0.5)
        return None
    finally:
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
    return None if overflow.is_set() else bytes(output)


@dataclass(frozen=True)
class QuotaWindow:
    percent: float | None
    reset_at: datetime | None


@dataclass(frozen=True)
class CliSnapshot:
    status: str
    five_hour: QuotaWindow = QuotaWindow(None, None)
    seven_day: QuotaWindow = QuotaWindow(None, None)
    observed_at: datetime | None = None
    retry_after: int | None = None
    identity: str | None = dataclass_field(default=None, repr=False, compare=False)
    auth_status: str = "unknown"


def discover_claude() -> str | None:
    """Find only named CLI executable, never cwd-relative project executables."""
    known = Path.home() / ".local" / "bin" / "claude.exe"
    found = shutil.which("claude.exe") or shutil.which("claude")
    for candidate in (str(known), found):
        if candidate and Path(candidate).is_absolute() and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    return None


def cli_version(executable: str) -> str | None:
    raw = _run_cli_metadata(executable, ["--version"], 3)
    if raw is None or not raw:
        return None
    value = raw.decode("utf-8", errors="replace").strip()
    return value[:100] if value and all(char.isprintable() for char in value) else None


def cli_auth_status(executable: str) -> str:
    """Run documented read-only auth status; discard identity and all raw output."""
    raw = _run_cli_metadata(executable, ["auth", "status", "--json"], 3)
    if raw is None:
        return "unsupported_auth"
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return "unsupported_auth"
    if not isinstance(value, dict):
        return "unsupported_auth"
    if value.get("loggedIn") is False:
        return "not_authenticated"
    if value.get("loggedIn") is not True:
        return "unsupported_auth"
    provider = value.get("apiProvider")
    if provider not in (None, "firstParty"):
        return "unsupported_auth"
    auth_method = value.get("authMethod")
    if auth_method is None:
        return "authenticated" if provider == "firstParty" else "unsupported_auth"
    if not isinstance(auth_method, str):
        return "unsupported_auth"
    normalized = auth_method.casefold()
    if normalized in {"claude.ai", "claudeai"}:
        return "authenticated" if provider == "firstParty" else "unsupported_auth"
    if normalized == "oauth":
        return "authenticated"
    return "unsupported_auth"


def credential_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(config_dir) if config_dir else Path.home() / ".claude") / ".credentials.json"


def credential_identity(path: Path | None = None) -> str | None:
    """Opaque process-local credential fingerprint; never serialize or log."""
    target = path or credential_path()
    try:
        before = target.stat()
        if before.st_size > MAX_CREDENTIAL_BYTES:
            return hmac.new(
                _IDENTITY_KEY,
                f"oversized:{target}:{before.st_mtime_ns}:{before.st_size}".encode(),
                hashlib.sha256,
            ).hexdigest()
        with target.open("rb") as stream:
            data = stream.read(MAX_CREDENTIAL_BYTES + 1)
        if len(data) > MAX_CREDENTIAL_BYTES:
            return None
        after = target.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return None
        material = f"{after.st_mtime_ns}:{after.st_size}:".encode() + data
        return hmac.new(_IDENTITY_KEY, material, hashlib.sha256).hexdigest()
    except OSError:
        return hmac.new(_IDENTITY_KEY, f"missing:{target}".encode(), hashlib.sha256).hexdigest()


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.astimezone(UTC) if dt.tzinfo else None
        numeric = float(value)
        if numeric != numeric or abs(numeric) == float("inf"):
            return None
        if numeric > 1e12:
            numeric /= 1000
        return datetime.fromtimestamp(numeric, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _window(raw: object) -> QuotaWindow:
    if not isinstance(raw, dict):
        return QuotaWindow(None, None)
    pct = raw.get("utilization")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
        pct = None
    return QuotaWindow(float(pct) if pct is not None else None, _timestamp(raw.get("resets_at")))


def parse_usage(payload: object) -> tuple[QuotaWindow, QuotaWindow]:
    if not isinstance(payload, dict):
        raise ValueError("invalid quota response")
    return _window(payload.get("five_hour")), _window(payload.get("seven_day"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_usage(*, timeout: float = 5.0, opener=None, path: Path | None = None) -> CliSnapshot:
    """Read official local credential and send bearer only to fixed Anthropic endpoint."""
    credential = path or credential_path()
    try:
        executable = discover_claude()
        if executable is None:
            return CliSnapshot("cli_not_installed", auth_status="not_checked")
        auth = cli_auth_status(executable)
        if auth != "authenticated":
            return CliSnapshot(auth, auth_status=auth)
        if not credential.is_file() or credential.stat().st_size > MAX_CREDENTIAL_BYTES:
            return CliSnapshot("quota_unavailable", auth_status=auth)
        before_stat = credential.stat()
        with credential.open("rb") as stream:
            credential_bytes = stream.read(MAX_CREDENTIAL_BYTES + 1)
        if len(credential_bytes) > MAX_CREDENTIAL_BYTES:
            return CliSnapshot("quota_unavailable", auth_status=auth)
        after_stat = credential.stat()
        if (before_stat.st_mtime_ns, before_stat.st_size) != (
            after_stat.st_mtime_ns,
            after_stat.st_size,
        ):
            return CliSnapshot("identity_changed", auth_status=auth)
        identity = hmac.new(
            _IDENTITY_KEY,
            f"{after_stat.st_mtime_ns}:{after_stat.st_size}:".encode() + credential_bytes,
            hashlib.sha256,
        ).hexdigest()
        raw = json.loads(credential_bytes)
        oauth = raw.get("claudeAiOauth") if isinstance(raw, dict) else None
        if not isinstance(oauth, dict):
            return CliSnapshot("quota_unavailable", auth_status=auth)
        token = oauth.get("accessToken")
        expiry = _timestamp(oauth.get("expiresAt"))
        scopes = oauth.get("scopes")
        provider = raw.get("apiProvider")
        if provider and provider != "firstParty":
            return CliSnapshot("quota_unavailable", auth_status=auth)
        if not isinstance(token, str) or not token or len(token) > 8192:
            return CliSnapshot("quota_unavailable", auth_status=auth)
        if not isinstance(scopes, list) or "user:profile" not in scopes:
            return CliSnapshot("quota_unavailable", auth_status=auth)
        if expiry is None:
            return CliSnapshot("quota_unavailable", auth_status=auth)
        if expiry <= datetime.now(UTC):
            return CliSnapshot("quota_unavailable", auth_status=auth)
        request = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json",
            },
            method="GET",
        )
        client = opener or urllib.request.build_opener(_NoRedirect())
        response = client.open(request, timeout=timeout)
        try:
            if response.geturl() != USAGE_URL:
                return CliSnapshot("request_failed", auth_status=auth)
            body = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
        if len(body) > MAX_RESPONSE_BYTES:
            return CliSnapshot("request_failed", auth_status=auth)
        five, seven = parse_usage(json.loads(body))
        if credential_identity(credential) != identity:
            return CliSnapshot("identity_changed", auth_status=auth)
        return CliSnapshot(
            "available", five, seven, datetime.now(UTC), identity=identity, auth_status=auth
        )
    except urllib.error.HTTPError as exc:
        status = (
            "quota_unavailable"
            if exc.code in (401, 403)
            else "rate_limited"
            if exc.code == 429
            else "request_failed"
        )
        retry_after = None
        if exc.code == 429:
            try:
                retry_after = max(60, min(3600, int(exc.headers.get("Retry-After", "60"))))
            except (TypeError, ValueError):
                retry_after = 60
        return CliSnapshot(status, retry_after=retry_after, auth_status="authenticated")
    except Exception:
        return CliSnapshot("request_failed", auth_status="authenticated")


class QuotaPoller:
    """Opt-in bounded poller; stale callback suppressed by generation counter."""

    def __init__(
        self,
        callback: Callable[[CliSnapshot], None],
        fetch=read_usage,
        interval: int = 60,
        pass_cancel_event: bool = False,
    ):
        self.callback, self.fetch = callback, fetch
        self.interval = max(60, interval)
        self.pass_cancel_event = pass_cancel_event
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._rate_limit_until = 0.0
        self._thread: threading.Thread | None = None
        self._generation = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._generation += 1
        generation = self._generation
        self._thread = threading.Thread(target=self._run, args=(generation,), daemon=True)
        self._thread.start()

    def _run(self, generation: int) -> None:
        delay = self.interval
        while not self._stop.is_set():
            result = self.fetch(cancel_event=self._stop) if self.pass_cancel_event else self.fetch()
            if self._stop.is_set() or generation != self._generation:
                return
            delay = (
                min(3600, result.retry_after or min(self.interval * 8, delay * 2))
                if result.status == "rate_limited"
                else self.interval
            )
            if result.status == "rate_limited":
                self._rate_limit_until = time.monotonic() + delay
            self.callback(result)
            self._wake.wait(delay)
            self._wake.clear()

    def request_refresh(self) -> bool:
        """Wake poller for one immediate read-only refresh without a new worker."""
        if time.monotonic() < self._rate_limit_until:
            return False
        self._wake.set()
        return True

    def stop(self) -> None:
        self._generation += 1
        self._stop.set()
        self._wake.set()

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=timeout)
