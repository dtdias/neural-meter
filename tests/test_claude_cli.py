import io
import json
import threading
import time
import urllib.error
from datetime import UTC, datetime
from email.message import Message

from neuralmeter.claude_cli import (
    CliSnapshot,
    QuotaPoller,
    _NoRedirect,
    _run_cli_metadata,
    _timestamp,
    cli_auth_status,
    credential_identity,
    parse_usage,
    read_usage,
)


def test_parse_usage_valid_and_unknown():
    five, seven = parse_usage(
        {
            "five_hour": {"utilization": 70, "resets_at": "2026-11-14T12:00:00Z"},
            "seven_day": {"utilization": True, "resets_at": None},
        }
    )
    assert five.percent == 70
    assert five.reset_at == datetime(2026, 11, 14, 12, tzinfo=UTC)
    assert seven.percent is None and seven.reset_at is None


def test_timestamp_rejects_invalid_values():
    assert _timestamp(True) is None
    assert _timestamp("not-a-date") is None
    assert _timestamp(float("nan")) is None


def test_redirects_are_not_followed():
    assert (
        _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://attacker.invalid/")
        is None
    )


def test_cli_metadata_subprocess_has_bounded_output_and_no_auth_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "secret-test")
    captured = {}

    class Process:
        def __init__(self):
            self.stdout = io.BytesIO(b'{"loggedIn":false}')
            self.stderr = io.BytesIO(b"private stderr")

        def wait(self, timeout):
            assert timeout == 3
            return 0

        def kill(self):
            raise AssertionError("unexpected kill")

    def launch(args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return Process()

    monkeypatch.setattr("neuralmeter.claude_cli.subprocess.Popen", launch)
    output = _run_cli_metadata("C:/trusted/claude.exe", ["auth", "status", "--json"], 3)
    assert output == b'{"loggedIn":false}'
    assert captured["kwargs"]["shell"] is False
    assert "ANTHROPIC_API_KEY" not in captured["kwargs"]["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in captured["kwargs"]["env"]


def test_cli_auth_status_accepts_public_claude_code_auth_shapes(monkeypatch):
    from neuralmeter import claude_cli

    payloads = (
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "claudeAi", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "CLAUDE.AI", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "oauth"},
        {"loggedIn": True, "apiProvider": "firstParty"},
    )
    for payload in payloads:
        monkeypatch.setattr(
            claude_cli,
            "_run_cli_metadata",
            lambda *_args, document=payload: json.dumps(document).encode(),
        )
        assert cli_auth_status("C:/trusted/claude.exe") == "authenticated"


def test_cli_auth_status_rejects_untrusted_or_invalid_auth_shapes(monkeypatch):
    from neuralmeter import claude_cli

    payloads = (
        {"loggedIn": False, "authMethod": "claude.ai", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "thirdParty"},
        {"loggedIn": True, "apiProvider": "thirdParty"},
        {"loggedIn": True, "authMethod": 7, "apiProvider": "firstParty"},
        {"loggedIn": True},
        [],
    )
    for payload in payloads:
        monkeypatch.setattr(
            claude_cli,
            "_run_cli_metadata",
            lambda *_args, document=payload: json.dumps(document).encode(),
        )
        expected = (
            "not_authenticated"
            if isinstance(payload, dict) and payload.get("loggedIn") is False
            else "unsupported_auth"
        )
        assert cli_auth_status("C:/trusted/claude.exe") == expected


def test_read_usage_requires_cli_and_does_not_touch_credential(tmp_path, monkeypatch):
    file = tmp_path / ".credentials.json"
    original = b'{"claudeAiOauth":{"accessToken":"sentinel"}}'
    file.write_bytes(original)
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: None)
    snap = read_usage(path=file)
    assert snap.status == "cli_not_installed"
    assert file.read_bytes() == original


def test_bad_or_missing_credentials_are_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: "C:/claude.exe")
    monkeypatch.setattr("neuralmeter.claude_cli.cli_auth_status", lambda _: "authenticated")
    snap = read_usage(path=tmp_path / "missing")
    assert snap.status == "quota_unavailable"
    assert snap.auth_status == "authenticated"
    credential = tmp_path / "bad"
    credential.write_text("not-json", encoding="utf-8")
    assert read_usage(path=credential).status == "request_failed"


def test_auth_token_sent_only_to_fixed_url_and_never_in_snapshot(tmp_path, monkeypatch):
    token = "private-sentinel-token"
    creds = tmp_path / "creds"
    creds.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": token,
                    "expiresAt": 4102444800000,
                    "scopes": ["user:profile"],
                },
                "apiProvider": "firstParty",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: "C:/trusted/claude.exe")
    monkeypatch.setattr(
        "neuralmeter.claude_cli._run_cli_metadata",
        lambda *_args: b'{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty"}',
    )

    class Response:
        def geturl(self):
            return "https://api.anthropic.com/api/oauth/usage"

        def read(self, size):
            assert size > 0
            return b'{"five_hour":{"utilization":25},"seven_day":{"utilization":50}}'

        def close(self):
            pass

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == "https://api.anthropic.com/api/oauth/usage"
            assert request.get_header("Authorization") == f"Bearer {token}"
            assert timeout <= 5
            return Response()

    snap = read_usage(path=creds, opener=Opener())
    assert snap.status == "available"
    assert snap.auth_status == "authenticated"
    assert snap.five_hour.percent == 25 and snap.seven_day.percent == 50
    assert token not in repr(snap)
    assert snap.identity is not None and len(snap.identity) == 64
    assert token not in snap.identity


