import importlib.util
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


def test_ws_to_hud_latency(qapp, tmp_data_dir):
    lat = perf_check.measure_ws_to_hud_latency(frames=20)
    assert len(lat) == 20
    assert max(lat) < 500
    assert perf_check.percentile(lat, 95) < 100


@pytest.mark.perf
def test_idle_resources():
    r = perf_check.measure_idle_resources(warmup=5, cpu_samples=5, frames=5)
    assert not perf_check.evaluate(r)
