"""Shared numpy->QImage helpers.

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
