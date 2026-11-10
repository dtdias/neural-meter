import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from neuralmeter.ui import tray  # noqa: E402
from neuralmeter.ui.icons import app_icon_path, load_app_icon  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def test_app_icon_path_exists():
    p = app_icon_path()
    assert p is not None and p.is_file()


def test_load_app_icon_has_sizes():
    _app()
    icon = load_app_icon()
    assert icon is not None and not icon.isNull()
    assert icon.availableSizes()


def test_icon_png_256_rgba():
    with Image.open(app_icon_path()) as im:
        assert im.size == (256, 256)
        assert im.mode == "RGBA"


def test_ico_has_16_and_256():
    ico = app_icon_path().parent / "neuralmeter.ico"
    with Image.open(ico) as im:
        sizes = im.info["sizes"]
    assert (16, 16) in sizes and (256, 256) in sizes


def test_tray_fallback(monkeypatch):
    _app()
    monkeypatch.setattr("neuralmeter.ui.icons.app_icon_path", lambda: None)
    icon = tray.make_tray_icon()
    assert isinstance(icon, QIcon) and not icon.isNull()
