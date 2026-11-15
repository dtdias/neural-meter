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
from neuralmeter.ui.settings import clamp_opacity, parse_quota_visibility
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
        self.metric_rows: dict[str, QWidget] = {}

        head = QHBoxLayout()
        head.addWidget(self.name_label)
        head.addWidget(self.model_label, 1)
        metrics = QVBoxLayout()
        metrics.setSpacing(2)
        for tag, key, bar, text in (
            ("5h", "5h", self.bar_5h, self.text_5h),
            ("sem", "weekly", self.bar_week, self.text_week),
        ):
            row = QWidget()
            grid = QGridLayout(row)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(2)
            lbl = QLabel(tag)
            lbl.setStyleSheet("color: #a0a0a0; font-size: 10px;")
            grid.addWidget(lbl, 0, 0)
            grid.addWidget(bar, 0, 1)
            grid.addWidget(text, 0, 2)
            grid.setColumnStretch(1, 1)
            metrics.addWidget(row)
            self.metric_rows[key] = row
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(head)
        layout.addLayout(metrics)

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

    def set_metrics_visible(self, five_hour: bool, weekly: bool) -> None:
        self.metric_rows["5h"].setVisible(five_hour)
        self.metric_rows["weekly"].setVisible(weekly)
        self.setVisible(five_hour or weekly)


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

    def render(
        self, view: ProviderView, now: datetime, *, five_hour: bool = True, weekly: bool = False
    ) -> None:
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
        metrics = []
        if five_hour:
            metrics.append(("5h", view.pct_5h, view.level_5h, view.reset_5h, view.source_5h))
        if weekly:
            metrics.append(
                ("sem", view.pct_week, view.level_week, view.reset_week, view.source_week)
            )
        if not metrics:
            self.bar.hide()
            self.text.clear()
            return
        primary = metrics[0]
        self.bar.setValue(round(primary[1]))
        ProviderRow._style(self.bar, primary[2])
        if len(metrics) == 1 and primary[0] == "5h":
            reset_label = format_countdown(primary[3], now)
            reset_source = view.reset_source_5h
            blocked = "Bloqueado · " if view.rate_limited else ""
            self.text.setText(
                f"{blocked}{primary[4].capitalize()}\n{round(primary[1])}% usado · "
                f"reset {reset_source}: {reset_label}"
            )
        else:
            blocked = "Bloqueado · " if view.rate_limited else ""
            self.text.setText(
                blocked
                + "\n".join(
                    f"{name}: {round(pct)}% {source}; reset "
                    f"{view.reset_source_5h if name == '5h' else view.reset_source_week}: "
                    f"{format_countdown(reset, now)}"
                    for name, pct, _, reset, source in metrics
                )
            )
        tooltip = "\n".join(
            f"{name}: {round(pct)}%; reset {format_countdown(reset, now)} ({source})"
            for name, pct, _, reset, source in metrics
        )
        self.setToolTip("; ".join(part for part in (view.model_quota, tooltip) if part))


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
        self.cli_status = QLabel("Claude CLI: desconectado")
        self.cli_status.setStyleSheet("color: #a0a0a0; font-size: 10px;")
        self.cli_status.hide()
        self.codex_status = QLabel("Codex / Work: não conectado")
        self.codex_status.setStyleSheet("color: #a0a0a0; font-size: 10px;")
        self.codex_status.hide()
        self.antigravity_status = QLabel("Antigravity: não conectado")
        self.antigravity_status.setStyleSheet("color: #a0a0a0; font-size: 10px;")
        self.antigravity_status.setWordWrap(True)
        self.antigravity_status.hide()
        self._quota_visibility = {
            "claude_web_5h": True,
            "claude_web_weekly": True,
            "chatgpt_web_5h": True,
            "chatgpt_web_weekly": True,
            "gemini_web_activity": True,
            "claude_cli_5h": False,
            "claude_cli_weekly": False,
            "codex_cli_primary": False,
            "codex_cli_secondary": False,
            "antigravity": False,
        }
        self._selection_active = False
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
        layout.addWidget(self.cli_status)
        layout.addWidget(self.codex_status)
        layout.addWidget(self.antigravity_status)
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
        self.cli_compact = QLabel("Claude CLI: desconectado")
        self.cli_compact.setStyleSheet("color: #a0a0a0; font-size: 9px;")
        self.cli_compact.setToolTip("Quota Claude Code CLI, fonte separada do Claude Web")
        clayout.addWidget(self.cli_compact)
        self.cli_compact.hide()
        self.codex_compact = QLabel("Codex: não conectado")
        self.codex_compact.setStyleSheet("color: #a0a0a0; font-size: 9px;")
        clayout.addWidget(self.codex_compact)
        self.codex_compact.hide()
        self.antigravity_compact = QLabel("Antigravity: não conectado")
        self.antigravity_compact.setStyleSheet("color: #a0a0a0; font-size: 9px;")
        self.antigravity_compact.setWordWrap(True)
        self.antigravity_compact.setToolTip("Antigravity é fonte separada do Gemini Web")
        clayout.addWidget(self.antigravity_compact)
        self.antigravity_compact.hide()

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
            prefix = {"claude": "claude_web", "chatgpt": "chatgpt_web"}.get(provider)
            if provider == "gemini":
                show5 = showweek = self._quota_visibility["gemini_web_activity"]
            else:
                show5 = self._quota_visibility[f"{prefix}_5h"]
                showweek = self._quota_visibility[f"{prefix}_weekly"]
            row.set_metrics_visible(show5, showweek)
            compact_visible = show5 or showweek
            self.compact_rows[provider].setVisible(compact_visible)
            view = effective.get(provider)
            if view is not None:
                row.render(view, now)
                self.compact_rows[provider].render(view, now, five_hour=show5, weekly=showweek)

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

    @Slot(dict)
    def set_cli_status(self, data: dict) -> None:
        """Render sanitized CLI metadata only; browser provider state untouched."""
        status = data.get("status", "unknown")
        self._cli_payload = dict(data)
        self._cli_observed_at = data.get("observed_at")
        labels = {
            "unknown": "verificando",
            "not_connected": "não conectado",
            "available": "conectado",
            "not_authenticated": "não autenticado",
            "token_expired": "token expirado",
            "identity_changed": "identidade mudou; verificando",
            "unsupported_auth": "auth não suportada",
            "request_failed": "quota indisponível",
            "rate_limited": "limite temporário",
            "cli_not_installed": "CLI não instalado",
            "disconnected": "desconectado",
        }
        label = labels.get(status, "desconhecido")
        parts = ["Claude CLI:", label]
        compact_metrics = []
        for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
            selection_key = "claude_cli_5h" if key == "five_hour" else "claude_cli_weekly"
            if not self._quota_visibility[selection_key]:
                continue
            window = data.get(key)
            if isinstance(window, dict) and window.get("percent") is not None:
                reset = window.get("reset_at")
                if isinstance(reset, datetime) and reset <= self._clock():
                    parts.append(f"{name} quota desconhecida (reset vencido)")
                    compact_metrics.append(f"{name} ?")
                    continue
                parts.append(f"{name} {window['percent']:g}%")
                compact_metrics.append(f"{name} {window['percent']:g}%")
                if isinstance(reset, datetime):
                    parts.append(f"reset {format_countdown(reset, self._clock())}")
        self.cli_status.setText(" · ".join(parts))
        cli_selected = (
            self._quota_visibility["claude_cli_5h"] or self._quota_visibility["claude_cli_weekly"]
        )
        self.cli_status.setVisible(cli_selected)
        self.cli_compact.setText(
            "Claude CLI " + (" · ".join(compact_metrics) if compact_metrics else label)
        )
        self.cli_compact.setToolTip(" ".join(parts) + "; separado de Claude Web")
        self.cli_compact.setVisible(cli_selected)
        if not any(self._quota_visibility.values()):
            self.cli_status.setText("Nenhuma quota selecionada")
            self.cli_status.show()
            self.cli_compact.setText("Nenhuma quota selecionada")
            self.cli_compact.show()
        self._apply_mode()

    def set_quota_visibility(self, selection: dict[str, bool], *, resize: bool = True) -> None:
        selection = parse_quota_visibility(selection)
        self._quota_visibility = selection
        self._selection_active = resize
        if not any(key.startswith("codex_cli_") and value for key, value in selection.items()):
            self.codex_status.hide()
            self.codex_compact.hide()
        self.set_codex_status(getattr(self, "_codex_payload", {"status": "not_connected"}))
        agy_selected = selection.get("antigravity", False) or any(
            key.startswith("antigravity_") and value for key, value in selection.items()
        )
        self.antigravity_status.setVisible(agy_selected)
        self.antigravity_compact.setVisible(agy_selected)
        if agy_selected:
            self.set_antigravity_status(
                getattr(self, "_antigravity_payload", {"status": "not_connected"})
            )
        if not any(selection.values()):
            self.cli_status.setText("Nenhuma quota selecionada")
            self.cli_status.setVisible(True)
            self.cli_compact.setText("Nenhuma quota selecionada")
            self.cli_compact.show()
        else:
            if self.cli_status.text() == "Nenhuma quota selecionada":
                self.cli_status.hide()
            self.set_cli_status(getattr(self, "_cli_payload", {"status": "not_connected"}))
        self._apply_mode()

    @Slot(dict)
    def set_codex_status(self, data: dict) -> None:
        self._codex_payload = dict(data)
        self._codex_observed_at = data.get("observed_at")
        labels = {
            "available": "conectado",
            "not_connected": "não conectado",
            "cli_not_installed": "CLI não instalado",
            "not_authenticated": "não autenticado",
            "auth_required": "autenticação necessária",
            "identity_changed": "identidade mudou; verificando",
            "timeout": "sem resposta",
            "rate_limited": "limite temporário",
            "quota_unavailable": "quota indisponível",
            "request_failed": "quota indisponível",
        }
        status = data.get("status", "unknown")
        parts = ["Codex / Work:", labels.get(status, "desconhecido")]
        if data.get("coverage_partial"):
            parts.append("cobertura parcial (limite 256)")
        scopes = data.get("windows", [])
        if isinstance(scopes, list):
            for window in scopes:
                if not isinstance(window, dict):
                    continue
                scope = str(window.get("scope", ""))[:32]
                percent = window.get("percent")
                limit_id = str(window.get("limit_id", "codex"))[:48].lower()
                selected_scope = (
                    f"codex_cli_{scope}" if limit_id == "codex" else f"codex_cli_{limit_id}_{scope}"
                )
                if not self._quota_visibility.get(selected_scope, False):
                    continue
                suffix = f" {percent:g}%" if isinstance(percent, int | float) else " desconhecido"
                reset = window.get("reset_at")
                if isinstance(reset, datetime) and reset <= self._clock():
                    suffix = " quota desconhecida (reset vencido)"
                reset_label = (
                    format_countdown(reset, self._clock())
                    if isinstance(reset, datetime)
                    else "desconhecido"
                )
                name = str(window.get("limit_name") or limit_id)[:48]
                duration = window.get("window_minutes")
                duration_label = f" {duration}m" if isinstance(duration, int) else ""
                parts.append(f"{name} {scope}{duration_label}{suffix}; reset {reset_label}")
        self.codex_status.setText(" · ".join(parts))
        visible = any(
            key.startswith("codex_cli_") and value for key, value in self._quota_visibility.items()
        )
        self.codex_status.setVisible(visible)
        compact_scopes = []
        if isinstance(scopes, list):
            for window in scopes:
                if not isinstance(window, dict):
                    continue
                scope = str(window.get("scope", "quota"))[:16]
                limit_id = str(window.get("limit_id", "codex"))[:48].lower()
                selected_key = (
                    f"codex_cli_{scope}" if limit_id == "codex" else f"codex_cli_{limit_id}_{scope}"
                )
                if not self._quota_visibility.get(selected_key, False):
                    continue
                reset = window.get("reset_at")
                expired = isinstance(reset, datetime) and reset <= self._clock()
                pct = window.get("percent")
                percent_label = f"{pct:g}%" if isinstance(pct, int | float) and not expired else "?"
                duration = window.get("window_minutes")
                duration_label = f" {duration}m" if isinstance(duration, int) else ""
                compact_scopes.append(
                    f"{str(window.get('limit_name') or limit_id)[:20]} "
                    f"{scope}{duration_label} {percent_label}"
                )
        self.codex_compact.setText("Codex " + (" · ".join(compact_scopes) or parts[1]))
        self.codex_compact.setToolTip(" ".join(parts) + "; fonte Codex / Work, não ChatGPT Web")
        self.codex_compact.setVisible(visible)
        self._apply_mode()

    @Slot(dict)
    def set_antigravity_status(self, data: dict) -> None:
        self._antigravity_payload = dict(data)
        self._antigravity_observed_at = data.get("observed_at")
        labels = {
            "unknown": "verificando",
            "not_connected": "não conectado",
            "ide_not_found": "IDE não encontrado",
            "server_not_found": "serviço local não detectado",
            "quota_unavailable": "quota indisponível",
            "request_failed": "serviço local indisponível",
            "identity_changed": "sessão mudou; verificando",
            "available": "conectado · experimental",
        }
        rows = []
        for bucket in data.get("buckets", [])[:256]:
            if not isinstance(bucket, dict):
                continue
            bucket_id = str(bucket.get("bucket_id", ""))[:48].lower()
            window = str(bucket.get("window", "unknown"))[:24].lower()
            key = f"antigravity_{bucket_id}_{window}"
            selected = self._quota_visibility.get(key, self._quota_visibility["antigravity"])
            if not selected or not bucket_id:
                continue
            pct = bucket.get("used_percent")
            reset = bucket.get("reset_at")
            expired = isinstance(reset, datetime) and reset <= self._clock()
            pct_label = (
                f"{pct:g}% usado"
                if isinstance(pct, int | float) and not expired
                else "quota desconhecida"
            )
            reset_label = (
                format_countdown(reset, self._clock())
                if isinstance(reset, datetime) and not expired
                else "desconhecido"
            )
            model = str(bucket.get("model") or bucket_id)[:48]
            rows.append(f"{model} · {window} · {pct_label} · reset {reset_label}")
        status = data.get("status", "unknown")
        text = [f"Antigravity (experimental): {labels.get(status, 'desconhecido')}", *rows]
        if data.get("coverage_partial"):
            text.insert(1, "cobertura parcial (limite 256 buckets)")
        if status == "available" and not data.get("identity_known", False):
            text.insert(1, "identidade de conta não exposta pelo serviço")
        self.antigravity_status.setText("\n".join(text))
        self.antigravity_status.setToolTip(
            "Quota via serviço loopback do Antigravity IDE; protocolo experimental; "
            "fonte distinta de Gemini Web"
        )
        short_rows = rows[:3]
        if len(rows) > 3:
            short_rows.append(f"+{len(rows) - 3} quotas")
        self.antigravity_compact.setText(
            "Antigravity: " + ("\n".join(short_rows) or labels.get(status, "desconhecido"))
        )
        visible = self._quota_visibility["antigravity"] or any(
            key.startswith("antigravity_") and value
            for key, value in self._quota_visibility.items()
        )
        self.antigravity_status.setVisible(visible)
        self.antigravity_compact.setVisible(visible)
        self._apply_mode()

    def tick(self) -> None:
        """Re-renderiza countdowns com o relógio atual; dispara renovação vencida."""
        now = self._clock()
        observed = getattr(self, "_cli_observed_at", None)
        if isinstance(observed, datetime) and (now - observed).total_seconds() > 120:
            self.set_cli_status({"status": "unknown"})
        codex_observed = getattr(self, "_codex_observed_at", None)
        codex_windows = getattr(self, "_codex_payload", {}).get("windows", [])
        codex_reset_due = any(
            isinstance(window, dict)
            and isinstance(window.get("reset_at"), datetime)
            and window["reset_at"] <= now
            for window in codex_windows
        )
        if codex_reset_due:
            codex_payload = dict(self._codex_payload)
            codex_payload["windows"] = [
                {
                    **window,
                    "percent": None,
                    "reset_at": None,
                }
                if isinstance(window, dict)
                and isinstance(window.get("reset_at"), datetime)
                and window["reset_at"] <= now
                else window
                for window in codex_windows
            ]
            self.set_codex_status(codex_payload)
        elif isinstance(codex_observed, datetime) and (now - codex_observed).total_seconds() > 120:
            self.set_codex_status({"status": "unknown"})
        agy_observed = getattr(self, "_antigravity_observed_at", None)
        agy_buckets = getattr(self, "_antigravity_payload", {}).get("buckets", [])
        agy_reset_due = any(
            isinstance(bucket, dict)
            and isinstance(bucket.get("reset_at"), datetime)
            and bucket["reset_at"] <= now
            for bucket in agy_buckets
        )
        if agy_reset_due:
            agy_payload = dict(self._antigravity_payload)
            agy_payload["buckets"] = [
                {**bucket, "used_percent": None, "reset_at": None}
                if isinstance(bucket, dict)
                and isinstance(bucket.get("reset_at"), datetime)
                and bucket["reset_at"] <= now
                else bucket
                for bucket in agy_buckets
            ]
            self.set_antigravity_status(agy_payload)
        elif isinstance(agy_observed, datetime) and (now - agy_observed).total_seconds() > 120:
            self.set_antigravity_status({"status": "unknown"})
        self._render(now)
        self._observe_renewals(now)
        if self._emit_due(now):
            self._reload()

    @Slot(str, dict)
    def on_update(self, kind: str, payload: dict) -> None:
        if kind == "cli":
            self.set_cli_status(payload)
        elif kind == "codex":
            self.set_codex_status(payload)
        elif kind == "antigravity":
            self.set_antigravity_status(payload)
        elif kind in ("usage", "limit", "quota"):
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
        if self._selection_active:
            if compact:
                visible = int(
                    self._quota_visibility["claude_web_5h"]
                    or self._quota_visibility["claude_web_weekly"]
                )
                visible += int(
                    self._quota_visibility["chatgpt_web_5h"]
                    or self._quota_visibility["chatgpt_web_weekly"]
                )
                visible += int(self._quota_visibility["gemini_web_activity"])
                visible += int(
                    self._quota_visibility["claude_cli_5h"]
                    or self._quota_visibility["claude_cli_weekly"]
                )
                visible += int(
                    self._quota_visibility["codex_cli_primary"]
                    or self._quota_visibility["codex_cli_secondary"]
                )
                visible += int(
                    self._quota_visibility["antigravity"]
                    or any(
                        key.startswith("antigravity_") and value
                        for key, value in self._quota_visibility.items()
                    )
                )
                if not any(self._quota_visibility.values()):
                    visible = 1
                desired = (COMPACT_SIZE[0], max(44, min(COMPACT_SIZE[1], 14 + visible * 34)))
            else:
                height = 40 + int(self.cli_status.isVisible()) * 22
                height += int(self.codex_status.isVisible()) * 22
                height += int(self.antigravity_status.isVisible()) * (
                    22
                    + min(5, len(getattr(self, "_antigravity_payload", {}).get("buckets", []))) * 18
                )
                for provider in self.rows:
                    prefix = {"claude": "claude_web", "chatgpt": "chatgpt_web"}.get(provider)
                    if provider == "gemini":
                        count = int(self._quota_visibility["gemini_web_activity"]) * 2
                    else:
                        count = int(self._quota_visibility[f"{prefix}_5h"])
                        count += int(self._quota_visibility[f"{prefix}_weekly"])
                    if count:
                        height += 20 + count * 23
                desired = (EXPANDED_SIZE[0], max(60, min(EXPANDED_SIZE[1], height)))
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
