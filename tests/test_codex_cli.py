import io
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from neuralmeter.codex_cli import (
    _parse_time,
    parse_rate_limits,
    read_codex_quota,
)


def test_parse_codex_work_scopes_and_unknown_metrics():
    rows = parse_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 25, "resetsAt": "2026-11-14T12:00:00Z"},
                "secondary": {"usedPercent": True, "resetsAt": "bad"},
            }
        }
    )
    assert rows[0].scope == "primary" and rows[0].percent == 25
    assert rows[0].reset_at == datetime(2026, 11, 14, 12, tzinfo=UTC)
    assert rows[1].percent is None and rows[1].reset_at is None
    assert _parse_time(True) is None


class FakeProcess:
    def __init__(self, output: bytes):
        self.stdin = TrackingPipe()
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO()
        self.terminated = False
        self.terminated_event = threading.Event()

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True
        self.terminated_event.set()

    def kill(self):
        self.terminated = True
        self.terminated_event.set()

    def wait(self, timeout=None):
        return 0


class TrackingPipe(io.BytesIO):
    def close(self):
        pass


def test_codex_rpc_sends_only_initialize_and_read_only_rate_limit_method(monkeypatch):
    responses = [
        {"method": "account/updated", "params": {}},
        {
            "id": 3,
            "result": {
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "primary": {
                            "usedPercent": 42,
                            "windowDurationMins": 60,
                            "resetsAt": 1794672000,
                        },
                        "secondary": {
                            "usedPercent": 82,
                            "windowDurationMins": 10080,
                            "resetsAt": None,
                        },
                    }
                }
            },
        },
        {"id": 1, "result": {"userAgent": "fake"}},
        {"id": 2, "result": {"account": {"type": "chatgpt", "email": "private"}}},
        {"id": 4, "result": {"account": {"type": "chatgpt", "email": "private"}}},
    ]
    process = FakeProcess(b"".join(json.dumps(item).encode() + b"\n" for item in responses))
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted")
    args_seen = []

    def launch(args, **kwargs):
        args_seen.extend(args)
        assert kwargs["shell"] is False
        assert "OPENAI_API_KEY" not in kwargs["env"]
        assert "NODE_OPTIONS" not in kwargs["env"]
        assert "read-only" in args and "never" in args
        return process

    snap = read_codex_quota(launcher=launch, installation=("C:/node.exe", "C:/codex.js"))
    sent = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    methods = [item["method"] for item in sent]
    assert methods == [
        "initialize",
        "initialized",
        "account/read",
        "account/rateLimits/read",
        "account/read",
    ]
    assert "jsonrpc" not in sent[0]
    assert sent[2]["params"] == {"refreshToken": False}
    assert all("turn/start" not in method and "thread/start" not in method for method in methods)
    assert snap.status == "available" and snap.windows[0].percent == 42
    assert snap.windows[0].window_minutes == 60 and snap.windows[0].reset_at is not None
    assert "private" not in repr(snap)
    assert process.terminated


def test_codex_unavailable_login_and_malformed_rpc_fail_closed(monkeypatch):
    monkeypatch.setattr("neuralmeter.codex_cli.discover_codex", lambda: None)
    assert read_codex_quota().status == "cli_not_installed"

    responses = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": None}},
    ]
    process = FakeProcess(b"".join(json.dumps(item).encode() + b"\n" for item in responses))
    snap = read_codex_quota(
        launcher=lambda *_a, **_k: process, installation=("C:/node.exe", "C:/codex.js")
    )
    assert snap.status == "not_authenticated"
    assert "secret-marker" not in repr(snap)


