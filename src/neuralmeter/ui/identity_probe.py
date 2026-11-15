"""Single-flight identity probes; never perform filesystem/process IO on Qt thread."""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, Signal, Slot

IDENTITY_UNSEEN = object()


def identity_changed(previous, current) -> bool:
    """Only compare after first probe; stable missing identity is a valid baseline."""
    return previous is not IDENTITY_UNSEEN and current != previous


class IdentityProbeManager(QObject):
    result = Signal(str, int, int, object)

    def __init__(self, probes: dict, parent=None):
        super().__init__(parent)
        self._probes = dict(probes)
        self._queue: queue.Queue[tuple[str, int, int] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._epochs = {source: 0 for source in probes}
        self._source_generations = {source: 0 for source in probes}
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="identity-probes", daemon=True)
        self._thread.start()

    def schedule(self, source: str, generation: int = 0) -> int | None:
        with self._lock:
            if self._stopping.is_set() or source not in self._probes or source in self._pending:
                return None
            self._epochs[source] += 1
            epoch = self._epochs[source]
            self._source_generations[source] = generation
            self._pending.add(source)
            self._queue.put((source, epoch, generation))
            return epoch

    def cancel(self, source: str, generation: int | None = None) -> int:
        with self._lock:
            self._epochs[source] = self._epochs.get(source, 0) + 1
            if generation is not None:
                self._source_generations[source] = generation
            return self._epochs[source]

    def current_epoch(self, source: str) -> int:
        with self._lock:
            return self._epochs.get(source, 0)

    def pending(self, source: str) -> bool:
        with self._lock:
            return source in self._pending

    def is_current(self, source: str, epoch: int, generation: int) -> bool:
        with self._lock:
            return (
                not self._stopping.is_set()
                and self._epochs.get(source) == epoch
                and self._source_generations.get(source) == generation
            )

    def _run(self) -> None:
        while not self._stopping.is_set():
            task = self._queue.get()
            if task is None:
                return
            source, epoch, generation = task
            try:
                value = self._probes[source]()
            except Exception:
                value = None
            with self._lock:
                current = (
                    self._epochs.get(source) == epoch
                    and self._source_generations.get(source) == generation
                )
                self._pending.discard(source)
            if current and not self._stopping.is_set():
                self.result.emit(source, epoch, generation, value)

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)


class IdentityProbeGate(QObject):
    """Qt receiver fence for results already queued before cancel/reconnect."""

    def __init__(self, manager: IdentityProbeManager, generation_for, apply, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._generation_for = generation_for
        self._apply = apply

    @Slot(str, int, int, object)
    def consume(self, source: str, epoch: int, generation: int, identity) -> None:
        if generation != self._generation_for(source):
            return
        if not self._manager.is_current(source, epoch, generation):
            return
        self._apply(source, epoch, generation, identity)
