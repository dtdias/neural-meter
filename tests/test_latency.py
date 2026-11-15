import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "perf_check", Path(__file__).resolve().parent.parent / "scripts" / "perf_check.py"
)
perf_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_check)


def test_percentile():
    assert perf_check.percentile([5], 95) == 5
    assert perf_check.percentile([1, 2, 3, 4, 5], 50) == 3
    assert perf_check.percentile([0, 10], 50) == 5
    assert perf_check.percentile([3, 1, 2], 100) == 3
    with pytest.raises(ValueError):
        perf_check.percentile([], 50)


def test_synthetic_cli_delivery_latency(qapp, tmp_data_dir):
    lat = perf_check.measure_cli_delivery_latency(frames=20)
    assert len(lat) == 20
    assert max(lat) < 500
    assert perf_check.percentile(lat, 95) < 100


def test_process_handshake_measures_qt_ready_descendant_not_only_launcher(tmp_path):
    import psutil

    ready = tmp_path / ".neuralmeter-perf-ready.json"
    nonce = "test-owned-launcher-child-nonce"
    child_code = (
        "import json,os,sys,time; "
        "from pathlib import Path; "
        "Path(sys.argv[1]).write_text(json.dumps({'pid':os.getpid(),'nonce':sys.argv[2]})); "
        "time.sleep(20)"
    )
    launcher_code = (
        "import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',sys.argv[1],"
        "sys.argv[2],sys.argv[3]]); child.wait()"
    )
    records = {}
    with tempfile.TemporaryFile() as stderr:
        launcher = subprocess.Popen(
            [sys.executable, "-c", launcher_code, child_code, str(ready), nonce],
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            ready_pid = perf_check._wait_for_ready_pid(
                launcher,
                ready,
                nonce,
                psutil,
                records,
                timeout=3,
                stderr_path=tmp_path / "stderr.log",
            )
            assert ready_pid != launcher.pid
            assert ready_pid in {p.pid for p in perf_check._active_owned(records, psutil)}
            perf_check._record_owned_tree(launcher.pid, records, psutil)
            uss_mb, _rss_mb = perf_check._owned_tree_metrics(
                records, psutil, psutil.cpu_count() or 1
            )
            assert uss_mb > 0
            assert len(perf_check._active_owned(records, psutil)) >= 2
        finally:
            perf_check._terminate_owned_tree(launcher, records, psutil)


def test_process_handshake_timeout_and_early_exit_are_fail_closed(tmp_path):
    import psutil

    ready = tmp_path / ".neuralmeter-perf-ready.json"
    records = {}
    sleeping = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(TimeoutError, match="Qt-ready handshake timed out"):
            perf_check._wait_for_ready_pid(
                sleeping,
                ready,
                "nonce",
                psutil,
                records,
                timeout=0.08,
                stderr_path=tmp_path / "app.stderr.log",
            )
    finally:
        perf_check._terminate_owned_tree(sleeping, records, psutil)

    exited = subprocess.Popen(
        [sys.executable, "-c", "raise SystemExit(7)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with pytest.raises(RuntimeError, match="exit=7.*stderr="):
        perf_check._wait_for_ready_pid(
            exited,
            ready,
            "nonce",
            psutil,
            {},
            timeout=2,
            stderr_path=tmp_path / "captured-app.stderr.log",
        )
    exited.wait(timeout=2)


def test_perf_resource_thresholds_remain_hard_gates():
    result = {
        "uss_mb": 50.1,
        "cpu_pct_avg": 1.1,
        "fake_cli_delivery_p95_ms": 100.1,
    }
    assert len(perf_check.evaluate(result)) == 3


@pytest.mark.perf
def test_idle_resources():
    r = perf_check.measure_idle_resources(warmup=5, cpu_samples=5, frames=5)
    assert not perf_check.evaluate(r)
