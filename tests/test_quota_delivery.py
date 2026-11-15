from threading import Thread

from PySide6.QtCore import QCoreApplication

from neuralmeter.ui.quota_delivery import QuotaDeliveryGate


def test_queued_snapshot_rejected_after_disconnect_and_reconnect(qapp):
    applied = []
    gate = QuotaDeliveryGate(lambda source, snapshot: applied.append((source, snapshot)))
    generation = gate.enable("codex")
    stale_callback = gate.callback("codex", generation)

    worker = Thread(target=stale_callback, args=("queued-before-disconnect",))
    worker.start()
    worker.join()
    gate.invalidate("codex")
    QCoreApplication.processEvents()
    assert applied == []

    new_generation = gate.enable("codex")
    assert new_generation > generation
    worker = Thread(target=stale_callback, args=("queued-before-reconnect",))
    worker.start()
    worker.join()
    worker = Thread(target=gate.callback("codex", new_generation), args=("current",))
    worker.start()
    worker.join()
    QCoreApplication.processEvents()
    assert applied == [("codex", "current")]


def test_each_source_generation_is_independent(qapp):
    applied = []
    gate = QuotaDeliveryGate(lambda source, snapshot: applied.append((source, snapshot)))
    gate.enable("claude")
    codex_generation = gate.enable("codex")
    worker = Thread(target=gate.callback("codex", codex_generation), args=("codex-current",))
    worker.start()
    worker.join()
    gate.invalidate("claude")
    QCoreApplication.processEvents()
    assert applied == [("codex", "codex-current")]
