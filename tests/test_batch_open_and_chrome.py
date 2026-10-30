"""v0.5.2 regression tests: filter-button rendering, batch open, status text.

Covers
------
1. ``presetFilterButton`` renders its filter icon (qt-material's global
   ``QToolButton`` rule added 36px of horizontal chrome inside the 30px button,
   clipping the icon away entirely and leaving only the InstantPopup indicator),
   and its content centroid stays within ±1.5px of the button centre in both
   themes.
2. Opening several files decodes **every** one of them silently, each with its
   own parameters; one failure cannot block the rest.
3. Switching tabs always shows that tab's own status text, never a neighbour's
   "Restored remembered parameters (...)".
4. ``ViewerItem`` equality is identity, so ``item in items`` / ``items.index()``
   no longer raise "truth value of an array ... is ambiguous" after two items
   decoded numpy pixel arrays.
5. qt_material's generated icon tree is process-private, so concurrent pytest
   processes cannot delete each other's SVGs.

No ``time.sleep`` polling is used for decode completion: every wait pumps Qt
events against an observable state (``_pending_decode_item`` / queue flags).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

# unittest discovery does not load pytest's conftest.py.  Establish its owned
# test INI before any raw_view import; under pytest this reuses conftest's path.
from _settings_isolation import (
    ensure_test_settings_file,
    isolated_qt_material_resources,
)
from _qt_cleanup import destroy_widget  # noqa: E402

ensure_test_settings_file()
isolated_qt_material_resources()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtGui import QColor  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.models import AppSettings, DecodeOptions, ViewerItem  # noqa: E402

_CENTRE_TOLERANCE = 1.5
_INK_THRESHOLD = 40  # |ΔR| + |ΔG| + |ΔB| against the button background


def _pump(condition, timeout: float = 30.0) -> bool:
    """Pump Qt events until *condition()* holds (never a blind sleep loop)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _APP.processEvents()
        if condition():
            return True
    return condition()


def _ink_metrics(image) -> tuple[int, float, float]:
    """Return ``(ink_pixels, centroid_x, centroid_y)`` for a button grab."""
    background = image.pixelColor(0, 0)
    total = weighted_x = weighted_y = 0
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            delta = (
                abs(colour.red() - background.red())
                + abs(colour.green() - background.green())
                + abs(colour.blue() - background.blue())
            )
            if delta > _INK_THRESHOLD:
                total += 1
                weighted_x += x
                weighted_y += y
    if not total:
        return 0, 0.0, 0.0
    return total, weighted_x / total, weighted_y / total


def _avatar_rows(image) -> list[str]:
    """ASCII map of the grab, used in assertion messages."""
    background = image.pixelColor(0, 0)
    rows = []
    for y in range(image.height()):
        row = ""
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            delta = (
                abs(colour.red() - background.red())
                + abs(colour.green() - background.green())
                + abs(colour.blue() - background.blue())
            )
            row += "#" if delta > _INK_THRESHOLD else "."
        rows.append(row)
    return rows