def test_codex_rpc_429_maps_to_bounded_backoff():
    responses = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": {"type": "chatgpt"}}},
        {
            "id": 3,
            "error": {"code": 429, "message": "private", "data": {"retryAfter": 99999}},
        },
    ]
    process = FakeProcess(b"".join(json.dumps(item).encode() + b"\n" for item in responses))
    snap = read_codex_quota(
        launcher=lambda *_a, **_k: process,
        installation=("C:/node.exe", "C:/codex.js"),
    )
    assert snap.status == "rate_limited" and snap.retry_after == 3600
    assert "private" not in repr(snap)

    bad = FakeProcess(b"not-json\n")
    snap = read_codex_quota(
        launcher=lambda *_a, **_k: bad, installation=("C:/node.exe", "C:/codex.js")
    )
    assert snap.status == "request_failed"


def test_codex_rpc_timeout_is_bounded_and_process_cleaned():
    entered = threading.Event()

    class BlockingStream:
        def readline(self, _size):
            entered.set()
            time.sleep(0.2)
            return b""

        def close(self):
            pass

    process = FakeProcess(b"")
    process.stdout = BlockingStream()
    snap = read_codex_quota(
        timeout=0.02,
        launcher=lambda *_a, **_k: process,
        installation=("C:/node.exe", "C:/codex.js"),
    )
    assert entered.is_set()
    assert snap.status == "timeout"
    assert process.terminated


def test_codex_cancel_event_terminates_child_and_suppresses_late_result():
    entered = threading.Event()
    process = FakeProcess(b"")

    class BlockingStream:
        def readline(self, _size):
            entered.set()
            process.terminated_event.wait(1)
            return b""

        def close(self):
            pass

    process.stdout = BlockingStream()
    cancel = threading.Event()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            read_codex_quota(
                timeout=2,
                launcher=lambda *_a, **_k: process,
                installation=("C:/node.exe", "C:/codex.js"),
                cancel_event=cancel,
            )
        )
    )
    thread.start()
    assert entered.wait(1)
    cancel.set()
    thread.join(1)
    assert not thread.is_alive()
    assert process.terminated
    assert result[0].status == "not_connected"


