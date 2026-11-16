from __future__ import annotations

import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from neuralmeter.web import LocalDashboard, _OwnedSource


@pytest.fixture
def dashboard(tmp_path: Path):
    instance = LocalDashboard(port=0, data_dir=tmp_path)
    instance.start(open_browser=False)
    try:
        yield instance
    finally:
        instance.close()


def test_dashboard_binds_loopback_and_serves_local_assets(dashboard):
    assert dashboard.server.server_address[0] == "127.0.0.1"
    with urlopen(dashboard.url) as response:
        assert response.status == 200
        assert response.headers["Content-Security-Policy"]
        assert b"Seu trabalho, em perspectiva." in response.read()
    with urlopen(dashboard.url + "style.css") as response:
        assert b"--page" in response.read()
    with urlopen(dashboard.url + "app.js") as response:
        assert b"textContent" in response.read()
    for asset in ("mark", "dashboard", "usage", "refresh"):
        with urlopen(f"{dashboard.url}assets/icons/{asset}.svg") as response:
            assert response.headers["Content-Type"].startswith("image/svg+xml")
            assert b"<svg" in response.read()


def test_snapshot_unknown_without_user_data_or_consent(dashboard):
    with urlopen(dashboard.url + "api/snapshot") as response:
        data = json.loads(response.read())
    assert set(data["sources"]) == {"claude", "codex", "antigravity_cli"}
    assert all(
        item["status"] == "unknown" and not item["consent"] and item["quotas"] == []
        for item in data["sources"].values()
    )


def test_host_header_rejects_rebinding(dashboard):
    request = Request(
        dashboard.url,
        headers={"Host": f"localhost:{dashboard.server.server_port}"},
    )
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403


def test_cross_site_fetch_rejected(dashboard):
    request = Request(dashboard.url, headers={"Sec-Fetch-Site": "cross-site"})
    with pytest.raises(HTTPError) as error:
        urlopen(request)
    assert error.value.code == 403


def test_foreign_origin_and_path_traversal_rejected(dashboard):
    for path, headers in (
        ("api/snapshot", {"Origin": "http://evil.invalid"}),
        ("%2e%2e/src/neuralmeter/config.py", {}),
    ):
        request = Request(dashboard.url + path, headers=headers)
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code in {403, 404}


def test_server_thread_stops(dashboard):
    thread = dashboard.thread
    dashboard.close()
    assert not thread.is_alive()


@pytest.mark.parametrize(
    "framing",
    [
        "Content-Length: 2\r\nContent-Length: 2\r\n",
        "Content-Length: 1\r\nContent-Length: 2\r\n",
        "Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",
        "Transfer-Encoding:\r\n",
        "Transfer-Encoding: identity\r\n",
        "Transfer-Encoding: chunked\r\n",
        "Transfer-Encoding: identity\r\nTransfer-Encoding: chunked\r\n",
        "Content-Length: +2\r\n",
        "Content-Length: -2\r\n",
        "Content-Length: 2, 2\r\n",
        "Content-Length: \xff2\r\n",
        "",
    ],
)
def test_post_rejects_invalid_framing_before_body_read(dashboard, framing):
    with urlopen(dashboard.url + "api/bootstrap") as response:
        bootstrap = json.loads(response.read())
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    request = (
        f"POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:{dashboard.server.server_port}\r\n"
        f"Origin: {dashboard.url.rstrip('/')}\r\nSec-Fetch-Site: same-origin\r\n"
        f"Cookie: {cookie}\r\nX-NeuralMeter-CSRF: {bootstrap['csrf']}\r\n"
        "Content-Type: application/json\r\n" + framing + "\r\n{}"
    ).encode("latin-1")
    with socket.create_connection(("127.0.0.1", dashboard.server.server_port), timeout=2) as client:
        client.sendall(request)
        chunks = []
        while chunk := client.recv(4096):
            chunks.append(chunk)
        response = b"".join(chunks)
    assert b"400 Bad Request" in response
    assert b"invalid framing" in response
    assert all(
        not worker.enabled and worker.generation == 0
        for worker in dashboard.service.workers.values()
    )
    assert not (dashboard.service.data_dir / "settings.json").exists()


