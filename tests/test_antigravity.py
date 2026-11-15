import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from neuralmeter.antigravity import (
    AntigravitySnapshot,
    _Endpoint,
    parse_antigravity_quota,
    read_antigravity_quota,
)


def test_parse_antigravity_model_and_dynamic_bucket_quotas_safely():
    status = {
        "userStatus": {
            "email": "private@example.invalid",
            "cascadeModelConfigData": {
                "clientModelConfigs": [
                    {
                        "modelOrAlias": {"model": "MODEL_GEMINI_FLASH"},
                        "quotaInfo": {
                            "remainingFraction": 0.75,
                            "resetTime": "2026-11-14T12:00:00Z",
                        },
                    },
                    {
                        "modelOrAlias": {"model": "MODEL_WITHOUT_QUOTA"},
                        "quotaInfo": {"remainingFraction": float("nan"), "resetTime": "bad"},
                    },
                ]
            },
        }
    }
    summary = {
        "groups": [
            {
                "name": "Gemini family",
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "window": "weekly",
                        "remainingFraction": 0.4,
                        "resetTime": 1794672000,
                    },
                    {
                        "bucketId": "amount-only",
                        "window": "5h",
                        "remainingAmount": 7,
                    },
                ],
            }
        ]
    }
    rows = parse_antigravity_quota(status, summary)
    flash = next(row for row in rows if row.model == "MODEL_GEMINI_FLASH")
    assert flash.used_percent == 25
    assert flash.reset_at == datetime(2026, 11, 14, 12, tzinfo=UTC)
    amount_only = next(row for row in rows if row.bucket_id == "amount-only")
    assert amount_only.used_percent is None and amount_only.reset_at is None
    bad = next(row for row in rows if row.model == "MODEL_WITHOUT_QUOTA")
    assert bad.used_percent is None and bad.reset_at is None
    assert "private@example.invalid" not in repr(rows)


def test_antigravity_source_runs_only_allowlisted_loopback_rpc_and_sanitizes(monkeypatch):
    token = "csrf-private-marker"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("X-Codeium-Csrf-Token")))
            if self.path.endswith("GetUserStatus"):
                payload = {
                    "userStatus": {
                        "email": "account-private",
                        "cascadeModelConfigData": {
                            "clientModelConfigs": [
                                {
                                    "modelOrAlias": {"model": "MODEL_GEMINI_PRO"},
                                    "quotaInfo": {"remainingFraction": 0.6},
                                }
                            ]
                        },
                    }
                }
            else:
                payload = {"groups": []}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        install = Path(__file__)
        endpoint = _Endpoint(
            server.server_port,
            token,
            __import__("os").getpid(),
            __import__("psutil").Process().create_time(),
            install,
        )
        monkeypatch.setattr("neuralmeter.antigravity._owned_endpoint", lambda _install: endpoint)
        monkeypatch.setattr("neuralmeter.antigravity._exact_listener_owned", lambda *_args: True)
        snapshot = read_antigravity_quota(install_path=install, endpoint=endpoint)
    finally:
        server.shutdown()
        thread.join(timeout=1)
        server.server_close()
    assert snapshot.status == "available"
    assert snapshot.buckets[0].used_percent == 40
    assert [path.rsplit("/", 1)[-1] for path, _csrf in requests] == [
        "GetUserStatus",
        "RetrieveUserQuotaSummary",
        "GetUserStatus",
    ]
    assert all(csrf == token for _path, csrf in requests)
    assert token not in repr(snapshot) and "account-private" not in repr(snapshot)


def test_antigravity_absent_and_cancelled_states_do_not_touch_transport(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    called = []
    snap = read_antigravity_quota(
        install_path=None, cancel_event=cancel, post=lambda *a: called.append(a)
    )
    assert snap.status == "not_connected" and called == []
    monkeypatch.setattr("neuralmeter.antigravity._antigravity_install", lambda: None)
    missing = read_antigravity_quota(post=lambda *a: called.append(a))
    assert missing.status == "ide_not_found" and called == []


def test_antigravity_missing_quota_is_unknown_never_zero():
    snap = read_antigravity_quota(
        install_path=Path("fake"),
        endpoint=_Endpoint(12345, "secret"),
        post=lambda *_args: {"groups": []},
    )
    assert snap == AntigravitySnapshot("quota_unavailable")


def test_antigravity_api_bucket_limit_marks_partial_coverage():
    summary = {
        "groups": [
            {
                "name": "Quota group",
                "buckets": [
                    {
                        "bucketId": f"bucket-{index}",
                        "window": "weekly",
                        "remainingFraction": 0.5,
                    }
                    for index in range(257)
                ],
            }
        ]
    }
    replies = iter([{"userStatus": {}}, summary, {"userStatus": {}}])
    snapshot = read_antigravity_quota(
        install_path=Path("fake"),
        endpoint=_Endpoint(40012, "csrf"),
        post=lambda *_args: next(replies),
    )
    assert snapshot.status == "available"
    assert len(snapshot.buckets) == 256
    assert snapshot.coverage_partial


def test_antigravity_discovery_uses_identified_process_owned_listener_only(monkeypatch, tmp_path):
    install = tmp_path / "Antigravity.exe"
    install.touch()
    port = 49321
    csrf = "never-print-this"

    class FakeChild:
        pid = 1456

        def name(self):
            return "language_server_windows_x64.exe"

        def create_time(self):
            return 123.0

        def exe(self):
            return str(install.parent / "language_server_windows_x64.exe")

        def cmdline(self):
            return [
                "language_server_windows_x64.exe",
                "--app_data_dir",
                "antigravity-ide",
                "--hub-port",
                str(port),
                "--csrf_token=" + csrf,
            ]

        def net_connections(self, kind):
            assert kind == "tcp"
            return [
                SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=port, ip="127.0.0.1"))
            ]

        def children(self, recursive):
            assert not recursive
            return []

    class FakeRoot:
        info = {"exe": str(install)}

        def name(self):
            return "Antigravity.exe"

        def children(self, recursive):
            assert not recursive
            return [FakeChild()]

    monkeypatch.setattr("neuralmeter.antigravity.psutil.process_iter", lambda _attrs: [FakeRoot()])
    monkeypatch.setattr(
        "neuralmeter.antigravity.psutil.net_connections",
        lambda **_kwargs: [
            SimpleNamespace(
                status="LISTEN", pid=1456, laddr=SimpleNamespace(port=port, ip="127.0.0.1")
            )
        ],
    )
    endpoint = __import__("neuralmeter.antigravity", fromlist=["_owned_endpoint"])._owned_endpoint(
        install.resolve()
    )
    assert endpoint is not None and endpoint.port == port and endpoint.pid == 1456
    assert csrf not in repr(endpoint)


