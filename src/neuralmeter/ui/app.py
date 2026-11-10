"""Ponto de entrada da UI: HUD + servidor WS."""

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from neuralmeter.db import connect_db
from neuralmeter.server import start_ws_server
from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.settings import load_settings, save_settings


def run_app(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else ["neuralmeter", *argv])
    app.setQuitOnLastWindowClosed(True)
    settings = load_settings()
    db = connect_db()
    bridge = UpdateBridge()

    def on_moved(x: int, y: int) -> None:
        settings["hud_pos"] = [x, y]
        save_settings(settings)

    hud = HudWindow(on_moved=on_moved)
    bridge.updated.connect(hud.on_update)
    hud.apply_saved_position(settings.get("hud_pos"))

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(500)

    server = start_ws_server(db, on_update=bridge.emit_update)
    hud.show()
    try:
        return app.exec()
    finally:
        server.stop()
