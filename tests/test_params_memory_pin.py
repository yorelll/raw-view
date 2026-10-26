"""v0.5.0 regression tests: per-file parameter memory and pinned parameters.

All tests use the real ``MainWindow``/``ControlPanel`` widgets with an isolated
INI-backed ``AppSettings`` (never the production HKCU store), and drive the
actual signals/slots instead of mirroring production logic in the test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

# unittest discovery does not load pytest's conftest.py.
from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSettings, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLineEdit  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.models import (  # noqa: E402
    FILE_PARAM_HISTORY_LIMIT,
    AppSettings,
    DecodeOptions,
    SensorPreset,
)


def _write(path: str, size: int) -> str:
    with open(path, "wb") as f:
        f.write(b"\x10" * size)
    return path


def _pump(condition, timeout: float = 15.0) -> bool:
    """Process Qt events until *condition()* or *timeout* elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return condition()


class _IsolatedWindowTest(unittest.TestCase):
    """Shared fixture: temp dir + isolated INI store + real MainWindow."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rv-mem-pin-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = AppSettings(
            QSettings(os.path.join(self.tmp, "settings.ini"), QSettings.IniFormat)
        )
        self.w = MainWindow()
        self.w.settings = self.settings
        self.w.show()
        _APP.processEvents()
        self.w._on_apply_clicked = self.w._on_apply_clicked.__get__(self.w, MainWindow)

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        _APP.processEvents()

    def _path(self, name: str, size: int) -> str:
        return _write(os.path.join(self.tmp, name), size)

    def _decode_and_wait(self, *, show_error: bool = False):
        """Non-Apply decode (frame/auto path): never writes the memory."""
        self.w.decode_current(show_error=show_error)
        _pump(lambda: self.w._pending_decode_item is None)

    def _apply_and_wait(self, *, mismatch_ok: bool = True):
        """Explicit user Apply; the only single-item path that may remember."""
        with mock.patch.object(
            self.w, "_warn_size_mismatch", return_value=mismatch_ok,
        ), mock.patch("raw_view.gui.app.QMessageBox.critical"):
            self.w._on_apply_clicked()
            _pump(lambda: self.w._pending_decode_item is None)


class FileParamHistorySettingsTests(unittest.TestCase):
    """Storage schema, normalization, LRU cap and corruption tolerance."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rv-mem-store-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = AppSettings(
            QSettings(os.path.join(self.tmp, "s.ini"), QSettings.IniFormat)
        )

    @staticmethod
    def _opts(width=1280, height=720):
        return DecodeOptions(
            image_type="RAW", format_name="RAW10 Packed", width=width, height=height,
            alignment="lsb", endianness="big", offset=16,
            preview_mode="Grayscale", bayer_pattern="BGGR",
        )

    def test_write_read_overwrite_and_full_parameter_round_trip(self):
        self.assertTrue(self.s.remember_file_params(r"C:\x\a\Capture.raw", self._opts()))
        got = self.s.get_file_params("capture.raw")
        self.assertEqual(got.width, 1280)
        self.assertEqual(got.format_name, "RAW10 Packed")
        self.assertEqual(got.alignment, "lsb")
        self.assertEqual(got.endianness, "big")
        self.assertEqual(got.offset, 16)
        self.assertEqual(got.preview_mode, "Grayscale")
        self.assertEqual(got.bayer_pattern, "BGGR")
        self.assertEqual(got.file_path, "", "path is not persisted")
        # Overwrite refreshes the stored values.
        self.s.remember_file_params("capture.raw", self._opts(640, 480))
        self.assertEqual(self.s.get_file_params("CAPTURE.RAW").width, 640)
        self.assertIsNone(self.s.get_file_params("missing.raw"))

    def test_basename_and_windows_case_normalization(self):
        self.assertEqual(
            self.s.normalize_file_key(r"D:\data\Shot.RAW"),
            self.s.normalize_file_key("shot.raw"),
        )
        self.assertEqual(self.s.normalize_file_key("/data/sub/shot.raw"),
                         self.s.normalize_file_key("SHOT.RAW"))
        self.assertEqual(self.s.normalize_file_key(""), "")
        self.s.remember_file_params("/one/dir/shot.raw", self._opts())
        # Same basename from another folder reuses the record.
        self.assertIsNotNone(self.s.get_file_params(r"E:\other\shot.raw"))

    def test_lru_cap_drops_oldest_and_refresh_moves_to_newest(self):
        for i in range(FILE_PARAM_HISTORY_LIMIT):
            self.s.remember_file_params(f"f{i}.raw", self._opts(), timestamp=float(i))
        self.assertEqual(len(self.s.file_param_history), FILE_PARAM_HISTORY_LIMIT)
        # Touch the oldest so it is no longer the LRU entry.
        self.s.remember_file_params("f0.raw", self._opts(), timestamp=10_000.0)
        self.s.remember_file_params("overflow.raw", self._opts(), timestamp=10_001.0)
        history = self.s.file_param_history
        self.assertEqual(len(history), FILE_PARAM_HISTORY_LIMIT)
        self.assertIn("f0.raw", history, "refreshed key must survive")
        self.assertIn("overflow.raw", history)
        self.assertNotIn("f1.raw", history, "oldest untouched entry is evicted")

    def test_corrupt_or_partial_data_degrades_safely(self):
        for payload in ("{not json", "[]", "null", '"text"'):
            self.s._store.setValue("file_params/history", payload)
            self.assertEqual(self.s.get_file_params("a.raw"), None)
            self.assertEqual(self.s.file_param_history, {})
        self.s._store.setValue(
            "file_params/history",
            '{"good.raw": {"width": 8, "height": 8, "image_type": "RAW",'
            ' "format_name": "RAW8", "last_used": 5},'
            ' "zero.raw": {"width": 0, "height": 8, "image_type": "RAW",'
            ' "format_name": "RAW8"},'
            ' "junk.raw": {"width": "oops", "height": 8}}',
        )
        history = self.s.file_param_history
        self.assertEqual(set(history), {"good.raw"}, "invalid records dropped")
        self.assertEqual(history["good.raw"].width, 8)

    def test_forget_and_clear(self):
        self.s.remember_file_params("a.raw", self._opts())
        self.s.remember_file_params("b.raw", self._opts())
        self.assertTrue(self.s.forget_file_params("A.RAW"))
        self.assertFalse(self.s.forget_file_params("a.raw"))
        self.assertEqual(set(self.s.file_param_history), {"b.raw"})
        self.s.clear_file_param_history()
        self.assertEqual(self.s.file_param_history, {})


