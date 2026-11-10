"""Dialogo para editar tetos 5h/semanal por provedor (provider_state)."""

import sqlite3

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from neuralmeter.db import PROVIDERS, get_provider_limits, set_provider_limits
from neuralmeter.ui.view_model import PROVIDER_LABELS

NOTE = "Overrides por modelo ChatGPT: settings.json → chatgpt_model_limits"


class LimitsDialog(QDialog):
    def __init__(self, conn: sqlite3.Connection, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._conn = conn
        self.setWindowTitle("Configurar limites")
        self.spins: dict[tuple[str, str], QDoubleSpinBox] = {}
        form = QFormLayout()
        for provider in PROVIDERS:
            limits = get_provider_limits(conn, provider)
            label = PROVIDER_LABELS.get(provider, provider)
            for field, suffix in (("window_5h_max_units", "5h"), ("weekly_max_units", "semanal")):
                spin = QDoubleSpinBox()
                spin.setRange(1, 100000)
                spin.setDecimals(1)
                spin.setValue(limits[field])
                self.spins[(provider, field)] = spin
                form.addRow(f"{label} — {suffix}", spin)
        self.note = QLabel(NOTE)
        self.note.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addWidget(buttons)

    def accept(self) -> None:
        for provider in PROVIDERS:
            set_provider_limits(
                self._conn,
                provider,
                window_5h_max_units=self.spins[(provider, "window_5h_max_units")].value(),
                weekly_max_units=self.spins[(provider, "weekly_max_units")].value(),
            )
        super().accept()


def open_limits_dialog(conn: sqlite3.Connection, parent: QWidget | None = None) -> bool:
    """Abre o dialogo modal; True se o usuario salvou."""
    dlg = LimitsDialog(conn, parent)
    return dlg.exec() == QDialog.DialogCode.Accepted