def test_post_body_limit_and_get_body_fail_closed(dashboard):
    with urlopen(dashboard.url + "api/bootstrap") as response:
        bootstrap = json.loads(response.read())
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    prefix = (
        f"Host: 127.0.0.1:{dashboard.server.server_port}\r\n"
        f"Origin: {dashboard.url.rstrip('/')}\r\nSec-Fetch-Site: same-origin\r\n"
        f"Cookie: {cookie}\r\nX-NeuralMeter-CSRF: {bootstrap['csrf']}\r\n"
    )
    for request, expected in (
        (
            "POST /api/refresh HTTP/1.1\r\n"
            + prefix
            + "Content-Type: application/json\r\nContent-Length: 8193\r\n\r\n",
            b"413 Content Too Large",
        ),
        (
            "GET /api/snapshot HTTP/1.1\r\n" + prefix + "Content-Length: 2\r\n\r\n{}",
            b"400 Bad Request",
        ),
        (
            "OPTIONS /api/snapshot HTTP/1.1\r\n" + prefix + "Transfer-Encoding: identity\r\n\r\n",
            b"400 Bad Request",
        ),
    ):
        with socket.create_connection(
            ("127.0.0.1", dashboard.server.server_port), timeout=2
        ) as client:
            client.sendall(request.encode("ascii"))
            chunks = []
            while chunk := client.recv(4096):
                chunks.append(chunk)
        assert expected in b"".join(chunks)
    assert all(
        not worker.enabled and worker.generation == 0
        for worker in dashboard.service.workers.values()
    )


def test_framing_rejection_closes_before_pipelined_request_and_options_requires_csrf(dashboard):
    with urlopen(dashboard.url + "api/bootstrap") as response:
        bootstrap = json.loads(response.read())
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    host = f"127.0.0.1:{dashboard.server.server_port}"
    origin = dashboard.url.rstrip("/")
    prefix = (
        f"Host: {host}\r\nOrigin: {origin}\r\nSec-Fetch-Site: same-origin\r\n"
        f"Cookie: {cookie}\r\nX-NeuralMeter-CSRF: {bootstrap['csrf']}\r\n"
    )
    smuggled = (
        f"POST /api/refresh HTTP/1.1\r\n{prefix}Content-Type: application/json\r\n"
        "Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
        f"GET /api/snapshot HTTP/1.1\r\nHost: {host}\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", dashboard.server.server_port), timeout=2) as client:
        client.sendall(smuggled.encode("ascii"))
        chunks = []
        while chunk := client.recv(4096):
            chunks.append(chunk)
    response = b"".join(chunks)
    assert response.count(b"HTTP/1.0") == 1
    assert b"400 Bad Request" in response
    assert b"200 OK" not in response

    options = f"OPTIONS /api/snapshot HTTP/1.1\r\n{prefix}\r\n".encode("ascii")
    with socket.create_connection(("127.0.0.1", dashboard.server.server_port), timeout=2) as client:
        client.sendall(options)
        assert b"405 Method Not Allowed" in client.recv(4096)
    csrf_line = f"X-NeuralMeter-CSRF: {bootstrap['csrf']}\r\n".encode()
    no_csrf = options.replace(csrf_line, b"")
    with socket.create_connection(("127.0.0.1", dashboard.server.server_port), timeout=2) as client:
        client.sendall(no_csrf)
        assert b"403 Forbidden" in client.recv(4096)


def test_source_consent_worker_idempotence_and_cooldown(monkeypatch):
    import neuralmeter.web as web

    now = [100.0]
    monkeypatch.setattr(web.time, "monotonic", lambda: now[0])
    worker = _OwnedSource("claude", lambda _cancel: None, lambda *_args: None)
    generation = worker.set_enabled(True)
    assert worker.set_enabled(True) == generation
    worker.rate_limited_until = 160.0
    assert worker.refresh() is False
    worker.set_enabled(False)
    worker.set_enabled(True)
    assert worker.rate_limited_until == 160.0
    assert worker.refresh() is False
    now[0] = 160.0
    assert worker.refresh() is True


def test_owned_worker_enforces_normal_interval_and_keeps_floor_across_reconsent():
    now = [100.0]
    calls = []
    second_call = threading.Event()

    def reader(_cancel):
        calls.append(len(calls) + 1)
        if len(calls) == 2:
            second_call.set()
        raise RuntimeError("synthetic read failure")

    worker = _OwnedSource(
        "claude",
        reader,
        lambda *_args: None,
        interval=60,
        monotonic=lambda: now[0],
    )
    worker.start()
    try:
        worker.set_enabled(True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with worker.condition:
                if not worker.read_inflight and calls:
                    break
            time.sleep(0.005)
        assert calls == [1]
        assert worker.refresh() is False
        assert worker.refresh() is False
        worker.set_enabled(False)
        worker.set_enabled(True)
        assert worker.refresh() is False
        now[0] = 159.0
        with worker.condition:
            worker.condition.notify_all()
        time.sleep(0.03)
        assert calls == [1]
        now[0] = 160.0
        with worker.condition:
            worker.condition.notify_all()
        assert second_call.wait(2)
        assert calls == [1, 2]
    finally:
        worker.close()
        worker.thread.join()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (60, 60.0),
        (90.5, 90.5),
        (1, 60.0),
        (3601, 3600.0),
        (0, 60.0),
        (-5, 60.0),
        (float("inf"), 60.0),
        (float("nan"), 60.0),
        (True, 60.0),
        ("60", 60.0),
        (10**10000, 60.0),
    ],
    ids=[
        "normal",
        "fractional",
        "below-min",
        "above-max",
        "zero",
        "negative",
        "infinity",
        "nan",
        "bool",
        "string",
        "huge-integer",
    ],
)
def test_retry_after_is_finite_positive_and_bounded(value, expected):
    from neuralmeter.web import _bounded_retry_delay

    assert _bounded_retry_delay(value) == expected


