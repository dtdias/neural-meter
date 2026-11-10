"""Icone na bandeja: mostrar/ocultar, compacto, opacidade e click-through do HUD."""

import sqlite3
import sys
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from neuralmeter.autostart import is_autostart_enabled, set_autostart
from neuralmeter.ui.hud import HudWindow
from neuralmeter.ui.limits_dialog import open_limits_dialog
from neuralmeter.ui.settings import OPACITY_PRESETS


def make_tray_icon() -> QIcon:
    """Icone provisorio 32x32: circulo #6366f1 com 'NM' branco."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#6366f1"))
    p.drawEllipse(1, 1, 30, 30)
    p.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(13)
    p.setFont(font)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "NM")
    p.end()
    return QIcon(pm)


class TrayController(QSystemTrayIcon):
    def __init__(
        self,
        hud: HudWindow,
        on_quit: Callable[[], None],
        conn: sqlite3.Connection | None = None,
        on_limits_saved: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(make_tray_icon())
        self.hud = hud
        self.conn = conn
        self.on_limits_saved = on_limits_saved
        self.setToolTip("NeuralMeter")
        self.menu = QMenu()
        self.act_toggle = self.menu.addAction("Mostrar/Ocultar HUD")
        self.act_toggle.triggered.connect(lambda: hud.toggle_visible())

        self.act_compact = self.menu.addAction("Modo compacto")
        self.act_compact.setCheckable(True)
        self.act_compact.setChecked(hud.compact)
        self.act_compact.toggled.connect(lambda v: hud.set_compact(bool(v)))
        hud.compact_changed.connect(self._sync_compact)

        self.opacity_menu = self.menu.addMenu("Opacidade")
        self.opacity_group = QActionGroup(self.opacity_menu)
        self.opacity_group.setExclusive(True)
        self.opacity_actions: dict[int, QAction] = {}
        for pct in OPACITY_PRESETS:
            act = self.opacity_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setActionGroup(self.opacity_group)
            act.setChecked(pct == hud.opacity)
            act.triggered.connect(lambda _=False, v=pct: hud.set_opacity(v))
            self.opacity_actions[pct] = act
        hud.opacity_changed.connect(self._sync_opacity)

        self.act_click_through = self.menu.addAction("Click-through")
        self.act_click_through.setCheckable(True)
        self.act_click_through.setChecked(hud.click_through)
        self.act_click_through.toggled.connect(lambda v: hud.set_click_through(bool(v)))
        hud.click_through_changed.connect(self._sync_click_through)

        self.menu.addSeparator()
        self.act_limits = self.menu.addAction("Configurar limites…")
        self.act_limits.triggered.connect(self._configure_limits)

        self.act_autostart = self.menu.addAction("Iniciar com o Windows")
        self.act_autostart.setCheckable(True)
        self.act_autostart.setEnabled(sys.platform == "win32")
        self.act_autostart.setChecked(is_autostart_enabled())
        self.act_autostart.toggled.connect(self._on_autostart_toggled)

        self.menu.addSeparator()
        self.act_quit = self.menu.addAction("Sair")
        self.act_quit.triggered.connect(lambda: on_quit())

        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _configure_limits(self) -> None:
        if self.conn is None:
            return
        if open_limits_dialog(self.conn):
            if self.on_limits_saved is not None:
                self.on_limits_saved()
            self.hud.refresh()

    def _on_autostart_toggled(self, value: bool) -> None:
        if set_autostart(bool(value)):
            return
        self.act_autostart.blockSignals(True)
        self.act_autostart.setChecked(not value)
        self.act_autostart.blockSignals(False)

    def _sync_compact(self, value: bool) -> None:
        self.act_compact.setChecked(value)

    def _sync_opacity(self, value: int) -> None:
        for pct, act in self.opacity_actions.items():
            act.setChecked(pct == value)

    def _sync_click_through(self, value: bool) -> None:
        self.act_click_through.setChecked(value)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.hud.toggle_visible()


def build_tray(
    hud: HudWindow,
    on_quit: Callable[[], None],
    conn: sqlite3.Connection | None = None,
    on_limits_saved: Callable[[], None] | None = None,
) -> TrayController:
    return TrayController(hud, on_quit, conn, on_limits_saved)