class FileParamRememberTimingTests(_IsolatedWindowTest):
    """Only a successful Apply is remembered; close/exit sync is idempotent."""

    def test_panel_edits_without_apply_are_not_remembered(self):
        path = self._path("plain.raw", 8 * 8)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        self.assertEqual(self.settings.file_param_history, {})
        self.w.close_item(0)
        self.assertEqual(self.settings.file_param_history, {})

    def test_successful_apply_writes_immediately_then_close_is_idempotent(self):
        path = self._path("shot.raw", 8 * 8)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        self._apply_and_wait()
        stored = self.settings.get_file_params("shot.raw")
        self.assertIsNotNone(stored)
        self.assertEqual((stored.width, stored.height), (8, 8))
        first_stamp = self.settings._load_file_params_raw()["shot.raw"]["last_used"]
        self.w.close_item(0)
        after = self.settings._load_file_params_raw()
        self.assertEqual(set(after), {"shot.raw"})
        self.assertEqual(after["shot.raw"]["params"].width, 8)
        self.assertGreaterEqual(after["shot.raw"]["last_used"], first_stamp)

    def test_failed_apply_is_not_remembered(self):
        # 4 bytes can never satisfy 8x8 RAW8: the decode fails.
        path = self._path("short.raw", 4)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        self._decode_and_wait()
        self.assertEqual(self.settings.file_param_history, {})
        self.w.close_item(0)
        self.assertEqual(self.settings.file_param_history, {})

    def test_close_event_flushes_applied_snapshot(self):
        path = self._path("flush.raw", 8 * 8)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        self._apply_and_wait()
        # Simulate a memory write lost after Apply, then verify exit re-flushes
        # the applied snapshot (idempotent fallback).
        self.settings.clear_file_param_history()
        self.w.close()
        self.assertIsNotNone(self.settings.get_file_params("flush.raw"))

    def test_remembered_parameters_are_restored_and_auto_decoded(self):
        path = self._path("shot.raw", 8 * 8)
        self.settings.remember_file_params(
            path, DecodeOptions(image_type="RAW", format_name="RAW8", width=8, height=8)
        )
        self.w._open_item(path, decode=True)
        _pump(lambda: self.w._pending_decode_item is None)
        item = self.w.items[0]
        self.assertEqual(item.options.width, 8)
        self.assertEqual(item.options.format_name, "RAW8")
        self.assertEqual(self.w.panel.width_spin.value(), 8)
        self.assertIn("Restored remembered parameters", self.w.state_status.text())
        self.assertNotIn("failed", self.w.state_status.text().lower())

    def test_remembered_parameters_failure_is_silent_with_status(self):
        # Record 8x8 RAW8, then replace the file with too few bytes.
        path = self._path("mismatch.raw", 8 * 8)
        self.settings.remember_file_params(
            path, DecodeOptions(image_type="RAW", format_name="RAW8", width=8, height=8)
        )
        _write(path, 4)
        with mock.patch.object(
            __import__("raw_view.gui.app", fromlist=["QMessageBox"]).QMessageBox,
            "critical",
        ) as critical:
            self.w._open_item(path, decode=True)
            _pump(lambda: self.w._pending_decode_item is None)
        self.assertFalse(critical.called, "remembered-parameter failure must be silent")
        self.assertIn(
            "Restored remembered parameters, but decoding failed",
            self.w.state_status.text(),
        )
        self.assertTrue(self.w.items[0].remembered_params_failed)

    def test_same_basename_in_two_folders_restores_per_item(self):
        first_dir = os.path.join(self.tmp, "a")
        second_dir = os.path.join(self.tmp, "b")
        os.makedirs(first_dir)
        os.makedirs(second_dir)
        shot_a = _write(os.path.join(first_dir, "same.raw"), 8 * 8)
        shot_b = _write(os.path.join(second_dir, "same.raw"), 16 * 16)
        self.settings.remember_file_params(
            shot_a, DecodeOptions(image_type="RAW", format_name="RAW8", width=8, height=8)
        )
        self.w._open_item(shot_a, decode=False)
        self.w._open_item(shot_b, decode=False)
        # Both items were restored from the same basename record, but remain
        # independent objects with their own options.
        self.assertEqual(self.w.items[0].options.width, 8)
        self.assertEqual(self.w.items[1].options.width, 8)
        self.assertIsNot(self.w.items[0].options, self.w.items[1].options)

    def test_forget_action_state_and_click(self):
        path = self._path("forget.raw", 8 * 8)
        self.settings.remember_file_params(
            path, DecodeOptions(image_type="RAW", format_name="RAW8", width=8, height=8)
        )
        self.w._open_item(path, decode=False)
        self.w._refresh_forget_action()
        self.assertTrue(self.w.panel.forget_remembered_action.isEnabled())
        with mock.patch.object(self.w, "_set_state") as set_state:
            self.w._on_forget_remembered_clicked()
        self.assertIsNone(self.settings.get_file_params("forget.raw"))
        self.assertFalse(self.w.panel.forget_remembered_action.isEnabled())
        self.assertIn("Forgot remembered parameters", set_state.call_args[0][0])

    def test_forget_action_disabled_without_record(self):
        path = self._path("norec.raw", 8 * 8)
        self.w._open_item(path, decode=False)
        self.w._refresh_forget_action()
        self.assertFalse(self.w.panel.forget_remembered_action.isEnabled())


