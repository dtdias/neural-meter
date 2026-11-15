"""Qt-thread generation gate for opt-in quota snapshots."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot


class QuotaDeliveryGate(QObject):
    """Reject queued results from disabled or superseded source instances."""

    queued = Signal(str, int, object)

    def __init__(self, apply_snapshot, parent=None):
        super().__init__(parent)
        self._apply_snapshot = apply_snapshot
        self._epochs: dict[str, tuple[int, bool]] = {}
        self.queued.connect(self._consume)

    def enable(self, source: str) -> int:
        generation = self._epochs.get(source, (0, False))[0] + 1
        self._epochs[source] = (generation, True)
        return generation

    def invalidate(self, source: str) -> int:
        generation = self._epochs.get(source, (0, False))[0] + 1
        self._epochs[source] = (generation, False)
        return generation

    def callback(self, source: str, generation: int):
        def deliver(snapshot):
            self.queued.emit(source, generation, snapshot)

        return deliver

    @Slot(str, int, object)
    def _consume(self, source: str, generation: int, snapshot) -> None:
        if self._epochs.get(source) != (generation, True):
            return
        self._apply_snapshot(source, snapshot)