def test_discover_codex_native_only_without_node_or_cwd_poison(monkeypatch, tmp_path):
    appdata = tmp_path / "roaming"
    native = (
        appdata
        / "npm"
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(tmp_path)
    assert read_installation() == (str(native.resolve()), None)


def test_discover_codex_trusted_native_when_cwd_is_root_or_home(monkeypatch, tmp_path):
    appdata = tmp_path / "roaming"
    native = (
        appdata
        / "npm"
        / "node_modules"
        / "@openai"
        / "codex"
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    native.parent.mkdir(parents=True)
    native.touch()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("neuralmeter.codex_cli.Path.cwd", lambda: Path("C:/"))
    assert read_installation() == (str(native.resolve()), None)


def test_discover_codex_explicit_path_executable_under_home_and_unicode_spaces(
    monkeypatch, tmp_path
):
    home = tmp_path / "home üser" / "Codex Tools"
    home.mkdir(parents=True)
    exe = home / "codex.exe"
    exe.touch()
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty appdata"))
    monkeypatch.setenv("PATH", str(home))
    for cwd in (Path("C:/"), home.parent):
        monkeypatch.setattr("neuralmeter.codex_cli.Path.cwd", lambda cwd=cwd: cwd)
        assert read_installation() == (str(exe.resolve()), None)


def test_discover_codex_ignores_implicit_relative_empty_and_dot_path_entries(monkeypatch, tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    poisoned = cwd / "codex.exe"
    poisoned.touch()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty"))
    monkeypatch.setenv("PATH", f"{os.pathsep}.{os.pathsep}tools{os.pathsep}")
    assert read_installation() is None


def test_discover_codex_npm_node_fallback_when_npm_tree_under_cwd(monkeypatch, tmp_path):
    cwd = tmp_path / "home with spaces"
    package = cwd / "AppData" / "Roaming" / "npm" / "node_modules" / "@openai" / "codex"
    script = package / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.touch()
    vendor = (
        package
        / "node_modules"
        / "@openai"
        / "codex-win32-x64"
        / "vendor"
        / "x86_64-pc-windows-msvc"
        / "bin"
        / "codex.exe"
    )
    vendor.parent.mkdir(parents=True)
    vendor.touch()
    node_dir = cwd / "Node Runtime"
    node_dir.mkdir()
    node = node_dir / "node.exe"
    node.touch()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("APPDATA", str(cwd / "AppData" / "Roaming"))
    monkeypatch.setenv("PATH", f'"{node_dir}"')
    assert read_installation() == (str(vendor.resolve()), None)
    vendor.unlink()
    assert read_installation() == (str(node.resolve()), str(script.resolve()))


def read_installation():
    from neuralmeter.codex_cli import discover_codex

    return discover_codex()


def test_discover_codex_npm_node_fallback_and_no_cli(monkeypatch, tmp_path):
    appdata = tmp_path / "roaming"
    script = appdata / "npm" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.touch()
    node = tmp_path / "node.exe"
    node.touch()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert read_installation() == (str(node.resolve()), str(script.resolve()))
    script.unlink()
    assert read_installation() is None


def test_discover_codex_rejects_cwd_poison_path(monkeypatch, tmp_path):
    appdata = tmp_path / "roaming"
    cwd = tmp_path / "project"
    cwd.mkdir()
    poisoned = cwd / "codex.exe"
    poisoned.touch()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(cwd)
    assert read_installation() is None


def test_codex_account_switch_between_rpc_reads_discards_quota():
    replies = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": {"type": "chatgpt", "accountId": "account-a"}}},
        {"id": 3, "result": {"rateLimits": {"primary": {"usedPercent": 55}}}},
        {"id": 4, "result": {"account": {"type": "chatgpt", "accountId": "account-b"}}},
    ]
    process = FakeProcess(b"".join(json.dumps(item).encode() + b"\n" for item in replies))
    launched = []

    def launch(args, **kwargs):
        launched.extend(args)
        assert kwargs["shell"] is False
        return process

    snapshot = read_codex_quota(
        launcher=launch,
        installation=("C:/codex.exe", None),
    )
    assert snapshot.status == "identity_changed"
    assert snapshot.windows == ()
    assert "account-a" not in repr(snapshot) and "account-b" not in repr(snapshot)
    assert launched[0] == "C:/codex.exe" and "codex.js" not in launched


def test_codex_more_than_256_returned_windows_marks_partial_coverage():
    limits = {
        f"limit-{index}": {
            "primary": {"usedPercent": index % 100},
        }
        for index in range(257)
    }
    replies = [
        {"id": 1, "result": {}},
        {"id": 2, "result": {"account": {"type": "chatgpt", "accountId": "local"}}},
        {"id": 3, "result": {"rateLimitsByLimitId": limits}},
        {"id": 4, "result": {"account": {"type": "chatgpt", "accountId": "local"}}},
    ]
    process = FakeProcess(b"".join(json.dumps(item).encode() + b"\n" for item in replies))
    snapshot = read_codex_quota(
        launcher=lambda *_args, **_kwargs: process,
        installation=("C:/codex.exe", None),
    )
    assert snapshot.status == "available"
    assert len(snapshot.windows) == 256
    assert snapshot.coverage_partial


def test_codex_auth_metadata_probe_detects_change_between_polls(monkeypatch, tmp_path):
    from neuralmeter.codex_cli import codex_auth_metadata_identity

    home = tmp_path / "codex-home"
    home.mkdir()
    auth_file = home / "auth.json"
    auth_file.write_text('{"opaque":"not-read-by-probe"}', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    first = codex_auth_metadata_identity()
    auth_file.write_text('{"opaque":"replacement-token"}', encoding="utf-8")
    os.utime(
        auth_file,
        ns=(auth_file.stat().st_atime_ns, auth_file.stat().st_mtime_ns + 2_000_000_000),
    )
    second = codex_auth_metadata_identity()
    assert first != second
    assert "replacement-token" not in repr((first, second))