class UserApplyIntentTests(_IsolatedWindowTest):
    """H-1: only an explicit user Apply may write the per-file memory."""

    def _frame_file(self, name: str = "frames.raw") -> str:
        # 4 frames of 4x4 RAW8 (16 bytes each).
        return self._path(name, 4 * 16)

    def test_frame_navigation_does_not_write_memory(self):
        path = self._frame_file()
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        self._decode_and_wait()  # automatic/non-Apply decode
        self.assertEqual(self.settings.file_param_history, {})

        self.w._on_apply_clicked()
        self._decode_and_wait()
        self.assertEqual(self.settings.get_file_params("frames.raw").width, 4)

        # Change the panel WITHOUT applying, then navigate frames.
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        item = self.w.items[0]
        item.total_frames = 4
        self.w._nav_frame(item, 1)
        _pump(lambda: self.w._pending_decode_item is None)
        stored = self.settings.get_file_params("frames.raw")
        self.assertEqual(
            (stored.width, stored.height), (4, 4),
            "frame navigation must not persist unapplied panel edits",
        )

    def test_silent_decode_current_does_not_write_memory(self):
        path = self._path("silent.raw", 4 * 4)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        self.w.decode_current(show_error=False)
        _pump(lambda: self.w._pending_decode_item is None)
        self.assertEqual(self.settings.file_param_history, {})
        # Reopening the same name must therefore keep the default parameters.
        self.w._open_item(path, decode=False)
        self.assertEqual(self.w.items[1].remembered_params_pending, False)

    def test_remembered_restore_decode_does_not_re_record(self):
        path = self._path("restore.raw", 4 * 4)
        self.settings.remember_file_params(
            path, DecodeOptions(image_type="RAW", format_name="RAW8", width=4, height=4),
            timestamp=100.0,
        )
        before = self.settings._load_file_params_raw()["restore.raw"]["last_used"]
        self.w._open_item(path, decode=True)
        _pump(lambda: self.w._pending_decode_item is None)
        after = self.settings._load_file_params_raw()["restore.raw"]["last_used"]
        self.assertEqual(before, after, "restore decode must not rewrite the record")
        self.assertIn("Restored remembered parameters", self.w.state_status.text())

    def test_failed_apply_does_not_write_memory(self):
        path = self._path("fail.raw", 4)  # too short for 8x8
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        # The explicit Apply path asks "parse anyway?" on a size mismatch; answer
        # yes so the worker runs and fails, without opening a modal in tests.
        # Explicit Apply shows the modal on failure; silence it for the test.
        with mock.patch.object(self.w, "_warn_size_mismatch", return_value=True),              mock.patch("raw_view.gui.app.QMessageBox.critical"):
            self.w._on_apply_clicked()
            _pump(lambda: self.w._pending_decode_item is None)
        self.assertEqual(self.settings.file_param_history, {})

    def test_late_silent_success_cannot_inherit_a_later_apply_intent(self):
        path = self._path("late.raw", 4 * 4)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        # Start a silent (non-Apply) decode, then immediately supersede it with
        # an Apply before the first worker can deliver its result.
        self.w.decode_current(show_error=False)
        self.w._on_apply_clicked()
        _pump(lambda: self.w._pending_decode_item is None)
        # Only the Apply-originated request can be recorded; the superseded
        # silent one is dropped by the generation check and cannot flip the
        # intent of the newer request.
        stored = self.settings.get_file_params("late.raw")
        self.assertIsNotNone(stored)
        self.assertEqual((stored.width, stored.height), (4, 4))
        self.assertEqual(self.w._pending_user_apply, False)

    def test_apply_to_all_items_writes_memory_per_file(self):
        # The batch is a user-initiated bulk Apply, so each success records.
        paths = [self._path(f"all{i}.raw", 4 * 4) for i in range(2)]
        for path in paths:
            self.w._open_item(path, decode=False)
        self.w._apply_all_confirmed_forever = True
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        self.w._on_apply_to_all_requested()
        _pump(
            lambda: not self.w._apply_all_queue
            and self.w.__dict__.get("_apply_all_current") is None,
            timeout=30.0,
        )
        self.assertEqual(self.w._apply_all_ok, 2)
        for path in paths:
            self.assertIsNotNone(self.settings.get_file_params(path))


