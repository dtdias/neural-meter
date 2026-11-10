import threading
import time
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.view_model import LEVEL_COLORS, ProviderView, level_for_percent


def test_flags(qapp):
    hud = HudWindow()
    f = hud.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint
    assert f & Qt.WindowType.WindowStaysOnTopHint
    assert f & Qt.WindowType.Tool


def _view(provider="claude", pct=75.0, reset=None, model="sonnet"):
    return ProviderView(
        provider=provider,
        label=provider.title(),
        pct_5h=pct,
        level_5h=level_for_percent(pct),
        reset_5h=reset,
        pct_week=10.0,
        level_week="green",
        reset_week=None,
        last_model=model,
        rate_limited=False,
    )


def test_renders_percent_and_color(qapp):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    reset = now + timedelta(minutes=30)
    views = {"claude": _view(pct=75, reset=reset), "chatgpt": _view("chatgpt", pct=95)}
    hud = HudWindow(view_loader=lambda n: views, clock=lambda: now)
    row = hud.rows["claude"]
    assert row.bar_5h.value() == 75
    assert LEVEL_COLORS["yellow"] in row.bar_5h.styleSheet()
    assert LEVEL_COLORS["red"] in hud.rows["chatgpt"].bar_5h.styleSheet()
    assert LEVEL_COLORS["green"] in row.bar_week.styleSheet()
    assert row.text_5h.text() == "75% · reset 30m 00s"
    assert row.model_label.text() == "sonnet"


def test_conn_label_updates(qapp):
    hud = HudWindow()
    assert hud.status_label.text() == "offline"
    hud.on_update("conn", {"clients": 1})
    assert hud.status_label.text() == "aguardando aba"
    hud.on_update("status", {"tabs": {"claude": True, "chatgpt": False}})
    assert hud.status_label.text() == "online"
    hud.on_update("conn", {"clients": 0})
    assert hud.status_label.text() == "offline"


def test_tick_updates_countdown_without_reload(qapp):
    clock = {"now": datetime(2026, 11, 10, 12, 0, tzinfo=UTC)}
    calls = []
    reset = clock["now"] + timedelta(minutes=30)

    def loader(n):
        calls.append(n)
        return {"claude": _view(reset=reset)}

    hud = HudWindow(view_loader=loader, clock=lambda: clock["now"])
    before = hud.rows["claude"].text_5h.text()
    clock["now"] += timedelta(seconds=10)
    hud.tick()
    assert hud.rows["claude"].text_5h.text() != before
    assert hud.rows["claude"].text_5h.text() == "75% · reset 29m 50s"
    assert len(calls) == 1


def test_usage_and_limit_refresh(qapp):
    calls = []
    hud = HudWindow(view_loader=lambda n: calls.append(n) or {})
    hud.on_update("usage", {})
    hud.on_update("limit", {})
    assert len(calls) == 3


def test_bridge_cross_thread(qapp):
    hud = HudWindow()
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    t = threading.Thread(target=bridge.emit_update, args=("conn", {"clients": 2}))
    t.start()
    t.join()
    deadline = time.time() + 1
    while hud.status_label.text() != "aguardando aba" and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert hud.status_label.text() == "aguardando aba"


def test_apply_saved_position_none_inside_screen(qapp):
    hud = HudWindow()
    hud.apply_saved_position(None)
    g = QGuiApplication.screens()[0].availableGeometry()
    assert g.contains(hud.x(), hud.y())
    assert hud.x() + hud.width() <= g.x() + g.width()
