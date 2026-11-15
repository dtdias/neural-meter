"""Gera ícone do app e .ico a partir de assets/logo.svg. Idempotente."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "logo.svg"
ASSETS_DIR = ROOT / "src" / "neuralmeter" / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 256)


def render_svg_png(svg_path: Path, size: int) -> QImage:
    """Renderiza o SVG em QImage ARGB32 transparente size x size."""
    renderer = QSvgRenderer(str(svg_path))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    return img


def build_ico(svg_path: Path, out: Path, sizes: tuple[int, ...] = ICO_SIZES) -> None:
    """Gera .ico multi-tamanho (Pillow) a partir do SVG, renderizando cada tamanho."""
    from PIL import Image

    tmp = out.parent / ".ico_src.png"
    render_svg_png(svg_path, max(sizes)).save(str(tmp), "PNG")
    try:
        with Image.open(tmp) as base:
            base.load()
            base.save(out, format="ICO", sizes=[(s, s) for s in sizes])
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    render_svg_png(SVG, 256).save(str(ASSETS_DIR / "icon.png"), "PNG")
    build_ico(SVG, ASSETS_DIR / "neuralmeter.ico")
    print("icons ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
