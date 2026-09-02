"""Tests for core decode/data-model/main-window logic (group A fixes).

Covers, without spinning up the full GUI:

  * M-1  — ``DecodeOptions`` carries preview_mode / bayer_pattern and the
           panel <-> item sync helpers round-trip them.
  * H-2  — ``decode_current`` reads only the current frame's byte interval
           instead of slurping the whole file (and size-mismatch gating).
  * H-1  — generation-counter stale-result dropping in the async decode path.
  * M-2  — error messages include file/frame/offset context.
  * M-4  — frame-nav bar colours come from the theme palette (models.py),
           framenav.py no longer hard-codes dark colours.
  * M-10 — directory drag-drop scans are filtered to supported extensions and
           >N files triggers a "too many" flag.
  * L-2  — ``ViewerItem.rotation_angle`` dead field removed.

Requires a Qt platform plugin; we force the offscreen platform so the module
imports and widget construction work headless.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

# The offscreen platform lets us construct plain Qt widgets (FrameNavBar) and
# bare MainWindow instances without opening a window.
_APP = QApplication.instance() or QApplication([])

from raw_view.gui import app as app_module  # noqa: E402
from raw_view.gui.app import (  # noqa: E402
    SUPPORTED_EXTENSIONS,
    MainWindow,
    _is_supported_file,
    _scan_directory,
    handle_drop_paths,
)
from raw_view.gui.framenav import FrameNavBar  # noqa: E402
from raw_view.models import (  # noqa: E402
    BAYER_PATTERNS,
    THEME_PALETTES,
    DecodeOptions,
    ViewerItem,
    build_ui_stylesheet,
)


def _new_window() -> MainWindow:
    """Create a bare MainWindow Python object without running its build.

    ``MainWindow.__new__`` creates the Qt wrapper without calling ``__init__``
    (which would build menus/toolbars/panels). The methods under test
    (decode_current, _on_decode_*, _save/_load_panel_to_item) only touch plain
    Python attributes plus a side-effect-free stub panel, keeping the tests
    hermetic and fast.
    """
    w = MainWindow.__new__(MainWindow)
    w.state_status = SimpleNamespace(setText=lambda _t: None)
    return w


class _StubPanel:
    """Minimal stand-in for ControlPanel: get/set_values round-trip a dict."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = {
            "image_type": "RAW",
            "format_name": "RAW8",
            "width": 2,
            "height": 2,
            "alignment": "msb",
            "endianness": "little",
            "offset": 0,
            "preview_mode": "Bayer Color",
            "bayer_pattern": "RGGB",
        }
        if values:
            self.values.update(values)

    def get_values(self) -> dict:
        return dict(self.values)

    def set_values(self, **kwargs) -> None:
        self.values.update(kwargs)

    def reset_preset_selection(self) -> None:
        pass

    def set_zoom_percent(self, percent: int) -> None:
        pass


class _FakeUrl:
    """Mime-url stand-in exposing only ``toLocalFile()``."""

    def __init__(self, path: str) -> None:
        self._path = path

    def toLocalFile(self) -> str:
        return self._path


# ── M-1: DecodeOptions preview/bayer fields ──────────────────────────────


class DecodeOptionsPersistenceTests(unittest.TestCase):
    def test_defaults(self):
        opts = DecodeOptions()
        self.assertEqual(opts.preview_mode, "Bayer Color")
        self.assertEqual(opts.bayer_pattern, "RGGB")
        self.assertIn(opts.preview_mode, {"Bayer Color", "Grayscale"})
        self.assertIn(opts.bayer_pattern, BAYER_PATTERNS)

    def test_round_trip_via_dataclass(self):
        opts = DecodeOptions(
            file_path="/tmp/a.raw",
            image_type="RAW",
            format_name="RAW12",
            width=1920,
            height=1080,
            alignment="lsb",
            endianness="big",
            offset=99,
            preview_mode="Grayscale",
            bayer_pattern="BGGR",
        )
        restored = DecodeOptions(**asdict(opts))
        self.assertEqual(restored, opts)
        self.assertEqual(restored.preview_mode, "Grayscale")
        self.assertEqual(restored.bayer_pattern, "BGGR")

    def test_panel_item_sync_persists_preview_bayer(self):
        """_save_panel_to_item / _load_item_to_panel round-trip the new fields."""
        w = _new_window()
        w.panel = _StubPanel({"preview_mode": "Grayscale", "bayer_pattern": "GBRG"})
        w._loading_item = False
        w.zoom_status = SimpleNamespace(setText=lambda _t: None)

        item = ViewerItem()
        w._save_panel_to_item(item)
        self.assertEqual(item.options.preview_mode, "Grayscale")
        self.assertEqual(item.options.bayer_pattern, "GBRG")

        # Mutate the item away from the panel, then load the item back onto
        # the panel — the panel must reflect the item again.
        item.options.preview_mode = "Bayer Color"
        item.options.bayer_pattern = "RGGB"
        w._load_item_to_panel(item)
        self.assertEqual(w.panel.values["preview_mode"], "Bayer Color")
        self.assertEqual(w.panel.values["bayer_pattern"], "RGGB")


