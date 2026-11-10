"""Servidor WebSocket loopback em thread daemon (asyncio próprio)."""

import asyncio
import logging
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus

from websockets.asyncio.server import serve

from neuralmeter.config import WS_HOST, WS_PORT
from neuralmeter.server.handler import handle_message
from neuralmeter.server.protocol import (
    MAX_PAYLOAD_BYTES,
    ProtocolError,
    encode_reply,
    parse_message,
)

log = logging.getLogger(__name__)


def check_origin(origin: str | None) -> bool:
    """Origin ausente (clientes locais/testes) ou de extensão é aceito."""
    return origin is None or origin.startswith("chrome-extension://")


class WsServer:
    """Servidor WS. `on_update(kind, payload)` é chamado NA THREAD WS (não na thread da UI);
    a UI deve fazer a ponte (ex.: Qt signal). Exceções do callback são logadas."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        host: str = WS_HOST,
        port: int = WS_PORT,
        on_update: Callable[[str, dict], None] | None = None,
    ):
        if host != "127.0.0.1":
            raise ValueError("WS server must bind to 127.0.0.1 only")
        self._conn = conn
        self._host = host
        self._port = port
        self._on_update = on_update
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._clients = 0
        self._error: BaseException | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def connected_clients(self) -> int:
        return self._clients

    def _process_request(self, connection, request):
        if not check_origin(request.headers.get("Origin")):
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden origin\n")
        return None

    async def _handler(self, ws):
        self._clients += 1
        self._notify("conn", {"clients": self._clients})
        try:
            async for raw in ws:
                try:
                    msg = parse_message(raw, datetime.now(UTC))
                    reply, update = handle_message(self._conn, msg)
                except ProtocolError as e:
                    await ws.send(encode_reply("error", reason=str(e)))
                    continue
                except Exception:
                    log.exception("handler failure")
                    await ws.send(encode_reply("error", reason="internal error"))
                    continue
                if update is not None:
                    self._notify(*update)
                await ws.send(json_reply(reply))
        finally:
            self._clients -= 1
            self._notify("conn", {"clients": self._clients})

    def _notify(self, kind: str, payload: dict) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(kind, payload)
        except Exception:
            log.exception("on_update failed")

    async def _main(self):
        self._stop = asyncio.Event()
        try:
            async with serve(
                self._handler,
                self._host,
                self._port,
                max_size=MAX_PAYLOAD_BYTES,
                process_request=self._process_request,
            ) as server:
                self._port = server.sockets[0].getsockname()[1]
                self.ready.set()
                await self._stop.wait()
        except BaseException as e:
            self._error = e
            raise
        finally:
            self.ready.set()

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:
            log.exception("ws server crashed")
        finally:
            self._loop.close()

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="neuralmeter-ws", daemon=True)
        self.thread.start()
        self.ready.wait(10)
        if self._error is not None:
            raise RuntimeError(f"ws server failed to start: {self._error}") from self._error

    def stop(self) -> None:
        loop, ev = self._loop, self._stop
        if loop is not None and ev is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:
                pass
        if self.thread is not None:
            self.thread.join(timeout=5)


def json_reply(reply: dict) -> str:
    fields = {k: v for k, v in reply.items() if k != "type"}
    return encode_reply(reply["type"], **fields)


def start_ws_server(conn: sqlite3.Connection, **kw) -> WsServer:
    """Cria e inicia o servidor; retorna a instância já pronta."""
    server = WsServer(conn, **kw)
    server.start()
    return server
