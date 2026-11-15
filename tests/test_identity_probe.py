import threading
import time

import pytest
from PySide6.QtCore import QTimer

from neuralmeter.ui.identity_probe import IdentityProbeGate, IdentityProbeManager


def test_slow_identity_probe_never_blocks_qt_and_emits_queued_result(qapp):
    entered = threading.Event()
    release = threading.Event()
    results = []

    def slow_probe():
        entered.set()
        release.wait(2)
        return "identity-a"

    manager = IdentityProbeManager({"antigravity": slow_probe})
    manager.result.connect(lambda *args: results.append(args))
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    assert manager.schedule("antigravity") is not None
    assert entered.wait(1)
    deadline = time.monotonic() + 0.15
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    assert len(ticks) >= 5
    assert results == []
    release.set()
    deadline = time.monotonic() + 1
    while not results and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    assert results and results[0][0] == "antigravity" and results[0][3] == "identity-a"
    timer.stop()
    manager.stop()
    manager.join(timeout=1)


def test_cancelled_probe_result_cannot_resurrect_disconnected_source(qapp):
    entered = threading.Event()
    release = threading.Event()
    results = []
    manager = IdentityProbeManager(
        {"codex": lambda: (entered.set(), release.wait(2), "old-account")[-1]}
    )
    manager.result.connect(lambda *args: results.append(args))
    manager.schedule("codex")
    assert entered.wait(1)
    manager.cancel("codex")
    release.set()
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    assert results == []
    manager.stop()
    manager.join(timeout=1)


@pytest.mark.parametrize("source", ["claude", "codex", "antigravity"])
def test_already_queued_old_identity_result_rejected_after_reconnect(qapp, source):
    entered = threading.Event()
    release = threading.Event()
    applied = []
    probes = [0]

    def probe():
        probes[0] += 1
        if probes[0] == 1:
            entered.set()
            release.wait(2)
            return "old-identity"
        return "new-identity"

    manager = IdentityProbeManager({source: probe})
    current_generation = [10]

    def apply(applied_source, _epoch, _generation, identity):
        applied.append((applied_source, identity))

    receiver = IdentityProbeGate(manager, lambda _source: current_generation[0], apply)
    manager.result.connect(receiver.consume)
    manager.schedule(source, current_generation[0])
    assert entered.wait(1)
    release.set()
    deadline = time.monotonic() + 1
    while manager.pending(source) and time.monotonic() < deadline:
        time.sleep(0.002)
    # Worker has emitted old result; Qt event queue remains deliberately unprocessed.
    assert manager.pending(source) is False
    manager.cancel(source, generation=11)
    current_generation[0] = 12
    manager.schedule(source, current_generation[0])
    deadline = time.monotonic() + 1
    while len(applied) < 1 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    assert applied == [(source, "new-identity")]
    manager.stop()
    manager.join(timeout=1)