def test_source_service_closes_started_workers_if_startup_fails(tmp_path, monkeypatch):
    import neuralmeter.web as web

    started_workers = []
    original_start = web._OwnedSource.start

    def failing_start(worker):
        if worker.source == "codex":
            raise RuntimeError("synthetic startup fault")
        original_start(worker)
        started_workers.append(worker)

    monkeypatch.setattr(web._OwnedSource, "start", failing_start)
    with pytest.raises(RuntimeError, match="synthetic startup fault"):
        web._SourceService(tmp_path)
    assert len(started_workers) == 1
    assert all(not worker.thread.is_alive() for worker in started_workers)


@pytest.mark.parametrize(
    "consents",
    [
        (False, False, False),
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
        (True, True, True),
    ],
)
def test_startup_restores_only_literal_persisted_opt_ins_without_rewriting_settings(
    tmp_path, monkeypatch, consents
):
    import neuralmeter.web as web

    source_flags = dict(zip(web.SOURCES, consents, strict=True))
    settings = {web.CONSENT_KEYS[source]: source_flags[source] for source in web.SOURCES}
    settings.update({"theme": "aurora", "unrelated_preference": {"keep": True}})
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    original_bytes = settings_path.read_bytes()
    original_mtime = settings_path.stat().st_mtime_ns
    calls = {source: [] for source in web.SOURCES}
    identities = {source: [] for source in web.SOURCES}
    called = {source: threading.Event() for source in web.SOURCES}
    release = {source: threading.Event() for source in web.SOURCES}

    def reader_for(source):
        def reader(_cancel):
            calls[source].append("read")
            called[source].set()
            assert release[source].wait(2)
            if source == "antigravity_cli":
                return {"status": "no_local_data", "models": [], "periods": {}}
            window = SimpleNamespace(percent=None, reset_at=None)
            if source == "claude":
                return SimpleNamespace(
                    status="not_authenticated",
                    auth_status="not_authenticated",
                    five_hour=window,
                    seven_day=window,
                    observed_at=datetime.now(UTC),
                    retry_after=None,
                )
            return SimpleNamespace(
                status="not_authenticated",
                windows=[],
                observed_at=datetime.now(UTC),
                coverage_partial=False,
                retry_after=None,
            )

        return reader

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(lambda: {source: reader_for(source) for source in web.SOURCES}),
    )
    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(
            lambda: {
                source: (lambda source=source: identities[source].append("identity") or None)
                for source in ("claude", "codex")
            }
        ),
    )
    for _launch in range(2):
        service = web._SourceService(tmp_path)
        try:
            for source in web.SOURCES:
                if source_flags[source]:
                    assert called[source].wait(2), source
                    current = service.get_snapshot()["sources"][source]
                    assert current["quotas"] == []
                    assert current["models"] == [] and current["periods"] == {}
                    assert current["totals"] == dict.fromkeys(web._SOURCE_TOTAL_FIELDS)
                else:
                    assert calls[source] == []
                    assert identities[source] == []
                assert service.workers[source].enabled is source_flags[source]
                assert service.get_snapshot()["sources"][source]["consent"] is source_flags[source]
                if source in {"claude", "codex"}:
                    assert len(identities[source]) == int(source_flags[source])
            for source in web.SOURCES:
                release[source].set()
            for source in web.SOURCES:
                if source_flags[source]:
                    worker = service.workers[source]
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        with worker.condition:
                            if not worker.read_inflight:
                                break
                        time.sleep(0.005)
            assert all(len(calls[source]) == int(source_flags[source]) for source in web.SOURCES)
            for source in {"claude", "codex"}:
                assert len(identities[source]) == 2 * int(source_flags[source])
            assert settings_path.read_bytes() == original_bytes
            assert settings_path.stat().st_mtime_ns == original_mtime
        finally:
            for source in web.SOURCES:
                release[source].set()
            service.close()
        for source in web.SOURCES:
            calls[source].clear()
            called[source].clear()
            release[source].clear()
            if source in identities:
                identities[source].clear()


