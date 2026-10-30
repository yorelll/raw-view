"""Regression tests for the open/close performance work (S1-S6).

Everything here asserts **behaviour or call counts**, never wall-clock time:
CI machines vary too much for latency assertions, while the structural
properties below are exactly what regressed before (a full directory rescan per
item, a leaked page per closed tab, an RGB round-trip per standard image, a
rebuild of the conditional rows per panel refresh, N refreshes per batch).

Coverage map:

* S1 — ``_same_dir_items`` uses one ``os.scandir`` pass, caches per directory,
  revalidates on mtime change, and keeps the exact old filtering semantics.
* S2 — closing a tab destroys its page widget (no widget leak per close).
* S3 — standard images reach Qt as BGR without an RGB round-trip, and saving
  keeps red/blue in place.
* S4 — conditional-row rebuilds short-circuit on identical (visible, enabled)
  state, including the hidden-field ``enabled`` invariant (0.5.1 M-1).
* S5 — batch open/close refreshes the shared UI surfaces once, and still
  hands decoding to the v0.5.2 per-item queue.
* S6 — closing a background tab does not reload the panel or lose its own
  status text / pin state.

Settings always go through the isolated INI store installed by
``_settings_isolation``; the production HKCU store is never touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_cleanup import destroy_widget, flush_deferred_deletes  # noqa: E402
from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

import numpy as np  # noqa: E402

from raw_view.gui import app as app_module  # noqa: E402
from raw_view.gui.app import MainWindow, _suffix_of  # noqa: E402
from raw_view.gui.image_utils import qimage_from_bgr, qimage_from_rgb  # noqa: E402
from raw_view.models import AppSettings, DecodeOptions, ViewerItem  # noqa: E402

_COND_FIELDS = (
    "bit_depth_combo",
    "align_combo",
    "endian_combo",
    "raw_preview_combo",
    "bayer_pattern_combo",
)


def _write(path: str, size: int = 64) -> str:
    with open(path, "wb") as f:
        f.write(b"\x00" * size)
    return path


class _WindowFixture(unittest.TestCase):
    """Temp dir + isolated INI settings + a real, shown MainWindow."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rv-perf-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = AppSettings(
            QSettings(os.path.join(self.tmp, "settings.ini"), QSettings.IniFormat)
        )
        self.w = MainWindow()
        self.w.settings = self.settings
        self.w.resize(900, 600)
        self.w.show()
        _APP.processEvents()

    def tearDown(self):
        destroy_widget(self.w)

    def _file(self, name: str, size: int = 64) -> str:
        return _write(os.path.join(self.tmp, name), size)

    def _many(self, names) -> list[str]:
        return [self._file(n) for n in names]


# ── S1: scandir + cached directory listing ───────────────────────────────


class SuffixHelperTests(unittest.TestCase):
    """``_suffix_of`` equals ``Path(name).suffix.lower()`` for bare names."""

    def test_matches_pathlib_lowercase(self):
        from pathlib import Path

        cases = [
            "a.raw", "a.tar.gz", "a.", "a..b", "a", "", ".raw", ".hidden",
            "..", "...", "a..", "x.y.z", "frame0001",
        ]
        for name in cases:
            with self.subTest(name=name):
                self.assertEqual(_suffix_of(name), Path(name).suffix.lower())

    def test_uppercase_is_case_folded_unlike_path_suffix(self):
        """Documents the one intentional difference: lower-casing (0.5.1 L-4)."""
        from pathlib import Path

        self.assertEqual(_suffix_of("CAP.RAW"), ".raw")
        self.assertEqual(Path("CAP.RAW").suffix, ".RAW")
        self.assertEqual(_suffix_of("CAP.RAW"), Path("CAP.RAW").suffix.lower())

    def test_known_values(self):
        self.assertEqual(_suffix_of("cap.raw"), ".raw")
        self.assertEqual(_suffix_of("frame0001"), "")
        self.assertEqual(_suffix_of(".hidden"), "")
        self.assertEqual(_suffix_of("trailing."), "")


