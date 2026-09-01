"""Background decode workers using QThread."""

from __future__ import annotations

import os

from PyQt5.QtCore import QObject, pyqtSignal

from raw_view.formats import (
    YUV_BYTES_PER_PIXEL,
    ImageSpec,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    raw_to_display_gray,
)
from raw_view.converter import bayer8_to_rgb
from raw_view.logger import get_logger

logger = get_logger(__name__)


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
    finished(int, object)
        Emitted on successful decode. Carries the generation counter (so the
        main window can drop stale results) and the DecodeResult.
    error(int, str)
        Emitted when decoding fails. Carries the generation counter and a
        human-readable message that includes the file/frame/parameters.
    """

    finished = pyqtSignal(int, object)  # generation, DecodeResult
    error = pyqtSignal(int, str)        # generation, message

    def __init__(self) -> None:
        super().__init__()
        self._data: bytes | None = None
        self._spec: ImageSpec | None = None
        self._format_name: str = ""
        self._alignment: str = "msb"
        self._endianness: str = "little"
        self._preview_mode: str = "Bayer Color"
        self._bayer_pattern: str = "RGGB"
        self._generation: int = 0
        # Error-reporting context: remembered from configure() so the worker
        # can build a precise "which file / which frame" message on failure.
        self._file_path: str = ""
        self._frame_index: int = 0
        # The real byte offset of this frame in the file. `spec.offset` is 0
        # because `data` is already the sliced frame; keep this for messages.
        self._source_offset: int = 0

    def configure(
        self,
        data: bytes,
        spec: ImageSpec,
        format_name: str,
        alignment: str = "msb",
        endianness: str = "little",
        preview_mode: str = "Bayer Color",
        bayer_pattern: str = "RGGB",
        generation: int = 0,
        file_path: str = "",
        frame_index: int = 0,
        source_offset: int = 0,
    ) -> None:
        """Set decode parameters before starting the thread."""
        self._data = data
        self._spec = spec
        self._format_name = format_name
        self._alignment = alignment
        self._endianness = endianness
        self._preview_mode = preview_mode
        self._bayer_pattern = bayer_pattern
        self._generation = generation
        self._file_path = file_path
        self._frame_index = frame_index
        self._source_offset = source_offset

    def _describe_source(self) -> str:
        """Human-readable "which file / which frame" prefix for error messages."""
        name = os.path.basename(self._file_path) if self._file_path else "?"
        if self._spec is not None:
            detail = (
                f"{name} frame {self._frame_index} "
                f"(format={self._format_name}, {self._spec.width}x{self._spec.height}, "
                f"offset={self._source_offset})"
            )
        else:
            detail = f"{name} frame {self._frame_index}"
        return detail

    def run(self) -> None:
        """Decode the image (call from QThread)."""
        try:
            if self._data is None or self._spec is None:
                logger.error("No data configured for decode")
                self.error.emit(self._generation, f"No data configured for decode ({self._describe_source()})")
                return

            from PyQt5.QtGui import QImage

            if self._format_name in YUV_BYTES_PER_PIXEL:
                # ── YUV path ────────────────────────────────────────
                # 含 YOnly（YUV 4:0:0 全分辨率灰度），其命名已在字典中，
                # 自动走进 YUV 解码分支。
                logger.debug(
                    "Worker decoding YUV: %s, %dx%d, offset=%d",
                    self._format_name, self._spec.width, self._spec.height, self._spec.offset,
                )
                # alignment/endianness 只对 YOnly 多 bit（10/12/14/16，16-bit 存储）
                # 有效；其余 YUV 格式忽略这两个参数（decode_yuv 保持向后兼容）。
                rgb = decode_yuv(
                    self._data,
                    self._spec,
                    self._format_name,
                    alignment=self._alignment,
                    endianness=self._endianness,
                )
                h, w = rgb.shape[:2]
                qimg = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format_RGB888).copy()
                result = DecodeResult(rgb, qimg, w, h, self._format_name)
            else:
                # ── RAW path ────────────────────────────────────────
                logger.debug(
                    "Worker decoding RAW: %s, %dx%d, align=%s, endian=%s, offset=%d",
                    self._format_name, self._spec.width, self._spec.height,
                    self._alignment, self._endianness, self._spec.offset,
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
                    except ValueError as exc:
                        logger.warning("Bayer demosaic failed (%s), falling back to grayscale", exc)
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

            logger.debug("Worker decode finished successfully")
            self.finished.emit(self._generation, result)
        except Exception as exc:
            logger.exception("Worker decode failed: %s", self._describe_source())
            self.error.emit(self._generation, f"{self._describe_source()}: {exc}")
