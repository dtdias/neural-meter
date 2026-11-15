"""CLI-only resource gate with Qt-ready process handshake and synthetic delivery latency."""

from __future__ import annotations

import argparse
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

USS_LIMIT_MB = 50.0
CPU_LIMIT_PCT = 1.0
LATENCY_P95_LIMIT_MS = 100.0


def percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * p / 100
    low, high = math.floor(rank), math.ceil(rank)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def measure_cli_delivery_latency(frames: int = 20, timeout: float = 2.0) -> list[float]:
    """Fake collector signal to HUD slot; excludes network and provider query."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication

    from neuralmeter.ui.hud import HudWindow

    app = QApplication.instance() or QApplication([])
    hud = HudWindow()

    class FakeCollectorBridge(QObject):
        result = Signal(dict)

    bridge = FakeCollectorBridge()
    received: list[float] = []

    def deliver(snapshot: dict) -> None:
        hud.set_cli_status(snapshot)
        received.append(time.perf_counter())

    bridge.result.connect(deliver)
    latencies = []
    try:
        for index in range(frames):
            mark = len(received)
            started = time.perf_counter()
            worker = threading.Thread(
                target=bridge.result.emit,
                args=({"status": "available", "five_hour": {"percent": index}},),
            )
            worker.start()
            worker.join()
            while len(received) == mark:
                app.processEvents()
                if time.perf_counter() - started > timeout:
                    raise TimeoutError(f"Qt HUD update timeout frame={index}")
                time.sleep(0.0005)
            latencies.append((received[mark] - started) * 1000)
    finally:
        hud.close()
    return latencies


def _record_owned_tree(root_pid: int, records: dict, psutil_module) -> None:
    try:
        root = psutil_module.Process(root_pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
        return
    for process in processes:
        try:
            records[process.pid] = (process.create_time(), process)
        except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
            continue


def _active_owned(records: dict, psutil_module) -> list:
    active = []
    for pid, (created_at, _old_process) in tuple(records.items()):
        try:
            process = psutil_module.Process(pid)
            if process.is_running() and process.create_time() == created_at:
                active.append(process)
        except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
            continue
    return active


def _wait_for_ready_pid(
    launcher,
    ready_file: Path,
    nonce: str,
    psutil_module,
    records: dict,
    *,
    timeout: float,
    stderr_path: Path,
) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _record_owned_tree(launcher.pid, records, psutil_module)
        if ready_file.is_file():
            try:
                message = json.loads(ready_file.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                message = None
            if isinstance(message, dict) and message.get("nonce") == nonce:
                pid = message.get("pid")
                if isinstance(pid, int) and pid in records:
                    active = {process.pid for process in _active_owned(records, psutil_module)}
                    if pid in active:
                        return pid
        if launcher.poll() is not None:
            raise RuntimeError(
                f"GUI process exited before Qt-ready handshake (exit={launcher.returncode}); "
                f"stderr={stderr_path}"
            )
        time.sleep(0.025)
    raise TimeoutError(f"Qt-ready handshake timed out; stderr={stderr_path}")


def _owned_tree_metrics(records: dict, psutil_module, cores: int) -> tuple[float, float]:
    active = _active_owned(records, psutil_module)
    if not active:
        raise RuntimeError("no live profiler-owned app processes at measurement")
    uss = 0
    rss = 0
    for process in active:
        memory = process.memory_full_info()
        uss += memory.uss
        rss += memory.rss
    return uss / 2**20, rss / 2**20


def _terminate_owned_tree(launcher, records: dict, psutil_module) -> None:
    _record_owned_tree(launcher.pid, records, psutil_module)
    active = _active_owned(records, psutil_module)
    # Terminate recorded descendants first; never enumerate or kill unrelated processes.
    active.sort(key=lambda process: process.pid == launcher.pid)
    for process in active:
        try:
            process.terminate()
        except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
            continue
    _, alive = psutil_module.wait_procs(active, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
            continue
    try:
        launcher.wait(timeout=5)
    except subprocess.TimeoutExpired:
        # Only launcher PID created by this profiler.
        launcher.kill()
        launcher.wait(timeout=5)


def measure_idle_resources(
    warmup: float = 10.0,
    cpu_samples: int = 30,
    frames: int = 20,
    readiness_timeout: float = 30.0,
    artifact_dir: Path | str | None = None,
) -> dict:
    """Measure ready QApplication process tree owned by this profiler instance."""
    import psutil

    artifacts = Path(
        artifact_dir
        or os.environ.get("NEURALMETER_PERF_ARTIFACT_DIR")
        or (Path(tempfile.gettempdir()) / "neuralmeter-perf")
    )
    artifacts.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    stderr_path = artifacts / f"app-stderr-{run_id}.log"
    with tempfile.TemporaryDirectory(prefix="neuralmeter-perf-") as tmp:
        data_dir = Path(tmp)
        ready_file = data_dir / ".neuralmeter-perf-ready.json"
        nonce = secrets.token_hex(24)
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "NEURALMETER_DATA_DIR": str(data_dir),
            "NEURALMETER_PERF_READY_FILE": str(ready_file),
            "NEURALMETER_PERF_NONCE": nonce,
            "PYTHONIOENCODING": "utf-8",
        }
        windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        if windows_fonts.is_dir():
            env["QT_QPA_FONTDIR"] = str(windows_fonts)
        records: dict = {}
        with stderr_path.open("wb") as stderr_stream:
            launcher = subprocess.Popen(
                [sys.executable, "-m", "neuralmeter"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=stderr_stream,
            )
            try:
                ready_pid = _wait_for_ready_pid(
                    launcher,
                    ready_file,
                    nonce,
                    psutil,
                    records,
                    timeout=readiness_timeout,
                    stderr_path=stderr_path,
                )
                _record_owned_tree(launcher.pid, records, psutil)
                if ready_pid not in records:
                    raise RuntimeError("Qt-ready PID not in profiler-owned process tree")
                time.sleep(warmup)
                if launcher.poll() is not None:
                    raise RuntimeError(
                        f"GUI launcher exited during warmup (exit={launcher.returncode}); "
                        f"stderr={stderr_path}"
                    )
                _record_owned_tree(launcher.pid, records, psutil)
                active = _active_owned(records, psutil)
                for process in active:
                    process.cpu_percent(None)
                cores = psutil.cpu_count(logical=True) or 1
                cpu_samples_pct = []
                for _ in range(max(1, cpu_samples)):
                    time.sleep(1)
                    if launcher.poll() is not None:
                        raise RuntimeError(
                            f"GUI launcher exited during CPU sampling "
                            f"(exit={launcher.returncode}); stderr={stderr_path}"
                        )
                    _record_owned_tree(launcher.pid, records, psutil)
                    active = _active_owned(records, psutil)
                    cpu_samples_pct.append(
                        sum(process.cpu_percent(None) for process in active) / cores
                    )
                uss_mb, rss_mb = _owned_tree_metrics(records, psutil, cores)
                latency = measure_cli_delivery_latency(frames)
                count = len(_active_owned(records, psutil))
            finally:
                _terminate_owned_tree(launcher, records, psutil)
    return {
        "uss_mb": round(uss_mb, 1),
        "rss_mb": round(rss_mb, 1),
        "cpu_pct_avg": round(sum(cpu_samples_pct) / len(cpu_samples_pct), 3),
        "measured_process_count": count,
        "readiness": "QApplication + visible HudWindow event loop handshake",
        "fake_cli_delivery_p50_ms": round(percentile(latency, 50), 2),
        "fake_cli_delivery_p95_ms": round(percentile(latency, 95), 2),
        "latency_evidence": "synthetic collector signal; real provider and network excluded",
        "app_stderr_path": str(stderr_path),
    }


def evaluate(result: dict) -> list[str]:
    failures = []
    if result["uss_mb"] >= USS_LIMIT_MB:
        failures.append(f"USS {result['uss_mb']} MB >= {USS_LIMIT_MB}")
    if result["cpu_pct_avg"] >= CPU_LIMIT_PCT:
        failures.append(f"CPU {result['cpu_pct_avg']}% >= {CPU_LIMIT_PCT}")
    if result["fake_cli_delivery_p95_ms"] >= LATENCY_P95_LIMIT_MS:
        latency = result["fake_cli_delivery_p95_ms"]
        failures.append(f"fake CLI delivery p95 {latency} ms >= {LATENCY_P95_LIMIT_MS}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args(argv)
    result = measure_idle_resources(artifact_dir=args.artifact_dir)
    failures = evaluate(result)
    result["fails"] = failures
    print(json.dumps(result, indent=None if args.json else 2))
    return int(bool(failures) and not args.report_only)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    raise SystemExit(main())
