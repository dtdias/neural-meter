import threading
import time

import pytest
from PySide6.QtCore import QTimer

from neuralmeter.claude_cli import CliSnapshot, QuotaPoller
from neuralmeter.ui.identity_probe import (
    IDENTITY_UNSEEN,
    IdentityProbeGate,
    IdentityProbeManager,
    identity_changed,
)


@pytest.mark.parametrize("identity", [None, "stable-fingerprint"])
def test_identity_baseline_and_stable_unknown_do_not_trigger_restart(identity):
    assert not identity_changed(IDENTITY_UNSEEN, identity)
    assert not identity_changed(identity, identity)
    assert identity_changed(identity, "rotated")
    assert identity_changed("stable-fingerprint", None)


def test_repeated_auth_failure_with_stable_credential_does_not_restart_collector():
    state = {}
    restarts = 0
    stable_credential_fingerprint = "opaque-process-local-test-fingerprint"
    for _ in range(20):
        previous = state.get("claude", IDENTITY_UNSEEN)
        state["claude"] = stable_credential_fingerprint
        restarts += int(identity_changed(previous, stable_credential_fingerprint))
        # Auth/quota errors carry no snapshot identity; probe baseline owns identity state.
    assert restarts == 0
    assert identity_changed(state["claude"], "credential-rotated")


def test_repeated_auth_failure_during_slow_fetch_keeps_stable_identity_worker(qapp):
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    fetch_calls = []
    fetch_results = []

    def fetch():
        fetch_calls.append(threading.get_ident())
        fetch_started.set()
        release_fetch.wait(2)
        return CliSnapshot("unsupported_auth", auth_status="unsupported_auth")

    poller = QuotaPoller(fetch_results.append, fetch=fetch, interval=60)
    poller.start()
    assert fetch_started.wait(1)
    worker_ident = poller._thread.ident

    manager = IdentityProbeManager({"claude": lambda: "stable-credential-fingerprint"})
    baseline = {"claude": IDENTITY_UNSEEN}
    transitions = []
    observations = []

    def observe(source, _epoch, _generation, current):
        previous = baseline[source]
        baseline[source] = current
        observations.append(current)
        if identity_changed(previous, current):
            transitions.append((previous, current))

    manager.result.connect(observe)
    try:
        for _ in range(10):
            assert manager.schedule("claude", generation=1) is not None
            deadline = time.monotonic() + 1
            while len(observations) < _ + 1 and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(0.002)
            assert len(observations) == _ + 1
        assert transitions == []
        assert poller._thread.ident == worker_ident
        assert len(fetch_calls) == 1
    finally:
        manager.stop()
        manager.join(timeout=1)
        release_fetch.set()
        poller.stop()
        poller.join(timeout=1)


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