class PinnedParameterTests(_IsolatedWindowTest):
    """The exact user-specified pin timeline (steps 1-8)."""

    def _apply(self, width, height, path):
        self.w.panel.set_values(
            image_type="RAW", format_name="RAW8", width=width, height=height,
        )
        self._apply_and_wait()

    def test_user_timeline_keeps_item_parameters_separate_from_pin(self):
        a = self._path("A.raw", 1280 * 720)
        b = self._path("B.raw", 1280 * 720)
        c = self._path("C.raw", 16 * 16)
        # 1. Open A (defaults), set 1280x720, Apply -> A's own parameters.
        self.w._open_item(a, decode=False)
        self._apply(1280, 720, a)
        item_a = self.w.items[0]
        self.assertEqual((item_a.options.width, item_a.options.height), (1280, 720))

        # 2. Pin (pin parameters = A's 1280x720).
        self.w.panel.toggle_pin()
        self.assertTrue(self.w.panel.is_pinned())
        self.assertEqual(self.w._pinned_params.width, 1280)

        # 3. Switch to B: panel shows pinned params, B's own remain default.
        self.w._open_item(b, decode=False)
        item_b = self.w.items[1]
        self.assertEqual(self.w.panel.width_spin.value(), 1280)
        self.assertEqual((item_b.options.width, item_b.options.height), (2560, 1440))

        # 4. Apply -> B's own parameters become the pinned set and it decodes.
        self._decode_and_wait()
        self.assertEqual((item_b.options.width, item_b.options.height), (1280, 720))

        # 5. Switch to C, Apply -> decode fails, but C's own parameters are the
        #    pinned set (per "Apply writes the pinned params into the item").
        self.w._open_item(c, decode=False)
        item_c = self.w.items[2]
        self._decode_and_wait()
        self.assertEqual((item_c.options.width, item_c.options.height), (1280, 720))
        self.assertIn("Decode failed", self.w.state_status.text())

        # 6. Change the pinned set to 1920x1080-ish values and Apply on C.
        #    (Use 2x2 so the tiny C file can actually decode.)
        self._apply(4, 4, c)
        self.assertEqual((item_c.options.width, item_c.options.height), (4, 4))
        self.assertEqual((self.w._pinned_params.width, self.w._pinned_params.height), (4, 4))
        self.assertIn("Decoded", self.w.state_status.text())

        # 7. Back to A: panel shows the pinned set, A's own still 1280x720.
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        self.assertEqual(self.w.panel.width_spin.value(), 4)
        self.assertEqual((item_a.options.width, item_a.options.height), (1280, 720))

        # 8. Unpin -> panel immediately shows A's own 1280x720 again.
        self.w.panel.toggle_pin()
        self.assertFalse(self.w.panel.is_pinned())
        self.assertEqual(self.w.panel.width_spin.value(), 1280)
        self.assertEqual(self.w.panel.height_spin.value(), 720)

    def test_pin_survives_restart_and_applies_to_first_file(self):
        a = self._path("A.raw", 8 * 8)
        # Open first (which loads item defaults), then configure and Apply.
        self.w._open_item(a, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        self._apply_and_wait()
        self.w.panel.toggle_pin()
        pinned = self.w._pinned_params
        self.assertIsNotNone(pinned)

        second = MainWindow()
        try:
            second.settings = self.settings
            second._pinned_params = self.settings.pinned_params
            second.panel.set_pinned(second._pinned_params is not None)
            self.assertTrue(second.panel.is_pinned())
            self.assertEqual(second._pinned_params.width, 8)
            other = self._path("other.raw", 8 * 8)
            second._open_item(other, decode=False)
            self.assertEqual(second.panel.width_spin.value(), 8, "pin drives panel")
            self.assertEqual(second.items[0].options.width, 2560, "item untouched")
        finally:
            second.close()
            second.deleteLater()
            _APP.processEvents()

    def test_pin_toggle_updates_icon_tooltip_and_accessibility(self):
        self.assertIn("Pin", self.w.panel.pin_btn.toolTip())
        self.assertEqual(self.w.panel.pin_btn.accessibleName(), "Pin parameters")
        self.w.panel.toggle_pin()
        self.assertIn("pinned", self.w.panel.pin_btn.toolTip().lower())
        self.assertEqual(self.w.panel.pin_btn.accessibleName(), "Unpin parameters")
        self.assertIn("pinned", self.w.panel.pin_btn.accessibleDescription().lower())
        self.assertIsNotNone(self.w.panel.pin_btn.icon())

    def test_ctrl_n_toggles_pin_but_not_inside_text_editors(self):
        self.w.panel.setEnabled(True)
        self.w.item_tabs.setFocus()
        _APP.processEvents()
        QTest.keyClick(self.w.item_tabs, Qt.Key_N, Qt.ControlModifier)
        _APP.processEvents()
        self.assertTrue(self.w.panel.is_pinned())
        editor = QLineEdit(self.w)
        editor.show()
        editor.setFocus()
        _APP.processEvents()
        QTest.keyClick(editor, Qt.Key_N, Qt.ControlModifier)
        _APP.processEvents()
        self.assertTrue(self.w.panel.is_pinned(), "text editor keeps Ctrl+N")
        editor.deleteLater()

    def test_pin_action_shortcut_is_registered_without_conflict(self):
        self.assertEqual(self.w.pin_action.shortcut().toString(), "Ctrl+N")
        self.assertEqual(self.w.pin_action.shortcutContext(), Qt.WindowShortcut)
        self.assertEqual(self.w.apply_action.shortcut().toString(), "Ctrl+A")
        self.assertIsNot(self.w.pin_action, self.w.apply_action)
        # No other window action may claim Ctrl+N.
        conflicts = [
            action.shortcut().toString()
            for action in self.w.actions()
            if action is not self.w.pin_action
            and not action.shortcut().isEmpty()
            and action.shortcut().toString() == "Ctrl+N"
        ]
        self.assertEqual(conflicts, [])
        # Shortcut list is documented in the in-app help.
        import raw_view.help_content as hc
        keys = {k for _cat, items in hc.SHORTCUTS for k, _d in items}
        self.assertIn("Ctrl+N", keys)

    def test_pin_button_click_toggles_and_persists(self):
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=8, height=8)
        QTest.mouseClick(self.w.panel.pin_btn, Qt.LeftButton)
        _APP.processEvents()
        self.assertTrue(self.w.panel.is_pinned())
        self.assertEqual(self.settings.pinned_params.width, 8)
        QTest.mouseClick(self.w.panel.pin_btn, Qt.LeftButton)
        _APP.processEvents()
        self.assertFalse(self.w.panel.is_pinned())
        self.assertIsNone(self.settings.pinned_params)


