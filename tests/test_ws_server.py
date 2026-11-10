import asyncio
import json
import time
from datetime import UTC, datetime

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from neuralmeter.db import connect_db, from_db_ts, get_provider_state
from neuralmeter.server import WsServer, check_origin, start_ws_server


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURALMETER_DATA_DIR", str(tmp_path))
    return connect_db()


class _NonConn:
    """Vista das atualizações sem os eventos internos `conn`."""

    def __init__(self, updates):
        self._u = updates

    def __getitem__(self, i):
        return [u for u in self._u if u[0] != "conn"][i]


@pytest.fixture
def server(conn):
    updates = []
    s = start_ws_server(conn, port=0, on_update=lambda k, p: updates.append((k, p)))
    s.all_updates = updates
    s.updates = _NonConn(updates)
    yield s
    s.stop()


def url(s):
    return f"ws://127.0.0.1:{s.port}"


def run(coro):
    return asyncio.run(coro)


def test_check_origin():
    assert check_origin(None)
    assert check_origin("chrome-extension://abc")
    assert not check_origin("https://evil.com")


def test_usage_ack_and_db(server, conn):
    async def go():
        async with connect(url(server)) as ws:
            await ws.send(
                json.dumps(
                    {"type": "usage", "provider": "claude", "model": "sonnet", "prompt_chars": 42}
                )
            )
            return json.loads(await ws.recv())

    assert run(go()) == {"type": "ack", "ref": "usage"}
    rows = conn.execute("SELECT provider, prompt_chars FROM usage_events").fetchall()
    assert [(r["provider"], r["prompt_chars"]) for r in rows] == [("claude", 42)]
    assert server.updates[0][0] == "usage"
    assert server.updates[0][1]["provider"] == "claude"


def test_limit_5h(server, conn):
    reset = int(datetime(2026, 12, 1, tzinfo=UTC).timestamp() * 1000)

    async def go():
        async with connect(url(server)) as ws:
            await ws.send(
                json.dumps(
                    {"type": "limit", "provider": "claude", "scope": "5h", "reset_at": reset}
                )
            )
            return json.loads(await ws.recv())

    assert run(go())["type"] == "ack"
    st = get_provider_state(conn, "claude")
    assert from_db_ts(st["window_5h_reset_at"]) == datetime(2026, 12, 1, tzinfo=UTC)
    assert server.updates[0][0] == "limit"


def test_invalid_json_keeps_connection(server):
    async def go():
        async with connect(url(server)) as ws:
            await ws.send("{bad")
            err = json.loads(await ws.recv())
            await ws.send('{"type":"ping"}')
            return err, json.loads(await ws.recv())

    err, pong = run(go())
    assert err["type"] == "error" and err["reason"]
    assert pong == {"type": "pong"}


def test_origin_rejected(server):
    async def go():
        async with connect(url(server), origin="https://evil.com"):
            pass

    with pytest.raises(InvalidStatus) as ei:
        run(go())
    assert ei.value.response.status_code == 403


def test_origin_extension_ok(server):
    async def go():
        async with connect(url(server), origin="chrome-extension://abc") as ws:
            await ws.send('{"type":"ping"}')
            return json.loads(await ws.recv())

    assert run(go()) == {"type": "pong"}


def test_on_update_exception_does_not_break(conn):
    def boom(k, p):
        raise RuntimeError("x")

    s = start_ws_server(conn, port=0, on_update=boom)
    try:

        async def go():
            async with connect(url(s)) as ws:
                await ws.send(json.dumps({"type": "hello", "client": "t"}))
                await ws.recv()
                await ws.send('{"type":"ping"}')
                return json.loads(await ws.recv())

        assert run(go()) == {"type": "pong"}
    finally:
        s.stop()


def test_stop_ends_thread(conn):
    s = start_ws_server(conn, port=0)
    assert s.thread.is_alive()
    s.stop()
    assert not s.thread.is_alive()


def test_non_loopback_host(conn):
    with pytest.raises(ValueError):
        WsServer(conn, host="0.0.0.0")


def test_conn_events_open_and_close(server):
    async def go():
        async with connect(url(server)) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            await ws.recv()

    run(go())
    deadline = time.time() + 2
    while len([u for u in server.all_updates if u[0] == "conn"]) < 2:
        assert time.time() < deadline
        time.sleep(0.01)
    conns = [p["clients"] for k, p in server.all_updates if k == "conn"]
    assert conns[:2] == [1, 0]
