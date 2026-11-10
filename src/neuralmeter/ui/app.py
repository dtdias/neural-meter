"""Ponto de entrada da UI: HUD + servidor WS."""

import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from neuralmeter.db import connect_db
from neuralmeter.engine import DEFAULT_CHATGPT_LIMITS
from neuralmeter.server import start_ws_server
from neuralmeter.ui.hud import HudWindow, UpdateBridge
from neuralmeter.ui.settings import (
    clamp_opacity,
    load_settings,
    parse_model_limits,
    save_settings,
)
from neuralmeter.ui.tray import build_tray
from neuralmeter.ui.view_model import load_hud_view


def run_app(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else ["neuralmeter", *argv])
    app.setQuitOnLastWindowClosed(False)
    settings = load_settings()
    db = connect_db()
    bridge = UpdateBridge()

    def on_moved(x: int, y: int) -> None:
        settings["hud_pos"] = [x, y]
        save_settings(settings)

    def on_compact_changed(value: bool) -> None:
        settings["compact"] = value
        save_settings(settings)

    def on_opacity_changed(value: int) -> None:
        settings["opacity"] = value
        save_settings(settings)

    def on_click_through_changed(value: bool) -> None:
        settings["click_through"] = value
        save_settings(settings)

    def model_limits() -> dict:
        return {
            **DEFAULT_CHATGPT_LIMITS,
            **parse_model_limits(settings.get("chatgpt_model_limits")),
        }

    # Sem tray, click-through prenderia o usuario: forca desligado.
    tray_ok = QSystemTrayIcon.isSystemTrayAvailable()
    hud = HudWindow(
        on_moved=on_moved,
        view_loader=lambda now: load_hud_view(db, now, chatgpt_limits=model_limits()),
        compact=bool(settings.get("compact")),
        on_compact_changed=on_compact_changed,
        opacity=clamp_opacity(settings.get("opacity")),
        click_through=bool(settings.get("click_through")) and tray_ok,
    )
    hud.opacity_changed.connect(on_opacity_changed)
    hud.click_through_changed.connect(on_click_through_changed)
    hud.refresh()
    bridge.updated.connect(hud.on_update)
    hud.apply_saved_position(settings.get("hud_pos"))

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(500)

    server = start_ws_server(db, on_update=bridge.emit_update)

    def quit_app() -> None:
        server.stop()
        app.quit()

    tray = build_tray(hud, quit_app, conn=db)
    if tray_ok:
        tray.show()
    hud.show()
    try:
        return app.exec()
    finally:
        server.stop()