class ApplyToAllTests(_IsolatedWindowTest):
    """Serial apply-to-all: parameters, per-item decode, failures, summary."""

    def setUp(self):
        super().setUp()
        self.w._apply_all_confirmed_forever = True

    def _wait_batch(self):
        _pump(
            lambda: not self.w._apply_all_queue
            and getattr(self.w, "_apply_all_current", None) is None,
            timeout=30.0,
        )

    def test_applies_parameters_and_decodes_each_item_serially(self):
        paths = [self._path(f"i{i}.raw", 4 * 4) for i in range(3)]
        for path in paths:
            self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        self.w._on_apply_to_all_requested()
        self._wait_batch()
        self.assertEqual(self.w._apply_all_total, 3)
        self.assertEqual(self.w._apply_all_ok, 3)
        self.assertEqual(self.w._apply_all_failed, 0)
        for item in self.w.items:
            self.assertEqual((item.options.width, item.options.height), (4, 4))
            self.assertEqual(item.options.format_name, "RAW8")
            self.assertIn("RAW8", self.settings.get_file_params(item.options.file_path).format_name)
        self.assertIn("Applied to 3 item(s), 0 failed", self.w.state_status.text())

    def test_failure_does_not_block_the_rest_or_pop_dialogs(self):
        good = self._path("good.raw", 4 * 4)
        bad = self._path("bad.raw", 2)  # far too short for 4x4 RAW8
        later_good = self._path("later.raw", 4 * 4)
        for path in (good, bad, later_good):
            self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        with mock.patch(
            "raw_view.gui.app.QMessageBox.critical"
        ) as critical, mock.patch("raw_view.gui.app.QMessageBox.warning") as warning:
            self.w._on_apply_to_all_requested()
            self._wait_batch()
        self.assertFalse(critical.called, "batch failures must not show modals")
        self.assertFalse(warning.called)
        self.assertEqual((self.w._apply_all_ok, self.w._apply_all_failed), (2, 1))
        self.assertIn("Applied to 2 item(s), 1 failed", self.w.state_status.text())
        # Parameters are written even for the failed item; its memory is not.
        bad_item = next(i for i in self.w.items if i.options.file_path == bad)
        self.assertEqual(bad_item.options.width, 4)
        self.assertIsNone(self.settings.get_file_params(bad))
        self.assertIsNotNone(self.settings.get_file_params(good))

    def test_confirmation_required_unless_remembered(self):
        path = self._path("one.raw", 4 * 4)
        self.w._open_item(path, decode=False)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        del self.w.__dict__["_apply_all_confirmed_forever"]
        with mock.patch.object(self.w, "_ask_apply_all_confirmation", return_value=False) as ask:
            self.w._on_apply_to_all_requested()
        self.assertTrue(ask.called)
        self.assertEqual(self.w._apply_all_queue, [], "cancelled batch must not start")
        with mock.patch.object(self.w, "_ask_apply_all_confirmation", return_value=True):
            self.w._on_apply_to_all_requested()
        self._wait_batch()
        self.assertEqual(self.w._apply_all_ok, 1)

    def test_no_items_reports_status(self):
        with mock.patch.object(self.w, "_set_state") as set_state:
            self.w._on_apply_to_all_requested()
        self.assertIn("No items", set_state.call_args[0][0])


