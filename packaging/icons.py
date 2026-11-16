"""Render the app icon (packaging/substance-version-tool.svg) with the Qt that is bundled anyway,
so no SVG or icon tool is needed on the build machine.

    python packaging/icons.py png OUT.png        # 256 px, for Linux
    python packaging/icons.py ico OUT.ico        # 16 - 256 px, for Windows

Set QT_QPA_PLATFORM=offscreen when there is no display.
"""
import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SVG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'substance-version-tool.svg')
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(SVG).render(p)
    p.end()
    return img


def png_bytes(img):
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, 'PNG')
    return bytes(data.data())


def ico_bytes(sizes):
    """An .ico whose images are PNGs (supported since Windows Vista)."""
    images = [png_bytes(render(s)) for s in sizes]
    out = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b''.join(images)


def main():
    kind, dst = sys.argv[1:3]
    app = QGuiApplication([])                  # QPainter needs one
    data = png_bytes(render(256)) if kind == 'png' else ico_bytes(ICO_SIZES)
    app.quit()
    with open(dst, 'wb') as f:
        f.write(data)


if __name__ == '__main__':
    main()
