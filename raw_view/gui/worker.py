"""Background decode workers using QThread."""

from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from raw_view.formats import (
    ImageSpec,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    raw_to_display_gray,
)
from raw_view.converter import bayer8_to_rgb


class DecodeResult:
    """Holds the result of a decode operation."""

    def __init__(self, display_array, qimage, width: int, height: int, format_name: str) -> None:
        self.display_array = display_array
        self.qimage = qimage
        self.width = width
        self.height = height
        self.format_name = format_name


class DecodeWorker(QObject):
    """Decodes RAW/YUV data in a background thread.

    Signals
    -------
    finished(result: DecodeResult)
        Emitted on successful decode.
    error(message: str)
        Emitted when decoding fails.
    """

    finished = pyqtSignal(object)  # DecodeResult
    error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._data: bytes | None = None
        self._spec: ImageSpec | None = None
        self._format_name: str = ""
        self._alignment: str = "lsb"
        self._endianness: str = "little"
        self._preview_mode: str = "Bayer Color"
        self._bayer_pattern: str = "RGGB"

    def configure(
        self,
        data: bytes,
        spec: ImageSpec,
        format_name: str,
        alignment: str = "lsb",
        endianness: str = "little",
        preview_mode: str = "Bayer Color",
        bayer_pattern: str = "RGGB",
    ) -> None:
        """Set decode parameters before starting the thread."""
        self._data = data
        self._spec = spec
        self._format_name = format_name
        self._alignment = alignment
        self._endianness = endianness
        self._preview_mode = preview_mode
        self._bayer_pattern = bayer_pattern

    def run(self) -> None:
        """Decode the image (call from QThread)."""
        try:
            if self._data is None or self._spec is None:
                self.error.emit("No data configured for decode")
                return

            from PyQt5.QtGui import QImage

            if self._format_name in ("I420", "YV12", "NV12", "NV21", "YUYV", "UYVY", "NV16"):
                # ── YUV path ────────────────────────────────────────
                expected = expected_frame_size_yuv(
                    self._format_name, self._spec.width, self._spec.height
                )
                # We only run when the caller already validated the size
                rgb = decode_yuv(self._data, self._spec, self._format_name)
                h, w = rgb.shape[:2]
                qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
                result = DecodeResult(rgb, qimg, w, h, self._format_name)
            else:
                # ── RAW path ────────────────────────────────────────
                expected = expected_frame_size_raw(
                    self._format_name, self._spec.width, self._spec.height
                )
                raw = decode_raw(
                    self._data,
                    self._spec,
                    self._format_name,
                    self._alignment,
                    self._endianness,
                )
                raw8 = raw_to_display_gray(raw, self._format_name)

                if self._preview_mode.startswith("Bayer"):
                    try:
                        rgb = bayer8_to_rgb(raw8, pattern=self._bayer_pattern)
                        h, w = rgb.shape[:2]
                        qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
                        result = DecodeResult(rgb, qimg, w, h, self._format_name)
                    except ValueError:
                        fallback = raw8
                        h, w = fallback.shape
                        qimg = QImage(
                            fallback.data, w, h, fallback.strides[0], QImage.Format_Grayscale8
                        ).copy()
                        result = DecodeResult(fallback, qimg, w, h, self._format_name)
                else:
                    h, w = raw8.shape
                    qimg = QImage(
                        raw8.data, w, h, raw8.strides[0], QImage.Format_Grayscale8
                    ).copy()
                    result = DecodeResult(raw8, qimg, w, h, self._format_name)

            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