class _IsolatedWindowTest(unittest.TestCase):
    """Temp dir + isolated INI + real MainWindow."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rv-batch5-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = AppSettings(
            QSettings(os.path.join(self.tmp, "settings.ini"), QSettings.IniFormat)
        )
        self.w = MainWindow()
        self.w.settings = self.settings
        self.w.show()
        _APP.processEvents()

    def tearDown(self):
        self.w.close()
        destroy_widget(self.w)

    def _raw(self, name: str, frames: int, width: int, height: int) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(b"\x20" * (frames * width * height))
        return path

    def _remember(self, path: str, width: int, height: int) -> None:
        self.settings.remember_file_params(
            path,
            DecodeOptions(
                image_type="RAW", format_name="RAW8", width=width, height=height,
            ),
        )

    def _wait_batch_open(self, timeout: float = 40.0) -> None:
        """Wait for the whole multi-file queue to drain (event driven)."""
        _pump(
            lambda: not self.w.__dict__.get("_open_decode_pending")
            and self.w.__dict__.get("_open_decode_in_flight") is None
            and self.w._pending_decode_item is None,
            timeout=timeout,
        )


class FilterButtonRenderingTests(_IsolatedWindowTest):
    """Issue 1: the filter icon must actually render, and stay centred."""

    def _set_theme(self, theme: str) -> None:
        self.settings._store.setValue("ui/theme", theme)
        self.w._apply_theme()
        _APP.processEvents()

    def test_filter_button_renders_icon_and_indicator(self):
        button = self.w.panel.preset_filter_btn
        # objectName must stay stable (test_ui_interactions asserts on it).
        self.assertEqual(button.objectName(), "presetFilterButton")
        self.assertEqual(button.size().width(), 30)
        self.assertEqual(button.size().height(), 30)

        for theme in ("light", "dark"):
            self._set_theme(theme)
            image = button.grab().toImage()
            rows = _avatar_rows(image)
            ink, centroid_x, centroid_y = _ink_metrics(image)
            cx, cy = (image.width() - 1) / 2, (image.height() - 1) / 2
            context = "\n".join(f"{i:2d} {row}" for i, row in enumerate(rows))

            self.assertGreater(ink, 60, f"{theme}: icon vanished\n{context}")
            # The funnel body occupies the upper half; the menu indicator sits
            # below it.  Requiring ink in the top half is what catches the old
            # regression (only the indicator survived).
            upper = sum(
                1
                for i, row in enumerate(rows)
                if i < image.height() // 2
                for char in row
                if char == "#"
            )
            self.assertGreater(upper, 20, f"{theme}: no icon above the indicator\n{context}")
            # Both icon and indicator are rendered when ink is this high: the
            # bare 14px triangle was only ~25 pixels.
            self.assertGreater(ink, 80, f"{theme}: indicator only?\n{context}")
            self.assertLessEqual(
                abs(centroid_x - cx), _CENTRE_TOLERANCE,
                f"{theme}: dx={centroid_x - cx:+.2f}\n{context}",
            )
            self.assertLessEqual(
                abs(centroid_y - cy), _CENTRE_TOLERANCE,
                f"{theme}: dy={centroid_y - cy:+.2f}\n{context}",
            )

    def test_pin_button_icon_still_renders_in_both_themes(self):
        button = self.w.panel.pin_btn
        for theme in ("light", "dark"):
            self._set_theme(theme)
            image = button.grab().toImage()
            ink, centroid_x, centroid_y = _ink_metrics(image)
            cx, cy = (image.width() - 1) / 2, (image.height() - 1) / 2
            rows = "\n".join(_avatar_rows(image))
            self.assertGreater(ink, 40, f"{theme}: pin icon vanished\n{rows}")
            self.assertLessEqual(abs(centroid_x - cx), _CENTRE_TOLERANCE, rows)
            self.assertLessEqual(abs(centroid_y - cy), _CENTRE_TOLERANCE, rows)


class BatchOpenTests(_IsolatedWindowTest):
    """Issue 2: every opened file is decoded, silently and independently."""

    def test_open_paths_decodes_every_file_with_its_own_parameters(self):
        paths = [self._raw(f"shot{i}.raw", 1, 8, 8) for i in range(4)]
        for path in paths:
            self._remember(path, 8, 8)

        self.w._open_paths(paths, source="explicit")
        self.assertEqual(len(self.w.items), 4)
        self._wait_batch_open()

        for item in self.w.items:
            name = os.path.basename(item.options.file_path)
            self.assertIsNotNone(
                item.current_display, f"{name} was never decoded",
            )
            self.assertFalse(item.decoding, f"{name} still flagged as decoding")
            self.assertEqual(
                (item.options.width, item.options.height), (8, 8),
                f"{name} did not use its own remembered parameters",
            )
            self.assertIn("Restored remembered parameters", item.status_text or "")

    def test_one_failure_does_not_block_the_remaining_files(self):
        good_a = self._raw("good-a.raw", 1, 8, 8)
        broken = os.path.join(self.tmp, "broken.raw")
        with open(broken, "wb") as handle:
            handle.write(b"\x00\x01")  # far too short for any frame
        good_b = self._raw("good-b.raw", 1, 8, 8)
        for path in (good_a, good_b):
            self._remember(path, 8, 8)

        with mock.patch("raw_view.gui.app.QMessageBox.critical") as critical:
            self.w._open_paths([good_a, broken, good_b], source="explicit")
            self._wait_batch_open()

        self.assertFalse(critical.called, "batch failures must stay silent")
        by_name = {
            os.path.basename(item.options.file_path): item for item in self.w.items
        }
        self.assertIsNotNone(by_name["good-a.raw"].current_display)
        self.assertIsNotNone(by_name["good-b.raw"].current_display)
        self.assertIsNone(by_name["broken.raw"].current_display)
        self.assertEqual(by_name["broken.raw"].status_text, "Decode failed")
        self.assertFalse(by_name["broken.raw"].decoding)

    def _assert_queue_fully_drained(self) -> None:
        self.assertIsNone(
            self.w.__dict__.get("_open_decode_in_flight"),
            "a preflight failure left the batch thinking a decode is in flight",
        )
        self.assertEqual(self.w.__dict__.get("_open_decode_pending"), [])

    def test_preflight_failure_does_not_stall_the_open_queue(self):
        """All four preflight/read early returns must advance the open queue.

        They never reach the async error slot, so without an explicit advance
        the failing tab kept ``decoding`` forever and every file behind it was
        never decoded (v0.5.2 G-review P1).
        """

        def run_case(label: str, bad_path: str, bad_options: dict) -> None:
            good1 = self._raw(f"{label}-good1.raw", 1, 8, 8)
            good2 = self._raw(f"{label}-good2.raw", 1, 8, 8)
            self._remember(good1, 8, 8)
            self._remember(good2, 8, 8)

            with mock.patch(
                "raw_view.gui.app.QMessageBox.critical",
            ) as critical, mock.patch("raw_view.gui.app.QMessageBox.warning"):
                self.w._open_paths([good1, bad_path, good2], source="explicit")
                self._wait_batch_open()

            by_name = {
                item.options.file_path: item for item in self.w.items
            }
            self.assertIsNotNone(
                by_name[good1].current_display, f"{label}: first file not decoded",
            )
            self.assertIsNotNone(
                by_name[good2].current_display,
                f"{label}: queue stalled, the file after the bad one never decoded",
            )
            bad = by_name[bad_path]
            self.assertFalse(bad.decoding, f"{label}: failing tab stuck on Decoding...")
            self.assertIsNotNone(bad.status_text, f"{label}: failing tab has no status")
            self._assert_queue_fully_drained()
            self.assertFalse(critical.called, f"{label}: batch failures must be silent")

            # Each case starts from a clean window for the next path.
            self.w.close_all_items()
            _APP.processEvents()

        # 1. Unsupported format for the declared image type.
        bad_format = self._raw("preflight-format.raw", 1, 8, 8)
        self.settings.remember_file_params(
            bad_format,
            DecodeOptions(
                image_type="RAW", format_name="YUYV", width=8, height=8,
            ),
        )
        run_case("format", bad_format, {})

        # 2. Unreadable file size (the path vanishes between open and decode).
        vanished = self._raw("preflight-vanish.raw", 1, 8, 8)
        self._remember(vanished, 8, 8)
        real_getsize = os.path.getsize

        def flaky_getsize(path):
            if path == vanished:
                raise OSError("gone")
            return real_getsize(path)

        with mock.patch("raw_view.gui.app.os.path.getsize", side_effect=flaky_getsize):
            run_case("size", vanished, {})

        # 3. Frame too large for the 512MB guard.
        huge = self._raw("preflight-huge.raw", 1, 8, 8)
        self.settings.remember_file_params(
            huge,
            DecodeOptions(
                image_type="RAW", format_name="RAW32",
                width=65535, height=65535,
            ),
        )
        run_case("huge", huge, {})

        # 4. Frame bytes cannot be read (the read returns nothing for this file).
        unreadable = self._raw("preflight-read.raw", 1, 8, 8)
        self._remember(unreadable, 8, 8)
        real_read = self.w._read_frame_data

        def flaky_read(path, *args, **kwargs):
            if path == unreadable:
                return b""
            return real_read(path, *args, **kwargs)

        with mock.patch.object(self.w, "_read_frame_data", side_effect=flaky_read):
            run_case("read", unreadable, {})

    def test_closing_a_queued_item_does_not_stall_or_break_the_batch(self):
        paths = [self._raw(f"q{i}.raw", 1, 8, 8) for i in range(4)]
        for path in paths:
            self._remember(path, 8, 8)

        self.w._open_paths(paths, source="explicit")
        # Close the last queued tab immediately, while the batch is running.
        closing = self.w.items[-1]
        self.w.close_item(len(self.w.items) - 1)
        self._wait_batch_open()

        self.assertNotIn(closing, self.w.items)
        decoded = [item for item in self.w.items if item.current_display is not None]
        self.assertEqual(len(decoded), 3, "every surviving file must be decoded")

    def test_batch_decode_does_not_steal_the_visible_tab(self):
        paths = [self._raw(f"t{i}.raw", 1, 8, 8) for i in range(3)]
        for path in paths:
            self._remember(path, 8, 8)

        self.w._open_paths(paths, source="explicit")
        first_path = self.w.items[0].options.file_path
        self._wait_batch_open()

        # The first opened file stays selected; background decodes never switch.
        self.assertEqual(self.w._current_item().options.file_path, first_path)
        self.assertEqual(self.w.panel.width_spin.value(), 8)
        # The batch bookkeeping must be fully released, otherwise a later
        # user decode would be routed to a stale "in flight" item.
        self.assertIsNone(self.w.__dict__.get("_open_decode_in_flight"))
        self.assertEqual(self.w.__dict__.get("_open_decode_pending"), [])


class StatusTextTests(_IsolatedWindowTest):
    """Issue 3: switching tabs always shows that tab's own status."""

    def test_switching_to_a_queued_tab_never_shows_the_previous_files_text(self):
        decoded = self._raw("decoded.raw", 1, 8, 8)
        pending = self._raw("pending.raw", 1, 8, 8)
        self._remember(decoded, 8, 8)
        self._remember(pending, 8, 8)

        # Open the first file normally so it is decoded and "restored ...".
        self.w._open_item(decoded, decode=True)
        _pump(lambda: self.w._pending_decode_item is None)
        self.assertIn("Restored remembered parameters", self.w.state_status.text())

        # Opening a second item via the plain (non-decode) path leaves it with
        # no status of its own — exactly the queued-tab state from the report.
        self.w._open_item(pending, decode=False)
        _APP.processEvents()

        text = self.w.state_status.text()
        self.assertNotIn(
            "Restored remembered parameters", text,
            "a never-decoded tab must not show another file's restore text",
        )
        self.assertIn("Not decoded yet", text)
        self.assertNotIn(os.path.basename(decoded), text)

        # Coming back shows the first file's own restore text again.
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        self.assertIn("Restored remembered parameters", self.w.state_status.text())
        self.assertIn(os.path.basename(decoded), self.w.state_status.text())

    def test_failed_tab_shows_its_own_failure_after_switching(self):
        good = self._raw("ok.raw", 1, 8, 8)
        broken = os.path.join(self.tmp, "nope.raw")
        with open(broken, "wb") as handle:
            handle.write(b"\x00")
        self._remember(good, 8, 8)
        self._remember(broken, 8, 8)

        with mock.patch("raw_view.gui.app.QMessageBox.critical"):
            self.w._open_paths([good, broken], source="explicit")
            self._wait_batch_open()

        failed = next(
            item for item in self.w.items
            if item.options.file_path == broken
        )
        index = self.w.items.index(failed)
        self.w.item_tabs.setCurrentIndex(index)
        _APP.processEvents()
        self.assertIn("decoding failed", self.w.state_status.text().lower())
        self.assertIn("decoding failed", (failed.status_text or "").lower())

        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        self.assertIn("Restored remembered parameters", self.w.state_status.text())


