"""Janela HUD frameless always-on-top."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from neuralmeter.ui.snap import clamp_to_screens, pick_screen, snap_to_edges
from neuralmeter.ui.view_model import (
    LEVEL_COLORS,
    NO_DATA,
    ConnState,
    ProviderView,
    apply_conn_update,
    conn_label,
    format_countdown,
)

log = logging.getLogger(__name__)

HUD_SIZE = (300, 190)


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


class ProviderRow(QWidget):
    """Linha de um provedor: nome, último modelo, barra 5h e barra semanal."""

    def __init__(self) -> None:
        super().__init__()
        self.name_label = QLabel("")
        self.name_label.setStyleSheet("color: #f2f2f2; font-weight: bold; font-size: 12px;")
        self.model_label = QLabel(NO_DATA)
        self.model_label.setStyleSheet("color: #a0a0a0; font-size: 10px;")
        self.model_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.bar_5h = self._make_bar()
        self.text_5h = self._make_text()
        self.bar_week = self._make_bar()
        self.text_week = self._make_text()

        head = QHBoxLayout()
        head.addWidget(self.name_label)
        head.addWidget(self.model_label, 1)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        for i, (tag, bar, text) in enumerate(
            (("5h", self.bar_5h, self.text_5h), ("sem", self.bar_week, self.text_week))
        ):
            lbl = QLabel(tag)
            lbl.setStyleSheet("color: #a0a0a0; font-size: 10px;")
            grid.addWidget(lbl, i, 0)
            grid.addWidget(bar, i, 1)
            grid.addWidget(text, i, 2)
        grid.setColumnStretch(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addLayout(grid)

    @staticmethod
    def _make_bar() -> QProgressBar:
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        return bar

    @staticmethod
    def _make_text() -> QLabel:
        lbl = QLabel("")
        lbl.setStyleSheet("color: #d0d0d0; font-size: 10px;")
        lbl.setMinimumWidth(110)
        return lbl

    @staticmethod
    def _style(bar: QProgressBar, level: str) -> None:
        bar.setStyleSheet(
            "QProgressBar{background:#3a3a42;border:none;border-radius:4px;}"
            f"QProgressBar::chunk{{background:{LEVEL_COLORS[level]};border-radius:4px;}}"
        )

    def render(self, view: ProviderView, now: datetime) -> None:
        self.name_label.setText(view.label)
        self.model_label.setText(view.last_model)
        for bar, text, pct, level, reset in (
            (self.bar_5h, self.text_5h, view.pct_5h, view.level_5h, view.reset_5h),
            (self.bar_week, self.text_week, view.pct_week, view.level_week, view.reset_week),
        ):
            bar.setValue(round(pct))
            self._style(bar, level)
            text.setText(f"{round(pct)}% · reset {format_countdown(reset, now)}")


def _default_loader(now: datetime) -> dict:
    return {}


class HudWindow(QWidget):
    def __init__(
        self,
        on_moved: Callable[[int, int], None] | None = None,
        view_loader: Callable[[datetime], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__()
        self.on_moved = on_moved
        self._view_loader = view_loader or _default_loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._drag_offset: QPoint | None = None
        self._views: dict[str, ProviderView] = {}
        self._conn = ConnState()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(*HUD_SIZE)

        self.title_label = QLabel("NeuralMeter")
        self.title_label.setStyleSheet("color: #f2f2f2; font-weight: bold; font-size: 13px;")
        self.status_label = QLabel(conn_label(self._conn))
        self.status_label.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addWidget(self.status_label, 1)
        self.rows: dict[str, ProviderRow] = {"claude": ProviderRow(), "chatgpt": ProviderRow()}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        layout.addLayout(header)
        for row in self.rows.values():
            layout.addWidget(row)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self.tick)
        self._tick_timer.start(1000)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(30_000)
        self.refresh()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(24, 24, 28, 235))
        p.drawRoundedRect(self.rect(), 12, 12)

    def _render(self, now: datetime) -> None:
        for provider, row in self.rows.items():
            view = self._views.get(provider)
            if view is not None:
                row.render(view, now)

    def refresh(self) -> None:
        """Recarrega o view-model (lê DB) e re-renderiza."""
        now = self._clock()
        try:
            self._views = self._view_loader(now)
        except Exception:
            log.exception("view_loader failed")
        self._render(now)

    def tick(self) -> None:
        """Só re-renderiza countdowns com o relógio atual (sem DB)."""
        self._render(self._clock())

    @Slot(str, dict)
    def on_update(self, kind: str, payload: dict) -> None:
        if kind in ("usage", "limit"):
            self.refresh()
        elif kind in ("conn", "status"):
            self._conn = apply_conn_update(self._conn, kind, payload)
            self.status_label.setText(conn_label(self._conn))

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
