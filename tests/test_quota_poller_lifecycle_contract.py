import threading
import time

from neuralmeter.claude_cli import CliSnapshot, QuotaPoller
from neuralmeter.ui.collector_lifecycle import CollectorLifecycle


def test_real_quota_poller_alive_tracks_thread_through_stopped_fetch():
    entered = threading.Event()
    release = threading.Event()
    delivered = []

    def fetch():
        entered.set()
        release.wait(2)
        return CliSnapshot("unknown")

    poller = QuotaPoller(delivered.append, fetch=fetch)
    assert poller.is_alive() is False
    poller.start()
    assert entered.wait(1)
    assert poller.is_alive() is True
    poller.stop()
    assert poller.is_alive() is True
    poller.join(timeout=0.02)
    assert poller.is_alive() is True
    release.set()
    poller.join(timeout=1)
    assert poller.is_alive() is False
    assert delivered == []


def test_real_quota_pollers_start_for_multiple_sources_and_prune_retired(qapp):
    entered = {source: threading.Event() for source in ("claude", "codex", "antigravity")}
    release = {source: threading.Event() for source in entered}
    lifecycle = CollectorLifecycle(retry_ms=1)
    made = {}

    def factory(source):
        def create(callback):
            def fetch():
                entered[source].set()
                release[source].wait(2)
                return CliSnapshot("unknown")

            poller = QuotaPoller(callback, fetch=fetch)
            made[source] = poller
            return poller

        return create

    try:
        for source in entered:
            lifecycle.start(source, factory(source), lambda _snapshot: None)
        assert all(event.wait(1) for event in entered.values())
        assert all(lifecycle.active_count(source) == 1 for source in entered)
        assert lifecycle.owned_count() == 3

        lifecycle.restart("claude", factory("claude"), lambda _snapshot: None)
        assert made["claude"].is_alive()
        assert lifecycle.owned_count("claude") == 1
        release["claude"].set()
        deadline = time.monotonic() + 1
        while lifecycle.active_count("claude") == 0 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.002)
        assert lifecycle.active_count("claude") == 1
        assert lifecycle.owned_count("claude") == 1
    finally:
        lifecycle.shutdown()
        for event in release.values():
            event.set()
        lifecycle.join_all(timeout=2)