def test_credential_rotation_during_usage_request_discards_snapshot(tmp_path, monkeypatch):
    credential = tmp_path / "creds"
    payload = {
        "claudeAiOauth": {
            "accessToken": "credential-old",
            "expiresAt": 4102444800000,
            "scopes": ["user:profile"],
        }
    }
    credential.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: "C:/trusted/claude.exe")
    monkeypatch.setattr("neuralmeter.claude_cli.cli_auth_status", lambda _: "authenticated")
    before = credential_identity(credential)

    class Response:
        def geturl(self):
            return "https://api.anthropic.com/api/oauth/usage"

        def read(self, _size):
            credential.write_text(
                json.dumps(
                    {
                        **payload,
                        "claudeAiOauth": {
                            **payload["claudeAiOauth"],
                            "accessToken": "credential-new",
                        },
                    }
                ),
                encoding="utf-8",
            )
            return b'{"five_hour":{"utilization":5}}'

        def close(self):
            pass

    class Opener:
        def open(self, *_args, **_kwargs):
            return Response()

    snapshot = read_usage(path=credential, opener=Opener())
    assert snapshot.status == "identity_changed"
    assert snapshot.five_hour.percent is None
    assert credential_identity(credential) != before


def test_claude_identity_probe_detects_rotation_between_polls(tmp_path):
    from neuralmeter.claude_cli import credential_identity

    credential = tmp_path / "creds"
    credential.write_text('{"opaque":"credential-A"}', encoding="utf-8")
    identity_a = credential_identity(credential)
    credential.write_text('{"opaque":"credential-B"}', encoding="utf-8")
    identity_b = credential_identity(credential)
    assert identity_a != identity_b
    assert "credential-A" not in identity_a and "credential-B" not in identity_b


def test_expired_credential_does_not_send_request(tmp_path, monkeypatch):
    credential = tmp_path / "creds"
    credential.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "secret-marker",
                    "expiresAt": 1,
                    "scopes": ["user:profile", "user:inference"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: "C:/trusted/claude.exe")
    monkeypatch.setattr("neuralmeter.claude_cli.cli_auth_status", lambda _: "authenticated")

    class MustNotOpen:
        def open(self, *_args, **_kwargs):
            raise AssertionError("expired token must not be sent")

    original = credential.read_bytes()
    assert read_usage(path=credential, opener=MustNotOpen()).status == "quota_unavailable"
    assert credential.read_bytes() == original


def test_http_failures_are_sanitized_and_429_retry_is_bounded(tmp_path, monkeypatch):
    credential = tmp_path / "creds"
    credential.write_text(
        json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "secret-marker",
                    "expiresAt": 4102444800000,
                    "scopes": ["user:profile", "user:inference"],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("neuralmeter.claude_cli.discover_claude", lambda: "C:/trusted/claude.exe")
    monkeypatch.setattr("neuralmeter.claude_cli.cli_auth_status", lambda _: "authenticated")

    class FailingOpener:
        code = 429

        def open(self, request, timeout):
            headers = Message()
            headers["Retry-After"] = "90000"
            raise urllib.error.HTTPError(
                request.full_url, self.code, "private error", headers, None
            )

    limited = read_usage(path=credential, opener=FailingOpener())
    assert limited.status == "rate_limited" and limited.retry_after == 3600
    FailingOpener.code = 401
    unauthorized = read_usage(path=credential, opener=FailingOpener())
    assert unauthorized.status == "quota_unavailable" and unauthorized.retry_after is None
    assert unauthorized.auth_status == "authenticated"
    assert "secret-marker" not in repr((limited, unauthorized))


def test_poller_stops_and_suppresses_late_result():
    entered, release = threading.Event(), threading.Event()
    seen = []

    def fetch():
        entered.set()
        release.wait(2)
        return CliSnapshot("available")

    poller = QuotaPoller(seen.append, fetch=fetch)
    poller.start()
    assert entered.wait(1)
    poller.stop()
    release.set()
    time.sleep(0.05)
    assert seen == []


def test_manual_refresh_wakes_existing_poller_without_spawning_second_worker():
    calls = []
    second_call = threading.Event()

    def fetch():
        calls.append(threading.get_ident())
        if len(calls) >= 2:
            second_call.set()
        return CliSnapshot("quota_unavailable", auth_status="authenticated")

    poller = QuotaPoller(lambda _snapshot: None, fetch=fetch, interval=3600)
    poller.start()
    deadline = time.monotonic() + 1
    while not calls and time.monotonic() < deadline:
        time.sleep(0.002)
    assert len(calls) == 1
    poller.request_refresh()
    assert second_call.wait(1)
    assert len(set(calls)) == 1
    poller.stop()
    poller.join(timeout=1)


def test_manual_refresh_respects_provider_rate_limit_cooldown():
    observed = threading.Event()

    def fetch():
        return CliSnapshot("rate_limited", retry_after=60)

    poller = QuotaPoller(lambda _snapshot: observed.set(), fetch=fetch, interval=60)
    poller.start()
    assert observed.wait(1)
    deadline = time.monotonic() + 1
    while poller._rate_limit_until == 0 and time.monotonic() < deadline:
        time.sleep(0.002)
    assert poller._rate_limit_until > time.monotonic()
    assert poller.request_refresh() is False
    poller.stop()
    poller.join(timeout=1)
