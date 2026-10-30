"""Shared numpy->QImage helpers and byte-size formatting.

PyQt5's ``QImage(data, ...)`` requires a ``bytes``-like buffer. NumPy 2.x
changed ``ndarray.data`` from a ``bytearray``/``voidptr`` to a ``memoryview``,
which PyQt5 cannot construct QImage from ("argument 1 has unexpected type
'memoryview'"). These helpers always hand the contiguous bytes to QImage so
both NumPy 1.x and 2.x work.

All arrays passed in must be contiguous (callers use ``np.ascontiguousarray``
when unsure); the helper copies rows to a contiguous buffer defensively.
"""

from __future__ import annotations

import numpy as np
from PyQt5.QtGui import QImage


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a compact human-readable string (UI-9).

    全项目统一的字节数 helper（0.3.0-L-4 / 0.4.0-L-1 / 0.4.1-L-1：曾有三份并存，
    单位/千分位不一）。1024 进制，GB 封顶；面板 Estimated frame、Convert/Batch
    预览与主窗状态共用此实现，避免漂移。
    """
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def qimage_from_grayscale(gray: np.ndarray) -> QImage:
    """Build a QImage (Format_Grayscale8) from a uint8 ``(h, w)`` array."""
    h, w = gray.shape
    data = np.ascontiguousarray(gray)
    return QImage(data.tobytes(), w, h, w, QImage.Format_Grayscale8).copy()


def qimage_from_rgb(rgb: np.ndarray) -> QImage:
    """Build a QImage (Format_RGB888) from a uint8 ``(h, w, 3)`` array."""
    h, w = rgb.shape[:2]
    data = np.ascontiguousarray(rgb)
    bytes_per_line = w * 3
    return QImage(data.tobytes(), w, h, bytes_per_line, QImage.Format_RGB888).copy()


def qimage_from_bgr(bgr: np.ndarray) -> QImage:
    """Build a QImage (Format_BGR888) directly from a uint8 ``(h, w, 3)`` BGR array.

    OpenCV decodes to BGR.  Routing it through :func:`qimage_from_rgb` first
    reverses the channel order (``bgr[:, :, ::-1]``, a negative-stride view)
    and then copies that view into a contiguous array *and* into ``bytes``
    before Qt can take it — two full-frame copies on the UI thread (measured
    183 ms for 4000x3000, 93 ms of which was ``ascontiguousarray`` alone).

    ``Format_BGR888`` (Qt 5.14+) accepts BGR bytes as-is, so the frame is copied
    once inside ``QImage.copy()`` — measured 30-35 ms for the same image, with
    identical pixel content.

    Callers must remember that the resulting array is **BGR**, not RGB; the
    viewer marks that explicitly with ``ViewerItem.display_is_bgr``.
    """
    h, w = bgr.shape[:2]
    data = np.ascontiguousarray(bgr)
    bytes_per_line = w * 3
    return QImage(data.tobytes(), w, h, bytes_per_line, QImage.Format_BGR888).copy()
