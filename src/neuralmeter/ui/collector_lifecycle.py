"""Own every source collector and fence callbacks across restart/disconnect races."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QTimer


class CollectorLifecycle:
    def __init__(self, retry_ms: int = 50, schedule=QTimer.singleShot):
        self._retry_ms = max(1, retry_ms)
        self._schedule = schedule
        self._lock = threading.RLock()
        self._generations: dict[str, int] = {}
        self._enabled: dict[str, bool] = {}
        self._desired: dict[str, tuple[int, Callable, Callable]] = {}
        self._active: dict[str, object] = {}
        self._owned: dict[object, str] = {}

    def generation(self, source: str) -> int:
        with self._lock:
            return self._generations.get(source, 0)

    def is_current(self, source: str, generation: int) -> bool:
        with self._lock:
            return self._enabled.get(source, False) and self._generations.get(source) == generation

    def active_count(self, source: str) -> int:
        with self._lock:
            poller = self._active.get(source)
            return int(poller is not None and poller.is_alive())

    def owned_count(self, source: str | None = None) -> int:
        with self._lock:
            return sum(source is None or owner == source for owner in self._owned.values())

    def start(self, source: str, factory: Callable, deliver: Callable) -> int:
        with self._lock:
            generation = self._generations.get(source, 0) + 1
            self._generations[source] = generation
            self._enabled[source] = True
            self._desired[source] = (generation, factory, deliver)
            current = self._active.pop(source, None)
            if current is not None:
                current.stop()
        self._try_start(source, generation)
        return generation

    def restart(self, source: str, factory: Callable, deliver: Callable) -> int:
        return self.start(source, factory, deliver)

    def stop(self, source: str) -> int:
        with self._lock:
            generation = self._generations.get(source, 0) + 1
            self._generations[source] = generation
            self._enabled[source] = False
            self._desired.pop(source, None)
            poller = self._active.pop(source, None)
            if poller is not None:
                poller.stop()
            return generation

    def _try_start(self, source: str, generation: int) -> None:
        with self._lock:
            desired = self._desired.get(source)
            if (
                desired is None
                or desired[0] != generation
                or not self._enabled.get(source, False)
                or self._generations.get(source) != generation
            ):
                return
            alive = [
                owner for owner, name in self._owned.items() if name == source and owner.is_alive()
            ]
            if alive:
                self._schedule(self._retry_ms, lambda: self._try_start(source, generation))
                return
            self._owned = {
                worker: name for worker, name in self._owned.items() if worker.is_alive()
            }
            _generation, factory, deliver = desired
            holder: dict[str, object] = {}

            def callback(snapshot) -> None:
                worker = holder.get("worker")
                with self._lock:
                    current = (
                        worker is not None
                        and self._active.get(source) is worker
                        and self._enabled.get(source, False)
                        and self._generations.get(source) == generation
                    )
                if current:
                    deliver(snapshot)

            worker = factory(callback)
            holder["worker"] = worker
            self._active[source] = worker
            self._owned[worker] = source
            worker.start()

    def shutdown(self) -> None:
        with self._lock:
            for source in set(self._generations) | set(self._enabled):
                self._generations[source] = self._generations.get(source, 0) + 1
                self._enabled[source] = False
                self._desired.pop(source, None)
            workers = tuple(self._owned)
            self._active.clear()
            for worker in workers:
                worker.stop()

    def join_all(self, timeout: float | None = None) -> None:
        with self._lock:
            workers = tuple(self._owned)
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        for worker in workers:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0:
                break
            worker.join(timeout=remaining)
