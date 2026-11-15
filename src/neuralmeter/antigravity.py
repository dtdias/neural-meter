"""Opt-in Antigravity IDE loopback quota collector (experimental protocol)."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import math
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psutil

MAX_BODY_BYTES = 64 * 1024
REQUEST_TIMEOUT = 3.0
_IDENTITY_KEY = secrets.token_bytes(32)
_PORT_FLAGS = ("--extension_server_port", "--hub-port")
_SERVER_NAMES = {"language_server_windows_x64.exe", "agy.exe"}
_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_LABEL_RE = re.compile(r"[^a-zA-Z0-9 ._/-]+")
_MODELS_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
_QUOTA_PATH = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"


@dataclass(frozen=True)
class AntigravityBucket:
    bucket_id: str
    model: str
    window: str
    used_percent: float | None
    reset_at: datetime | None


@dataclass(frozen=True)
class AntigravitySnapshot:
    status: str
    buckets: tuple[AntigravityBucket, ...] = ()
    observed_at: datetime | None = None
    identity: str | None = field(default=None, repr=False, compare=False)
    local_identity: str | None = field(default=None, repr=False, compare=False)
    coverage_partial: bool = False


@dataclass(frozen=True)
class _Endpoint:
    port: int
    csrf: str = field(repr=False)
    pid: int | None = field(default=None, repr=False)
    created_at: float | None = field(default=None, repr=False)
    install_path: Path | None = field(default=None, repr=False)


def _endpoint_identity(endpoint: _Endpoint) -> str:
    material = f"{endpoint.pid}:{endpoint.created_at}:{endpoint.port}:{endpoint.csrf}".encode()
    return hmac.new(_IDENTITY_KEY, material, hashlib.sha256).hexdigest()


def _exact_listener_owned(pid: int, port: int) -> bool:
    """Require sole OS listener ownership for literal 127.0.0.1:port."""
    try:
        matches = [
            conn
            for conn in psutil.net_connections(kind="tcp")
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port
        ]
    except psutil.Error:
        return False
    return bool(matches) and all(
        conn.laddr.ip == "127.0.0.1" and conn.pid == pid for conn in matches
    )


def _antigravity_install() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    candidate = Path(local) / "Programs" / "Antigravity" / "Antigravity.exe"
    return candidate.resolve() if candidate.is_file() else None


def _flag_value(args: list[str], flag: str) -> str | None:
    for index, value in enumerate(args):
        if value.lower() == flag.lower() and index + 1 < len(args):
            return args[index + 1]
        if value.lower().startswith(flag.lower() + "="):
            return value.split("=", 1)[1]
    return None


def _is_antigravity_data_dir(value: str | None) -> bool:
    if not value:
        return False
    clean = value.strip().strip("\"'").replace("\\", "/").rstrip("/").lower()
    return clean in {"antigravity", "antigravity-ide"} or clean.endswith(
        ("/antigravity", "/antigravity-ide")
    )


def _owned_endpoint(install: Path) -> _Endpoint | None:
    """Find only listeners owned by identified Antigravity processes; never scan ports."""
    roots = []
    try:
        for index, proc in enumerate(psutil.process_iter(["pid", "name", "exe"])):
            if index >= 512:
                break
            try:
                if Path(proc.info.get("exe") or "").resolve() == install:
                    roots.append(proc)
            except (OSError, ValueError, psutil.Error):
                continue
    except psutil.Error:
        return None
    for root in roots:
        candidates = [root]
        inspected = 0
        while candidates and inspected < 512:
            child = candidates.pop(0)
            inspected += 1
            try:
                name = (child.name() or "").lower()
                if name in _SERVER_NAMES:
                    child_exe = Path(child.exe()).resolve()
                    if child_exe.is_relative_to(install.parent):
                        args = child.cmdline()
                        if _is_antigravity_data_dir(_flag_value(args, "--app_data_dir")):
                            port_raw = next(
                                (
                                    _flag_value(args, flag)
                                    for flag in _PORT_FLAGS
                                    if _flag_value(args, flag)
                                ),
                                None,
                            )
                            csrf = _flag_value(args, "--csrf_token")
                            if port_raw and port_raw.isdecimal() and csrf and len(csrf) <= 4096:
                                port = int(port_raw)
                                own_listener = any(
                                    conn.status == psutil.CONN_LISTEN
                                    and conn.laddr
                                    and conn.laddr.ip == "127.0.0.1"
                                    and conn.laddr.port == port
                                    for conn in child.net_connections(kind="tcp")
                                )
                                if (
                                    1 <= port <= 65535
                                    and own_listener
                                    and _exact_listener_owned(child.pid, port)
                                ):
                                    return _Endpoint(
                                        port, csrf, child.pid, child.create_time(), install
                                    )
            except (OSError, ValueError, psutil.Error):
                pass
            try:
                candidates.extend(child.children(recursive=False)[: max(0, 512 - inspected)])
            except psutil.Error:
                continue
    return None


def antigravity_endpoint_identity() -> str | None:
    install = _antigravity_install()
    endpoint = _owned_endpoint(install) if install else None
    return (
        _endpoint_identity(endpoint)
        if endpoint
        else hmac.new(_IDENTITY_KEY, b"antigravity-endpoint-missing", hashlib.sha256).hexdigest()
    )


def antigravity_endpoint_available(install: Path | None = None) -> bool:
    """Detect exact known IDE-owned listener without scanning candidate ports."""
    install = install or _antigravity_install()
    return bool(install and _owned_endpoint(install))


def _post(endpoint: _Endpoint, path: str, timeout: float, cancel_event=None) -> dict | None:
    if cancel_event is not None and cancel_event.is_set():
        return None
    if endpoint.pid is None or endpoint.install_path is None:
        return None
    current = _owned_endpoint(endpoint.install_path)
    if current is None or _endpoint_identity(current) != _endpoint_identity(endpoint):
        return None
    if not _exact_listener_owned(endpoint.pid, endpoint.port):
        return None
    connection = http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=timeout)
    try:
        body = json.dumps(
            {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}
        ).encode("utf-8")
        connection.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "X-Codeium-Csrf-Token": endpoint.csrf,
                "Content-Length": str(len(body)),
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status != 200 or response.getheader("Location"):
            response.close()
            return None
        raw = response.read(MAX_BODY_BYTES + 1)
        response.close()
        if len(raw) > MAX_BODY_BYTES:
            return None
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    finally:
        connection.close()


def _walk_dicts(value, *, max_nodes: int = 10000):
    stack = [value]
    visited = 0
    while stack and visited < max_nodes:
        item = stack.pop()
        visited += 1
        if isinstance(item, dict):
            yield item
            stack.extend(v for v in item.values() if isinstance(v, dict | list))
        elif isinstance(item, list):
            stack.extend(item)


def _safe_id(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        value = fallback
    if "@" in value or "\\" in value or "://" in value:
        return "id_" + hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:12]
    result = _ID_RE.sub("_", value).strip("_-").lower()[:48]
    return result or fallback


def _safe_label(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        value = fallback
    if "@" in value or "\\" in value or "://" in value:
        value = fallback
    result = _LABEL_RE.sub("", value).strip()[:48]
    return result or fallback


def _percent_from_quota(quota: object) -> float | None:
    if not isinstance(quota, dict):
        return None
    remaining = quota.get("remainingFraction")
    if isinstance(remaining, bool) or not isinstance(remaining, int | float):
        return None
    if not math.isfinite(remaining) or not 0 <= remaining <= 1:
        return None
    return round((1 - remaining) * 100, 2)


def _reset_time(value: object) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value) or value < 0:
            return None
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except ValueError:
        return None


def parse_antigravity_quota(
    user_status: dict | None, quota_summary: dict | None
) -> tuple[AntigravityBucket, ...]:
    rows: dict[tuple[str, str], AntigravityBucket] = {}
    if user_status:
        for node in _walk_dicts(user_status):
            configs = node.get("clientModelConfigs")
            if not isinstance(configs, list):
                continue
            for config in configs[:256]:
                if not isinstance(config, dict):
                    continue
                model_obj = config.get("modelOrAlias")
                model = model_obj.get("model") if isinstance(model_obj, dict) else None
                info = config.get("quotaInfo")
                if not isinstance(model, str) or not isinstance(info, dict):
                    continue
                model = _safe_label(model, "model")
                bucket = _safe_id(model, "model")
                window = "weekly" if "week" in str(info.get("window", "")).lower() else "unknown"
                rows[(bucket, window)] = AntigravityBucket(
                    bucket,
                    model,
                    window,
                    _percent_from_quota(info),
                    _reset_time(info.get("resetTime")),
                )
    if quota_summary:
        for node in _walk_dicts(quota_summary):
            groups = node.get("groups")
            if not isinstance(groups, list):
                continue
            for group in groups[:128]:
                if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
                    continue
                group_name = _safe_label(group.get("name") or group.get("groupId"), "quota")
                for bucket in group["buckets"][:257]:
                    if not isinstance(bucket, dict):
                        continue
                    bucket_name = bucket.get("bucketId") or bucket.get("window") or "bucket"
                    bucket_id = _safe_id(bucket_name, "bucket")
                    model = _safe_label(bucket.get("model") or group_name, group_name)
                    raw_window = _safe_id(bucket.get("window"), "unknown")
                    window = raw_window if raw_window in {"weekly", "5h"} else "unknown"
                    remaining = bucket.get("remainingFraction")
                    if isinstance(remaining, bool) or not isinstance(remaining, int | float):
                        remaining = None
                    percent = (
                        round((1 - remaining) * 100, 2)
                        if remaining is not None
                        and math.isfinite(remaining)
                        and 0 <= remaining <= 1
                        else None
                    )
                    rows[(bucket_id, window)] = AntigravityBucket(
                        bucket_id, model, window, percent, _reset_time(bucket.get("resetTime"))
                    )
    return tuple(rows.values())[:256]


def _quota_entry_count(*payloads: dict | None) -> int:
    count = 0
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for node in _walk_dicts(payload):
            configs = node.get("clientModelConfigs")
            if isinstance(configs, list):
                count += len(configs)
            buckets = node.get("buckets")
            if isinstance(buckets, list):
                count += len(buckets)
    return count


def _response_identity(user_status: dict | None) -> str | None:
    for node in _walk_dicts(user_status or {}):
        for key in ("userId", "accountId", "user_id"):
            value = node.get(key)
            if not isinstance(value, str) or not value or "@" in value or "\\" in value:
                continue
            return hmac.new(
                _IDENTITY_KEY, value.encode("utf-8", errors="ignore"), hashlib.sha256
            ).hexdigest()
    return None


def read_antigravity_quota(
    *,
    timeout: float = REQUEST_TIMEOUT,
    cancel_event=None,
    install_path: Path | None = None,
    endpoint: _Endpoint | None = None,
    post=_post,
) -> AntigravitySnapshot:
    if cancel_event is not None and cancel_event.is_set():
        return AntigravitySnapshot("not_connected")
    install = install_path or _antigravity_install()
    if install is None:
        return AntigravitySnapshot("ide_not_found")
    endpoint = endpoint or _owned_endpoint(install)
    if endpoint is None:
        return AntigravitySnapshot("server_not_found")
    identity = _endpoint_identity(endpoint)
    verify_endpoint = _owned_endpoint if endpoint.pid is not None else None

    def endpoint_valid() -> bool:
        if verify_endpoint is None:
            return True  # Explicit test seam; production discovery always supplies PID.
        current = verify_endpoint(install)
        return (
            current is not None
            and _endpoint_identity(current) == identity
            and _exact_listener_owned(endpoint.pid, endpoint.port)
        )

    try:
        if not endpoint_valid():
            return AntigravitySnapshot("identity_changed")
        user = post(endpoint, _MODELS_PATH, timeout, cancel_event)
        initial_response_identity = _response_identity(user)
        if cancel_event is not None and cancel_event.is_set():
            return AntigravitySnapshot("not_connected")
        if not endpoint_valid():
            return AntigravitySnapshot("identity_changed")
        summary = post(endpoint, _QUOTA_PATH, timeout, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return AntigravitySnapshot("not_connected")
        if not endpoint_valid():
            return AntigravitySnapshot("identity_changed")
        user_after = post(endpoint, _MODELS_PATH, timeout, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            return AntigravitySnapshot("not_connected")
        if not endpoint_valid():
            return AntigravitySnapshot("identity_changed")
        response_identity = _response_identity(user_after)
        if response_identity != initial_response_identity:
            return AntigravitySnapshot("identity_changed")
        buckets = parse_antigravity_quota(user, summary)
        if not buckets:
            return AntigravitySnapshot("quota_unavailable")
        return AntigravitySnapshot(
            "available",
            buckets,
            datetime.now(UTC),
            response_identity,
            identity,
            _quota_entry_count(user, summary) > 256,
        )
    except (OSError, http.client.HTTPException, TimeoutError, ValueError):
        return AntigravitySnapshot("request_failed")
