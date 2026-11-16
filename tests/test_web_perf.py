import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "perf_web_check", Path(__file__).resolve().parent.parent / "scripts" / "perf_web_check.py"
)
perf_web = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(perf_web)


def test_percentile():
    assert perf_web.percentile([5], 95) == 5
    assert perf_web.percentile([1, 2, 3, 4, 5], 50) == 3
    assert perf_web.percentile([0, 10], 50) == 5
    with pytest.raises(ValueError):
        perf_web.percentile([], 50)


def test_web_http_resource_gate_thresholds():
    good = {
        "uss_mb": 40,
        "cpu_pct_avg": 0.4,
        "http_snapshot_p95_ms": 60,
    }
    assert perf_web.evaluate(good) == []
    bad = {
        "uss_mb": 50,
        "cpu_pct_avg": 1,
        "http_snapshot_p95_ms": 100,
    }
    assert len(perf_web.evaluate(bad)) == 3