def test_antigravity_rejects_nonliteral_or_conflicting_listener(monkeypatch):
    from neuralmeter.antigravity import _exact_listener_owned

    rows = []
    monkeypatch.setattr("neuralmeter.antigravity.psutil.net_connections", lambda **_kwargs: rows)
    for host, pid in (("::1", 1456), ("127.0.0.2", 1456), ("0.0.0.0", 1456), ("127.0.0.1", 9999)):
        rows[:] = [
            SimpleNamespace(status="LISTEN", pid=pid, laddr=SimpleNamespace(ip=host, port=43000))
        ]
        assert not _exact_listener_owned(1456, 43000)
    rows[:] = [
        SimpleNamespace(
            status="LISTEN", pid=1456, laddr=SimpleNamespace(ip="127.0.0.1", port=43000)
        ),
        SimpleNamespace(
            status="LISTEN", pid=9999, laddr=SimpleNamespace(ip="127.0.0.1", port=43000)
        ),
    ]
    assert not _exact_listener_owned(1456, 43000)


def test_antigravity_post_never_sends_csrf_to_replaced_listener(monkeypatch, tmp_path):
    from neuralmeter.antigravity import _MODELS_PATH, _post

    install = tmp_path / "Antigravity.exe"
    endpoint = _Endpoint(43001, "private-csrf", 1456, 123.0, install)
    monkeypatch.setattr("neuralmeter.antigravity._owned_endpoint", lambda _path: None)

    class NeverConnect:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("connection attempted before ownership validation")

    monkeypatch.setattr("neuralmeter.antigravity.http.client.HTTPConnection", NeverConnect)
    assert _post(endpoint, _MODELS_PATH, 1.0) is None


def test_antigravity_process_endpoint_change_during_rpc_discards_snapshot(monkeypatch, tmp_path):

    install = tmp_path / "Antigravity.exe"
    install.touch()
    old = _Endpoint(43001, "csrf-old", 100, 1000.0)
    new = _Endpoint(43002, "csrf-new", 101, 1100.0)
    monkeypatch.setattr("neuralmeter.antigravity._owned_endpoint", lambda _install: new)
    calls = []

    def post(endpoint, path, *_args):
        calls.append(path)
        assert endpoint is old
        return {"userStatus": {"email": "never-export"}}

    snapshot = read_antigravity_quota(
        install_path=install,
        endpoint=old,
        post=post,
    )
    assert snapshot.status == "identity_changed"
    assert calls == []
    assert "csrf-old" not in repr(snapshot) and "never-export" not in repr(snapshot)


def test_antigravity_endpoint_probe_detects_process_change_between_polls(monkeypatch):
    from neuralmeter.antigravity import antigravity_endpoint_identity

    endpoint = [_Endpoint(40001, "csrf-a", 10, 100.0)]
    monkeypatch.setattr("neuralmeter.antigravity._antigravity_install", lambda: Path("fake"))
    monkeypatch.setattr("neuralmeter.antigravity._owned_endpoint", lambda _install: endpoint[0])
    first = antigravity_endpoint_identity()
    endpoint[0] = _Endpoint(40002, "csrf-b", 11, 101.0)
    second = antigravity_endpoint_identity()
    assert first != second
    assert "csrf" not in repr((first, second))


def test_antigravity_account_identity_change_during_rpc_discards_snapshot(tmp_path):
    calls = 0

    def post(_endpoint, path, *_args):
        nonlocal calls
        calls += 1
        if path.endswith("GetUserStatus"):
            account = "account-one" if calls == 1 else "account-two"
            return {
                "userStatus": {
                    "userId": account,
                    "cascadeModelConfigData": {
                        "clientModelConfigs": [
                            {
                                "modelOrAlias": {"model": "FLASH"},
                                "quotaInfo": {"remainingFraction": 0.5},
                            }
                        ]
                    },
                }
            }
        return {"groups": []}

    snapshot = read_antigravity_quota(
        install_path=Path("fake"),
        endpoint=_Endpoint(40011, "csrf"),
        post=post,
    )
    assert snapshot.status == "identity_changed"
    assert snapshot.buckets == ()
    assert "account-one" not in repr(snapshot)
