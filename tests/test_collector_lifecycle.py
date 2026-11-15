import time
from threading import Event, Thread

from neuralmeter.ui.collector_lifecycle import CollectorLifecycle
from neuralmeter.ui.quota_delivery import QuotaDeliveryGate


class SlowPoller:
    def __init__(self, callback):
        self.callback = callback
        self.started = False
        self.stop_requested = Event()
        self.finished = Event()
        self.join_called = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.finished.is_set()

    def stop(self):
        self.stop_requested.set()

    def join(self, timeout=None):
        self.join_called = True
        self.finished.wait(timeout)

    def finish(self):
        self.finished.set()


def drain_until(qapp, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.002)
    qapp.processEvents()


def test_identity_restart_disconnect_reconnect_never_orphans_collector(qapp):
    made = []
    delivered = []
    lifecycle = CollectorLifecycle()

    def factory(callback):
        poller = SlowPoller(callback)
        made.append(poller)
        return poller

    first_generation = lifecycle.start("codex", factory, delivered.append)
    first = made[0]
    assert first.started and lifecycle.active_count("codex") == 1

    restart_generation = lifecycle.restart("codex", factory, delivered.append)
    assert restart_generation > first_generation
    assert first.stop_requested.is_set()
    assert len(made) == 1  # restart waits asynchronously; never overwrites active pointer

    lifecycle.stop("codex")  # invalidate pending restart before its queued timer fires
    reconnect_generation = lifecycle.start("codex", factory, delivered.append)
    assert reconnect_generation > restart_generation
    first.callback("stale-before-release")
    assert delivered == []

    first.finish()
    drain_until(qapp, lambda: len(made) == 2)
    assert len(made) == 2
    current = made[1]
    assert current.started and lifecycle.active_count("codex") == 1
    assert lifecycle.generation("codex") == reconnect_generation

    first.callback("stale-after-reconnect")
    current.callback("current")
    assert delivered == ["current"]

    lifecycle.stop("codex")
    assert current.stop_requested.is_set()
    current.callback("must-not-read-after-disconnect")
    assert delivered == ["current"]
    current.finish()
    lifecycle.shutdown()
    lifecycle.join_all(timeout=0.1)
    assert lifecycle.active_count("codex") == 0
    assert all(poller.stop_requested.is_set() for poller in made)
    assert made[-1].join_called


def test_lifecycle_rejects_callback_queued_by_retired_worker(qapp):
    made = []
    results = []
    lifecycle = CollectorLifecycle()

    def factory(callback):
        made.append(SlowPoller(callback))
        return made[-1]

    lifecycle.start("antigravity", factory, results.append)
    old = made[0]
    lifecycle.stop("antigravity")
    lifecycle.start("antigravity", factory, results.append)
    old.callback("queued-old")
    assert results == []
    old.finish()
    drain_until(qapp, lambda: len(made) == 2)
    assert len(made) == 2
    lifecycle.stop("antigravity")
    made[1].finish()
    lifecycle.shutdown()
    lifecycle.join_all(timeout=0.1)


def test_disconnect_reconnect_rejects_snapshot_already_queued_from_retired_worker(qapp):
    made = []
    applied = []
    lifecycle = CollectorLifecycle()
    delivery = QuotaDeliveryGate(lambda _source, value: applied.append(value))

    def factory(callback):
        poller = SlowPoller(callback)
        made.append(poller)
        return poller

    first_delivery_generation = delivery.enable("claude")
    lifecycle.start("claude", factory, delivery.callback("claude", first_delivery_generation))
    retired = made[0]
    worker = Thread(target=retired.callback, args=("queued-before-disconnect",))
    worker.start()
    worker.join()
    assert applied == []

    delivery.invalidate("claude")
    lifecycle.stop("claude")
    current_delivery_generation = delivery.enable("claude")
    lifecycle.start("claude", factory, delivery.callback("claude", current_delivery_generation))
    qapp.processEvents()
    assert applied == []
    assert len(made) == 1  # old worker still retiring; replacement not launched/registered yet

    retired.finish()
    drain_until(qapp, lambda: len(made) == 2)
    current = made[1]
    worker = Thread(target=current.callback, args=("current-snapshot",))
    worker.start()
    worker.join()
    drain_until(qapp, lambda: bool(applied))
    assert applied == ["current-snapshot"]

    delivery.invalidate("claude")
    lifecycle.stop("claude")
    current.finish()
    lifecycle.shutdown()
    lifecycle.join_all(timeout=0.1)