# ── H-2: on-demand (per-frame) file reads ────────────────────────────────


class OnDemandReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-on-demand-")
        self.path = os.path.join(self._tmp, "frames.raw")
        # 10 frames x 4 bytes; frame i contains the byte value i repeated.
        with open(self.path, "wb") as f:
            f.write(b"".join(bytes([i]) * 4 for i in range(10)))
        self.w = _new_window()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_remaining_bytes_helper(self):
        self.assertEqual(self.w._remaining_bytes(self.path, 0), 40)
        self.assertEqual(self.w._remaining_bytes(self.path, 8), 32)
        self.assertEqual(self.w._remaining_bytes(self.path, 100), 0)
        self.assertIsNone(self.w._remaining_bytes(os.path.join(self._tmp, "nope.raw"), 0))

    def test_read_frame_data_reads_only_requested_slice(self):
        self.assertEqual(self.w._read_frame_data(self.path, 0, 4), b"\x00\x00\x00\x00")
        self.assertEqual(self.w._read_frame_data(self.path, 8, 4), b"\x02\x02\x02\x02")
        self.assertEqual(self.w._read_frame_data(self.path, 36, 4), b"\x09\x09\x09\x09")
        # beyond EOF returns what's left, not an error
        self.assertEqual(self.w._read_frame_data(self.path, 38, 4), b"\x09\x09")

    def test_decode_current_reads_only_current_frame(self):
        """decode_current must hand the worker exactly one frame slice."""
        item = ViewerItem()
        item.options.file_path = self.path
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 0
        item.current_frame = 2  # third frame → byte offset 8

        captured: list = []
        w = _new_window()
        w.panel = _StubPanel()
        w._current_item = lambda: item
        w._start_async_decode = lambda *args: captured.append(args)

        w.decode_current()

        self.assertEqual(len(captured), 1)
        data, dec_item, opts, eff = captured[0]
        # 4 bytes for one frame, not the whole 40-byte file
        self.assertEqual(data, b"\x02\x02\x02\x02")
        self.assertEqual(len(data), 4)
        self.assertEqual(eff, 8)
        self.assertIs(dec_item, item)
        self.assertIs(opts, item.options)

    def test_decode_current_frame_offset_with_base_offset(self):
        item = ViewerItem()
        item.options.file_path = self.path
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 4  # skip one frame at the start
        item.current_frame = 1  # effective offset: 4 + 1*4 = 8

        captured: list = []
        # The panel carries the base offset — decode_current saves the panel
        # into the item before computing the effective offset.
        w = _new_window()
        w.panel = _StubPanel({"offset": 4})
        w._current_item = lambda: item
        w._start_async_decode = lambda *args: captured.append(args)

        w.decode_current()

        self.assertEqual(len(captured), 1)
        data, _item, _opts, eff = captured[0]
        self.assertEqual(eff, 8)
        self.assertEqual(data, b"\x02\x02\x02\x02")

    def test_apply_warn_mismatch_gates_decode(self):
        """Only Apply pops the mismatch dialog; declining aborts the decode."""
        truncated = os.path.join(self._tmp, "truncated.raw")
        with open(truncated, "wb") as f:
            f.write(b"\x00\x00\x00")  # 3 bytes < one 4-byte frame

        item = ViewerItem()
        item.options.file_path = truncated
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 0
        item.current_frame = 0

        captured: list = []
        w = _new_window()
        w.panel = _StubPanel()
        w._current_item = lambda: item
        w._start_async_decode = lambda *args: captured.append(args)

        # User declines → nothing decoded
        w._warn_size_mismatch = lambda *_: False
        w.decode_current(warn_mismatch=True)
        self.assertEqual(captured, [])

        # User accepts → decode proceeds with the 3 available bytes
        w._warn_size_mismatch = lambda *_: True
        w.decode_current(warn_mismatch=True)
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(captured[0][0]), 3)