@pytest.mark.parametrize("source_name", ["claude", "codex", "antigravity_cli"])
@pytest.mark.parametrize("invalid", [1, "true", None, [], {}])
def test_startup_invalid_persisted_consent_never_enables_reader(
    tmp_path, monkeypatch, invalid, source_name
):
    import neuralmeter.web as web

    settings = {
        web.CONSENT_KEYS[source_name]: invalid,
        "theme": "light",
        "other_setting": "preserve",
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    original = path.read_bytes()
    reads = []
    identities = []
    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": lambda _cancel: reads.append("claude"),
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: None,
            }
        ),
    )
    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(lambda: {"claude": lambda: identities.append("identity")}),
    )
    service = web._SourceService(tmp_path)
    try:
        time.sleep(0.03)
        assert not service.workers[source_name].enabled
        assert reads == identities == []
        assert service.get_snapshot()["sources"][source_name]["consent"] is False
        assert path.read_bytes() == original
    finally:
        service.close()


def test_startup_partial_settings_missing_consent_keys_stays_opted_out(tmp_path, monkeypatch):
    import neuralmeter.web as web

    path = tmp_path / "settings.json"
    original = b'{"theme":"aurora","custom_setting":7}'
    path.write_bytes(original)
    calls = []
    identities = []
    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                source: (lambda _cancel, source=source: calls.append(source))
                for source in web.SOURCES
            }
        ),
    )
    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(
            lambda: {
                "claude": lambda: identities.append("claude"),
                "codex": lambda: identities.append("codex"),
            }
        ),
    )
    service = web._SourceService(tmp_path)
    try:
        time.sleep(0.03)
        assert calls == identities == []
        assert all(not worker.enabled for worker in service.workers.values())
        assert path.read_bytes() == original
    finally:
        service.close()


def test_refresh_spam_from_parallel_http_clients_coalesces_and_reconsent_keeps_floor(
    tmp_path, monkeypatch
):
    import neuralmeter.web as web

    calls = []
    first_read = threading.Event()
    window = SimpleNamespace(percent=31, reset_at=datetime.now(UTC) + timedelta(hours=1))

    def reader(_cancel):
        calls.append("read")
        first_read.set()
        return SimpleNamespace(
            status="available",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
            auth_status="authenticated",
            retry_after=None,
        )

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": reader,
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: {},
            }
        ),
    )
    monkeypatch.setattr(web._SourceService, "_identity_readers", staticmethod(lambda: {}))
    dashboard = LocalDashboard(port=0, data_dir=tmp_path)
    dashboard.start(open_browser=False)
    try:
        with urlopen(dashboard.url + "api/bootstrap") as response:
            bootstrap = json.loads(response.read())
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]

        def post(path, payload):
            request = Request(
                dashboard.url + path,
                data=json.dumps(payload).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                    "Origin": dashboard.url.rstrip("/"),
                    "Sec-Fetch-Site": "same-origin",
                    "X-NeuralMeter-CSRF": bootstrap["csrf"],
                },
                method="POST",
            )
            with urlopen(request) as response:
                return json.loads(response.read())

        assert post("api/consent", {"source": "claude", "enabled": True}) == {"ok": True}
        assert first_read.wait(2)
        worker = dashboard.service.workers["claude"]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with worker.condition:
                if not worker.read_inflight:
                    break
            time.sleep(0.005)
        generation = worker.current_generation()
        settings_before_idempotent_post = (tmp_path / "settings.json").read_bytes()
        mtime_before_idempotent_post = (tmp_path / "settings.json").stat().st_mtime_ns
        assert post("api/consent", {"source": "claude", "enabled": True}) == {"ok": True}
        assert worker.current_generation() == generation
        assert (tmp_path / "settings.json").read_bytes() == settings_before_idempotent_post
        assert (tmp_path / "settings.json").stat().st_mtime_ns == mtime_before_idempotent_post
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(
                pool.map(
                    lambda _n: post("api/refresh", {"source": "claude"}),
                    range(20),
                )
            )
        assert replies == [{"accepted": False, "reason": "cooldown"}] * 20
        assert post("api/refresh", {"source": "all"}) == {
            "accepted": False,
            "reason": "cooldown",
        }
        assert post("api/consent", {"source": "claude", "enabled": False}) == {"ok": True}
        assert post("api/consent", {"source": "claude", "enabled": True}) == {"ok": True}
        assert post("api/refresh", {"source": "claude"}) == {
            "accepted": False,
            "reason": "cooldown",
        }
        time.sleep(0.03)
        assert calls == ["read"]
    finally:
        dashboard.close()


