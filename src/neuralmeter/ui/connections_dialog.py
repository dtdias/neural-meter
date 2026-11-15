"""Visible CLI source manager; discovery is explicit and read-only."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from neuralmeter.antigravity import _antigravity_install, antigravity_endpoint_available
from neuralmeter.claude_cli import discover_claude
from neuralmeter.codex_cli import discover_codex


class _DiscoveryResult(QObject):
    ready = Signal(object)


class ConnectionsDialog(QDialog):
    """Three explicit source cards; installing/discovering never grants collection consent."""

    status_updated = Signal(str, str, str)

    SOURCES = (
        (
            "claude",
            "Claude Code",
            "Quota OAuth Claude Code; consulta oficial read-only após opt-in.",
        ),
        ("codex", "Codex CLI", "Quotas consultadas via app-server oficial após opt-in."),
        (
            "antigravity",
            "Antigravity IDE",
            "Experimental: quota via serviço local do IDE; Gemini CLI não substitui esta fonte.",
        ),
    )

    def __init__(self, enabled: dict[str, bool], on_toggle, parent=None, on_refresh=None):
        super().__init__(parent)
        self.setWindowTitle("Gerenciar conexões CLI")
        self.setMinimumSize(360, 280)
        self.resize(540, 500)
        self._enabled = dict(enabled)
        self._on_toggle = on_toggle
        self._on_refresh = on_refresh
        self._locations = {key: None for key, _name, _description in self.SOURCES}
        self._antigravity_active = False
        self._auth_status = {key: "unknown" for key, _name, _description in self.SOURCES}
        self._quota_status = {key: "unknown" for key, _name, _description in self.SOURCES}
        self._searched = False
        self._rows = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cli-discovery")
        self._result = _DiscoveryResult(self)
        self._result.ready.connect(self._show_discovery)
        self.status_updated.connect(self._apply_status)

        layout = QVBoxLayout(self)
        disclosure = QLabel(
            "Descoberta verifica instalações/serviço local sem rede e não coleta quotas. "
            "Conectar grava consentimento local e inicia consultas read-only; sem login, "
            "refresh de sessão, prompts ou inferência."
        )
        disclosure.setWordWrap(True)
        disclosure.setStyleSheet("font-size: 10pt;")
        layout.addWidget(disclosure)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cards = QWidget()
        cards_layout = QVBoxLayout(cards)
        cards_layout.setContentsMargins(2, 2, 2, 2)
        for key, title, description in self.SOURCES:
            card = QWidget()
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.addWidget(QLabel(f"<b>{title}</b>"))
            card_layout.addWidget(QLabel(description))
            status_labels = {}
            for state_key in ("installation", "auth", "consent", "quota"):
                label = QLabel()
                label.setWordWrap(True)
                label.setStyleSheet("font-size: 10pt;")
                status_labels[state_key] = label
                card_layout.addWidget(label)
            action = QPushButton()
            action.setMinimumHeight(34)
            action.clicked.connect(lambda _checked=False, source=key: self._toggle(source))
            self._rows[key] = {**status_labels, "action": action}
            card_layout.addWidget(action)
            cards_layout.addWidget(card)
            self._render_action(key)
        cards_layout.addStretch(1)
        self._scroll.setWidget(cards)
        layout.addWidget(self._scroll, 1)
        self._discovery = QPushButton("Procurar CLIs novamente")
        self._discovery.clicked.connect(self.discover)
        layout.addWidget(self._discovery)
        self._connect_detected = QPushButton("Conectar fontes detectadas")
        self._connect_detected.setEnabled(False)
        self._connect_detected.clicked.connect(self.connect_detected)
        layout.addWidget(self._connect_detected)
        self._refresh = QPushButton("Atualizar quotas autorizadas")
        self._refresh.clicked.connect(self._request_refresh)
        layout.addWidget(self._refresh)
        self._update_refresh_enabled()

    def _render_action(self, source: str) -> None:
        row = self._rows[source]
        button = row["action"]
        button.setText("Desconectar" if self._enabled.get(source, False) else "Conectar")
        button.setEnabled(self._locations[source] is not None or self._enabled.get(source, False))
        if self._locations[source] is not None:
            if source == "antigravity":
                service = (
                    "serviço identificado ativo"
                    if self._antigravity_active
                    else "serviço não detectado"
                )
                install = f"IDE instalado; {service}"
            elif self._locations[source] == "collector-confirmed":
                install = "Instalado (CLI respondeu)"
            else:
                install = "Instalado (CLI detectado)"
        else:
            install = (
                "Não encontrado nos locais conhecidos" if self._searched else "Ainda não verificada"
            )
        consent = (
            "Autorizada (opt-in salvo)" if self._enabled.get(source, False) else "Não autorizada"
        )
        row["installation"].setText(f"Instalação: {install}")
        row["auth"].setText(f"Autenticação: {self._auth_label(source, self._auth_status[source])}")
        row["consent"].setText(f"Coleta: {consent}")
        row["quota"].setText(f"Quota: {self._quota_label(self._quota_status[source])}")
        self._update_refresh_enabled()

    def _update_refresh_enabled(self) -> None:
        button = getattr(self, "_refresh", None)
        if button is not None:
            button.setEnabled(self._on_refresh is not None and any(self._enabled.values()))

    @Slot()
    def _request_refresh(self) -> None:
        if self._on_refresh is not None:
            self._on_refresh(tuple(source for source, enabled in self._enabled.items() if enabled))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        bounds = screen.availableGeometry()
        width = min(max(self.width(), self.minimumWidth()), bounds.width())
        height = min(max(self.height(), self.minimumHeight()), bounds.height())
        self.resize(width, height)
        self.move(
            min(max(self.x(), bounds.left()), bounds.right() - width + 1),
            min(max(self.y(), bounds.top()), bounds.bottom() - height + 1),
        )

    @staticmethod
    def _auth_label(source: str, status: str) -> str:
        if source == "antigravity":
            return "serviço local não expõe identidade de autenticação"
        return {
            "unknown": "ainda não verificada",
            "not_checked": "não verificada",
            "authenticated": "sessão reconhecida pelo CLI",
            "not_authenticated": "CLI informa sessão não autenticada",
            "unsupported_auth": "sessão/método incompatível ou desconhecido",
            "auth_required": "autenticação necessária",
        }.get(status, "estado não confirmado")

    @staticmethod
    def _quota_label(status: str) -> str:
        return {
            "unknown": "aguardando consulta",
            "not_connected": "não consultada (opt-in desligado)",
            "cli_not_installed": "CLI não detectado",
            "available": "consulta recebida; valores ausentes permanecem desconhecidos",
            "quota_unavailable": "indisponível; sessão autenticada não prova quota disponível",
            "request_failed": "consulta falhou; quota desconhecida",
            "rate_limited": "serviço limitou consultas; quota desconhecida",
            "identity_changed": "sessão mudou; consulta reiniciada",
            "not_authenticated": "indisponível; CLI informa sessão não autenticada",
            "unsupported_auth": "indisponível; auth incompatível ou desconhecida",
            "ide_not_found": "IDE não detectado",
            "server_not_found": "IDE detectado; serviço local ausente",
            "auth_required": "autenticação necessária no serviço do provider",
            "timeout": "sem resposta; quota desconhecida",
        }.get(status, "estado não confirmado; quota desconhecida")

    def _toggle(self, source: str) -> None:
        enabled = not self._enabled.get(source, False)
        self._enabled[source] = enabled
        self._on_toggle(source, enabled)
        self._render_action(source)
        if enabled:
            self._quota_status[source] = "unknown"
            self._render_action(source)

    def discover(self) -> None:
        self._discovery.setEnabled(False)
        self._discovery.setText("Procurando…")

        def work():
            try:
                antigravity_install = _antigravity_install()
                codex_install = discover_codex()
                antigravity_active = antigravity_endpoint_available(antigravity_install)
                found = {
                    "claude": discover_claude(),
                    "codex": codex_install[0] if codex_install else None,
                    "antigravity": str(antigravity_install) if antigravity_install else None,
                    "antigravity_active": antigravity_active,
                }
            except Exception:
                found = {
                    **{key: None for key, _title, _description in self.SOURCES},
                    "antigravity_active": False,
                }
            self._result.ready.emit(found)

        self._executor.submit(work)

    @Slot()
    def connect_detected(self) -> None:
        for source, _title, _description in self.SOURCES:
            if self._locations[source] is not None and not self._enabled[source]:
                self._toggle(source)

    @Slot(object)
    def _show_discovery(self, found: dict) -> None:
        self._locations.update(found)
        self._antigravity_active = bool(found.get("antigravity_active", False))
        self._searched = True
        self._connect_detected.setEnabled(True)
        self._discovery.setEnabled(True)
        self._discovery.setText("Procurar CLIs novamente")
        for source, _title, _description in self.SOURCES:
            self._render_action(source)

    def update_status(self, source: str, quota_status: str, auth_status: str = "unknown") -> None:
        self.status_updated.emit(source, quota_status, auth_status)

    @Slot(str, str, str)
    def _apply_status(self, source: str, quota_status: str, auth_status: str) -> None:
        if source not in self._rows:
            return
        confirmed_installed = {
            "available",
            "quota_unavailable",
            "request_failed",
            "rate_limited",
            "identity_changed",
            "not_authenticated",
            "unsupported_auth",
            "auth_required",
            "server_not_found",
        }
        absent = {"cli_not_installed", "ide_not_found"}
        if quota_status in confirmed_installed:
            self._locations[source] = "collector-confirmed"
            self._searched = True
        elif quota_status in absent:
            self._locations[source] = None
            self._searched = True
        self._quota_status[source] = quota_status
        self._auth_status[source] = auth_status
        self._render_action(source)

    def set_enabled(self, source: str, enabled: bool) -> None:
        self._enabled[source] = bool(enabled)
        if not enabled:
            self._quota_status[source] = "not_connected"
        self._render_action(source)
