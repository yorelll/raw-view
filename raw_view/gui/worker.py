"""Background decode workers using QThread."""

from __future__ import annotations

import os
from collections import OrderedDict

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
from raw_view.gui.image_utils import qimage_from_grayscale, qimage_from_rgb
from raw_view.logger import get_logger

# P1-1 解码缓存的容量上限：总字节（默认 ~250MB，约半帧 512MB 上限）与条目数。
MAX_DECODE_CACHE_BYTES = 250 * 1024 * 1024
MAX_DECODE_CACHE_ITEMS = 32

logger = get_logger(__name__)


class DecodeResult:
    """Holds the result of a decode operation."""

    def __init__(self, display_array, qimage, width: int, height: int, format_name: str) -> None:
        self.display_array = display_array
        self.qimage = qimage
        self.width = width
        self.height = height
        self.format_name = format_name


class DecodeCache:
    """按 (file_path, format, width, height, alignment, endianness, frame)
    缓存的单帧解码结果（P1-1）。

    - 纯 Python 对象、无 Qt 依赖，可被单元测试直接覆盖；
    - 容量上限（字节 + 条目数双保险）到上限时淘汰最久未用（LRU 顺序由
      ``collections.OrderedDict`` 顺序保证）；
    - ``store`` 返回是否真正插入（调用方用于 heap 增长判断 vs. 命中场景）。
    """

    def __init__(self, max_bytes: int = MAX_DECODE_CACHE_BYTES, max_items: int = MAX_DECODE_CACHE_ITEMS):
        # 显式传参时不设下界（测试可用小容量）；默认值保持 250MB/32 条。
        self._max_bytes = int(max_bytes) if max_bytes else MAX_DECODE_CACHE_BYTES
        self._max_bytes = max(self._max_bytes, 1)
        self._max_items = max(int(max_items), 1)
        self._entries: OrderedDict[str, DecodeResult] = OrderedDict()
        self._total_bytes = 0
        self.hits = 0
        self.misses = 0
        self.store_calls = 0

    @staticmethod
    def _canonical_path(path: str) -> str:
        """Return a stable path identity for cache keys/invalidation.

        Windows is case-insensitive in normal use; normalising case plus an
        absolute real path avoids treating relative/case variants as different
        cache namespaces.
        """
        return os.path.normcase(os.path.abspath(os.path.realpath(path)))

    @classmethod
    def _file_signature(cls, path: str) -> tuple[str, int | None, int | None, int | None]:
        """Return a lightweight on-disk version fingerprint for *path*.

        Cache entries must not survive an external overwrite of the same file
        path.  Size + nanosecond mtime/ctime make a replacement file receive a
        new key without hashing multi-gigabyte RAW files.  If stat fails, the
        path still identifies the request; normal file reads will report the
        actual error later.
        """
        canonical = cls._canonical_path(path)
        try:
            st = os.stat(canonical)
        except OSError:
            return canonical, None, None, None
        return canonical, st.st_size, st.st_mtime_ns, st.st_ctime_ns

    @classmethod
    def key(cls, options, frame_index: int) -> str:
        # offset 决定帧数据在文件中的起始位置（effective_offset = offset + frame*size），
        # 必须在键里：否则同一文件在 offset=0 与 offset=N 两种设定下会命中同一份缓存。
        # 文件签名（size/mtime_ns/ctime_ns）则防止“关闭 item 后外部覆盖同路径文件、
        # 重开仍命中旧像素”的陈旧缓存问题。
        path, size, mtime_ns, ctime_ns = cls._file_signature(options.file_path)
        return f"{path}\x00{size}\x00{mtime_ns}\x00{ctime_ns}" \
               f"\x00{options.format_name}\x00{options.width}x{options.height}" \
               f"\x00{options.alignment}\x00{options.endianness}\x00{options.offset}" \
               f"\x00{options.preview_mode}\x00{options.bayer_pattern}\x00{frame_index}"

    def remove_file(self, path: str) -> int:
        """Drop every cached frame for *path* and return the number removed.

        Closing an item is an explicit user boundary: reopening a path must
        reread disk rather than reuse its in-process decode result, even before
        an external writer's timestamp is observed.
        """
        prefix = f"{self._canonical_path(path)}\x00"
        removed = 0
        for key in list(self._entries):
            if key.startswith(prefix):
                result = self._entries.pop(key)
                self._total_bytes -= self._entry_bytes(result)
                removed += 1
        return removed

    @staticmethod
    def _entry_bytes(result: DecodeResult) -> int:
        arr = getattr(result, "display_array", None)
        return int(arr.nbytes) if arr is not None else 0

    def clear(self) -> None:
        self._entries.clear()
        self._total_bytes = 0

    def get(self, key: str) -> "DecodeResult | None":
        entry = self._entries.pop(key, None)
        if entry is None:
            return None
        # 命中：移到末尾（最近使用），总字节数不变。
        self._entries[key] = entry
        return entry

    def store(self, key: str, result: DecodeResult) -> bool:
        """存入缓存（LRU）。返回是否真正插入（False = 单帧超大直接拒绝）。"""
        if result is None:
            return False
        nbytes = self._entry_bytes(result)
        if nbytes > self._max_bytes:
            return False
        self.store_calls += 1
        # 重新插入同一 key 时先扣掉旧结果，避免 _total_bytes 累积虚高并过早
        # 淘汰 LRU（文件重复解码/外部覆盖后同签名请求均可能走到这里）。
        old = self._entries.pop(key, None)
        if old is not None:
            self._total_bytes -= self._entry_bytes(old)
        self._entries[key] = result
        self._total_bytes += nbytes
        # 双上限淘汰：条目数 / 总字节数任一超限就淘汰最久未用（首部）。
        while self._entries and (
            len(self._entries) > self._max_items or self._total_bytes > self._max_bytes
        ):
            _old_key, old = self._entries.popitem(last=False)
            self._total_bytes -= self._entry_bytes(old)
        return True


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
                qimg = qimage_from_rgb(rgb)
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
                        qimg = qimage_from_rgb(rgb)
                        result = DecodeResult(rgb, qimg, w, h, self._format_name)
                    except ValueError as exc:
                        logger.warning("Bayer demosaic failed (%s), falling back to grayscale", exc)
                        fallback = raw8
                        h, w = fallback.shape
                        qimg = qimage_from_grayscale(fallback)
                        result = DecodeResult(fallback, qimg, w, h, self._format_name)
                else:
                    h, w = raw8.shape
                    qimg = qimage_from_grayscale(raw8)
                    result = DecodeResult(raw8, qimg, w, h, self._format_name)

            # 注意：这里**不能**因 cancel 而 suppress finished/error——主窗口的
            # 线程清理（thread.quit() / deleteLater）就连接在这两个信号上，
            # 取消后不发信号会让旧 QThread/worker 永久泄漏（0.1.1-M-1 复查发现）。
            # 真正的"过期结果丢弃"已由主线程 generation check（_should_apply_decode）
            # 完成；cancel() 仅作可选中断标记，不影响信号发射。
            logger.debug("Worker decode finished successfully")
            self.finished.emit(self._generation, result)
        except Exception as exc:
            logger.exception("Worker decode failed: %s", self._describe_source())
            self.error.emit(self._generation, f"{self._describe_source()}: {exc}")
