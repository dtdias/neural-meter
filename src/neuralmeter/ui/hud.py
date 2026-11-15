"""CLI-only always-on-top HUD and visible connection entry point."""

from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from neuralmeter.ui.settings import clamp_opacity, parse_quota_visibility
from neuralmeter.ui.snap import anchor_resize, clamp_to_screens, pick_screen, snap_to_edges
from neuralmeter.ui.view_model import format_countdown

EXPANDED_SIZE = (550, 270)
COMPACT_SIZE = (320, 150)
LEAVE_DELAY_MS = 600


class UpdateBridge(QObject):
    """Thread-safe Qt signal bridge for CLI collector results."""

    updated = Signal(str, dict)

    def emit_update(self, kind: str, payload: dict) -> None:
        self.updated.emit(kind, payload)


def _areas() -> list[tuple[int, int, int, int]]:
    return [
        (
            screen.availableGeometry().x(),
            screen.availableGeometry().y(),
            screen.availableGeometry().width(),
            screen.availableGeometry().height(),
        )
        for screen in QGuiApplication.screens()
    ]


class HudWindow(QWidget):
    opacity_changed = Signal(int)
    click_through_changed = Signal(bool)
    compact_changed = Signal(bool)
    request_quota_visibility = Signal()
    renewed = Signal(str)

    def __init__(
        self,
        on_moved: Callable[[int, int], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        compact: bool = False,
        on_compact_changed: Callable[[bool], None] | None = None,
        opacity: int = 100,
        click_through: bool = False,
    ) -> None:
        super().__init__()
        self.on_moved = on_moved
        self._clock = clock or (lambda: datetime.now(UTC))
        self._drag_offset: QPoint | None = None
        self._manage_callback: Callable[[], None] | None = None
        self._source_has_data = {"claude": False, "codex": False, "antigravity": False}
        self._selection_active = False
        self._quota_visibility = {
            "claude_cli_5h": True,
            "claude_cli_weekly": True,
            "codex_cli_primary": True,
            "codex_cli_secondary": True,
            "antigravity": True,
        }
        self.compact = bool(compact)
        self.on_compact_changed = on_compact_changed
        self._hover_expanded = False
        self.opacity = clamp_opacity(opacity)
        self.click_through = bool(click_through)
        self._payloads = {
            "claude": {"status": "not_connected"},
            "codex": {"status": "not_connected"},
            "antigravity": {"status": "not_connected"},
        }
        self._codex_payload = self._payloads["codex"]
        self._codex_observed_at = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        if self.click_through:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setWindowOpacity(self.opacity / 100)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(LEAVE_DELAY_MS)
        self._leave_timer.timeout.connect(self._on_leave_timeout)

        self.title_label = QLabel("NeuralMeter")
        self.title_label.setStyleSheet("color:#f2f2f2;font-weight:bold;font-size:13pt;")
        self.status_label = QLabel("Nenhuma fonte consultada")
        self.status_label.setStyleSheet("color:#a0a0a0;font-size:9pt;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.manage_button = QPushButton("Gerenciar conexões")
        self.manage_button.clicked.connect(self.open_connections)
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addWidget(self.status_label, 1)

        self.cli_status = self._status_label("Claude Code: desconectado")
        self.codex_status = self._status_label("Codex CLI: desconectado")
        self.antigravity_status = self._status_label("Antigravity IDE: desconectado")
        self.cli_compact = self._status_label("Claude Code: desconectado", 8)
        self.codex_compact = self._status_label("Codex CLI: desconectado", 8)
        self.antigravity_compact = self._status_label("Antigravity IDE: desconectado", 8)

        self.expanded_page = QWidget()
        expanded = QVBoxLayout(self.expanded_page)
        expanded.setContentsMargins(14, 10, 14, 10)
        expanded.setSpacing(8)
        expanded.addLayout(header)
        expanded.addWidget(self.manage_button)
        expanded.addWidget(self.cli_status)
        expanded.addWidget(self.codex_status)
        expanded.addWidget(self.antigravity_status)
        self.empty_quota_label = QLabel("Nenhuma quota selecionada")
        self.empty_quota_label.setStyleSheet("color:#d0d0d0;font-size:9pt;")
        self.empty_quota_action = QPushButton("Escolher quotas")
        self.empty_quota_action.clicked.connect(self.request_quota_visibility.emit)
        expanded.addWidget(self.empty_quota_label)
        expanded.addWidget(self.empty_quota_action)
        expanded.addStretch(1)

        self.compact_page = QWidget()
        compact_layout = QVBoxLayout(self.compact_page)
        compact_layout.setContentsMargins(10, 6, 10, 6)
        compact_layout.setSpacing(4)
        self.manage_compact_button = QPushButton("Conexões")
        self.manage_compact_button.clicked.connect(self.open_connections)
        compact_layout.addWidget(self.manage_compact_button)
        compact_layout.addWidget(self.cli_compact)
        compact_layout.addWidget(self.codex_compact)
        compact_layout.addWidget(self.antigravity_compact)
        self.empty_quota_compact_label = QLabel("Nenhuma quota selecionada")
        self.empty_quota_compact_label.setStyleSheet("color:#d0d0d0;font-size:8pt;")
        self.empty_quota_compact_action = QPushButton("Mostrar quotas CLI")
        self.empty_quota_compact_action.clicked.connect(self.request_quota_visibility.emit)
        compact_layout.addWidget(self.empty_quota_compact_label)
        compact_layout.addWidget(self.empty_quota_compact_action)
        self.stack = QWidget()
        from PySide6.QtWidgets import QStackedWidget

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
        self._render_statuses()
        self._render_quota_selection()

    @staticmethod
    def _status_label(text: str, size: int = 10) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color:#d0d0d0;font-size:{size}pt;")
        label.setWordWrap(True)
        return label

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 24, 28, 235))
        painter.drawRoundedRect(self.rect(), 12, 12)

    def set_connections_callback(self, callback: Callable[[], None]) -> None:
        self._manage_callback = callback

    def open_connections(self) -> None:
        if self._manage_callback is not None:
            self._manage_callback()

    def _update_source_summary(self, source: str, status: str) -> None:
        self._source_has_data[source] = status in {"available", "connected", "ok"}
        count = sum(self._source_has_data.values())
        self.status_label.setText(
            f"{count} de 3 fontes responderam" if count else "Nenhuma fonte consultada"
        )

    def set_cli_status(self, data: dict) -> None:
        self._payloads["claude"] = dict(data)
        status = str(data.get("status", "unknown"))
        auth_status = data.get("auth_status", "unknown")
        self._update_source_summary("claude", status)
        labels = {
            "unknown": "verificando",
            "not_connected": "não conectado",
            "available": "conectado com dados",
            "not_authenticated": "sessão sem autenticação OAuth compatível",
            "token_expired": "sessão reconhecida; token de quota indisponível",
            "identity_changed": "sessão mudou; verificando",
            "unsupported_auth": "método de autenticação não suportado",
            "request_failed": "quota indisponível",
            "quota_unavailable": "sessão reconhecida; quota indisponível",
            "rate_limited": "limite temporário",
            "cli_not_installed": "CLI não encontrado",
            "disconnected": "desconectado",
        }
        if status == "quota_unavailable" and auth_status == "authenticated":
            labels[status] = "sessão autenticada; quota indisponível"
        self._render_statuses()
        parts = [f"Claude Code: {labels.get(status, 'estado desconhecido')}"]
        windows = (("five_hour", "5h", "claude_cli_5h"), ("seven_day", "7d", "claude_cli_weekly"))
        for key, name, selection in windows:
            if not self._quota_visibility.get(selection, False):
                continue
            quota = data.get(key)
            if not isinstance(quota, dict):
                continue
            percent = quota.get("percent")
            reset = quota.get("reset_at")
            if isinstance(reset, datetime) and reset <= self._clock():
                parts.append(f"{name} quota desconhecida (reset vencido)")
            elif isinstance(percent, int | float):
                parts.append(f"{name} {percent:g}% usado")
                if isinstance(reset, datetime):
                    parts.append(f"reset {format_countdown(reset, self._clock())}")
            else:
                parts.append(f"{name} quota desconhecida")
        self.cli_status.setText(" · ".join(parts))
        self.cli_compact.setText(
            "Claude Code · " + " · ".join(parts[1:]) if len(parts) > 1 else parts[0]
        )
        self.cli_compact.setToolTip(" · ".join(parts))
        self._apply_mode()

    def set_codex_status(self, data: dict) -> None:
        self._payloads["codex"] = dict(data)
        self._codex_payload = dict(data)
        self._codex_observed_at = data.get("observed_at")
        status = str(data.get("status", "unknown"))
        self._update_source_summary("codex", status)
        labels = {
            "available": "conectado com dados",
            "not_connected": "não conectado",
            "cli_not_installed": "CLI não encontrado",
            "not_authenticated": "sessão sem autenticação",
            "auth_required": "autenticação necessária",
            "identity_changed": "sessão mudou; verificando",
            "timeout": "sem resposta",
            "rate_limited": "limite temporário",
            "quota_unavailable": "quota indisponível",
            "request_failed": "consulta indisponível",
            "unsupported_auth": "autenticação não suportada",
        }
        parts = [f"Codex CLI: {labels.get(status, 'estado desconhecido')}"]
        if data.get("coverage_partial"):
            parts.append("cobertura parcial (limite 256)")
        for window in data.get("windows", [])[:256]:
            if not isinstance(window, dict):
                continue
            scope = str(window.get("scope", ""))[:32]
            limit_id = str(window.get("limit_id", "codex"))[:48].lower()
            selected = (
                f"codex_cli_{scope}" if limit_id == "codex" else f"codex_cli_{limit_id}_{scope}"
            )
            if not self._quota_visibility.get(selected, False):
                continue
            percent = window.get("percent")
            reset = window.get("reset_at")
            if isinstance(reset, datetime) and reset <= self._clock():
                value = "quota desconhecida (reset vencido)"
            elif isinstance(percent, int | float):
                value = f"{percent:g}% usado"
            else:
                value = "quota desconhecida"
            reset_text = (
                format_countdown(reset, self._clock())
                if isinstance(reset, datetime)
                else "desconhecido"
            )
            label = str(window.get("limit_name") or limit_id)[:48]
            duration = window.get("window_minutes")
            duration_text = f" {duration}m" if isinstance(duration, int) else ""
            parts.append(f"{label} {scope}{duration_text}: {value}; reset {reset_text}")
        self.codex_status.setText(" · ".join(parts))
        self.codex_compact.setText("Codex · " + " · ".join(parts[1:] or parts[:1]))
        self.codex_compact.setToolTip(" · ".join(parts))
        self._apply_mode()

    def set_antigravity_status(self, data: dict) -> None:
        self._payloads["antigravity"] = dict(data)
        status = str(data.get("status", "unknown"))
        self._update_source_summary("antigravity", status)
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
            if not self._quota_visibility.get(
                key, self._quota_visibility.get("antigravity", False)
            ):
                continue
            percent = bucket.get("used_percent")
            reset = bucket.get("reset_at")
            expired = isinstance(reset, datetime) and reset <= self._clock()
            pct_text = (
                f"{percent:g}% usado"
                if isinstance(percent, int | float) and not expired
                else "quota desconhecida"
            )
            reset_text = (
                format_countdown(reset, self._clock())
                if isinstance(reset, datetime) and not expired
                else "desconhecido"
            )
            model = str(bucket.get("model") or bucket_id)[:48]
            rows.append(f"{model} · {window} · {pct_text} · reset {reset_text}")
        parts = [f"Antigravity IDE (experimental): {labels.get(status, 'estado desconhecido')}"]
        if data.get("coverage_partial"):
            parts.append("cobertura parcial (limite 256 buckets)")
        if status == "available" and not data.get("identity_known", False):
            parts.append("identidade de conta não exposta pelo serviço")
        parts.extend(rows)
        self.antigravity_status.setText("\n".join(parts))
        self.antigravity_status.setToolTip(
            "Quota por serviço loopback identificado; conector experimental."
        )
        self.antigravity_compact.setText("\n".join([parts[0], *rows[:3]]))
        self.antigravity_compact.setToolTip("\n".join(parts))
        self._apply_mode()

    def _render_statuses(self) -> None:
        self.cli_status.show()
        self.codex_status.show()
        self.antigravity_status.show()
        self.cli_compact.show()
        self.codex_compact.show()
        self.antigravity_compact.show()

    def set_quota_visibility(self, selection: dict[str, bool], *, resize: bool = True) -> None:
        self._quota_visibility = parse_quota_visibility(selection)
        self._selection_active = resize
        self._render_quota_selection()
        self._render_statuses()
        self.set_cli_status(self._payloads["claude"])
        self.set_codex_status(self._payloads["codex"])
        self.set_antigravity_status(self._payloads["antigravity"])
        self._apply_mode()

    def _render_quota_selection(self) -> None:
        empty = not any(self._quota_visibility.values())
        self.empty_quota_label.setVisible(empty)
        self.empty_quota_action.setVisible(empty)
        self.empty_quota_compact_label.setVisible(empty)
        self.empty_quota_compact_action.setVisible(empty)

    def on_update(self, kind: str, payload: dict) -> None:
        if kind == "cli":
            self.set_cli_status(payload)
        elif kind == "codex":
            self.set_codex_status(payload)
        elif kind == "antigravity":
            self.set_antigravity_status(payload)

    def tick(self) -> None:
        now = self._clock()
        for source, payload in tuple(self._payloads.items()):
            observed = payload.get("observed_at")
            if isinstance(observed, datetime) and (now - observed).total_seconds() > 120:
                status = {
                    "claude": self.set_cli_status,
                    "codex": self.set_codex_status,
                    "antigravity": self.set_antigravity_status,
                }[source]
                status({"status": "unknown"})
                continue
            if source == "codex":
                data = dict(payload)
                data["windows"] = [
                    {**w, "percent": None, "reset_at": None}
                    if isinstance(w, dict)
                    and isinstance(w.get("reset_at"), datetime)
                    and w["reset_at"] <= now
                    else w
                    for w in payload.get("windows", [])
                ]
                self.set_codex_status(data)
            elif source == "antigravity":
                data = dict(payload)
                data["buckets"] = [
                    {**b, "used_percent": None, "reset_at": None}
                    if isinstance(b, dict)
                    and isinstance(b.get("reset_at"), datetime)
                    and b["reset_at"] <= now
                    else b
                    for b in payload.get("buckets", [])
                ]
                self.set_antigravity_status(data)
        self._render_statuses()

    def refresh(self) -> None:
        """Refresh CLI labels without loading historic Web usage from SQLite."""
        self._render_statuses()

    def set_compact(self, value: bool) -> None:
        changed = bool(value) != self.compact
        self.compact = bool(value)
        self._hover_expanded = False
        self._leave_timer.stop()
        self._apply_mode()
        if changed:
            if self.on_compact_changed:
                self.on_compact_changed(self.compact)
            self.compact_changed.emit(self.compact)

    def _apply_mode(self) -> None:
        compact = self.effective_compact
        desired = COMPACT_SIZE if compact else EXPANDED_SIZE
        selected = sum(bool(value) for value in self._quota_visibility.values())
        if selected == 0:
            desired = (COMPACT_SIZE[0], 200) if compact else EXPANDED_SIZE
        elif self._selection_active:
            visible = selected or 1
            desired = (
                (COMPACT_SIZE[0], min(COMPACT_SIZE[1], max(80, 16 + visible * 32)))
                if compact
                else (EXPANDED_SIZE[0], min(EXPANDED_SIZE[1], max(120, 100 + visible * 20)))
            )
        areas = _areas()
        win = (self.x(), self.y(), self.width(), self.height())
        area = pick_screen(win, areas) if areas else (0, 0, *desired)
        size = (min(desired[0], area[2]), min(desired[1], area[3]))
        self.stack.setCurrentWidget(self.compact_page if compact else self.expanded_page)
        if (self.width(), self.height()) == size:
            return
        if self.isVisible():
            pos = clamp_to_screens(anchor_resize(win, size, pick_screen(win, areas)), size, areas)
            self.setFixedSize(*size)
            self.move(*pos)
        else:
            self.setFixedSize(*size)

    @property
    def effective_compact(self) -> bool:
        return self.compact and not self._hover_expanded

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
            self._hover_expanded = False
        pos, visible = self.pos(), self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, on)
        if visible:
            self.show()
            self.move(pos)
        self.click_through_changed.emit(on)

    def toggle_visible(self) -> None:
        self.hide() if self.isVisible() else self.show()

    def apply_saved_position(self, pos) -> None:
        x, y = clamp_to_screens(
            tuple(pos) if pos is not None else None, (self.width(), self.height()), _areas()
        )
        self.move(x, y)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_compact(not self.compact)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.compact and self._drag_offset is None and not self._hover_expanded:
            self._leave_timer.stop()
            self._hover_expanded = True
            self._apply_mode()

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_expanded and self._drag_offset is None:
            self._leave_timer.start(LEAVE_DELAY_MS)

    def _on_leave_timeout(self) -> None:
        if self._hover_expanded:
            self._hover_expanded = False
            self._apply_mode()

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
        x, y = snap_to_edges(win, pick_screen(win, _areas()))
        self.move(x, y)
        if self.on_moved:
            self.on_moved(x, y)