class ViewerItemIdentityTests(unittest.TestCase):
    """Issue 4: dataclass equality must be identity, never numpy comparison."""

    def test_membership_and_index_use_identity_with_numpy_displays(self):
        import numpy as np

        first = ViewerItem()
        second = ViewerItem()
        first.options.file_path = "/tmp/same.raw"
        second.options.file_path = "/tmp/same.raw"
        first.current_display = np.zeros((4, 4), dtype=np.uint8)
        second.current_display = np.ones((4, 4), dtype=np.uint8)
        items = [first, second]

        # The default dataclass __eq__ raised
        # "truth value of an array with more than one element is ambiguous".
        self.assertIn(first, items)
        self.assertIn(second, items)
        self.assertEqual(items.index(first), 0)
        self.assertEqual(items.index(second), 1)
        self.assertNotEqual(first, second)
        self.assertEqual(first, first)

    def test_apply_to_all_counts_a_closed_queue_item_as_skipped(self):
        tmp = tempfile.mkdtemp(prefix="rv-skip-")
        self.addCleanup(shutil.rmtree, tmp, True)
        settings = AppSettings(
            QSettings(os.path.join(tmp, "s.ini"), QSettings.IniFormat)
        )
        window = MainWindow()
        window.settings = settings
        window._apply_all_confirmed_forever = True
        window.show()
        _APP.processEvents()
        try:
            paths = []
            for i in range(3):
                path = os.path.join(tmp, f"s{i}.raw")
                with open(path, "wb") as handle:
                    handle.write(b"\x20" * (4 * 4))
                paths.append(path)
            for path in paths:
                window._open_item(path, decode=False)
                # Decoded pixels are what made the old __eq__ explode.
                window.items[-1].current_display = __import__("numpy").zeros(
                    (4, 4), dtype="uint8"
                )
            window.panel.set_values(
                image_type="RAW", format_name="RAW8", width=4, height=4,
            )
            with mock.patch("raw_view.gui.app.QMessageBox.critical"):
                window._on_apply_to_all_requested()
                window.close_item(2)
                _pump(
                    lambda: not window._apply_all_queue
                    and window.__dict__.get("_apply_all_current") is None,
                    timeout=30.0,
                )
            self.assertEqual(window._apply_all_total, 3)
            self.assertEqual(window._apply_all_skipped, 1)
            self.assertEqual(window._apply_all_ok, 2)
            self.assertIn("1 skipped", window.state_status.text())
        finally:
            window.close()
            destroy_widget(window)


