"""Render assets/logo.svg to PNG sizes and a multi-size Windows .ico.

Dev-only helper — run after editing logo.svg:

    .venv\\Scripts\\python scripts\\make_icon.py

Requires PyQt5 (QtSvg) and Pillow, both already project dependencies.
Outputs: assets/logo.png (256x256) and assets/raw-view.ico (multi-size).
"""

from __future__ import annotations

import io
import os
import sys

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SVG_PATH = os.path.join(ASSETS, "logo.svg")
PNG_PATH = os.path.join(ASSETS, "logo.png")
ICO_PATH = os.path.join(ASSETS, "raw-view.ico")

ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render_png(size: int) -> "Image.Image":
    from PIL import Image

    renderer = QSvgRenderer(SVG_PATH)
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()

    # Convert QImage -> PNG bytes -> PIL Image.
    from PyQt5.QtCore import QBuffer

    qbuf = QBuffer()
    qbuf.open(QBuffer.ReadWrite)
    img.save(qbuf, "PNG")
    data = bytes(qbuf.data())
    qbuf.close()
    return Image.open(io.BytesIO(data)).convert("RGBA")


def main() -> int:
    if not os.path.isfile(SVG_PATH):
        print(f"SVG not found: {SVG_PATH}", file=sys.stderr)
        return 1
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

    images = {size: render_png(size) for size in ICON_SIZES}
    images[256].save(PNG_PATH)
    images[256].save(ICO_PATH, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    print(f"Wrote {PNG_PATH}")
    print(f"Wrote {ICO_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
