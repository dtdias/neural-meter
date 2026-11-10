"""Janela HUD frameless always-on-top."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from neuralmeter.ui.snap import clamp_to_screens, pick_screen, snap_to_edges

HUD_SIZE = (260, 72)


class UpdateBridge(QObject):
    """Ponte thread-safe: o servidor WS chama emit_update; a UI recebe via sinal queued."""

    updated = Signal(str, dict)

    def emit_update(self, kind: str, payload: dict) -> None:
        self.updated.emit(kind, payload)


def _areas() -> list[tuple[int, int, int, int]]:
    out = []
    for s in QGuiApplication.screens():
        g = s.availableGeometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out


class HudWindow(QWidget):
    def __init__(self, on_moved: Callable[[int, int], None] | None = None) -> None:
        super().__init__()
        self.on_moved = on_moved
        self._drag_offset: QPoint | None = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(*HUD_SIZE)

        self.title_label = QLabel("NeuralMeter")
        self.title_label.setStyleSheet("color: #f2f2f2; font-weight: bold; font-size: 13px;")
        self.status_label = QLabel("aguardando…")
        self.status_label.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(24, 24, 28, 235))
        p.drawRoundedRect(self.rect(), 12, 12)

    @Slot(str, dict)
    def on_update(self, kind: str, payload: dict) -> None:
        self.status_label.setText(kind)

    def apply_saved_position(self, pos) -> None:
        p = tuple(pos) if pos is not None else None
        x, y = clamp_to_screens(p, (self.width(), self.height()), _areas())
        self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._drag_offset is None:
            return
        self._drag_offset = None
        win = (self.x(), self.y(), self.width(), self.height())
        areas = _areas()
        x, y = snap_to_edges(win, pick_screen(win, areas))
        self.move(x, y)
        if self.on_moved:
            self.on_moved(x, y)