class QtMaterialIsolationTests(unittest.TestCase):
    """Issue 5: the generated icon tree must live in a private temp dir."""

    def test_resources_are_redirected_to_a_private_directory(self):
        directory = isolated_qt_material_resources()
        self.assertTrue(os.path.isdir(directory))
        self.assertIn("raw-view-qt-material-", directory)
        home_tree = os.path.join(os.path.expanduser("~"), ".qt_material")
        self.assertNotEqual(
            os.path.abspath(directory), os.path.abspath(home_tree),
            "tests must not write into the user's shared ~/.qt_material",
        )

        import qt_material
        import qt_material.resources.generate as generate

        self.assertEqual(generate.RESOURCES_PATH, directory)
        self.assertEqual(qt_material.RESOURCES_PATH, directory)

    def test_apply_stylesheet_writes_icons_into_the_private_directory(self):
        directory = isolated_qt_material_resources()
        window = MainWindow()
        try:
            window.settings._store.setValue("ui/theme", "light")
            window._apply_theme()
            _APP.processEvents()
            primary = os.path.join(directory, "theme", "primary")
            self.assertTrue(os.path.isdir(primary), f"missing {primary}")
            names = os.listdir(primary)
            self.assertTrue(
                any(name.endswith(".svg") for name in names),
                f"no generated SVGs in {primary}: {names[:5]}",
            )
        finally:
            window.close()
            destroy_widget(window)


if __name__ == "__main__":
    unittest.main()