# ── H-1: generation-counter stale-result dropping ────────────────────────


class GenerationGuardTests(unittest.TestCase):
    def test_should_apply_accepts_current_generation_and_item(self):
        w = _new_window()
        item_a = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertTrue(w._should_apply_decode(3, item_a))

    def test_should_apply_rejects_stale_and_future_generations(self):
        w = _new_window()
        item_a = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertFalse(w._should_apply_decode(2, item_a))  # stale
        self.assertFalse(w._should_apply_decode(4, item_a))  # future
        self.assertFalse(w._should_apply_decode(3, None))    # no item

    def test_should_apply_rejects_different_item(self):
        w = _new_window()
        item_a = ViewerItem()
        item_b = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertFalse(w._should_apply_decode(3, item_b))  # other tab

    def test_stale_finished_result_is_dropped(self):
        """A late result from an abandoned decode must not touch the item."""
        w = _new_window()
        item = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = item
        w._current_item = lambda: item

        applied: list = []
        w._on_decode_success = lambda *args: applied.append(args)
        fake = SimpleNamespace(
            display_array=None, qimage=None, width=12, height=34, format_name="RAW8"
        )

        # Latest generation + same item → applied
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)
        self.assertEqual(item.options.width, 12)
        self.assertEqual(item.options.height, 34)

        # A newer decode started (generation bumped); the old worker's late
        # result carrying gen=1 must be silently discarded.
        w._decode_generation = 2
        w._pending_decode_item = item
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1, "stale generation result applied")

    def test_stale_error_is_dropped(self):
        w = _new_window()
        item = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = item
        w._current_item = lambda: item

        with mock.patch.object(app_module, "QMessageBox") as mb:
            w._on_decode_error(1, "boom")
            mb.critical.assert_called_once()
            # now stale
            w._decode_generation = 2
            w._on_decode_error(1, "boom late")
            self.assertEqual(mb.critical.call_count, 1)

    def test_finished_for_different_item_is_dropped(self):
        w = _new_window()
        shown = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = shown
        w._current_item = lambda: shown

        applied: list = []
        w._on_decode_success = lambda *args: applied.append(args)
        fake = SimpleNamespace(
            display_array=None, qimage=None, width=1, height=1, format_name="RAW8"
        )
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)

        # Switch to a different tab while the worker is still running.
        other = ViewerItem()
        w._current_item = lambda: other
        # The result was for `shown`, which is no longer visible -- dropped.
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)


# ── M-2: error messages carry decode context ─────────────────────────────


class ErrorContextTests(unittest.TestCase):
    def test_describe_source_mentions_file_frame_offset(self):
        w = _new_window()
        worker = app_module.DecodeWorker()
        from raw_view.formats import ImageSpec

        worker._data = b"\x00" * 4
        # spec.offset 固定为 0（data 已是按 source offset 切出的帧）；
        # 真实文件内 offset 独立记在 _source_offset 供错误信息定位。
        worker._spec = ImageSpec(2, 2, offset=0)
        worker._format_name = "RAW8"
        worker._file_path = os.path.join("d", "sensor", "cap.bin")
        worker._frame_index = 2
        worker._source_offset = 8
        text = worker._describe_source()
        self.assertIn("cap.bin", text)
        self.assertIn("frame 2", text)
        self.assertIn("offset=8", text)

    def test_error_signal_carries_generation_and_context(self):
        w = _new_window()
        from raw_view.formats import ImageSpec

        worker = app_module.DecodeWorker()
        errors: list = []

        def _on_error(gen, msg):
            errors.append((gen, msg))

        worker.error.connect(_on_error)
        worker._data = b"\x00\x00"  # too short for a 4-byte RAW8 frame
        worker._spec = ImageSpec(2, 2, offset=0)
        worker._format_name = "RAW8"
        worker._generation = 7
        worker._file_path = "cap.bin"
        worker._frame_index = 0
        worker.run()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 7)
        self.assertIn("cap.bin", errors[0][1])


