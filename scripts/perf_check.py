"""Validacao de desempenho (RNF01 RAM/CPU ocioso, RNF02 latencia local).

Uso: uv run python scripts/perf_check.py [--json] [--report-only]
"""

import argparse
import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

USS_LIMIT_MB = 50.0
CPU_LIMIT_PCT = 1.0
LATENCY_P95_LIMIT_MS = 100.0
WS_PORT = 8765


def percentile(values: list[float], p: float) -> float:
    """Percentil por interpolacao linear (p em 0..100)."""
    if not values:
        raise ValueError("percentile of empty sequence")
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _usage_frame(i: int) -> str:
    return json.dumps(
        {
            "type": "usage",
            "provider": "claude",
            "model": "sonnet",
            "prompt_chars": 100 + i,
            "context_chars": 1000 * i,
        }
    )


def measure_ws_to_hud_latency(frames: int = 20, timeout: float = 2.0) -> list[float]:
    """In-process: frame `usage` enviado por cliente WS -> HUD re-renderizado (ms por frame).

    Requer QApplication ja criada e NEURALMETER_DATA_DIR apontando para um dir temporario.
    """
    from PySide6.QtWidgets import QApplication
    from websockets.asyncio.client import connect

    from neuralmeter.db import connect_db
    from neuralmeter.engine import DEFAULT_CHATGPT_LIMITS
    from neuralmeter.server import start_ws_server
    from neuralmeter.ui.hud import HudWindow, UpdateBridge
    from neuralmeter.ui.view_model import load_hud_view

    app = QApplication.instance()
    assert app is not None, "QApplication necessaria"
    db = connect_db()
    bridge = UpdateBridge()
    hud = HudWindow(
        view_loader=lambda now: load_hud_view(db, now, chatgpt_limits=DEFAULT_CHATGPT_LIMITS)
    )
    bridge.updated.connect(hud.on_update)
    done: list[float] = []

    def mark(kind: str, _payload: dict) -> None:
        if kind == "usage":
            done.append(time.perf_counter())  # conectado depois do HUD: roda apos o refresh

    bridge.updated.connect(mark)
    server = start_ws_server(db, port=0, on_update=bridge.emit_update)

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def call(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result(10)

    async def open_ws():
        return await connect(f"ws://127.0.0.1:{server.port}")

    ws = call(open_ws())
    results: list[float] = []
    try:
        call(ws.send(json.dumps({"type": "hello", "client": "perf"})))
        for i in range(frames):
            n = len(done)
            t0 = time.perf_counter()
            asyncio.run_coroutine_threadsafe(ws.send(_usage_frame(i)), loop)
            while len(done) == n:
                app.processEvents()
                if time.perf_counter() - t0 > timeout:
                    raise TimeoutError(f"HUD nao atualizou no frame {i}")
                time.sleep(0.0005)
            results.append((done[n] - t0) * 1000)
    finally:
        try:
            call(ws.close())
            call(asyncio.sleep(0.1))  # deixa tasks internas de close terminarem
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        server.stop()
        hud.close()
        db.close()
    return results


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ack_latencies(frames: int = 20) -> list[float]:
    from websockets.sync.client import connect

    out = []
    with connect(f"ws://127.0.0.1:{WS_PORT}") as ws:
        ws.send(json.dumps({"type": "hello", "client": "perf"}))
        ws.recv(timeout=5)
        for i in range(frames):
            t0 = time.perf_counter()
            ws.send(_usage_frame(i))
            ws.recv(timeout=5)
            out.append((time.perf_counter() - t0) * 1000)
    return out


def measure_idle_resources(warmup: float = 10.0, cpu_samples: int = 30, frames: int = 20) -> dict:
    """Sobe o app como subprocesso e mede USS/RSS, CPU ocioso e latencia do ack `usage`."""
    import psutil

    if _port_busy(WS_PORT):
        raise SystemExit(f"porta {WS_PORT} ocupada: feche o NeuralMeter em execucao")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "NEURALMETER_DATA_DIR": tmp}
        proc = subprocess.Popen(
            [sys.executable, "-m", "neuralmeter"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(warmup)
            if proc.poll() is not None:
                raise SystemExit(f"app terminou cedo (exit {proc.returncode})")
            p = psutil.Process(proc.pid)
            while kids := p.children():  # launcher do venv no Windows: mede o python real
                p = kids[0]
            mem = p.memory_full_info()
            p.cpu_percent(None)
            cores = psutil.cpu_count() or 1
            samples = [p.cpu_percent(interval=1) / cores for _ in range(cpu_samples)]
            ack = _ack_latencies(frames)
        finally:
            try:
                tree = [*psutil.Process(proc.pid).children(recursive=True)]
            except psutil.Error:
                tree = []
            for q in [*tree, proc]:
                try:
                    q.kill()
                except (psutil.Error, OSError):
                    pass
            proc.wait(10)
            psutil.wait_procs(tree, timeout=10)
    return {
        "uss_mb": round(mem.uss / 2**20, 1),
        "rss_mb": round(mem.rss / 2**20, 1),
        "cpu_pct_avg": round(sum(samples) / len(samples), 3),
        "ack_p50_ms": round(percentile(ack, 50), 2),
        "ack_p95_ms": round(percentile(ack, 95), 2),
    }


def evaluate(r: dict) -> list[str]:
    fails = []
    if r["uss_mb"] >= USS_LIMIT_MB:
        fails.append(f"USS {r['uss_mb']} MB >= {USS_LIMIT_MB}")
    if r["cpu_pct_avg"] >= CPU_LIMIT_PCT:
        fails.append(f"CPU {r['cpu_pct_avg']}% >= {CPU_LIMIT_PCT}")
    if r["ack_p95_ms"] >= LATENCY_P95_LIMIT_MS:
        fails.append(f"ack p95 {r['ack_p95_ms']} ms >= {LATENCY_P95_LIMIT_MS}")
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="sempre exit 0")
    args = ap.parse_args(argv)
    r = measure_idle_resources()
    fails = evaluate(r)
    if args.json:
        print(json.dumps({**r, "fails": fails}))
    else:
        print(f"USS         {r['uss_mb']:8.1f} MB   (meta < {USS_LIMIT_MB})")
        print(f"RSS         {r['rss_mb']:8.1f} MB   (informativo)")
        print(f"CPU ocioso  {r['cpu_pct_avg']:8.3f} %    (meta < {CPU_LIMIT_PCT})")
        print(f"ack p50     {r['ack_p50_ms']:8.2f} ms")
        print(f"ack p95     {r['ack_p95_ms']:8.2f} ms   (meta < {LATENCY_P95_LIMIT_MS})")
        for f in fails:
            print("FALHA:", f)
    if fails and not args.report_only:
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())