@pytest.mark.parametrize("transition", ["identity-change", "disable-enable"])
def test_stale_429_retry_after_survives_identity_and_generation_fences(
    tmp_path, transition, monkeypatch
):
    import neuralmeter.web as web

    now = [1000.0]
    identity = ["a" * 64]
    started = threading.Event()
    release = threading.Event()
    second_read = threading.Event()
    calls = []
    window = SimpleNamespace(percent=23, reset_at=datetime.now(UTC) + timedelta(hours=1))

    def reader(cancel):
        calls.append(cancel)
        if len(calls) == 1:
            started.set()
            assert release.wait(2)
            # Deliberately return a 429 even when old generation's cancel is set.
            return SimpleNamespace(
                status="rate_limited",
                five_hour=window,
                seven_day=window,
                observed_at=datetime.now(UTC),
                auth_status="unknown",
                retry_after=60,
            )
        second_read.set()
        return SimpleNamespace(
            status="available",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
            auth_status="authenticated",
            retry_after=None,
            identity=identity[0],
        )

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": reader,
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: {},
            }
        ),
    )
    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(lambda: {"claude": lambda: identity[0]}),
    )
    service = web._SourceService(tmp_path, monotonic=lambda: now[0])
    try:
        service.set_consent("claude", True)
        assert started.wait(2)
        worker = service.workers["claude"]
        old_generation = worker.current_generation()
        if transition == "identity-change":
            identity[0] = "b" * 64
            assert service._observe_identity("claude", identity[0], old_generation) == 1
        else:
            service.set_consent("claude", False)
            service.set_consent("claude", True)
            assert calls[0].is_set()
            assert worker.current_generation() == old_generation + 2
        release.set()

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with worker.condition:
                limited_until = worker.rate_limited_until
                inflight = worker.read_inflight
            if limited_until >= 1060.0 and not inflight:
                break
            time.sleep(0.005)
        assert limited_until == 1060.0
        assert calls.__len__() == 1
        assert worker.refresh() is False
        service.set_consent("claude", True)
        stale = service.get_snapshot()["sources"]["claude"]
        assert stale["quotas"] == [] and stale["models"] == []
        assert not any(item.get("percent") == 23 for item in stale["quotas"])

        now[0] = 1059.0
        with worker.condition:
            worker.condition.notify_all()
        time.sleep(0.03)
        assert len(calls) == 1
        now[0] = 1060.0
        with worker.condition:
            worker.condition.notify_all()
        assert second_read.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = service.get_snapshot()["sources"]["claude"]
            if current["quotas"]:
                break
            time.sleep(0.005)
        assert len(calls) == 2
        assert [item["percent"] for item in current["quotas"]] == [23, 23]
        assert current["status"] == "available"
    finally:
        release.set()
        service.close()


def test_local_identity_change_fences_inflight_result_and_shutdown_joins(tmp_path, monkeypatch):
    import neuralmeter.web as web

    monkeypatch.setattr(web, "SOURCE_POLL_INTERVAL_SECONDS", 0.02)

    identity = ["a" * 64]
    started = threading.Event()
    release = threading.Event()
    calls = []
    identity_reads = []

    def reader(_cancel):
        calls.append(identity[0])
        if len(calls) == 1:
            started.set()
            assert release.wait(2)
        percent = 11 if calls[-1] == "a" * 64 else 22
        window = SimpleNamespace(percent=percent, reset_at=datetime.now(UTC) + timedelta(hours=1))
        return SimpleNamespace(
            status="available",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
            auth_status="authenticated",
            identity=calls[-1],
            retry_after=None,
        )

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": reader,
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: {},
            }
        ),
    )

    def identity_reader():
        identity_reads.append(identity[0])
        return identity[0]

    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(lambda: {"claude": identity_reader}),
    )
    dashboard = LocalDashboard(port=0, data_dir=tmp_path)
    try:
        assert calls == [] and identity_reads == []
        dashboard.service.get_snapshot()
        assert identity_reads == []
        dashboard.service.set_consent("claude", True)
        assert started.wait(2)
        worker = dashboard.service.workers["claude"]
        generation = worker.current_generation()
        stale_source = dashboard.service.snapshot["sources"]["claude"]
        stale_source.update(
            {
                "models": [{"model": "gemini-2.5-pro", "input_tokens": 7654321}],
                "periods": {"today": {"models": [{"output_tokens": 7654321}]}},
                "totals": {"input_tokens": 7654321},
                "coverage_partial": True,
            }
        )
        identity[0] = "b" * 64
        assert dashboard.service._observe_identity("claude", identity[0], generation) == 1
        while_snapshot = dashboard.service.get_snapshot()["sources"]["claude"]
        assert while_snapshot["quotas"] == []
        assert while_snapshot["models"] == [] and while_snapshot["periods"] == {}
        assert while_snapshot["totals"] == dict.fromkeys(web._SOURCE_TOTAL_FIELDS)
        assert "7654321" not in json.dumps(while_snapshot)
        assert len(calls) == 1
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            snapshot = dashboard.service.get_snapshot()["sources"]["claude"]
            if snapshot["quotas"]:
                break
            time.sleep(0.01)
        assert [item["percent"] for item in snapshot["quotas"]] == [22, 22]
        assert calls == ["a" * 64, "b" * 64]
        assert "a" * 64 not in json.dumps(snapshot)
        assert "b" * 64 not in json.dumps(snapshot)
        before_get = len(identity_reads)
        dashboard.service.get_snapshot()
        assert len(identity_reads) == before_get
    finally:
        release.set()
        dashboard.close()
    assert not dashboard.service.identity_thread.is_alive()
    assert all(not worker.thread.is_alive() for worker in dashboard.service.workers.values())


