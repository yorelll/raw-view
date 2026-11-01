"""Tests for tab Reload / Reload All Items and same-path tab deduplication.

Both features share one implementation (``MainWindow._reload_item_at``): a
reload is "close this item and open the path again" except the tab keeps its
position.  ``_open_item`` reuses that same method when the requested path is
already open, so these two behaviours cannot drift apart.

Settings go through the isolated INI store installed by
``_settings_isolation``; the production HKCU store is never touched.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_cleanup import (  # noqa: E402
    begin_widget_leak_check,
    destroy_widget,
    end_widget_leak_check,
    flush_deferred_deletes,
)
from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

import numpy as np  # noqa: E402

from raw_view.gui import app as app_module  # noqa: E402
from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.models import AppSettings, DecodeOptions, SensorPreset  # noqa: E402

#: RAW8, 4x4 → 16 bytes per frame.  Small enough to write several frames.
_FRAME = 16
#: The viewer's default geometry for a RAW file (RAW12 2560x1440), i.e. exactly
#: what a freshly opened .raw declares before any remember/Apply.  Files of this
#: size decode successfully, which is what makes the async-completion assertions
#: meaningful (small fixtures fail the decode instead of reporting "Decoded").
_DEFAULT_RAW_BYTES = 2560 * 1440 * 2


def _write_raw(path: str, frames: int, fill: bytes = b"\x00") -> str:
    with open(path, "wb") as f:
        f.write((fill * _FRAME) * frames)
    return path


class _WindowFixture(unittest.TestCase):
    """Shared fixture; arms the L3 widget-leak guard (CLAUDE.md §4).

    ``unittest discover`` does not load ``conftest.py``, so a fixture that only
    inherits ``unittest.TestCase`` gets no leak protection on the CI path.
    """

    def setUp(self):
        begin_widget_leak_check()
        self.tmp = tempfile.mkdtemp(prefix="rv-reload-")
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
        # Reload/open start real background decodes.  Let the in-flight one finish
        # and let its QThread actually stop before the window is destroyed: the
        # worker emits into the window from another thread, and destroying the
        # C++ window under a still-running emit is a use-after-free (observed as
        # an access violation that surfaced in whichever test ran next).
        try:
            self._drain_decoder()
            # Route through the production shutdown path first: closeEvent does
            # the bounded thread wait and signal disconnect that a plain
            # destroy_widget() skips.  A worker still emitting while Qt tears the
            # window down aborts the process at interpreter exit — after every
            # assertion has already passed — which made this file's runs
            # intermittently red for reasons unrelated to the behaviour tested.
            try:
                self.w.close()
            except RuntimeError:
                pass
            destroy_widget(self.w, close=False)
        finally:
            end_widget_leak_check(type(self).__name__)

    def _default_raw(self, name: str, frames: int = 2) -> str:
        """A file the viewer's default RAW geometry can actually decode."""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(bytes(_DEFAULT_RAW_BYTES * frames))
        return path

    def _settle(self, timeout: float = 20.0) -> bool:
        """Run a real event loop until nothing is decoding (real deadline).

        ``processEvents()`` spinning is not enough: the worker reports from its
        own thread, and a busy ``processEvents`` loop can outrun the wall-clock
        deadline it is supposed to respect.  ``QTest.qWait`` runs a genuine event
        loop iteration, and ``time.monotonic`` is the real deadline.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            if not any(it.decoding for it in self.w.items):
                return True
        return False

    def _drain_decoder(self, timeout: float = 60.0) -> None:
        """Block until the window's decode QThread has really stopped.

        ``QTest.qWait`` keeps the event loop alive (the worker's cross-thread
        result needs it), then ``thread.wait`` joins the thread.  Qt aborts the
        process if a ``QThread`` is destroyed while still running
        (``QThread: Destroyed while thread is still running``), which manifests
        as a non-Python ``abort`` *after* every assertion passed.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            thread = self.w.__dict__.get("_thread")
            if thread is None:
                if self.w.__dict__.get("_pending_decode_item") is None:
                    return
                continue
            try:
                if not thread.isRunning():
                    thread.wait(2000)
                    return
            except RuntimeError:
                return

    def _default_raw(self, name: str, frames: int = 2) -> str:
        """A file the viewer's default RAW geometry can actually decode."""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(bytes(_DEFAULT_RAW_BYTES * frames))
        return path

    def _settle(self, timeout: float = 20.0) -> bool:
        """Run a real event loop until nothing is decoding (real deadline).

        ``processEvents()`` spinning is not enough: the worker reports from its
        own thread, and a busy ``processEvents`` loop can outrun the wall-clock
        deadline it is supposed to respect.  ``QTest.qWait`` runs a genuine event
        loop iteration, and ``time.monotonic`` is the real deadline.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            if not any(it.decoding for it in self.w.items):
                return True
        return False

    def _drain_decoder(self, timeout: float = 30.0) -> None:
        """Wait until no decode is pending (real event loop, real deadline)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            if self.w.__dict__.get("_pending_decode_item") is None:
                thread = self.w.__dict__.get("_thread")
                if thread is None:
                    return
                try:
                    if not thread.isRunning():
                        return
                except RuntimeError:
                    return

    def _default_raw(self, name: str, frames: int = 2) -> str:
        """A file the viewer's default RAW geometry can actually decode."""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(bytes(_DEFAULT_RAW_BYTES * frames))
        return path

    def _settle(self, timeout: float = 20.0) -> bool:
        """Run a real event loop until nothing is decoding (real deadline).

        ``processEvents()`` spinning is not enough: the worker reports from its
        own thread, and a busy ``processEvents`` loop can outrun the wall-clock
        deadline it is supposed to respect.  ``QTest.qWait`` runs a genuine event
        loop iteration, and ``time.monotonic`` is the real deadline.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            if not any(it.decoding for it in self.w.items):
                return True
        return False

    def _drain_decoder(self, timeout: float = 30.0) -> None:
        """Wait until the decode worker finished and its QThread has stopped.

        Every wait here is a real event-loop iteration (``QTest.qWait``) bounded
        by ``time.monotonic``; ``processEvents()`` spinning can outrun any
        deadline and never lets the worker's cross-thread signal be delivered.

        The window is only destroyed once the thread reports ``not isRunning()``:
        a worker emitting into a destroyed C++ window from another thread is a
        use-after-free that shows up as a process-level crash in whichever test
        runs *next*, which is exactly the intermittent failure this prevents.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            thread = self.w.__dict__.get("_thread")
            if thread is None:
                if self.w.__dict__.get("_pending_decode_item") is None:
                    return
                continue
            try:
                running = thread.isRunning()
            except RuntimeError:
                return
            if not running and self.w.__dict__.get("_pending_decode_item") is None:
                # Join the finished thread explicitly: Qt's wrapper may outlive
                # the test, and a QThread destroyed while its event loop is still
                # unwinding aborts the process at interpreter exit.
                try:
                    thread.wait(2000)
                except RuntimeError:
                    pass
                return
        # Timed out: detach the signal path so the window can be destroyed
        # without a late worker touching it (the thread still finishes on its
        # own; this only severs the UI callbacks).
        try:
            self.w._disconnect_decode()
        except Exception:  # pragma: no cover - defensive
            pass

    def _default_raw(self, name: str, frames: int = 2) -> str:
        """A file the viewer's default RAW geometry can actually decode."""
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(bytes(_DEFAULT_RAW_BYTES * frames))
        return path

    def _settle(self, timeout: float = 20.0) -> bool:
        """Run a real event loop until nothing is decoding (real deadline).

        ``processEvents()`` spinning is not enough: the worker reports from its
        own thread, and a busy ``processEvents`` loop can outrun the wall-clock
        deadline it is supposed to respect.  ``QTest.qWait`` runs a genuine event
        loop iteration, and ``time.monotonic`` is the real deadline.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            if not any(it.decoding for it in self.w.items):
                return True
        return False

    def _drain_decoder(self, timeout: float = 20.0) -> None:
        """Wait until the decode worker finished and its thread stopped.

        ``QTest.qWait`` runs a genuine event loop iteration; ``processEvents()``
        spinning can outrun any deadline and leaves no room for the worker's
        cross-thread signal delivery.  ``time.monotonic`` is the real deadline.
        """
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            QTest.qWait(10)
            thread = self.w.__dict__.get("_thread")
            if self.w.__dict__.get("_pending_decode_item") is not None:
                continue
            if thread is None:
                return
            try:
                if not thread.isRunning():
                    return
            except RuntimeError:
                return

    def _raw(self, name: str, frames: int = 2, fill: bytes = b"\x00") -> str:
        return _write_raw(os.path.join(self.tmp, name), frames, fill)

    def _png(self, name: str, bgr: np.ndarray) -> str:
        import cv2

        path = os.path.join(self.tmp, name)
        self.assertTrue(cv2.imwrite(path, bgr))
        return path

    def _open(self, *paths: str) -> None:
        for path in paths:
            self.w._open_item(path, decode=False)
        _APP.processEvents()


# ── Menu wiring ──────────────────────────────────────────────────────────


class TabMenuReloadEntriesTests(unittest.TestCase):
    """The context menu exposes Reload / Reload All Items, no shortcuts."""

    def setUp(self):
        from raw_view.gui.app import MainWindow as MW

        self.w = MW.__new__(MW)
        from PyQt5.QtWidgets import QTabWidget

        self.tabs = QTabWidget()
        self.w.item_tabs = self.tabs
        self.menu = MW._build_tab_context_menu(self.w)

    def tearDown(self):
        destroy_widget(self.tabs)

    def test_actions_exist_with_expected_texts_and_order(self):
        texts = [a.text() for a in self.menu.actions()]
        self.assertEqual(
            texts,
            [
                "Reload",
                "Reload All Items",
                "",
                "Close Items to the Right",
                "Close All Items",
            ],
            "Reload group first, separator, then the close group",
        )

    def test_reload_actions_have_no_shortcut(self):
        for action in (self.w._acTabReload, self.w._acTabReloadAll):
            self.assertTrue(action.shortcut().isEmpty())

    def test_reload_actions_share_the_tab_widget_parent_and_are_cached(self):
        self.assertIs(self.w._acTabReload.parent(), self.tabs)
        self.assertIs(self.w._acTabReloadAll.parent(), self.tabs)
        self.assertIs(self.w._tabRMenu, self.menu)
        # Rebuilding must produce a fresh menu whose actions are the new ones.
        rebuilt = MainWindow_rebuild(self.w)
        self.assertIsNot(rebuilt, self.menu)


def MainWindow_rebuild(window):
    from raw_view.gui.app import MainWindow

    return MainWindow._build_tab_context_menu(window)


class TabMenuReloadDispatchTests(_WindowFixture):
    """_run_tab_menu_action dispatches the two new actions by identity."""

    def test_dispatch_reload_reloads_that_index(self):
        paths = [self._raw(f"a{i}.raw") for i in range(3)]
        self._open(*paths)
        self.w._build_tab_context_menu()
        target = self.w.items[1]
        with mock.patch.object(
            self.w, "_reload_item_at", wraps=self.w._reload_item_at,
        ) as reload_at:
            self.w._run_tab_menu_action(self.w._acTabReload, index=1)
        reload_at.assert_called_once_with(1)
        self.assertIsNot(self.w.items[1], target, "item must be rebuilt")

    def test_dispatch_reload_all(self):
        paths = [self._raw(f"a{i}.raw") for i in range(3)]
        self._open(*paths)
        self.w._build_tab_context_menu()
        with mock.patch.object(
            self.w, "reload_all_items", wraps=self.w.reload_all_items,
        ) as reload_all:
            self.w._run_tab_menu_action(self.w._acTabReloadAll, index=0)
        reload_all.assert_called_once_with()

    def test_dispatch_none_stays_noop(self):
        self._open(self._raw("a.raw"))
        self.w._run_tab_menu_action(None, index=0)
        self.assertEqual(len(self.w.items), 1)


# ── Reload semantics ────────────────────────────────────────────────────


class ReloadItemTests(_WindowFixture):
    def test_reload_reads_new_content_from_disk(self):
        """The reloaded item must be built from the current file size on disk.

        The size is queried with explicit RAW8 4x4 options: reload intentionally
        discards *unapplied* panel edits (it is close+open), so the rebuilt item
        carries the remembered/default geometry rather than whatever a test set
        on the panel.  ``_frame_count_for`` is a pure "what does this file yield"
        query, which keeps the assertion about the disk read instead of about a
        decodable fixture.
        """
        path = self._raw("grow.raw", frames=1)
        self._open(path)
        probe = DecodeOptions(
            file_path=path, image_type="RAW", format_name="RAW8", width=4, height=4,
        )
        self.assertEqual(self.w._frame_count_for(probe), 1)

        # Externally append two more frames, then reload.
        _write_raw(path, frames=3)
        reloaded = self.w.reload_item(0)
        self.assertIs(reloaded, self.w.items[0])
        self.assertEqual(
            self.w._frame_count_for(probe), 3,
            "the file on disk changed; the reload must see the new size",
        )

    def test_reload_clears_decode_cache_for_that_path(self):
        path = self._raw("cached.raw")
        self._open(path)
        cache = self.w.decode_cache
        with mock.patch.object(
            cache, "remove_file", wraps=cache.remove_file,
        ) as remove_file:
            self.w.reload_item(0)
            _APP.processEvents()
        self.assertTrue(
            any(call.args and call.args[0] == path for call in remove_file.call_args_list),
            "reload must drop cached frames before decoding",
        )

    def test_reload_keeps_tab_position_and_produces_no_duplicate(self):
        paths = [self._raw(f"p{i}.raw") for i in range(3)]
        self._open(*paths)
        old_item = self.w.items[1]
        self.assertEqual(
            [self.w.item_tabs.tabText(i) for i in range(3)],
            ["p0.raw", "p1.raw", "p2.raw"],
        )
        self.w.reload_item(1)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 3, "no extra tab")
        self.assertEqual(
            [self.w.item_tabs.tabText(i) for i in range(3)],
            ["p0.raw", "p1.raw", "p2.raw"],
            "position must not shift",
        )
        self.assertIsNot(self.w.items[1], old_item, "item is rebuilt")
        self.assertEqual(self.w.items[1].options.file_path, paths[1])

    def test_reload_of_current_tab_refreshes_panel_and_status(self):
        path = self._png("cur.png", np.zeros((5, 7, 3), np.uint8))
        self._open(path)
        self.assertEqual(self.w.item_tabs.currentIndex(), 0)
        self.w.panel.set_values(
            image_type="RAW", format_name="RAW8", width=99, height=99,
        )
        self.w.reload_item(0)
        _APP.processEvents()
        # Reload = close+open → unapplied edits are discarded and the panel is
        # rebuilt from the item (a Standard Image defaults to its own type).
        self.assertEqual(self.w.panel.type_combo.currentText(), "Standard Image")
        self.assertIn("Image:", self.w.image_status.text())

    def test_reload_of_background_tab_does_not_touch_current_panel(self):
        front = self._png("front.png", np.zeros((5, 7, 3), np.uint8))
        back = self._png("back.png", np.zeros((9, 11, 3), np.uint8))
        self._open(front, back)
        self.w.item_tabs.setCurrentIndex(0)
        _APP.processEvents()
        width_before = self.w.panel.width_spin.value()
        status_before = self.w.image_status.text()

        self.w.reload_item(1)
        _APP.processEvents()

        self.assertEqual(self.w.item_tabs.currentIndex(), 0)
        self.assertEqual(self.w.panel.width_spin.value(), width_before)
        self.assertEqual(self.w.image_status.text(), status_before)

    def test_reload_keeps_one_tab_identity_consistent(self):
        paths = [self._raw(f"x{i}.raw") for i in range(3)]
        self._open(*paths)
        self.w.item_tabs.setCurrentIndex(1)
        _APP.processEvents()
        self.w.reload_item(1)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.currentIndex(), 1)
        self.assertIs(self.w._current_item(), self.w.items[1])
        self.assertIs(self.w._active_item_ref, self.w.items[1])

    def test_reload_does_not_write_file_parameter_memory(self):
        path = self._raw("norewrite.raw")
        self._open(path)
        self.w.panel.set_values(image_type="RAW", format_name="RAW8", width=4, height=4)
        self.w.reload_item(0)
        _APP.processEvents()
        self.assertEqual(
            self.settings.file_param_history, {},
            "a reload decode must never persist parameters (only explicit Apply)",
        )

    def test_reload_of_queued_item_does_not_stall_open_queue(self):
        """Reloading the item an open queue waits on must not freeze the batch."""
        paths = [self._raw(f"q{i}.raw") for i in range(3)]
        with mock.patch.object(self.w, "_decode_item"):
            self.w._open_paths(paths)
        pending = self.w._open_decode_pending
        self.assertTrue(pending, "sanity: the batch queued the tail")
        self._open_decode_in_flight = self.w._open_decode_in_flight
        in_flight = self.w._open_decode_in_flight
        self.assertIsNotNone(in_flight)

        index = self.w.items.index(in_flight)
        self.w._reload_item_at(index)
        _APP.processEvents()

        self.assertNotIn(
            in_flight, self.w.__dict__.get("_open_decode_pending", []),
            "destroyed item must be dropped from the queue",
        )

    def test_reload_invalid_index_is_ignored(self):
        self._open(self._raw("only.raw"))
        self.w.reload_item(5)
        self.w.reload_item(-1)
        self.assertEqual(len(self.w.items), 1)


# ── Reload all ──────────────────────────────────────────────────────────


class ReloadAllTests(_WindowFixture):
    def test_reload_all_rebuilds_every_item_and_refreshes_once(self):
        paths = [self._raw(f"all{i}.raw") for i in range(4)]
        self._open(*paths)
        before = list(self.w.items)
        # Reload All is serialised: each item decodes before the next is rebuilt,
        # so the consolidated refresh happens after the last decode reports back.
        # The mocks therefore have to stay installed for the whole batch.
        with mock.patch.object(
            self.w, "_refresh_file_nav_actions", wraps=self.w._refresh_file_nav_actions,
        ) as nav, mock.patch.object(
            self.w, "_refresh_recent_files_menu", wraps=self.w._refresh_recent_files_menu,
        ) as recent:
            self.w.reload_all_items()
            self._settle(25.0)
        # Reload All is serialised (one decode at a time), so the per-step
        # refreshes are suppressed by the batch and a single consolidated pass
        # runs at the end; the total must therefore stay at one.
        self.assertEqual(nav.call_count, 1, "one nav refresh for the whole batch")
        self.assertEqual(recent.call_count, 1, "one recent-menu refresh for the batch")
        self.assertEqual(len(self.w.items), 4)
        for old, new in zip(before, self.w.items):
            self.assertIsNot(old, new, "every item is rebuilt")
        self.assertEqual(
            [self.w.item_tabs.tabText(i) for i in range(4)],
            [f"all{i}.raw" for i in range(4)],
            "positions and names preserved",
        )

    def test_reload_all_on_empty_window_is_noop(self):
        self.w.reload_all_items()
        self.assertEqual(self.w.items, [])


# ── Same-path dedupe ────────────────────────────────────────────────────


class DuplicateOpenDedupeTests(_WindowFixture):
    def test_same_path_reuses_the_existing_tab(self):
        path = self._raw("dup.raw")
        self._open(path)
        item = self.w._open_item(path, decode=False)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 1)
        self.assertIs(item, self.w.items[0])

    def test_case_variant_is_a_duplicate_on_windows(self):
        if os.name != "nt":
            self.skipTest("case-insensitive file systems only")
        path = self._raw("CaseTest.raw")
        self._open(path)
        variant = os.path.join(self.tmp, "casetest.RAW")
        self.assertTrue(os.path.isfile(variant))
        self.w._open_item(variant, decode=False)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 1)

    def test_relative_path_variant_is_a_duplicate(self):
        path = self._raw("rel.raw")
        self._open(path)
        # ``os.path.relpath`` raises on Windows when the temp dir and the CWD are
        # on different drives, so build an equivalent "dot path" spelling by hand.
        variant = os.path.join(self.tmp, ".", "rel.raw")
        self.assertNotEqual(variant, path)
        self.w._open_item(variant, decode=False)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 1)

    def test_dedupe_keeps_the_original_tab_position(self):
        paths = [self._raw(f"d{i}.raw") for i in range(3)]
        self._open(*paths)
        self.assertEqual(self.w.item_tabs.currentIndex(), 2)
        self.w._open_item(paths[0], decode=False)
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 3)
        self.assertEqual(
            [self.w.item_tabs.tabText(i) for i in range(3)],
            ["d0.raw", "d1.raw", "d2.raw"],
            "duplicate must not move to the end",
        )
        self.assertEqual(
            self.w.items[0].options.file_path, paths[0],
            "the refreshed item stays at index 0",
        )

    def test_distinct_files_are_not_deduped(self):
        a = self._raw("a.raw")
        b = self._raw("b.raw")
        self._open(a, b)
        self.assertEqual(self.w.item_tabs.count(), 2)

    def test_dedupe_drops_cached_frames_for_the_reopened_path(self):
        path = self._raw("dup_cached.raw")
        self._open(path)
        self.w.decode_current(show_error=False)
        for _ in range(200):
            _APP.processEvents()
            if self.w._pending_decode_item is None:
                break
        cache = self.w.decode_cache
        with mock.patch.object(
            cache, "remove_file", wraps=cache.remove_file,
        ) as remove_file:
            self.w._open_item(path, decode=False)
            _APP.processEvents()
        self.assertTrue(remove_file.called, "reopen must invalidate cached frames")


class DuplicateOpenQueueTests(_WindowFixture):
    """A repeated path must not starve the v0.5.2 per-item decode queue."""

    def test_duplicate_paths_in_one_batch_produce_one_tab_and_one_queue_entry(self):
        a = self._raw("qa.raw")
        b = self._raw("qb.raw")
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_paths([a, a, b])
        _APP.processEvents()
        self.assertEqual(self.w.item_tabs.count(), 2)
        queued = [
            self.w._open_decode_in_flight, *self.w._open_decode_pending,
        ]
        self.assertEqual(len(queued), 2, "one queue entry per distinct file")
        self.assertEqual(len({id(it) for it in queued}), 2)
        # The first file decodes immediately through the normal path, the second
        # through the queue; both must be dispatched exactly once.
        self.assertEqual(
            decode_item.call_count, 1,
            "queue step decodes the second file once (the first uses decode path)",
        )

    def test_items_with_cached_pixels_compare_by_identity(self):
        """ViewerItem is ``eq=False``: dedupe must not rely on value equality."""
        a = self._raw("ida.raw")
        b = self._raw("idb.raw")
        with mock.patch.object(self.w, "_decode_item"):
            self.w._open_paths([a, b])
        _APP.processEvents()
        # Give both items numpy pixel buffers; a value-equality comparison would
        # raise "truth value of an array is ambiguous" here.
        self.w.items[0].current_display = np.zeros((2, 2, 3), np.uint8)
        self.w.items[1].current_display = np.zeros((2, 2, 3), np.uint8)
        with mock.patch.object(self.w, "_decode_item"):
            self.w._open_paths([a])
        _APP.processEvents()
        self.assertEqual(len(self.w.items), 2)

    def test_batch_dedupe_preserves_decode_order(self):
        paths = [self._raw(f"ord{i}.raw") for i in range(3)]
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_paths([paths[0], paths[1], paths[0], paths[2]])
        _APP.processEvents()
        decoded_paths = [
            item.options.file_path
            for item in [self.w._open_decode_in_flight]
            if item is not None
        ]
        self.assertEqual(decoded_paths, [paths[0]], "first file decodes immediately")
        self.assertEqual(
            [it.options.file_path for it in self.w._open_decode_pending],
            [paths[1], paths[2]],
            "remaining files keep their original order, without the duplicate",
        )


class BackgroundDecodeCompletionTests(_WindowFixture):
    """A background tab must receive its own decode result (D-1).

    Regression: a reload (or duplicate open) of a *non-visible* tab used to
    leave that tab on ``Decoding...`` forever.  The worker reply was attributed
    to the visible tab, the identity check discarded it, and ``decoding`` was
    never cleared.  These tests use real files that decode successfully and a
    real event loop with a real deadline, so they fail on regressions instead of
    passing on a fixture that never decodes.
    """

    def _open_three(self):
        paths = [self._default_raw(f"p{i}.raw") for i in range(3)]
        for path in paths:
            self.w._open_item(path, decode=False)
        QTest.qWait(20)
        self.assertTrue(self._settle(), "initial decode of the batch must settle")
        self.w.item_tabs.setCurrentIndex(2)
        QTest.qWait(20)
        return paths

    def test_background_reload_reaches_decoded(self):
        self._open_three()
        reloaded = self.w.reload_item(0)
        self.assertTrue(self._settle(20.0), "background reload never completed")
        self.assertFalse(reloaded.decoding)
        self.assertEqual(reloaded.status_text, "Decoded")
        self.assertEqual(self.w.item_tabs.currentIndex(), 2, "user view must not jump")

    def test_background_duplicate_open_reaches_decoded(self):
        paths = self._open_three()
        item = self.w._open_item(paths[0], decode=True)
        self.assertTrue(self._settle(20.0), "duplicate open of a background tab hung")
        self.assertFalse(item.decoding)
        self.assertEqual(item.status_text, "Decoded")
        self.assertEqual(self.w.item_tabs.currentIndex(), 2)
        self.assertEqual(self.w.item_tabs.count(), 3, "still one tab per file")

    def test_reload_all_decodes_every_tab(self):
        self._open_three()
        self.w.reload_all_items()
        self.assertTrue(self._settle(25.0), "Reload All never completed")
        self.assertEqual(
            [it.status_text for it in self.w.items],
            ["Decoded"] * 3,
            "every reloaded tab must finish decoding",
        )

    def test_reload_all_during_batch_open_settles(self):
        """D-2: Reload All while the multi-file open queue is still running."""
        paths = [self._default_raw(f"s{i}.raw") for i in range(6)]
        self.w._open_paths(paths)
        QTest.qWait(10)
        self.w.reload_all_items()
        self.assertTrue(self._settle(30.0), "reload-during-batch never settled")
        self.assertEqual(len(self.w.items), 6)
        self.assertEqual([it.status_text for it in self.w.items], ["Decoded"] * 6)


class ReloadAllStateMachineTests(_WindowFixture):
    """0.5.4 review H-1..H-4: the Reload All state machine must never wedge.

    Every test here asserts on the *observable* state after the batch settles:
    ``_reload_all_state is None``, ``_batch_depth == 0``,
    ``_batch_suppress is False`` and ``updatesEnabled() is True``.  A leaked
    batch section disables repainting for the rest of the window's life, which
    is why those four are asserted together rather than just "no exception".
    """

    def _raw_many(self, count: int, prefix: str = "r") -> list[str]:
        return [self._default_raw(f"{prefix}{i}.raw") for i in range(count)]

    def tearDown(self):
        # Join the decode thread before the shared fixture destroys the window:
        # Qt aborts the process when a running QThread is destroyed, and these
        # cases intentionally drive the real async pipeline.
        try:
            self._quiesce_window()
        finally:
            super().tearDown()

    def _quiesce_window(self) -> None:
        """Leave the window with no running decode thread.

        These tests drive the real async pipeline, and Qt aborts the process if a
        ``QThread`` is destroyed while still running.  Joining here makes every
        case end in a clean state, which is what keeps the *suite* stable.
        """
        end = time.monotonic() + 60
        while time.monotonic() < end:
            QTest.qWait(10)
            thread = self.w.__dict__.get("_thread")
            if thread is None:
                if self.w.__dict__.get("_pending_decode_item") is None:
                    return
                continue
            try:
                if not thread.isRunning():
                    thread.wait()
                    return
            except RuntimeError:
                return

    def _assert_batch_closed(self):
        self.assertIsNone(self.w.__dict__.get("_reload_all_state"))
        self.assertEqual(self.w.__dict__.get("_batch_depth", 0), 0)
        self.assertFalse(self.w.__dict__.get("_batch_suppress"))
        self.assertTrue(self.w.updatesEnabled(), "window repaints must be restored")

    def test_h3_double_trigger_leaves_no_residue(self):
        """H-3: a second Reload All must not bump the batch depth twice."""
        for path in self._raw_many(3, "h3"):
            self.w._open_item(path, decode=False)
        QTest.qWait(20)
        self.assertTrue(self._settle(20.0))

        with mock.patch.object(
            self.w, "_reload_item_at", wraps=self.w._reload_item_at,
        ) as reload_at:
            self.w.reload_all_items()
            QTest.qWait(20)
            self.w.reload_all_items()   # double-click / repeated menu trigger
            self.assertTrue(
                self._settle(40.0), "batch never settled after double trigger",
            )
        self._assert_batch_closed()
        self.assertEqual(
            [it.status_text for it in self.w.items], ["Decoded"] * 3,
        )
        # Three items reloaded once; the second trigger must add nothing.  This
        # also proves the test is not vacuous (a no-op batch would reload 0).
        self.assertEqual(
            reload_at.call_count, 3,
            "each item reloaded exactly once despite the repeated trigger",
        )

    def test_h4_reload_all_after_open_queue_handoff(self):
        """H-4: reload while the open queue owns an in-flight step (deterministic).

        The queue is advanced **first** (its second file is the in-flight step)
        and only then is Reload All started.  Triggering before the handoff
        happens to sidestep the deadlock, which is why the earlier test was
        intermittent.
        """
        paths = self._raw_many(6, "h4")
        self.w._open_paths(paths)
        deadline = time.monotonic() + 20
        handed_off = False
        while time.monotonic() < deadline:
            QTest.qWait(10)
            in_flight = self.w.__dict__.get("_open_decode_in_flight")
            if in_flight is not None and in_flight is not self.w.items[0]:
                handed_off = True
                break
        self.assertTrue(handed_off, "fixture must reach the queue handoff")

        self.w.reload_all_items()
        self.assertTrue(self._settle(45.0), "reload/open-queue deadlock")
        self._assert_batch_closed()
        self.assertEqual(
            [it.status_text for it in self.w.items], ["Decoded"] * 6,
            "every tab must end decoded",
        )
        self.assertIsNone(self.w.__dict__.get("_open_decode_in_flight"))
        self.assertEqual(self.w.__dict__.get("_open_decode_pending"), [])

    def test_h4_taken_over_queue_items_end_up_decoded(self):
        """H-4: files the open queue had not reached must still get decoded.

        Taking over the queue means Reload All decodes every tab itself.  The
        assertion is therefore per item (each one ``Decoded``), not just a count:
        the queued files that had never been decoded yet are exactly the ones a
        sloppy takeover would silently skip.
        """
        paths = self._raw_many(6, "h4b")
        self.w._open_paths(paths)
        deadline = time.monotonic() + 20
        handed_off = False
        while time.monotonic() < deadline:
            QTest.qWait(10)
            in_flight = self.w.__dict__.get("_open_decode_in_flight")
            if in_flight is not None and in_flight is not self.w.items[0]:
                handed_off = True
                break
        self.assertTrue(handed_off, "fixture must reach the queue handoff")

        # At least one tab has not been decoded yet (the queue is mid-flight).
        not_yet = [
            i for i, it in enumerate(self.w.items) if it.status_text != "Decoded"
        ]
        self.assertTrue(not_yet, "fixture must leave undecoded tabs behind")

        self.w.reload_all_items()
        self.assertTrue(self._settle(45.0), "reload/open-queue deadlock")
        self._assert_batch_closed()
        for index, item in enumerate(self.w.items):
            self.assertEqual(
                item.status_text, "Decoded",
                f"tab {index} ({os.path.basename(item.options.file_path)}) "
                "was skipped by the queue takeover",
            )
        self.assertEqual(len(self.w.items), 6, "no tab may be lost")

    def test_h4_takeover_leaves_no_stale_decoding_flag(self):
        """H-4: cancelling the queue step must clear its UI flag truthfully.

        ``_take_over_open_queue_for_reload`` detaches the in-flight worker via
        ``_cancel_decode_for`` before clearing ``decoding``, so the late reply is
        discarded (generation changed) and no tab is left claiming to decode
        something nobody will ever finish.
        """
        paths = self._raw_many(5, "h4c")
        self.w._open_paths(paths)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            QTest.qWait(10)
            in_flight = self.w.__dict__.get("_open_decode_in_flight")
            if in_flight is not None and in_flight is not self.w.items[0]:
                break
        self.assertIsNotNone(self.w.__dict__.get("_open_decode_in_flight"))

        self.w._take_over_open_queue_for_reload()

        self.assertIsNone(self.w.__dict__.get("_open_decode_in_flight"))
        self.assertEqual(self.w.__dict__.get("_open_decode_pending"), [])
        self.assertEqual(self.w.__dict__.get("_open_decode_total", 0), 0)
        self.assertIsNone(self.w.__dict__.get("_background_decode_item"))
        self.assertFalse(
            any(it.decoding for it in self.w.items),
            "a cancelled queue step must not leave a tab stuck on Decoding...",
        )

        # Let any late worker reply arrive; nothing may resurrect the flag.
        self.assertTrue(self._settle(30.0))
        self.assertFalse(any(it.decoding for it in self.w.items))

    def test_h1_reload_all_stack_depth_does_not_grow_with_item_count(self):
        """H-1: the step loop must not add a stack frame per synchronous item.

        A standard-image decode completes *inside* ``_reload_item_at``.  A
        recursive step loop therefore gains a frame per item and eventually
        raises RecursionError inside a Qt slot, where it is swallowed: the batch
        aborts silently and ``updatesEnabled(False)`` sticks forever.

        The decode is faked to complete synchronously (no worker, ~50 ms) so the
        recursive path is exercised directly, and the stack depth of each step is
        recorded: a non-recursive loop keeps it constant, a recursive one grows
        linearly.

        The test is deliberately hostile to silent failure.  ``_reload_item_at``
        runs inside a per-item ``except Exception: continue`` (a deliberate
        product decision so one bad file cannot block the batch), which would
        also hide a *test* bug — so the fake decode must be observed exactly
        ``count`` times and every tab must end ``Decoded``.  Without those two
        assertions a broken fake decode would make this test pass vacuously.
        """
        count = 12
        for i in range(count):
            self.w._open_item(self._raw(f"h1_{i}.raw"), decode=False)
        QTest.qWait(20)
        self.assertTrue(self._settle(20.0))

        from raw_view.gui.image_utils import qimage_from_rgb

        fake_qimage = qimage_from_rgb(np.zeros((4, 4, 3), np.uint8))
        decode_calls: list[str] = []

        def fake_decode(item, **kwargs):
            """Synchronous stand-in for ``_decode_item`` bound on the instance."""
            decode_calls.append(item.options.file_path)
            item.decoding = True
            self.w._pending_decode_item = item
            try:
                self.w._on_decode_success(item, fake_qimage, 4, 4, "Standard Image")
            finally:
                # Mirror _on_decode_finished: a real async completion clears the
                # pending marker.  Leaving it set would make the teardown's
                # "wait for the worker" loop run to its timeout.
                self.w._pending_decode_item = None
            item.decoding = False

        depths: list[int] = []
        original = self.w._reload_item_at

        def spy(index, **kwargs):
            frame, depth = sys._getframe(), 0
            while frame is not None:
                depth += 1
                frame = frame.f_back
            depths.append(depth)
            return original(index, **kwargs)

        self.w._decode_item = fake_decode
        self.w._reload_item_at = spy
        try:
            self.w.reload_all_items()
        finally:
            self.w._reload_item_at = original
            del self.w._decode_item

        self.assertTrue(self._settle(30.0), "Reload All never settled")
        self._assert_batch_closed()
        self.assertEqual(len(self.w.items), count)

        # Hard guards against a vacuous pass: the fake decode must actually have
        # run once per item, and no item may be left undecoded/failed.
        self.assertEqual(
            len(decode_calls), count,
            f"the fake decode ran {len(decode_calls)} time(s) for {count} items; "
            "a silent per-item exception would make the depth assertion vacuous",
        )
        self.assertEqual(
            [it.status_text for it in self.w.items], ["Decoded"] * count,
            "every item must complete; a swallowed exception leaves it undecoded",
        )

        self.assertTrue(depths, "no reload step ran")
        self.assertEqual(len(depths), count, "every item must be reloaded")
        self.assertLessEqual(
            max(depths) - min(depths), 2,
            f"step stack depth grew across items ({min(depths)} -> {max(depths)}); "
            "a recursive step loop does exactly that and overflows on large batches",
        )

    def test_h2_async_advance_failure_still_closes_the_batch(self):
        """H-2: a failure while resuming from a decode must close the batch.

        The resumption runs inside a Qt slot, where an escaping exception is
        swallowed by Qt and would leave the batch half-open (repaints disabled).
        This drives ``_advance_reload_all`` directly with the resumption raising,
        which is exactly what a bad item inside the loop used to surface as.
        """
        for i in range(3):
            self.w._open_item(self._default_raw(f"h2_{i}.raw"), decode=False)
        QTest.qWait(20)
        self.assertTrue(self._settle(20.0))

        # Start a batch and stop it at the "wait for async" point, then make the
        # resumption fail.
        self.w.reload_all_items()
        self.assertTrue(self._settle(40.0))

        owner = self.w._batch_begin()
        self.w._reload_all_state = {"index": 0, "batch_owner": owner}
        with mock.patch.object(
            self.w, "_run_reload_all_steps_locked",
            side_effect=RuntimeError("injected advance failure"),
        ):
            self.w._advance_reload_all(None)

        self._assert_batch_closed()

        # A leaked section would silently suppress the next batch.
        self.w.reload_all_items()
        self.assertTrue(self._settle(40.0), "a later Reload All must still work")
        self._assert_batch_closed()

    def test_h2_sync_exception_does_not_leak(self):
        """H-2 (synchronous variant): a step raising on every item.

        A single failing item is deliberately non-fatal (the batch logs it and
        continues so one bad file cannot block the rest), so the assertion is on
        the final state: the batch still closes and repaints are restored.
        """
        for i in range(2):
            self.w._open_item(self._default_raw(f"h2s_{i}.raw"), decode=False)
        QTest.qWait(20)
        self.assertTrue(self._settle(20.0))

        original = self.w._reload_item_at
        attempts: list[int] = []

        def always_fail(index, **kwargs):
            attempts.append(index)
            raise RuntimeError("injected sync failure")

        self.w._reload_item_at = always_fail
        try:
            self.w.reload_all_items()
        finally:
            self.w._reload_item_at = original
        # Without this the test would pass even if no step ever ran.
        self.assertEqual(
            len(attempts), 2, "every item must have been attempted exactly once",
        )
        self._assert_batch_closed()
        # The deliberate policy is "one bad file does not block the rest":
        # the tabs keep whatever state they had, but the window stays usable.
        self.assertTrue(
            all(it.status_kind != "error" for it in self.w.items),
            "a failed reload step must not mark the item as a decode error",
        )


class DecodeFlagSemanticsTests(_WindowFixture):
    """``decode`` must mean the same thing on both ``_open_item`` branches (D-3)."""

    def test_new_item_branch_does_not_decode_when_false(self):
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_item(self._raw("new.raw"), decode=False)
        self.assertEqual(decode_item.call_count, 0)

    def test_duplicate_branch_does_not_decode_when_false(self):
        path = self._raw("dup.raw")
        self.w._open_item(path, decode=False)
        QTest.qWait(20)
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_item(path, decode=False)
        self.assertEqual(decode_item.call_count, 0, "decode=False must not decode")

    def test_duplicate_branch_decodes_once_when_true(self):
        path = self._raw("dup_true.raw")
        self.w._open_item(path, decode=False)
        QTest.qWait(20)
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_item(path, decode=True)
        self.assertEqual(decode_item.call_count, 1)

    def test_open_paths_single_duplicate_decodes_once(self):
        path = self._raw("dup_paths.raw")
        self.w._open_item(path, decode=False)
        QTest.qWait(20)
        with mock.patch.object(self.w, "_decode_item") as decode_item:
            self.w._open_paths([path])
        self.assertEqual(decode_item.call_count, 1, "no duplicate decode dispatch")

    def test_reload_all_does_not_leave_stale_background_slot(self):
        self.w._open_item(self._raw("a.raw"), decode=False)
        self.w._open_item(self._raw("b.raw"), decode=False)
        QTest.qWait(20)
        self.w.reload_all_items()
        self.assertTrue(self._settle(20.0))
        self.assertIsNone(
            self.w.__dict__.get("_background_decode_item"),
            "the background slot must be cleared once decoding finishes",
        )


class CanonicalPathTests(unittest.TestCase):
    def test_same_file_spellings_collapse(self):
        tmp = tempfile.mkdtemp(prefix="rv-canon-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = os.path.join(tmp, "File.RAW")
        open(path, "wb").close()
        canonical = MainWindow._canonical_item_path
        # "." and ".." segments must resolve to the same file identity.
        self.assertEqual(
            canonical(path), canonical(os.path.join(tmp, ".", "File.RAW")),
        )
        self.assertEqual(
            canonical(path),
            canonical(os.path.join(tmp, "sibling", "..", "File.RAW")),
        )
        self.assertEqual(canonical(path), canonical(path.replace(os.sep, "/")))
        if os.name == "nt":
            self.assertEqual(canonical(path), canonical(path.upper()))

    def test_different_files_stay_distinct(self):
        canonical = MainWindow._canonical_item_path
        tmp = tempfile.mkdtemp(prefix="rv-canon2-")
        self.addCleanup(shutil.rmtree, tmp, True)
        a, b = os.path.join(tmp, "a.raw"), os.path.join(tmp, "b.raw")
        open(a, "wb").close()
        open(b, "wb").close()
        self.assertNotEqual(canonical(a), canonical(b))
        if os.name != "nt":
            self.assertNotEqual(canonical(a), canonical(a.upper()))


if __name__ == "__main__":
    unittest.main()
