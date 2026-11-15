"""Read-only Codex app-server quota RPC over bounded stdio."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import subprocess
import threading
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path

MAX_LINE_BYTES = 16 * 1024
MAX_STDOUT_BYTES = 64 * 1024
MAX_STDERR_BYTES = 16 * 1024
RPC_TIMEOUT = 5.0
_IDENTITY_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class CodexWindow:
    scope: str
    percent: float | None
    reset_at: datetime | None
    limit_id: str = "codex"
    limit_name: str | None = None
    window_minutes: int | None = None


@dataclass(frozen=True)
class CodexSnapshot:
    status: str
    windows: tuple[CodexWindow, ...] = ()
    observed_at: datetime | None = None
    retry_after: int | None = None
    identity: str | None = dataclass_field(default=None, repr=False, compare=False)
    local_identity: str | None = dataclass_field(default=None, repr=False, compare=False)
    coverage_partial: bool = False


class CodexRpcError(ValueError):
    def __init__(self, status: str = "request_failed", retry_after: int | None = None):
        self.status = status
        self.retry_after = retry_after


def discover_codex() -> tuple[str, str | None] | None:
    """Resolve trusted npm vendor or explicit absolute PATH entries; never implicit cwd."""
    app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    package = app_data / "npm" / "node_modules" / "@openai" / "codex"
    native = (
        package
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )

    # Exact npm vendor hierarchy is trusted independently of current directory.
    if native.is_absolute() and native.is_file():
        return str(native.resolve()), None

    def explicit_path_executable(name: str) -> Path | None:
        """Trust only explicit absolute PATH directories; never resolve cwd aliases."""
        for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
            entry = raw_entry.strip()
            if len(entry) >= 2 and entry[0] == entry[-1] == '"':
                entry = entry[1:-1].strip()
            if not entry or entry in {".", ".."}:
                continue
            directory = Path(entry).expanduser()
            if not directory.is_absolute():
                continue
            try:
                candidate = (directory / name).resolve(strict=True)
                if candidate.is_file() and candidate.suffix.lower() == ".exe":
                    return candidate
            except OSError:
                continue
        return None

    path_native = explicit_path_executable("codex.exe")
    if path_native is not None:
        return str(path_native), None
    script = package / "bin" / "codex.js"
    node = explicit_path_executable("node.exe")
    if script.is_absolute() and script.is_file() and node is not None:
        return str(node), str(script.resolve())
    return None


def codex_auth_metadata_identity() -> str | None:
    """Opaque auth-file metadata only; never reads auth.json contents."""
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    auth = root / "auth.json"
    try:
        stat = auth.stat()
        material = f"{auth.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
    except OSError:
        material = f"{auth}:missing".encode()
    return hmac.new(_IDENTITY_KEY, material, hashlib.sha256).hexdigest()


def _account_identity(account: dict) -> str | None:
    account_id = account.get("accountId", account.get("id"))
    if not isinstance(account_id, str | int) or isinstance(account_id, bool):
        return None
    account_id = str(account_id)
    if not account_id or "@" in account_id or "://" in account_id or "\\" in account_id:
        return None
    material = account_id.encode("utf-8", errors="ignore")
    return hmac.new(_IDENTITY_KEY, material, hashlib.sha256).hexdigest()


def _account_state(account: dict) -> str:
    fields = {
        key: account.get(key)
        for key in ("type", "planType", "plan")
        if isinstance(account.get(key), str)
    }
    material = json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_IDENTITY_KEY, material, hashlib.sha256).hexdigest()


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value) or value < 0:
            return None
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except ValueError:
        return None


def parse_rate_limits(value: object) -> tuple[CodexWindow, ...]:
    if isinstance(value, dict):
        value = value.get("rateLimitsByLimitId") or value.get("rateLimits") or value
    if not isinstance(value, dict):
        raise ValueError("invalid rate limit response")
    if "primary" in value or "secondary" in value:
        value = {"codex": value}
    windows = []
    for raw_id, snapshot in value.items():
        if not isinstance(snapshot, dict):
            continue
        limit_id = snapshot.get("limitId", raw_id)
        if not isinstance(limit_id, str) or not limit_id or len(limit_id) > 100:
            continue
        if "@" in limit_id or "\\" in limit_id or "://" in limit_id:
            limit_id = (
                "id_" + hashlib.sha256(limit_id.encode("utf-8", errors="ignore")).hexdigest()[:12]
            )
        limit_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", limit_id).strip("_-").lower()[:48]
        if not limit_id:
            continue
        for scope in ("primary", "secondary"):
            raw = snapshot.get(scope)
            if not isinstance(raw, dict):
                continue
            pct = raw.get("usedPercent")
            if (
                isinstance(pct, bool)
                or not isinstance(pct, int | float)
                or not math.isfinite(pct)
                or not 0 <= pct <= 100
            ):
                pct = None
            duration = raw.get("windowDurationMins")
            duration = (
                duration
                if isinstance(duration, int)
                and not isinstance(duration, bool)
                and 0 < duration <= 525600
                else None
            )
            name = snapshot.get("limitName")
            name = (
                re.sub(r"[^a-zA-Z0-9 ._/-]+", "", name).strip()[:48]
                if isinstance(name, str) and "@" not in name and "\\" not in name
                else None
            )
            windows.append(
                CodexWindow(
                    scope,
                    float(pct) if pct is not None else None,
                    _parse_time(raw.get("resetsAt")),
                    limit_id,
                    name[:80] if isinstance(name, str) else None,
                    duration,
                )
            )
    if not windows:
        raise LookupError("quota unavailable")
    return tuple(windows[:257])


class _BoundedReader:
    def __init__(self, stream, limit: int, process):
        self.lines: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        self.limit = limit
        self.process = process
        self.overflow = threading.Event()
        self.pending: dict[int | str, dict] = {}
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        total = 0
        try:
            while True:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_LINE_BYTES or total > self.limit:
                    self.overflow.set()
                    self.process.terminate()
                    break
                try:
                    self.lines.put(line, timeout=0.1)
                except queue.Full:
                    self.overflow.set()
                    self.process.terminate()
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                self.lines.put_nowait(None)
            except queue.Full:
                pass


def _discard_stderr(stream, process) -> threading.Thread:
    def drain() -> None:
        total = 0
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                total += len(chunk)
                if total > MAX_STDERR_BYTES:
                    process.terminate()
                    return
        except (OSError, ValueError):
            return

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    return thread


def _request(
    proc, reader: _BoundedReader, request_id: int, method: str, params: dict, timeout: float
):
    proc.stdin.write(
        (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode()
    )
    proc.stdin.flush()
    # Queue timeout is bounded, even if child never answers.
    import time

    until = time.monotonic() + timeout
    if request_id in reader.pending:
        return reader.pending.pop(request_id).get("result")
    while True:
        remaining = until - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex RPC timeout")
        try:
            line = reader.lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Codex RPC timeout") from exc
        if line is None:
            raise ValueError("Codex server closed")
        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise ValueError("Malformed Codex RPC response") from None
        if not isinstance(message, dict):
            raise ValueError("Malformed Codex RPC response")
        if message.get("id") == request_id:
            if "error" in message:
                error = message.get("error")
                error_message = error.get("message", "") if isinstance(error, dict) else ""
                if isinstance(error_message, str) and any(
                    marker in error_message.lower()
                    for marker in ("account authentication required", "authentication required")
                ):
                    raise CodexRpcError("auth_required")
                data = error.get("data", {}) if isinstance(error, dict) else {}
                is_429 = isinstance(error, dict) and (
                    error.get("code") == 429 or isinstance(data, dict) and data.get("status") == 429
                )
                retry = data.get("retryAfter") if isinstance(data, dict) else None
                retry_after = (
                    max(60, min(3600, retry))
                    if is_429 and isinstance(retry, int) and not isinstance(retry, bool)
                    else 60
                    if is_429
                    else None
                )
                raise CodexRpcError("rate_limited" if is_429 else "request_failed", retry_after)
            return message.get("result")
        if isinstance(message.get("id"), int | str):
            reader.pending[message["id"]] = message
            if len(reader.pending) > 16:
                raise ValueError("Too many unmatched Codex RPC responses")


def read_codex_quota(
    *,
    timeout: float = RPC_TIMEOUT,
    launcher=subprocess.Popen,
    installation: tuple[str, str] | None = None,
    cancel_event: threading.Event | None = None,
) -> CodexSnapshot:
    if cancel_event is not None and cancel_event.is_set():
        return CodexSnapshot("not_connected")
    found = installation if installation is not None else discover_codex()
    if found is None:
        return CodexSnapshot("cli_not_installed")
    executable, script = found
    args = [executable]
    if script is not None:
        args.append(script)
    args.extend(["-s", "read-only", "-a", "never", "app-server", "--listen", "stdio://"])
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = os.environ.copy()
    for name in ("NODE_OPTIONS", "NODE_PATH", "OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        env.pop(name, None)
    proc = None
    readers = []
    cancel_done = threading.Event()
    local_identity = codex_auth_metadata_identity()
    try:
        proc = launcher(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(Path.home()),
            shell=False,
            creationflags=creationflags,
            bufsize=0,
            env=env,
        )

        def stop_on_cancel() -> None:
            while not cancel_done.wait(0.025):
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    return

        cancel_thread = threading.Thread(target=stop_on_cancel, daemon=True)
        cancel_thread.start()
        readers.append(cancel_thread)
        reader = _BoundedReader(proc.stdout, MAX_STDOUT_BYTES, proc)
        readers.extend([reader.thread, _discard_stderr(proc.stderr, proc)])
        initialize = _request(
            proc,
            reader,
            1,
            "initialize",
            {
                "clientInfo": {"name": "neuralmeter", "title": "NeuralMeter", "version": "0.2.1"},
                "capabilities": {},
            },
            timeout,
        )
        if not isinstance(initialize, dict):
            return CodexSnapshot("unsupported_auth")
        proc.stdin.write(b'{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        account = _request(proc, reader, 2, "account/read", {"refreshToken": False}, timeout)
        if not isinstance(account, dict) or not isinstance(account.get("account"), dict):
            return CodexSnapshot("not_authenticated")
        if account["account"].get("type") not in ("chatgpt", "chatgptAuthTokens"):
            return CodexSnapshot("unsupported_auth")
        account_identity = _account_identity(account["account"])
        account_state = _account_state(account["account"])
        result = _request(proc, reader, 3, "account/rateLimits/read", {}, timeout)
        try:
            windows = parse_rate_limits(result)
        except LookupError:
            return CodexSnapshot("quota_unavailable")
        account_after = _request(proc, reader, 4, "account/read", {"refreshToken": False}, timeout)
        if not isinstance(account_after, dict) or not isinstance(
            account_after.get("account"), dict
        ):
            return CodexSnapshot("identity_changed")
        if (
            _account_identity(account_after["account"]) != account_identity
            or _account_state(account_after["account"]) != account_state
        ):
            return CodexSnapshot("identity_changed")
        if codex_auth_metadata_identity() != local_identity:
            return CodexSnapshot("identity_changed")
        if reader.overflow.is_set():
            return CodexSnapshot("request_failed")
        partial = len(windows) > 256
        return CodexSnapshot(
            "available",
            windows[:256],
            datetime.now(UTC),
            identity=account_identity,
            local_identity=local_identity,
            coverage_partial=partial,
        )
    except TimeoutError:
        return CodexSnapshot("timeout")
    except CodexRpcError as exc:
        return CodexSnapshot(exc.status, retry_after=exc.retry_after)
    except Exception:
        return CodexSnapshot(
            "not_connected"
            if cancel_event is not None and cancel_event.is_set()
            else "request_failed"
        )
    finally:
        cancel_done.set()
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=0.5)
            except (OSError, subprocess.SubprocessError):
                pass
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except (OSError, AttributeError):
                    pass
        for thread in readers:
            if thread is not threading.current_thread():
                thread.join(timeout=0.2)
