"""Per-source quota visibility preferences; independent from connector consent."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from neuralmeter.ui.settings import (
    MAX_QUOTA_OVERRIDES,
    QUOTA_DYNAMIC_KEY_PATTERN,
    QUOTA_VISIBILITY_DEFAULTS,
    parse_quota_visibility,
)

QUOTA_LABELS = {
    "claude_cli_5h": "Claude Code · 5h",
    "claude_cli_weekly": "Claude Code · 7d",
    "codex_cli_primary": "Codex CLI / Work · limite primário",
    "codex_cli_secondary": "Codex CLI / Work · limite secundário",
    "antigravity": "Antigravity IDE · buckets detectados (experimental)",
}


class QuotaVisibilityDialog(QDialog):
    def __init__(self, selection: dict | None = None, available: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quotas exibidas")
        values = parse_quota_visibility(selection)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Seleção controla somente exibição; não autoriza coleta CLI."))
        self.checkboxes: dict[str, QCheckBox] = {}
        available = available if isinstance(available, dict) else {}
        by_source = {
            prefix: [(key, label) for key, label in available.items() if key.startswith(prefix)]
            for prefix in ("codex_cli_", "antigravity_")
        }
        interleaved = []
        for index in range(max((len(rows) for rows in by_source.values()), default=0)):
            for prefix in by_source:
                if index < len(by_source[prefix]):
                    interleaved.append(by_source[prefix][index])
        available = dict(interleaved)
        input_dynamic_count = sum(
            1
            for key in available
            if isinstance(key, str) and QUOTA_DYNAMIC_KEY_PATTERN.fullmatch(key)
        )
        dynamic_saved = [key for key in values if key not in QUOTA_VISIBILITY_DEFAULTS]
        valid_keys = parse_quota_visibility({key: False for key in available})
        valid_available = {
            key: label
            for key, label in available.items()
            if isinstance(key, str) and key not in QUOTA_VISIBILITY_DEFAULTS and key in valid_keys
        }
        free_slots = max(0, MAX_QUOTA_OVERRIDES - len(dynamic_saved))
        new_available = [key for key in valid_available if key not in dynamic_saved]
        shown_new = new_available[:free_slots]
        partial = input_dynamic_count > MAX_QUOTA_OVERRIDES or len(new_available) > len(shown_new)
        available = {
            **{key: valid_available[key] for key in dynamic_saved if key in valid_available},
            **{key: valid_available[key] for key in shown_new},
        }
        keys = list(QUOTA_VISIBILITY_DEFAULTS) + [
            key for key in dynamic_saved if key not in QUOTA_VISIBILITY_DEFAULTS
        ]
        keys.extend(key for key in available if key not in keys)
        coverage = QLabel(
            "Cobertura parcial: limite de 256 quotas dinâmicas; "
            "quotas omitidas não são selecionáveis."
            if partial
            else f"Quotas dinâmicas selecionáveis: {len(dynamic_saved) + len(shown_new)} / 256."
        )
        coverage.setWordWrap(True)
        layout.addWidget(coverage)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        rows = QVBoxLayout(content)
        for key in keys:
            label = QUOTA_LABELS.get(key, available.get(key, key.replace("_", " · ")))
            row = QHBoxLayout()
            box = QCheckBox()
            box.setChecked(values.get(key, False))
            text = QLabel(str(label))
            text.setWordWrap(True)
            row.addWidget(box, 0)
            row.addWidget(text, 1)
            rows.addLayout(row)
            self.checkboxes[key] = box
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        screen = QApplication.primaryScreen()
        available_geometry = screen.availableGeometry() if screen else None
        max_height = int(available_geometry.height() * 0.8) if available_geometry else 640
        max_width = int(available_geometry.width() * 0.8) if available_geometry else 600
        self.resize(min(520, max_width), min(640, max_height))
        self.setMaximumSize(max_width, max_height)
        if available_geometry:
            self.setMinimumWidth(min(320, max_width))

    def selection(self) -> dict[str, bool]:
        return {key: box.isChecked() for key, box in self.checkboxes.items()}