class PresetPlaceholderTests(_IsolatedWindowTest):
    """v0.5.0: bracket-free hint painted in the box, no duplicate popup row."""

    def setUp(self):
        super().setUp()
        for name in ("Alpha", "Beta", "Delta", "Gamma"):
            self.settings.save_sensor_preset(
                SensorPreset(name=name, image_type="RAW", format_name="RAW8")
            )
        self.w._refresh_preset_combo()
        # No file is open in this fixture; enable the panel like opening one.
        self.w.panel.set_enabled(True)
        _APP.processEvents()
        self.combo = self.w.panel.preset_combo
        self.emitted = []
        self.combo.activated.connect(
            lambda index: self.emitted.append(self.combo.itemData(index))
            if self.combo.itemData(index) else None
        )

    def _items(self):
        return [self.combo.itemText(i) for i in range(self.combo.count())]

    def test_hint_text_has_no_brackets(self):
        self.assertEqual(self.combo.placeholderText(), "Select a preset")
        self.assertNotIn("(", self.combo.placeholderText())
        self.assertNotIn(")", self.combo.placeholderText())
        self.assertEqual(self.combo.currentIndex(), -1)

    def test_popup_has_no_duplicate_placeholder_row(self):
        items = self._items()
        self.assertEqual(items, ["Alpha", "Beta", "Delta", "Gamma"])
        self.assertNotIn("Select a preset", items)
        self.assertEqual(len(items), len(set(items)))
        # Down starts on the first real preset.
        self.combo.setFocus()
        _APP.processEvents()
        QTest.keyClick(self.combo, Qt.Key_Down)
        _APP.processEvents()
        self.assertEqual(self.combo.currentData(), "Alpha")
        # Enter only loads real items; with no real row it is inert.
        visited = []
        for _ in range(4):
            QTest.keyClick(self.combo, Qt.Key_Down)
            _APP.processEvents()
            visited.append(self.combo.currentData())
        self.assertEqual(visited, ["Beta", "Delta", "Gamma", "Gamma"])

    def test_empty_candidate_list_has_no_selectable_pseudo_item(self):
        self.w.panel.set_preset_category("YUV")  # no YUV presets configured
        _APP.processEvents()
        self.assertEqual(self.combo.count(), 0)
        self.assertEqual(self.combo.currentIndex(), -1)
        self.assertIsNone(self.combo.currentData())
        self.combo.setFocus()
        _APP.processEvents()
        QTest.keyClick(self.combo, Qt.Key_Return)
        _APP.processEvents()
        self.assertEqual(self.emitted, [], "no pseudo item can be activated")

    def test_hint_colour_differs_between_themes_and_tracks_switch(self):
        from PyQt5.QtGui import QPalette

        colours = {}
        for theme in ("light", "dark"):
            self.settings._store.setValue("ui/theme", theme)
            self.w._apply_theme()
            _APP.processEvents()
            colours[theme] = (
                self.combo.hint_color().name(),
                self.combo.palette().color(QPalette.Text).name(),
            )
            # The hint must not be identical to body text (softer).
            self.assertNotEqual(colours[theme][0], colours[theme][1])
        self.assertNotEqual(colours["light"][0], colours["dark"][0])

    def test_hint_is_soft_but_legible(self):
        from PyQt5.QtGui import QPalette

        def luminance(col):
            def channel(value):
                v = value / 255
                return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return (
                0.2126 * channel(col.red())
                + 0.7152 * channel(col.green())
                + 0.0722 * channel(col.blue())
            )

        self.settings._store.setValue("ui/theme", "light")
        self.w._apply_theme()
        _APP.processEvents()
        hint = self.combo.hint_color()
        base = self.combo.palette().color(QPalette.Base)
        hi, lo = sorted((luminance(hint), luminance(base)), reverse=True)
        self.assertGreaterEqual((hi + 0.05) / (lo + 0.05), 3.0)

    def test_hint_is_actually_painted(self):
        """The hint must be drawn: compare renders with/without placeholder text."""
        from PyQt5.QtGui import QColor  # noqa: F401  (kept for image decoding)

        self.w.resize(700, 560)
        self.w.panel.set_preset_category("YUV")  # no candidates -> empty state
        _APP.processEvents()

        def render():
            image = self.combo.grab().toImage()
            return [
                image.pixel(x, y)
                for y in range(image.height())
                for x in range(image.width())
            ]

        with_hint = render()
        self.combo.setPlaceholderText("")
        self.combo.update()
        _APP.processEvents()
        without_hint = render()
        self.combo.setPlaceholderText("Select a preset")
        self.combo.update()
        _APP.processEvents()
        changed = sum(1 for a, b in zip(with_hint, without_hint) if a != b)
        self.assertGreater(changed, 50, "hint text must be painted inside the box")


