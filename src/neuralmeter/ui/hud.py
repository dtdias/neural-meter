"""Janela HUD frameless always-on-top."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from neuralmeter.ui.renewal import RenewalTracker
from neuralmeter.ui.settings import clamp_opacity
from neuralmeter.ui.snap import anchor_resize, clamp_to_screens, pick_screen, snap_to_edges
from neuralmeter.ui.view_model import (
    LEVEL_COLORS,
    NO_DATA,
    ConnState,
    ProviderView,
    apply_conn_update,
    conn_label,
    format_countdown,
    level_for_percent,
    model_quota_label,
    resolve_model_quotas,
)

log = logging.getLogger(__name__)

EXPANDED_SIZE = (550, 270)
HUD_SIZE = EXPANDED_SIZE
COMPACT_SIZE = (320, 150)
LEAVE_DELAY_MS = 600

# Placeholders (cor, letra); icones vetoriais ficam para o empacotamento.
MARKERS = {"claude": ("#d97757", "C"), "chatgpt": ("#10a37f", "G"), "gemini": ("#4285f4", "M")}


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
        lbl.setMinimumWidth(170)
        lbl.setWordWrap(True)
        return lbl

    @staticmethod
    def _style(bar: QProgressBar, level: str) -> None:
        bar.setStyleSheet(
            "QProgressBar{background:#3a3a42;border:none;border-radius:4px;}"
            f"QProgressBar::chunk{{background:{LEVEL_COLORS[level]};border-radius:4px;}}"
        )

    def render(self, view: ProviderView, now: datetime) -> None:
        self.name_label.setText(view.label)
        self.model_label.setText(view.model_quota or view.last_model)
        if view.quota_unknown:
            self.name_label.setText("Gemini · experimental")
            self.model_label.setText("cota oficial desconhecida")
            self.model_label.setToolTip(
                "Sem percentual ou reset oficial; contagem local de envios."
            )
            self.bar_5h.hide()
            self.bar_week.hide()
            narrow = self.text_week.minimumWidth() < 390
            if narrow:
                self.name_label.setText("Gemini · exp.")
                self.model_label.setText("quota/reset desconhecidos")
                self.text_5h.setText(f"{view.messages_5h} msgs/5h · quota desconhecida")
                self.text_week.setText(
                    f"{view.messages_7d} msgs/7d · ~{view.estimated_tokens_7d} tokens est."
                )
                self.setToolTip(
                    f"Gemini experimental; quota/reset oficiais desconhecidos. "
                    f"{view.messages_5h} mensagens observadas em 5h móvel; "
                    f"{view.messages_7d} em 7d; ~{view.estimated_tokens_7d} tokens estimados."
                )
            else:
                self.text_5h.setText(f"{view.messages_5h} mensagens observadas · 5h móvel")
                self.text_week.setText(
                    f"{view.messages_7d} mensagens · 7d\n"
                    f"~{view.estimated_tokens_7d} tokens estimados; quota desconhecida"
                )
            return
        self.setToolTip("")
        self.bar_5h.show()
        self.bar_week.show()
        if view.model_quota:
            self.model_label.setToolTip(
                f"{view.model_quota}; último modelo observado: {view.last_model}"
            )
        for bar, text, pct, level, reset in (
            (self.bar_5h, self.text_5h, view.pct_5h, view.level_5h, view.reset_5h),
            (self.bar_week, self.text_week, view.pct_week, view.level_week, view.reset_week),
        ):
            bar.setValue(round(pct))
            self._style(bar, level)
            source = view.source_5h if bar is self.bar_5h else view.source_week
            reset_source = view.reset_source_5h if bar is self.bar_5h else view.reset_source_week
            reset_label = (
                f"reset estimado (fonte omitiu): {format_countdown(reset, now)}"
                if reset_source == "não informado pela fonte"
                else f"reset {reset_source}: {format_countdown(reset, now)}"
            )
            text.setText(f"{round(pct)}% · {source}\n{reset_label}")


class CompactRow(QWidget):
    """Linha da pilula: marcador do provedor, barra 5h e 'restam N%'."""

    def __init__(self, provider: str) -> None:
        super().__init__()
        color, letter = MARKERS.get(provider, ("#888888", "?"))
        self.marker = QLabel(letter)
        self.marker.setFixedSize(14, 14)
        self.marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.marker.setStyleSheet(
            f"background:{color};color:white;font-size:9px;font-weight:bold;border-radius:7px;"
        )
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.text = QLabel("")
        self.text.setStyleSheet("color: #d0d0d0; font-size: 10px;")
        self.text.setMinimumWidth(150)
        self.text.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.marker)
        layout.addWidget(self.bar, 1)
        layout.addWidget(self.text)

    def render(self, view: ProviderView, now: datetime) -> None:
        if view.quota_unknown:
            self.bar.hide()
            narrow = bool(self.property("_nm_narrow"))
            if narrow:
                self.text.setText(
                    f"Gemini · quota desconhecida\n"
                    f"{view.messages_5h}/{view.messages_7d} msgs · "
                    f"~{view.estimated_tokens_7d} tokens"
                )
            else:
                self.text.setText(
                    f"Experimental · quota desconhecida\n"
                    f"{view.messages_5h} msgs/5h · {view.messages_7d} msgs/7d · "
                    f"~{view.estimated_tokens_7d} tokens"
                )
            self.setToolTip(
                f"Quota e reset oficiais desconhecidos. {view.messages_5h} mensagens em 5h móvel; "
                f"{view.messages_7d} em 7d; ~{view.estimated_tokens_7d} tokens estimados "
                "(~4 caracteres/token)."
            )
            return
        self.bar.show()
        self.bar.setValue(round(view.pct_5h))
        ProviderRow._style(self.bar, view.level_5h)
        source = view.source_5h.capitalize()
        reset_source = view.reset_source_5h
        reset_info = (
            f"reset estimado (fonte omitiu): {format_countdown(view.reset_5h, now)}"
            if reset_source == "não informado pela fonte"
            else f"reset {reset_source}: {format_countdown(view.reset_5h, now)}"
        )
        state_label = "Bloqueado · " if view.rate_limited else ""
        self.text.setText(f"{state_label}{source}\n{round(view.pct_5h)}% usado · {reset_info}")
        self.setToolTip("; ".join(part for part in (view.model_quota,) if part))


def _default_loader(now: datetime) -> dict:
    return {}


class HudWindow(QWidget):
    opacity_changed = Signal(int)
    click_through_changed = Signal(bool)
    compact_changed = Signal(bool)
    renewed = Signal(str)

    def __init__(
        self,
        on_moved: Callable[[int, int], None] | None = None,
        view_loader: Callable[[datetime], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
        compact: bool = False,
        on_compact_changed: Callable[[bool], None] | None = None,
        opacity: int = 100,
        click_through: bool = False,
    ) -> None:
        super().__init__()
        self.on_moved = on_moved
        self._view_loader = view_loader or _default_loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._drag_offset: QPoint | None = None
        self._views: dict[str, ProviderView] = {}
        self._conn = ConnState()
        self._renewal = RenewalTracker()
        self._renewal_source: dict[str, tuple[str, ...]] = {}
        self._renewal_armed: set[str] = set()
        self._renewal_invalidated_at: dict[str, datetime] = {}
        self.compact = compact
        self.on_compact_changed = on_compact_changed
        self._hover_expanded = False
        self.opacity = clamp_opacity(opacity)
        self.click_through = bool(click_through)
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(LEAVE_DELAY_MS)
        self._leave_timer.timeout.connect(self._on_leave_timeout)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if self.click_through:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setWindowOpacity(self.opacity / 100)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.title_label = QLabel("NeuralMeter")
        self.title_label.setStyleSheet("color: #f2f2f2; font-weight: bold; font-size: 13px;")
        self.status_label = QLabel(conn_label(self._conn))
        self.status_label.setStyleSheet("color: #a0a0a0; font-size: 11px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addWidget(self.status_label, 1)
        self.rows: dict[str, ProviderRow] = {
            p: ProviderRow() for p in ("claude", "chatgpt", "gemini")
        }
        self.expanded_page = QWidget()
        layout = QVBoxLayout(self.expanded_page)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        layout.addLayout(header)
        for row in self.rows.values():
            layout.addWidget(row)

        self.compact_page = QWidget()
        clayout = QVBoxLayout(self.compact_page)
        clayout.setContentsMargins(10, 6, 10, 6)
        clayout.setSpacing(4)
        self.compact_rows: dict[str, CompactRow] = {
            "claude": CompactRow("claude"),
            "chatgpt": CompactRow("chatgpt"),
            "gemini": CompactRow("gemini"),
        }
        for crow in self.compact_rows.values():
            clayout.addWidget(crow)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.expanded_page)
        self.stack.addWidget(self.compact_page)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self._apply_mode()

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

    def _effective_views(self, now: datetime) -> dict[str, ProviderView]:
        views = dict(self._views)
        view = views.get("claude")
        if view is None:
            return views
        if self._conn.claude_tabs != 1:
            if self._conn.claude_tabs is None:
                identity_label = "estimado · identidade não confirmada"
            elif self._conn.claude_tabs == 0:
                identity_label = "estimado · sem aba Claude ativa"
            else:
                identity_label = "estimado · múltiplas abas"
            view = replace(
                view,
                pct_5h=view.estimated_pct_5h if view.estimated_pct_5h is not None else view.pct_5h,
                level_5h=level_for_percent(view.estimated_pct_5h or 0, view.estimated_blocked_5h),
                reset_5h=view.estimated_reset_5h,
                pct_week=view.estimated_pct_week
                if view.estimated_pct_week is not None
                else view.pct_week,
                level_week=level_for_percent(
                    view.estimated_pct_week or 0, view.estimated_blocked_week
                ),
                reset_week=view.estimated_reset_week,
                rate_limited=view.estimated_rate_limited,
                source_5h=identity_label,
                source_week=identity_label,
                reset_source_5h="estimado",
                reset_source_week="estimado",
                model_quota="",
                model_quotas=(),
                official_until_5h=None,
                official_until_week=None,
                quota_identity=None,
                quota_state="unavailable",
                quota_fresh_5h=False,
                quota_fresh_week=False,
                official_reset_5h=None,
                official_reset_week=None,
                identity_state=(
                    "unconfirmed"
                    if self._conn.claude_tabs is None
                    else "no_tab"
                    if self._conn.claude_tabs == 0
                    else "multiple_tabs"
                ),
            )
        else:
            models = resolve_model_quotas(view.model_quotas, now)
            view = replace(view, model_quotas=models, model_quota=model_quota_label(models))
            updates = {}
            if (
                view.quota_fresh_5h
                and view.identity_state == "confirmed"
                and view.quota_identity
                and view.official_reset_5h
                and view.official_until_5h == view.official_reset_5h
                and now >= view.official_reset_5h
            ):
                view = replace(view, renewal_boundary_5h=view.official_reset_5h)
            if view.official_until_5h and now >= view.official_until_5h:
                pct = view.estimated_pct_5h if view.estimated_pct_5h is not None else 0
                updates.update(
                    pct_5h=pct,
                    level_5h=level_for_percent(pct, view.estimated_blocked_5h),
                    reset_5h=view.estimated_reset_5h,
                    source_5h="estimado · desatualizado",
                    reset_source_5h="estimado",
                    quota_fresh_5h=False,
                    official_reset_5h=None,
                )
            if view.official_until_week and now >= view.official_until_week:
                pct = view.estimated_pct_week if view.estimated_pct_week is not None else 0
                updates.update(
                    pct_week=pct,
                    level_week=level_for_percent(pct, view.estimated_blocked_week),
                    reset_week=view.estimated_reset_week,
                    source_week="estimado · desatualizado",
                    reset_source_week="estimado",
                    quota_fresh_week=False,
                    official_reset_week=None,
                )
            if updates:
                updates["rate_limited"] = (
                    (updates.get("pct_5h", view.pct_5h) >= 100 and not view.estimated_blocked_5h)
                    or (
                        updates.get("pct_week", view.pct_week) >= 100
                        and not view.estimated_blocked_week
                    )
                    or view.estimated_rate_limited
                )
                view = replace(view, **updates)
        views["claude"] = view
        return views

    def _render(self, now: datetime) -> None:
        effective = self._effective_views(now)
        for provider, row in self.rows.items():
            view = effective.get(provider)
            if view is not None:
                row.render(view, now)
                self.compact_rows[provider].render(view, now)

    def _observe_renewals(self, now: datetime) -> None:
        views = self._effective_views(now)
        view = views.get("claude")
        if view is not None:
            unsafe_identity = (
                self._conn.clients <= 0
                or self._conn.claude_tabs != 1
                or view.identity_state != "confirmed"
            )
            has_official_generation = (
                view.quota_fresh_5h
                or view.official_reset_5h is not None
                or "claude" in self._renewal_armed
            )
            if unsafe_identity and has_official_generation:
                self._renewal.forget("claude")
                self._renewal_armed.discard("claude")
                self._renewal_invalidated_at.setdefault("claude", now)
            else:
                signature = (
                    view.quota_state,
                    view.identity_state,
                    str(view.quota_fresh_5h),
                    str(view.official_reset_5h or ""),
                    view.quota_identity or "",
                    view.reset_5h.isoformat() if view.reset_5h else "",
                    str(view.quota_observed_at_5h or ""),
                )
                previous = self._renewal_source.get("claude")
                confirmed_due = (
                    view.renewal_boundary_5h is not None
                    and view.renewal_boundary_5h <= now
                    and previous is not None
                    and previous[4] == (view.quota_identity or "")
                    and previous[5] == view.renewal_boundary_5h.isoformat()
                    and "claude" in self._renewal_armed
                )
                if confirmed_due:
                    # Keep tracker's armed eligibility for this confirmed generation;
                    # effective fallback after reset must not rewrite it.
                    pass
                elif self._renewal_invalidated_at.get("claude") is not None:
                    invalidated_at = self._renewal_invalidated_at["claude"]
                    new_observation = (
                        view.quota_fresh_5h
                        and view.quota_observed_at_5h is not None
                        and view.quota_observed_at_5h > invalidated_at
                    )
                    if new_observation:
                        self._renewal_invalidated_at.pop("claude", None)
                        self._renewal_source.pop("claude", None)
                        previous = None
                    else:
                        self._renewal.forget("claude")
                        self._renewal_source["claude"] = signature
                        previous = signature
                if not confirmed_due and (
                    not unsafe_identity
                    and "claude" not in self._renewal_invalidated_at
                    and previous != signature
                ):
                    self._renewal.forget("claude")
                    self._renewal_armed.discard("claude")
                    self._renewal_source["claude"] = signature
                    if view.reset_5h is not None and view.reset_5h > now:
                        self._renewal.observe({"claude": view})
                        self._renewal_armed.add("claude")
                elif (
                    not confirmed_due
                    and not unsafe_identity
                    and "claude" not in self._renewal_invalidated_at
                    and "claude" in self._renewal_armed
                ):
                    self._renewal.observe({"claude": view})
                elif (
                    not confirmed_due
                    and not view.quota_fresh_5h
                    and "claude" not in self._renewal_invalidated_at
                    and view.source_5h.startswith("estimado")
                    and "desatualizado" not in view.source_5h
                ):
                    self._renewal.observe({"claude": view})
        chatgpt = views.get("chatgpt")
        if chatgpt is not None:
            self._renewal.observe({"chatgpt": chatgpt})

    def _reload(self) -> datetime:
        now = self._clock()
        try:
            self._views = self._view_loader(now)
        except Exception:
            log.exception("view_loader failed")
        self._render(now)
        return now

    def _emit_due(self, now: datetime) -> bool:
        providers = self._renewal.due(now)
        for p in providers:
            self.renewed.emit(p)
            self._renewal_armed.discard(p)
        return bool(providers)

    def refresh(self) -> None:
        """Recarrega o view-model (lê DB), re-renderiza e avisa renovações."""
        now = self._reload()
        self._observe_renewals(now)
        if self._emit_due(now):
            self._reload()  # snapshot pos-reset; due ja foi consumido

    def tick(self) -> None:
        """Re-renderiza countdowns com o relógio atual; dispara renovação vencida."""
        now = self._clock()
        self._render(now)
        self._observe_renewals(now)
        if self._emit_due(now):
            self._reload()

    @Slot(str, dict)
    def on_update(self, kind: str, payload: dict) -> None:
        if kind in ("usage", "limit", "quota"):
            self.refresh()
        elif kind in ("conn", "status"):
            self._conn = apply_conn_update(self._conn, kind, payload)
            self.status_label.setText(conn_label(self._conn))
            now = self._clock()
            self._render(now)
            self._observe_renewals(now)
            if self._emit_due(now):
                self._reload()

    @property
    def effective_compact(self) -> bool:
        return self.compact and not self._hover_expanded

    def _apply_mode(self) -> None:
        compact = self.effective_compact
        desired = COMPACT_SIZE if compact else EXPANDED_SIZE
        areas = _areas()
        win = (self.x(), self.y(), self.width(), self.height())
        area = pick_screen(win, areas) if areas else (0, 0, *desired)
        size = (min(desired[0], area[2]), min(desired[1], area[3]))
        # Constrain wrapped labels to current display width; preserve full meaning in tooltip.
        text_width = 390 if size[0] >= 550 else max(100, size[0] - 190)
        for row in self.rows.values():
            for label in (row.text_5h, row.text_week):
                label.setMinimumWidth(text_width)
                label.setMaximumWidth(max(text_width, size[0] - 100))
        self.compact_rows["gemini"].setProperty("_nm_narrow", size[0] < 400)
        self.stack.setCurrentWidget(self.compact_page if compact else self.expanded_page)
        if (self.width(), self.height()) == size:
            self._render(self._clock())
            return
        if self.isVisible():
            pos = anchor_resize(win, size, pick_screen(win, areas))
            pos = clamp_to_screens(pos, size, areas)
            self.setFixedSize(*size)
            self.move(*pos)
        else:
            self.setFixedSize(*size)
        self._render(self._clock())

    def set_compact(self, value: bool) -> None:
        changed = value != self.compact
        self.compact = value
        self._hover_expanded = False
        self._leave_timer.stop()
        self._apply_mode()
        if changed:
            if self.on_compact_changed:
                self.on_compact_changed(value)
            self.compact_changed.emit(value)

    def set_opacity(self, pct) -> None:
        value = clamp_opacity(pct)
        self.setWindowOpacity(value / 100)
        if value != self.opacity:
            self.opacity = value
            self.opacity_changed.emit(value)

    def set_click_through(self, on: bool) -> None:
        on = bool(on)
        if on == self.click_through:
            return
        self.click_through = on
        if on:
            self._leave_timer.stop()
            if self._hover_expanded:
                self._hover_expanded = False
                self._apply_mode()
        pos = self.pos()
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        if visible:
            self.show()
            self.move(pos)
        self.click_through_changed.emit(on)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_compact(not self.compact)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.compact and self._drag_offset is None:
            self._leave_timer.stop()
            if not self._hover_expanded:
                self._hover_expanded = True
                self._apply_mode()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_expanded and self._drag_offset is None:
            self._leave_timer.start()

    def _on_leave_timeout(self) -> None:
        if self._hover_expanded:
            self._hover_expanded = False
            self._apply_mode()

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
