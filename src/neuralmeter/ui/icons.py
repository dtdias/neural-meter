"""Carrega o icone real do app (assets/icon.png); None se ausente."""

import sys
from importlib.resources import files
from pathlib import Path

from PySide6.QtGui import QIcon


def app_icon_path() -> Path | None:
    """Caminho de icon.png no pacote ou no bundle PyInstaller; None se nao existe."""
    candidates = [Path(str(files("neuralmeter.assets").joinpath("icon.png")))]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "neuralmeter" / "assets" / "icon.png")
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_app_icon() -> QIcon | None:
    path = app_icon_path()
    if path is None:
        return None
    icon = QIcon(str(path))
    return None if icon.isNull() else icon