class PinShortcutTextEntryTests(_IsolatedWindowTest):
    """H-2: Ctrl+N must stay inert inside spin boxes and combos."""

    def _ctrl_n(self, widget):
        widget.setFocus()
        _APP.processEvents()
        before = self.w.panel.is_pinned()
        QTest.keyClick(widget, Qt.Key_N, Qt.ControlModifier)
        _APP.processEvents()
        return before, self.w.panel.is_pinned()

    def test_ctrl_n_ignored_in_spinbox_and_its_editor(self):
        self.w.panel.set_enabled(True)
        _APP.processEvents()
        self.assertEqual(self._ctrl_n(self.w.panel.width_spin), (False, False))
        self.assertEqual(self._ctrl_n(self.w.panel.width_spin.lineEdit()), (False, False))

    def test_ctrl_n_ignored_in_editable_combo_and_plain_line_edit(self):
        from PyQt5.QtWidgets import QComboBox

        combo = QComboBox(self.w)
        combo.setEditable(True)
        combo.addItem("editable")
        combo.show()
        _APP.processEvents()
        self.assertEqual(self._ctrl_n(combo), (False, False))
        self.assertEqual(self._ctrl_n(combo.lineEdit()), (False, False))
        combo.deleteLater()
        editor = QLineEdit(self.w)
        editor.show()
        _APP.processEvents()
        self.assertEqual(self._ctrl_n(editor), (False, False))
        editor.deleteLater()

    def test_ctrl_n_still_toggles_from_non_text_focus(self):
        self.w.panel.set_enabled(True)
        _APP.processEvents()
        before, after = self._ctrl_n(self.w.item_tabs)
        self.assertNotEqual(before, after, "Ctrl+N must work outside text entry")