def test_unavailable_identity_baseline_does_not_restart_reader(tmp_path, monkeypatch):
    import neuralmeter.web as web

    reads = []
    identity_probes = []

    def reader(_cancel):
        reads.append("read")
        window = SimpleNamespace(percent=12, reset_at=datetime.now(UTC) + timedelta(hours=1))
        return SimpleNamespace(
            status="available",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
            auth_status="authenticated",
            identity=None,
            retry_after=None,
        )

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": reader,
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: {},
            }
        ),
    )

    def unknown_identity():
        identity_probes.append("probe")
        return None

    monkeypatch.setattr(
        web._SourceService,
        "_identity_readers",
        staticmethod(lambda: {"claude": unknown_identity}),
    )
    dashboard = LocalDashboard(port=0, data_dir=tmp_path)
    try:
        dashboard.service.set_consent("claude", True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not reads:
            time.sleep(0.01)
        time.sleep(0.05)
        assert reads == ["read"]
        assert dashboard.service.identity_epochs["claude"] == 0
        assert identity_probes
    finally:
        dashboard.close()


@pytest.mark.parametrize("source_name", ["claude", "codex", "antigravity_cli"])
def test_snapshot_ttl_clears_all_payload_fields_from_public_api(dashboard, source_name):
    import neuralmeter.web as web

    source = dashboard.service.snapshot["sources"][source_name]
    now = datetime.now(UTC)
    sentinel = 9876543210123
    source.update(
        {
            "status": "available",
            "status_label": "Dados disponíveis",
            "consent": True,
            "installed": True,
            "auth_status": "authenticated",
            "quotas": [
                {
                    "label": "expired",
                    "percent": 77,
                    "reset_at": (now - timedelta(seconds=1)).isoformat(),
                },
                {
                    "label": "fresh",
                    "percent": 33,
                    "reset_at": (now + timedelta(hours=1)).isoformat(),
                },
            ],
            "models": [{"model": "gemini-2.5-pro", "input_tokens": sentinel}],
            "periods": {
                "today": {"models": [{"model": "gemini-2.5-pro", "output_tokens": sentinel}]},
                "last_7_days": {"models": [{"model": "gemini-2.5-pro", "output_tokens": sentinel}]},
                "history": {"models": [{"model": "gemini-2.5-pro", "output_tokens": sentinel}]},
            },
            "totals": {
                "input_tokens": sentinel,
                "output_tokens": sentinel,
                "calculated_input_tokens": sentinel,
                "calculated_output_tokens": sentinel,
            },
            "coverage_partial": True,
            "observed_at": now.isoformat(),
            "future_data_field": sentinel,
        }
    )
    dashboard.service.settings[web.CONSENT_KEYS[source_name]] = True
    dashboard.service.snapshot_observed_mono[source_name] = 0.0
    dashboard.service.monotonic = lambda: web.SOURCE_SNAPSHOT_TTL_SECONDS + 1
    with urlopen(dashboard.url + "api/snapshot") as response:
        serialized = response.read().decode("utf-8")
        result = json.loads(serialized)["sources"][source_name]
    assert result["status"] == "unknown"
    assert result["status_label"] == "Desconhecido"
    assert result["auth_status"] == "unknown"
    assert result["consent"] is True and result["installed"] is True
    assert result["quotas"] == []
    assert result["models"] == []
    assert result["periods"] == {}
    assert result["totals"] == dict.fromkeys(web._SOURCE_TOTAL_FIELDS)
    assert result["coverage_partial"] is False
    assert result["observed_at"] is None
    assert str(sentinel) not in serialized
    assert "future_data_field" not in result


def test_snapshot_payload_key_inventory_is_explicit():
    import neuralmeter.web as web

    window = SimpleNamespace(percent=None, reset_at=None)
    claude = web._claude_dto(
        SimpleNamespace(
            status="available",
            auth_status="authenticated",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
        )
    )
    codex = web._codex_dto(
        SimpleNamespace(
            status="available", windows=[], observed_at=datetime.now(UTC), coverage_partial=False
        )
    )
    agy = web._agy_dto({"status": "available", "models": [], "periods": {}})
    api_keys = set().union(*(set(item) for item in (claude, codex, agy)))
    data_keys = api_keys - web._SOURCE_METADATA_FIELDS
    assert data_keys == web._SOURCE_DATA_FIELDS
    assert api_keys == web._SOURCE_DATA_FIELDS | web._SOURCE_METADATA_FIELDS


def test_expired_quota_window_does_not_expire_other_window(dashboard):
    source = dashboard.service.snapshot["sources"]["claude"]
    now = datetime.now(UTC)
    source["quotas"] = [
        {"label": "expired", "percent": 77, "reset_at": (now - timedelta(seconds=1)).isoformat()},
        {"label": "fresh", "percent": 33, "reset_at": (now + timedelta(hours=1)).isoformat()},
    ]
    current = dashboard.service.get_snapshot()["sources"]["claude"]
    assert current["quotas"][0]["percent"] is None
    assert current["quotas"][0]["expired"] is True
    assert current["quotas"][1]["percent"] == 33


def test_provider_exception_clears_previously_published_quota(tmp_path, monkeypatch):
    import neuralmeter.web as web

    monkeypatch.setattr(web, "SOURCE_POLL_INTERVAL_SECONDS", 0.02)

    reads = [0]
    error_seen = threading.Event()

    def reader(_cancel):
        reads[0] += 1
        if reads[0] > 1:
            error_seen.set()
            raise RuntimeError("synthetic private exception")
        window = SimpleNamespace(percent=44, reset_at=datetime.now(UTC) + timedelta(hours=1))
        return SimpleNamespace(
            status="available",
            five_hour=window,
            seven_day=window,
            observed_at=datetime.now(UTC),
            auth_status="authenticated",
            identity=None,
            retry_after=None,
        )

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": reader,
                "codex": lambda _cancel: None,
                "antigravity_cli": lambda _cancel: {},
            }
        ),
    )
    monkeypatch.setattr(web._SourceService, "_identity_readers", staticmethod(dict))
    dashboard = LocalDashboard(port=0, data_dir=tmp_path)
    try:
        dashboard.service.set_consent("claude", True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            quota = dashboard.service.get_snapshot()["sources"]["claude"]["quotas"]
            if quota:
                break
            time.sleep(0.01)
        assert [item["percent"] for item in quota] == [44, 44]
        assert error_seen.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = dashboard.service.get_snapshot()["sources"]["claude"]
            if current["status"] == "request_failed":
                break
            time.sleep(0.01)
        assert current["status"] == "request_failed"
        assert current["quotas"] == []
        assert "synthetic private exception" not in json.dumps(current)
    finally:
        dashboard.close()


def test_unavailable_fetch_clears_nested_antigravity_usage_payload(tmp_path, monkeypatch):
    import neuralmeter.web as web

    monkeypatch.setattr(web, "SOURCE_POLL_INTERVAL_SECONDS", 0.02)
    calls = []
    complete = threading.Event()
    sentinel = 765432109876

    def reader(_cancel):
        calls.append("read")
        if len(calls) == 1:
            return {
                "status": "partial",
                "coverage_partial": True,
                "models": [
                    {
                        "model": "gemini-2.5-pro",
                        "input_tokens": sentinel,
                        "output_tokens": sentinel,
                        "cache_read_tokens": sentinel,
                        "cache_write_tokens": sentinel,
                    }
                ],
                "periods": {
                    "today": {"models": [{"model": "gemini-2.5-pro", "input_tokens": sentinel}]},
                    "last_7_days": {
                        "models": [{"model": "gemini-2.5-pro", "input_tokens": sentinel}]
                    },
                },
            }
        complete.set()
        return {"status": "no_local_data", "models": [], "periods": {}}

    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": lambda _cancel: None,
                "codex": lambda _cancel: None,
                "antigravity_cli": reader,
            }
        ),
    )
    monkeypatch.setattr(web._SourceService, "_identity_readers", staticmethod(lambda: {}))
    service = web._SourceService(tmp_path)
    try:
        service.set_consent("antigravity_cli", True)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = service.get_snapshot()["sources"]["antigravity_cli"]
            if current["models"]:
                break
            time.sleep(0.005)
        assert current["models"] and current["periods"]["today"]["models"]
        assert complete.wait(2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = service.get_snapshot()["sources"]["antigravity_cli"]
            if current["status"] == "no_local_data":
                break
            time.sleep(0.005)
        assert current["status"] == "no_local_data"
        assert current["models"] == [] and current["periods"] == {}
        assert current["totals"] == dict.fromkeys(web._SOURCE_TOTAL_FIELDS)
        assert current["coverage_partial"] is False
        assert current["observed_at"] is None
        assert str(sentinel) not in json.dumps(current)
    finally:
        service.close()


def test_mutation_requires_same_origin_session_and_csrf(tmp_path, monkeypatch):
    import neuralmeter.web as web

    reads = []
    result = {
        "status": "available",
        "models": [
            {
                "model": "GEMINI-2.5-FLASH",
                "input_tokens": 0,
                "output_tokens": 9,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "calculated_input_tokens": 0,
                "calculated_output_tokens": 0,
                "coverage_partial": False,
            }
        ],
        "periods": {},
        "coverage_partial": False,
    }
    monkeypatch.setattr(
        web._SourceService,
        "_readers",
        staticmethod(
            lambda: {
                "claude": lambda _cancel: reads.append("claude"),
                "codex": lambda _cancel: reads.append("codex"),
                "antigravity_cli": lambda _cancel: (reads.append("agy"), result)[1],
            }
        ),
    )
    dashboard = LocalDashboard(port=0, data_dir=tmp_path)
    dashboard.start(open_browser=False)
    try:
        with urlopen(dashboard.url + "api/bootstrap") as response:
            bootstrap = json.loads(response.read())
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        assert reads == []

        def post(payload, *, csrf="", origin=None):
            headers = {
                "Content-Type": "application/json",
                "Cookie": cookie,
                "X-NeuralMeter-CSRF": csrf,
                "Sec-Fetch-Site": "same-origin",
            }
            if origin is not None:
                headers["Origin"] = origin
            request = Request(
                dashboard.url + "api/consent",
                data=json.dumps(payload).encode(),
                headers=headers,
                method="POST",
            )
            return urlopen(request)

        with pytest.raises(HTTPError) as denied:
            post({"source": "antigravity_cli", "enabled": True})
        assert denied.value.code == 403
        assert reads == []

        with pytest.raises(HTTPError) as foreign:
            post(
                {"source": "antigravity_cli", "enabled": True},
                csrf=bootstrap["csrf"],
                origin="http://evil.invalid",
            )
        assert foreign.value.code == 403
        assert reads == []

        with post(
            {"source": "antigravity_cli", "enabled": True},
            csrf=bootstrap["csrf"],
            origin=dashboard.url.rstrip("/"),
        ) as response:
            assert response.status == 200
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = dashboard.service.get_snapshot()["sources"]["antigravity_cli"]
            if current.get("models"):
                break
            time.sleep(0.01)
        assert reads == ["agy"]
        snapshot = dashboard.service.get_snapshot()["sources"]["antigravity_cli"]
        assert snapshot["models"][0]["model"] == "gemini-2.5-flash"
        assert snapshot["models"][0]["input_tokens"] == 0
        assert snapshot["models"][0]["output_tokens"] == 9
        assert "private" not in json.dumps(snapshot).casefold()

        with post(
            {"source": "antigravity_cli", "enabled": False},
            csrf=bootstrap["csrf"],
            origin=dashboard.url.rstrip("/"),
        ) as response:
            assert response.status == 200
        cleared = dashboard.service.get_snapshot()["sources"]["antigravity_cli"]
        assert cleared["consent"] is False
        assert cleared["status"] == "not_connected"
        assert cleared["models"] == [] and cleared["periods"] == {}
        assert cleared["totals"] == dict.fromkeys(web._SOURCE_TOTAL_FIELDS)
        assert cleared["quotas"] == [] and cleared["observed_at"] is None
        persisted = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
        assert persisted["antigravity_cli_usage_enabled"] is False
    finally:
        dashboard.close()