class SameDirItemsScanningTests(_WindowFixture):
    """S1: one scandir per directory change instead of one per open."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="rv-perf-dir-", dir=self.tmp)
        for i in range(12):
            _write(os.path.join(self.dir, f"c{i:02d}.raw"))
        self.w._open_item(os.path.join(self.dir, "c00.raw"), decode=False)
        _APP.processEvents()

    def test_uses_scandir_without_per_entry_isfile(self):
        self.w._invalidate_dir_cache()
        real_scandir = os.scandir
        calls = {"n": 0}

        def counting(path):
            calls["n"] += 1
            return real_scandir(path)

        with mock.patch.object(
            app_module.os, "scandir", side_effect=counting,
        ), mock.patch.object(
            app_module.os.path, "isfile",
            side_effect=AssertionError(
                "listing must use entry.is_file(), not a per-entry os.path.isfile"
            ),
        ):
            items = self.w._same_dir_items()
        self.assertEqual(calls["n"], 1)
        self.assertEqual(len(items), 11)

    def test_repeated_calls_hit_the_cache(self):
        self.w._invalidate_dir_cache()
        real_scandir = os.scandir
        calls = {"n": 0}

        def counting(path):
            calls["n"] += 1
            return real_scandir(path)

        with mock.patch.object(app_module.os, "scandir", side_effect=counting):
            for _ in range(5):
                self.assertEqual(len(self.w._same_dir_items()), 11)
        self.assertEqual(calls["n"], 1, "repeat calls must reuse the cache")

    def test_cache_hit_slides_the_ttl_forward(self):
        """M-3: a hit refreshes ``cached_at`` so sessions do not rescan each second."""
        self.w._same_dir_items()
        key = next(iter(self.w._dir_listing_cache()))
        first = self.w._dir_listing_cache()[key][1]
        # Pretend the entry is nearly expired, then hit it again.
        self.w._dir_listing_cache()[key] = (
            self.w._dir_listing_cache()[key][0],
            first - (self.w._DIR_CACHE_TTL * 0.9),
            self.w._dir_listing_cache()[key][2],
        )
        stale_at = self.w._dir_listing_cache()[key][1]
        self.w._same_dir_items()
        refreshed = self.w._dir_listing_cache()[key][1]
        self.assertGreater(refreshed, stale_at, "hit must renew cached_at")

    def test_content_sniff_is_cached(self):
        """No-suffix entries must not be re-read (4 KB sniff) on every open."""
        d = tempfile.mkdtemp(prefix="rv-perf-noext-", dir=self.tmp)
        for i in range(6):
            _write(os.path.join(d, f"cap{i:03d}"))
        self.w._open_item(os.path.join(d, "cap000"), decode=False)
        _APP.processEvents()
        self.w._invalidate_dir_cache()
        self.w._same_dir_items()
        with mock.patch.object(
            app_module, "_looks_binary_candidate",
            side_effect=AssertionError("sniff must be served from the cache"),
        ):
            for _ in range(4):
                self.assertEqual(len(self.w._same_dir_items()), 5)

    def test_externally_created_file_becomes_visible(self):
        self.assertEqual(len(self.w._same_dir_items()), 11)
        _write(os.path.join(self.dir, "brand_new.raw"))
        self.assertIn("brand_new.raw", self.w._same_dir_items())

    def test_drop_invalidates_cached_listing(self):
        self.w._same_dir_items()
        _write(os.path.join(self.dir, "dropped.raw"))
        self.w._invalidate_dir_cache()
        self.assertIn("dropped.raw", self.w._same_dir_items())

    def test_semantics_preserved(self):
        """Subdirs and plain text stay excluded; binary no-ext files included."""
        d = tempfile.mkdtemp(prefix="rv-perf-sem-", dir=self.tmp)
        _write(os.path.join(d, "a.raw"))
        _write(os.path.join(d, "output"))
        with open(os.path.join(d, "notes"), "w", encoding="utf-8") as f:
            f.write("plain text\n")
        os.makedirs(os.path.join(d, "subdir_noext"))
        self.w._open_item(os.path.join(d, "a.raw"), decode=False)
        _APP.processEvents()
        self.assertEqual(sorted(self.w._same_dir_items()), ["output"])

    def test_cache_is_per_window(self):
        other = MainWindow()
        other.settings = self.settings
        try:
            self.assertIsNot(
                self.w._dir_listing_cache(), other._dir_listing_cache(),
                "listing cache must be per-instance, never module-global",
            )
        finally:
            destroy_widget(other)


class NavRankTests(_WindowFixture):
    """prev/next enablement must stay exact after the bisect rewrite."""

    def test_boundaries_match_linear_rank(self):
        names = ["a.raw", "b.raw", "c.raw", "d.raw"]
        paths = self._many(names)
        item = ViewerItem()
        self.w._current_item = lambda: item
        for name, path in zip(names, paths):
            item.options.file_path = path
            self.w._refresh_file_nav_actions()
            rank = names.index(name)
            self.assertEqual(self.w.prev_file_action.isEnabled(), rank > 0, name)
            self.assertEqual(
                self.w.next_file_action.isEnabled(), rank < len(names) - 1, name,
            )


# ── S2: closing a tab destroys its page ──────────────────────────────────


class CloseDestroysPageTests(_WindowFixture):
    def _open_n(self, count: int):
        for i in range(count):
            self.w._open_item(self._file(f"c{i:02d}.raw"), decode=False)
        _APP.processEvents()

    def test_widget_count_returns_to_baseline_after_20_cycles(self):
        baseline = len(QApplication.allWidgets())
        for i in range(20):
            self.w._open_item(self._file(f"cycle{i:02d}.raw"), decode=False)
            _APP.processEvents()
            self.w.close_item(self.w.item_tabs.count() - 1)
            flush_deferred_deletes()
        self.assertLessEqual(
            len(QApplication.allWidgets()), baseline + 4,
            "closed tabs must not leak widgets",
        )

    def test_page_and_view_are_destroyed(self):
        """The page and its ImageView must be gone from the C++ side.

        ``ViewerItem`` itself stays reachable through the signal lambdas it
        created (a pure-Python cycle the GC cannot break across the PyQt
        wrapper), but that object owns no widgets once the page is destroyed,
        which is what stops the per-close widget/memory growth.
        """
        from PyQt5 import sip

        self.w._open_item(self._file("one.raw"), decode=False)
        _APP.processEvents()
        page = self.w.item_tabs.widget(0)
        view = self.w.items[0].view
        self.w.close_item(0)
        flush_deferred_deletes()
        self.assertTrue(sip.isdeleted(page), "page widget must be destroyed")
        self.assertTrue(sip.isdeleted(view), "ImageView must be destroyed")

    def test_close_all_and_close_right_do_not_leak(self):
        baseline = len(QApplication.allWidgets())
        for _ in range(3):
            self._open_n(4)
            self.w.close_all_items()
            flush_deferred_deletes()
        self.assertLessEqual(len(QApplication.allWidgets()), baseline + 4)


# ── S3: BGR888 standard-image path ───────────────────────────────────────


class BgrImageTests(unittest.TestCase):
    def test_bgr_and_rgb_paths_agree_pixelwise(self):
        rng = np.random.default_rng(0)
        bgr = rng.integers(0, 256, (40, 60, 3), dtype=np.uint8)
        a = qimage_from_bgr(bgr)
        b = qimage_from_rgb(bgr[:, :, ::-1])
        self.assertEqual(a.size(), b.size())
        for x, y in ((0, 0), (17, 31), (59, 39)):
            ca, cb = a.pixelColor(x, y), b.pixelColor(x, y)
            self.assertEqual(
                (ca.red(), ca.green(), ca.blue()),
                (cb.red(), cb.green(), cb.blue()),
                f"pixel ({x},{y})",
            )

    def test_red_object_stays_red(self):
        bgr = np.zeros((4, 4, 3), dtype=np.uint8)
        bgr[:, :, 2] = 255  # red channel in BGR order
        c = qimage_from_bgr(bgr).pixelColor(1, 1)
        self.assertEqual((c.red(), c.green(), c.blue()), (255, 0, 0))


class StandardImageDecodeTests(_WindowFixture):
    def _png(self, name: str, bgr: np.ndarray) -> str:
        import cv2

        path = os.path.join(self.tmp, name)
        self.assertTrue(cv2.imwrite(path, bgr))
        return path

    def test_decode_does_not_convert_to_rgb(self):
        bgr = np.zeros((8, 8, 3), dtype=np.uint8)
        bgr[:, :, 2] = 200
        path = self._png("red.png", bgr)
        with mock.patch.object(
            self.w, "_qimage_from_rgb",
            side_effect=AssertionError("standard image must not build an RGB copy"),
        ):
            self.w._open_item(path, decode=True)
        _APP.processEvents()
        item = self.w.items[0]
        self.assertTrue(item.display_is_bgr)
        self.assertEqual(item.current_display[0, 0, 2], 200)

    def test_worker_result_clears_bgr_flag(self):
        item = ViewerItem()
        item.view = _PixmapSink()
        item.display_is_bgr = True
        self.w._pending_decode_item = item
        self.w._decode_generation = 0
        self.w._current_item = lambda: item
        self.w._on_decode_finished(0, _DecodeResult(np.zeros((4, 4, 3), np.uint8)))
        self.assertFalse(item.display_is_bgr)

    def test_save_display_keeps_channel_order(self):
        import cv2

        bgr = np.zeros((6, 6, 3), dtype=np.uint8)
        bgr[:, :, 2] = 255
        src = self._png("save_src.png", bgr)
        self.w._open_item(src, decode=True)
        _APP.processEvents()
        out = os.path.join(self.tmp, "saved.png")
        with mock.patch.object(
            app_module.QFileDialog, "getSaveFileName",
            return_value=(out, "PNG (*.png)"),
        ):
            self.w.save_display()
        self.assertTrue(os.path.isfile(out))
        saved = cv2.imread(out, cv2.IMREAD_COLOR)
        b, g, r = (int(v) for v in saved[2, 2])
        self.assertGreater(r, 200, f"red lost (B={b} G={g} R={r})")
        self.assertLess(b, 60, f"red/blue swapped (B={b} G={g} R={r})")

    def test_save_display_grayscale_uses_gray_helper(self):
        item = ViewerItem()
        item.current_display = np.zeros((4, 4), np.uint8)
        item.display_is_bgr = False
        seen = {}

        class _Qimg:
            def setDotsPerMeterX(self, _v):
                pass

            def setDotsPerMeterY(self, _v):
                pass

            def save(self, path, fmt):
                seen["path"] = path
                return True

        self.w._current_item = lambda: item
        with mock.patch.object(
            self.w, "_qimage_from_gray", return_value=_Qimg(),
        ) as gray, mock.patch.object(
            self.w, "_qimage_from_bgr",
            side_effect=AssertionError("grayscale must not use the BGR helper"),
        ), mock.patch.object(
            app_module.QFileDialog, "getSaveFileName",
            return_value=(os.path.join(self.tmp, "g.png"), "PNG (*.png)"),
        ):
            self.w.save_display()
        self.assertEqual(gray.call_count, 1)
        self.assertTrue(seen)

    def test_standard_image_size_still_synced_to_panel_on_apply(self):
        """v0.5.2 `_sync_standard_image_size_to_panel` must keep working.

        The sync runs after a *successful explicit Apply* (by design: a silent
        open must not rewrite what the user typed), so this drives the Apply
        path rather than a plain open.
        """
        import cv2

        path = os.path.join(self.tmp, "size.png")
        self.assertTrue(cv2.imwrite(path, np.zeros((5, 7, 3), np.uint8)))
        self.w._open_item(path, decode=False)
        _APP.processEvents()
        self.w._on_apply_clicked()
        _APP.processEvents()
        self.assertEqual(self.w.panel.width_spin.value(), 7)
        self.assertEqual(self.w.panel.height_spin.value(), 5)


class _PixmapSink:
    def set_pixmap(self, _pixmap):
        pass

    def fit_image(self):
        pass


class _DecodeResult:
    def __init__(self, array):
        self.display_array = array
        self.qimage = qimage_from_rgb(array)
        self.width = array.shape[1]
        self.height = array.shape[0]
        self.format_name = "RAW12"


# ── S4: idempotent conditional-row rebuild ───────────────────────────────


class CondRowShortCircuitTests(_WindowFixture):
    def setUp(self):
        super().setUp()
        self.panel = self.w.panel
        self.panel.set_values(
            image_type="RAW", format_name="RAW12", width=640, height=480,
            alignment="msb", endianness="little", offset=0,
            preview_mode="Bayer Color", bayer_pattern="RGGB",
        )
        _APP.processEvents()

    def test_identical_state_does_not_rebuild(self):
        self.panel._sync_type_enabled()
        with mock.patch.object(
            self.panel._main_form, "takeRow",
            side_effect=AssertionError("identical state must not rebuild rows"),
        ):
            for _ in range(4):
                self.panel._sync_type_enabled()

    def test_state_change_still_rebuilds(self):
        self.panel._sync_type_enabled()
        self.panel.set_type("YUV")
        self.panel.set_format("YOnly")
        _APP.processEvents()
        self.assertTrue(self.panel.bit_depth_combo.isVisibleTo(self.panel))
        self.assertFalse(self.panel.raw_preview_combo.isVisibleTo(self.panel))

    def test_hidden_fields_are_disabled_after_switching_type(self):
        """M-1: hidden conditional fields must never stay enabled."""
        self.panel.set_type("YUV")
        self.panel.set_format("NV12")
        _APP.processEvents()
        leaked = [
            name for name in _COND_FIELDS
            if not getattr(self.panel, name).isVisibleTo(self.panel)
            and getattr(self.panel, name).isEnabled()
        ]
        self.assertEqual(leaked, [], "hidden conditional fields left enabled")

    def test_hidden_enabled_key_survives_panel_disable_cycle(self):
        """M-1: verify the key includes enabled state, not only visibility.

        ``set_enabled(True)`` blindly enables every control, including the
        hidden conditional ones; the following sync must put them back to the
        visibility-derived state.  The panel starts disabled (no item open), so
        the baseline is taken with it explicitly enabled.
        """
        self.panel.set_enabled(True)
        self.panel._sync_type_enabled()
        before = [(n, getattr(self.panel, n).isEnabled()) for n in _COND_FIELDS]
        self.assertTrue(
            all(
                getattr(self.panel, n).isEnabled()
                == (n in ("align_combo", "endian_combo",
                          "raw_preview_combo", "bayer_pattern_combo"))
                for n in _COND_FIELDS
            ),
            f"unexpected enabled baseline: {before}",
        )
        self.panel.set_enabled(False)
        disabled = [(n, getattr(self.panel, n).isEnabled()) for n in _COND_FIELDS]
        self.panel.set_enabled(True)
        after = [(n, getattr(self.panel, n).isEnabled()) for n in _COND_FIELDS]
        self.assertTrue(all(not enabled for _, enabled in disabled))
        self.assertEqual(before, after)

    def test_hidden_rows_leave_the_form_while_panel_is_disabled(self):
        """Guard the ``in_form`` half of the S4 short-circuit condition.

        A hidden row must be *both* removed from the QFormLayout and disabled.
        Checking only ``isEnabled()`` was enough to keep the M-1 symptom away,
        but a row left in the layout still reserves its height -- the empty gap
        0.4.1 removed.  Reverting that half of the condition is invisible unless
        this exact state is asserted, so it needs its own regression test.
        """
        form = self.panel._main_form
        hidden_specs = [
            name for name in ("align_combo", "endian_combo",
                              "raw_preview_combo", "bayer_pattern_combo")
        ]
        # RAW currently owns these four rows.
        for name in hidden_specs:
            self.assertGreaterEqual(
                form.getWidgetPosition(getattr(self.panel, name))[0], 0,
                f"{name} should own a row while image_type is RAW",
            )

        # Disable the panel (no item open) and switch to a type that hides all
        # four rows while the panel is disabled -- the edge path where the gap
        # used to reappear.
        self.panel.set_enabled(False)
        self.panel.set_values(
            image_type="YUV", format_name="NV12", width=640, height=480,
        )
        _APP.processEvents()

        for name in hidden_specs:
            field = getattr(self.panel, name)
            self.assertLess(
                form.getWidgetPosition(field)[0], 0,
                f"{name} must be removed from the form, not merely disabled",
            )
            self.assertFalse(field.isEnabled(), f"{name} must stay disabled")

        # The row that follows the conditional block must sit directly under the
        # row that precedes it: no blank slot left behind.
        width_row = form.getWidgetPosition(self.panel.width_spin)[0]
        format_row = form.getWidgetPosition(self.panel.format_combo)[0]
        self.assertEqual(
            width_row - format_row, 1,
            "hidden conditional rows must not leave a gap in the form",
        )

    def test_raw_yuv_raw_round_trip_keeps_row_order(self):
        specs = [name for name, _ in self.panel._COND_ROW_SPECS]

        def visible_order():
            form = self.panel._main_form
            present = [
                name for name in specs
                if form.getWidgetPosition(getattr(self.panel, name))[0] >= 0
            ]
            return present

        self.panel.set_type("RAW")
        self.panel.set_format("RAW12")
        _APP.processEvents()
        raw_order = visible_order()
        self.assertEqual(
            raw_order,
            ["align_combo", "endian_combo", "raw_preview_combo", "bayer_pattern_combo"],
        )
        self.panel.set_type("YUV")
        self.panel.set_format("YOnly")
        _APP.processEvents()
        self.assertEqual(
            visible_order(), ["bit_depth_combo", "align_combo", "endian_combo"],
        )
        self.panel.set_type("RAW")
        self.panel.set_format("RAW12")
        _APP.processEvents()
        self.assertEqual(visible_order(), raw_order, "row order must round-trip")

    def test_tab_switch_applies_each_item_type(self):
        self.w._open_item(self._file("a.raw"), decode=False)
        self.w._open_item(self._file("b.yuv"), decode=False)
        _APP.processEvents()
        self.assertEqual(self.w.panel.type_combo.currentText(), "YUV")
        self.assertFalse(self.w.panel.align_combo.isVisibleTo(self.w.panel))
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        self.assertEqual(self.w.panel.type_combo.currentText(), "RAW")
        self.assertTrue(self.w.panel.align_combo.isVisibleTo(self.w.panel))


# ── S5: batch open/close coalescing ──────────────────────────────────────


class BatchUiRefreshTests(_WindowFixture):
    def test_batch_open_refreshes_shared_surfaces_once(self):
        paths = self._many([f"c{i:02d}.raw" for i in range(8)])
        with mock.patch.object(
            self.w, "_refresh_file_nav_actions",
            wraps=self.w._refresh_file_nav_actions,
        ) as nav, mock.patch.object(
            self.w, "_refresh_recent_files_menu",
            wraps=self.w._refresh_recent_files_menu,
        ) as recent:
            self.w._open_paths(paths)
        _APP.processEvents()
        self.assertEqual(len(self.w.items), 8)
        self.assertEqual(nav.call_count, 1, "one nav refresh per batch")
        self.assertEqual(recent.call_count, 1, "one recent-menu refresh per batch")

    def test_batch_open_queue_still_decodes_every_item(self):
        """S5 must not interfere with the v0.5.2 per-item decode queue.

        The queue advances from the real worker's success/error signals, so this
        waits on QTest.qWait (a genuine event-loop iteration) rather than
        processEvents alone, which never lets the queued thread signals run.
        """
        from PyQt5.QtTest import QTest

        paths = self._many([f"q{i}.raw" for i in range(4)])
        self.w._open_paths(paths)
        deadline = 300
        while deadline and (
            self.w.__dict__.get("_open_decode_pending")
            or self.w.__dict__.get("_open_decode_in_flight") is not None
        ):
            QTest.qWait(10)
            deadline -= 1
        self.assertEqual(self.w._open_decode_total, 0, "queue must drain")
        self.assertEqual(len(self.w.items), 4)
        self.assertEqual(
            [it.status_text is not None for it in self.w.items],
            [True] * 4,
            "every queued tab must have been decoded or marked failed",
        )

    def test_batch_open_nav_matches_sequential(self):
        paths = self._many([f"c{i}.raw" for i in range(6)])
        self.w._open_paths(paths)
        _APP.processEvents()
        batched = (self.w.prev_file_action.isEnabled(),
                   self.w.next_file_action.isEnabled())

        self.w.close_all_items()
        flush_deferred_deletes()
        for p in paths:
            self.w._open_item(p, decode=False)
            _APP.processEvents()
        sequential = (self.w.prev_file_action.isEnabled(),
                      self.w.next_file_action.isEnabled())
        self.assertEqual(batched, sequential)

    def test_batch_close_refreshes_once(self):
        self.w._open_paths(self._many([f"c{i}.raw" for i in range(6)]))
        _APP.processEvents()
        with mock.patch.object(
            self.w, "_refresh_file_nav_actions",
            wraps=self.w._refresh_file_nav_actions,
        ) as nav, mock.patch.object(
            self.w, "_update_center_stack",
            wraps=self.w._update_center_stack,
        ) as stack:
            self.w.close_all_items()
        flush_deferred_deletes()
        self.assertEqual(self.w.items, [])
        self.assertEqual(self.w.item_tabs.count(), 0)
        self.assertLessEqual(nav.call_count, 1)
        self.assertLessEqual(stack.call_count, 1)

    def test_close_all_leaves_no_item_state(self):
        self.w._open_paths(self._many([f"c{i}.raw" for i in range(4)]))
        _APP.processEvents()
        self.w.close_all_items()
        flush_deferred_deletes()
        self.assertEqual(self.w._active_item_index, -1)
        self.assertIsNone(self.w._active_item_ref)
        self.assertIn("No item", self.w.state_status.text())

    def test_close_right_leaves_consistent_state(self):
        self.w._open_paths(self._many([f"c{i}.raw" for i in range(5)]))
        _APP.processEvents()
        self.w.close_items_to_the_right(1)
        flush_deferred_deletes()
        self.assertEqual(len(self.w.items), 2)
        self.assertIs(
            self.w._current_item(),
            self.w.items[self.w.item_tabs.currentIndex()],
        )

    def test_batch_is_exception_safe(self):
        with mock.patch.object(
            self.w, "_open_item", side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self.w._open_paths([self._file("x.raw"), self._file("y.raw")])
        self.assertFalse(self.w._batch_active())
        self.assertTrue(self.w.updatesEnabled())

    def test_nested_batch_refreshes_once(self):
        paths = self._many([f"c{i}.raw" for i in range(3)])
        with mock.patch.object(
            self.w, "_refresh_file_nav_actions",
            wraps=self.w._refresh_file_nav_actions,
        ) as nav:
            owner = self.w._batch_begin()
            try:
                self.w._batch_begin()
                for p in paths:
                    self.w._open_item(p, decode=False)
                self.w._batch_end(False)
            finally:
                self.w._batch_end(owner)
        self.assertEqual(nav.call_count, 1)


# ── S6: background-tab close skips the panel reload ──────────────────────


class BackgroundCloseTests(_WindowFixture):
    def test_background_close_does_not_reload_panel(self):
        for i in range(3):
            self.w._open_item(self._file(f"c{i}.raw"), decode=False)
        _APP.processEvents()
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        self.w.panel.set_values(
            image_type="RAW", format_name="RAW16", width=1234, height=567,
            alignment="msb", endianness="little", offset=0,
            preview_mode="Bayer Color", bayer_pattern="RGGB",
        )
        _APP.processEvents()

        with mock.patch.object(
            self.w, "_load_item_to_panel",
            side_effect=AssertionError("background close must not reload the panel"),
        ):
            self.w.close_item(2)
        _APP.processEvents()

        self.assertEqual(len(self.w.items), 2)
        self.assertEqual(self.w.item_tabs.currentIndex(), 0)
        self.assertEqual(self.w.panel.width_spin.value(), 1234)
        self.assertEqual(self.w.panel.format_combo.currentText(), "RAW16")
        self.assertIs(self.w._current_item(), self.w.items[0])
        self.assertIs(self.w._active_item_ref, self.w.items[0])

    def test_background_close_keeps_status_bar_text(self):
        for i in range(3):
            self.w._open_item(self._file(f"c{i}.raw"), decode=False)
        _APP.processEvents()
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        before = self.w.file_status.text()
        self.w.close_item(2)
        _APP.processEvents()
        self.assertEqual(self.w.file_status.text(), before)

    def test_background_close_keeps_pin_state_and_item_options(self):
        for i in range(3):
            self.w._open_item(self._file(f"c{i}.raw"), decode=False)
        _APP.processEvents()
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()

        pinned = DecodeOptions(
            image_type="RAW", format_name="RAW16", width=1111, height=222,
            alignment="msb", endianness="little", offset=0,
            preview_mode="Bayer Color", bayer_pattern="RGGB",
        )
        self.w._pinned_params = pinned
        self.w._refresh_pin_state()
        self.w.panel.set_values(
            image_type=pinned.image_type, format_name=pinned.format_name,
            width=pinned.width, height=pinned.height,
            alignment=pinned.alignment, endianness=pinned.endianness,
            offset=pinned.offset, preview_mode=pinned.preview_mode,
            bayer_pattern=pinned.bayer_pattern,
        )
        _APP.processEvents()
        before = [
            (it.options.width, it.options.height, it.options.format_name)
            for it in self.w.items[:2]
        ]
        pin_before = self.w.panel.pin_state()

        self.w.close_item(2)
        _APP.processEvents()

        self.assertIs(self.w._pinned_params, pinned)
        self.assertEqual(self.w.panel.pin_state(), pin_before)
        self.assertEqual(
            [
                (it.options.width, it.options.height, it.options.format_name)
                for it in self.w.items
            ],
            before,
            "pinned background close must not write item options",
        )

    def test_current_tab_close_still_reloads(self):
        for i in range(3):
            self.w._open_item(self._file(f"c{i}.raw"), decode=False)
        _APP.processEvents()
        self.w.item_tabs.setCurrentIndex(1)
        _APP.processEvents()
        with mock.patch.object(
            self.w, "_load_item_to_panel",
            wraps=self.w._load_item_to_panel,
        ) as load:
            self.w.close_item(1)
        _APP.processEvents()
        self.assertEqual(load.call_count, 1, "current-tab close must reload")
        self.assertIs(
            self.w._active_item_ref,
            self.w.items[self.w.item_tabs.currentIndex()],
        )


if __name__ == "__main__":
    unittest.main()
