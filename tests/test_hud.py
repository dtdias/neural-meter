import threading
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from neuralmeter.ui.hud import HudWindow, UpdateBridge


def test_flags(qapp):
    hud = HudWindow()
    f = hud.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint
    assert f & Qt.WindowType.WindowStaysOnTopHint
    assert f & Qt.WindowType.Tool


def test_on_update_changes_status(qapp):
    hud = HudWindow()
    assert hud.status_label.text() == "aguardando…"
    hud.on_update("usage", {})
    assert hud.status_label.text() == "usage"


def test_bridge_cross_thread(qapp):
    hud = HudWindow()
    bridge = UpdateBridge()
    bridge.updated.connect(hud.on_update)
    t = threading.Thread(target=bridge.emit_update, args=("thread_kind", {"a": 1}))
    t.start()
    t.join()
    deadline = time.time() + 1
    while hud.status_label.text() != "thread_kind" and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert hud.status_label.text() == "thread_kind"


def test_apply_saved_position_none_inside_screen(qapp):
    hud = HudWindow()
    hud.apply_saved_position(None)
    g = QGuiApplication.screens()[0].availableGeometry()
    assert g.contains(hud.x(), hud.y())
    assert hud.x() + hud.width() <= g.x() + g.width()