class ForgetActionTabSwitchTests(_IsolatedWindowTest):
    """M-1: forget-action availability follows the active tab."""

    def test_forget_action_refreshes_on_tab_switch(self):
        with_record = self._path("has-record.raw", 4 * 4)
        without_record = self._path("no-record.raw", 4 * 4)
        self.settings.remember_file_params(
            with_record,
            DecodeOptions(image_type="RAW", format_name="RAW8", width=4, height=4),
        )
        self.w._open_item(with_record, decode=False)
        self.w._open_item(without_record, decode=False)
        _APP.processEvents()
        # Opening the second file made it active; it has no record.
        self.assertFalse(
            self.w.panel.forget_remembered_action.isEnabled(),
            "a file without a record must not offer the forget action",
        )
        self.w.item_tabs.setCurrentIndex(0)  # file WITH a record
        _APP.processEvents()
        self.assertTrue(self.w.panel.forget_remembered_action.isEnabled())
        self.w.item_tabs.setCurrentIndex(1)  # back to the file without one
        _APP.processEvents()
        self.assertFalse(self.w.panel.forget_remembered_action.isEnabled())


class ApplyAllSkipAccountingTests(_IsolatedWindowTest):
    """M-2: closing a queued item is reported as skipped."""

    def test_closing_queued_item_counts_as_skipped(self):
        paths = [self._path(f"q{i}.raw", 4 * 4) for i in range(3)]
        for path in paths:
            self.w._open_item(path, decode=False)
        self.w._apply_all_confirmed_forever = True
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        # Close the third item right after the batch is built.
        self.w._on_apply_to_all_requested()
        third = self.w.items[2]
        self.w.close_item(2)
        _pump(
            lambda: not self.w._apply_all_queue
            and self.w.__dict__.get("_apply_all_current") is None,
            timeout=30.0,
        )
        self.assertEqual(self.w._apply_all_total, 3)
        self.assertEqual(self.w._apply_all_skipped, 1)
        self.assertEqual(self.w._apply_all_ok, 2)
        self.assertEqual(self.w._apply_all_failed, 0)
        text = self.w.state_status.text()
        self.assertIn("Applied to 2 item(s)", text)
        self.assertIn("1 skipped", text)
        self.assertNotIn(third, self.w.items)


class DecodeOptionsWhitelistTests(unittest.TestCase):
    """L-2: invalid enum values fall back to defaults."""

    def test_invalid_enums_fall_back_to_defaults(self):
        from raw_view.models import BAYER_PATTERNS, decode_options_from_mapping

        opts = decode_options_from_mapping({
            "width": 8, "height": 8,
            "alignment": "GARBAGE", "endianness": "SIDEWAYS",
            "preview_mode": "Secret", "bayer_pattern": "ZZZZ",
        })
        self.assertIsNotNone(opts)
        self.assertEqual(opts.alignment, "msb")
        self.assertEqual(opts.endianness, "little")
        self.assertEqual(opts.preview_mode, "Bayer Color")
        self.assertIn(opts.bayer_pattern, BAYER_PATTERNS)
        self.assertEqual(opts.bayer_pattern, "RGGB")

    def test_valid_enums_are_preserved(self):
        from raw_view.models import decode_options_from_mapping

        opts = decode_options_from_mapping({
            "width": 8, "height": 8,
            "alignment": "lsb", "endianness": "big",
            "preview_mode": "Grayscale", "bayer_pattern": "BGGR",
        })
        self.assertEqual(
            (opts.alignment, opts.endianness, opts.preview_mode, opts.bayer_pattern),
            ("lsb", "big", "Grayscale", "BGGR"),
        )


class PanelPinUiTests(unittest.TestCase):
    """Panel-level pin/context-menu wiring without a full MainWindow."""

    def setUp(self):
        from raw_view.gui.panels import ControlPanel

        self.p = ControlPanel()
        self.p.show()
        _APP.processEvents()

    def tearDown(self):
        self.p.close()
        self.p.deleteLater()
        _APP.processEvents()

    def test_pin_button_and_menu_entries_present(self):
        texts = [a.text() for a in self.p._preset_context_menu().actions() if a.text()]
        self.assertIn("Pin parameters (Ctrl+N)", texts)
        self.assertIn("Apply these parameters to all items", texts)
        self.assertIn("Forget remembered parameters for this file", texts)
        self.assertEqual(self.p.pin_btn.objectName(), "pinButton")

    def test_set_pinned_does_not_emit(self):
        emitted = []
        self.p.pinToggled.connect(emitted.append)
        self.p.set_pinned(True)
        self.assertTrue(self.p.is_pinned())
        self.assertEqual(emitted, [], "programmatic restore must stay silent")
        self.p.toggle_pin()
        self.assertTrue(self.p.is_pinned() is False)
        self.assertEqual(emitted, [False])

    def test_pin_signals_reach_apply_all_and_forget_actions(self):
        fired = []
        self.p.applyToAllRequested.connect(lambda: fired.append("all"))
        self.p.forgetRememberedRequested.connect(lambda: fired.append("forget"))
        self.p.apply_to_all_action.trigger()
        self.p.forget_remembered_action.setEnabled(True)
        self.p.forget_remembered_action.trigger()
        self.assertEqual(fired, ["all", "forget"])