# ── M-4: frame-nav theming lives in the palette ──────────────────────────


class FramenavThemingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_palettes_define_nav_button_colors(self):
        for theme in ("light", "dark"):
            p = THEME_PALETTES[theme]
            for key in (
                "nav_button_bg", "nav_button_border", "nav_button_text",
                "nav_button_hover_bg", "nav_button_pressed_bg",
                "nav_button_disabled_bg", "nav_button_disabled_text",
            ):
                self.assertIn(key, p, f"{key} missing from {theme} palette")

    def test_stylesheets_embed_theme_specific_nav_colors(self):
        for theme in ("light", "dark"):
            sheet = build_ui_stylesheet(theme, 13)
            p = THEME_PALETTES[theme]
            self.assertIn("QWidget#frameNavBar QPushButton", sheet)
            self.assertIn(p["nav_button_bg"], sheet)
            self.assertIn(p["nav_button_text"], sheet)
            self.assertIn(":hover", sheet)
            self.assertIn(":pressed", sheet)
            self.assertIn(":disabled", sheet)
            # light text must not leak into the dark stylesheet and vice-versa
            if theme == "light":
                self.assertNotIn("#2A2D4A", sheet)
            else:
                self.assertIn("#2A2D4A", sheet)

    def test_framenav_no_longer_sets_hardcoded_stylesheet(self):
        nav = FrameNavBar()
        self.assertEqual(nav.styleSheet(), "")
        # layout/metrics are preserved
        self.assertEqual(nav.first_btn.width(), 36)
        self.assertEqual(nav.first_btn.height(), 32)


# ── M-10: directory drag-drop scanning ───────────────────────────────────


class DirectoryDropTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-drop-")
        self.root = os.path.join(self._tmp, "folder")
        os.makedirs(os.path.join(self.root, "sub"))
        for name in ("a.raw", "b.png", "notes.txt"):
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(b"x")
        with open(os.path.join(self.root, "sub", "c.yuv"), "wb") as f:
            f.write(b"x")
        # unsupported nested file must be skipped too
        with open(os.path.join(self.root, "sub", "cache.tmp"), "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_supported_extension_set(self):
        for ext in (".png", ".jpg", ".jpeg", ".bmp", ".yuv", ".nv12", ".raw", ".bin"):
            self.assertIn(ext, SUPPORTED_EXTENSIONS)
        self.assertNotIn(".txt", SUPPORTED_EXTENSIONS)

    def test_is_supported_file(self):
        self.assertTrue(_is_supported_file("x.RAW"))
        self.assertTrue(_is_supported_file("x.jpeg"))
        self.assertFalse(_is_supported_file("x.txt"))

    def test_scan_directory_filters_extensions(self):
        found = _scan_directory(self.root)
        names = {os.path.basename(p) for p in found}
        self.assertEqual(names, {"a.raw", "b.png", "c.yuv"})

    def test_handle_drop_paths_directory(self):
        files, too_many = handle_drop_paths([_FakeUrl(self.root)])
        names = {os.path.basename(p) for p in files}
        self.assertEqual(names, {"a.raw", "b.png", "c.yuv"})
        self.assertFalse(too_many)

    def test_handle_drop_paths_direct_unsupported_file_is_skipped(self):
        files, _ = handle_drop_paths([_FakeUrl(os.path.join(self.root, "notes.txt"))])
        self.assertEqual(files, [])

    def test_handle_drop_paths_too_many_triggers_flag(self):
        dense = os.path.join(self._tmp, "dense")
        os.makedirs(dense)
        for i in range(51):
            with open(os.path.join(dense, f"f{i}.raw"), "wb") as f:
                f.write(b"x")
        files, too_many = handle_drop_paths([_FakeUrl(dense)])
        self.assertEqual(len(files), 51)
        self.assertTrue(too_many)
        files, too_many = handle_drop_paths([_FakeUrl(dense)], max_files=100)
        self.assertFalse(too_many)


# ── L-2: dead field removed ──────────────────────────────────────────────


class ViewerItemFieldTests(unittest.TestCase):
    def test_rotation_angle_removed(self):
        item = ViewerItem()
        self.assertFalse(hasattr(item, "rotation_angle"))


# ── 多帧回归：data 已按 effective_offset 切片，spec.offset 必须为 0 ───────


class MultiFrameOffsetRegressionTests(unittest.TestCase):
    """Regression for the double-offset bug where frame 1+ reported
    "data too short: need 2x bytes" (H-2 seek-read optimisation sliced the
    frame at effective_offset, but the decode spec still used that offset,
    slicing an already-sliced buffer a second time).
    """

    def _worker_decode(self, data, width, height, offset, format_name="RAW12", frame_index=0):
        """Run a DecodeWorker synchronously (bypassing QThread) and return result/error."""
        from PyQt5.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        from raw_view.gui.worker import DecodeWorker
        from raw_view.formats import ImageSpec

        outcome = {}
        worker = DecodeWorker()

        def on_finish(_gen, res):
            outcome["ok"] = res

        def on_error(_gen, msg):
            outcome["err"] = msg

        worker.finished.connect(on_finish)
        worker.error.connect(on_error)
        # 修复后的调用契约：data 是已按 offset 读出的单帧，spec.offset 必须为 0
        worker.configure(
            data, ImageSpec(width, height, 0), format_name,
            alignment="msb", endianness="little", preview_mode="Grayscale",
            generation=1, file_path="cap.bin", frame_index=frame_index,
            source_offset=offset,
        )
        worker.run()
        return outcome

    def test_multi_frame_all_frames_decode(self):
        # 用户场景：256x36 RAW12，一帧 18432，5 帧 → 92160 字节
        import tempfile
        w, h, frames = 256, 36, 5
        frame_size = w * h * 2
        fd, path = tempfile.mkstemp(suffix=".raw")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                for fi in range(frames):
                    frame = (np.arange(w * h, dtype=np.uint16).reshape(h, w) * (fi + 1)) & 0x0FFF
                    frame.astype("<u2").tofile(f)
            for fi in range(frames):
                offset = fi * frame_size
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read(frame_size)
                outcome = self._worker_decode(data, w, h, offset, frame_index=fi)
                self.assertNotIn("err", outcome, f"frame {fi} failed: {outcome.get('err')}")
                self.assertEqual(outcome["ok"].width, w)
                self.assertEqual(outcome["ok"].height, h)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_worker_describe_source_shows_real_offset(self):
        # 错误消息应显示真实源 offset（即使 spec.offset=0）
        from raw_view.formats import ImageSpec
        from raw_view.gui.worker import DecodeWorker

        worker = DecodeWorker()
        worker.configure(b"", ImageSpec(4, 4, 0), "RAW12",
                         generation=1, file_path="/tmp/cap.bin",
                         frame_index=3, source_offset=6144)
        desc = worker._describe_source()
        self.assertIn("frame 3", desc)
        self.assertIn("offset=6144", desc)


# ── UI-6: 同目录文件组切换（上一/下一文件）─────────────────────────────


class SameDirNavTests(unittest.TestCase):
    """Regression for the P1-6-style prev/next-file nav: _same_dir_items 排除
    自身，_nav_file_by_dir 不能把「完整有序列表中的 rank」直接当下标（siblings
    少一个元素），否则 "c" 的下一文件会错误跳过、落到错误邻居。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.mkdtemp(prefix="rv-nav-")
        for fn in ("a.raw", "b.raw", "c.raw", "d.raw"):
            open(os.path.join(self._tmp, fn), "w").close()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _nav(self, path: str, delta: int):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = path
        w._current_item = lambda: item
        opened: list[str] = []
        w._open_item = lambda p, decode=True: opened.append(os.path.basename(p))
        w._nav_file_by_dir(delta)
        return opened

    def test_next_from_middle(self):
        base = os.path.join(self._tmp, "c.raw")
        self.assertEqual(self._nav(base, 1), ["d.raw"])
        self.assertEqual(self._nav(base, -1), ["b.raw"])

    def test_boundaries_do_not_wrap(self):
        head = os.path.join(self._tmp, "a.raw")
        tail = os.path.join(self._tmp, "d.raw")
        self.assertEqual(self._nav(head, -1), [])
        self.assertEqual(self._nav(tail, 1), [])
        self.assertEqual(self._nav(head, 1), ["b.raw"])

    def test_same_dir_items_excludes_self(self):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = os.path.join(self._tmp, "c.raw")
        w._current_item = lambda: item
        self.assertEqual(sorted(w._same_dir_items()), ["a.raw", "b.raw", "d.raw"])


if __name__ == "__main__":
    unittest.main()
