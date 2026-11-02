"""Diagnostic harness for the CI-only "QThread destroyed while running" abort.

Runs on ubuntu-latest / Python 3.12 only (that is where it reproduces).  Each
experiment is deliberately minimal so the outcome maps to exactly one
hypothesis.  Not part of the product or the test suite.

Usage: python -u tools/diag_thread_abort.py <experiment>
"""

from __future__ import annotations

import gc
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_tmp = tempfile.mkdtemp(prefix="diag-")
os.environ["RAW_VIEW_TEST_SETTINGS_FILE"] = os.path.join(_tmp, "settings.ini")

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()

from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.models import AppSettings, DecodeOptions  # noqa: E402

#: One default-geometry RAW8 frame; enough to make a decode take real time.
_SIZE = 8 * 8


def _raw(name: str) -> str:
    path = os.path.join(_tmp, name)
    with open(path, "wb") as handle:
        handle.write(b"\x20" * _SIZE)
    return path


def _remember(window, path: str) -> None:
    window.settings.remember_file_params(
        path, DecodeOptions(image_type="RAW", format_name="RAW8", width=8, height=8),
    )


def _new_window() -> MainWindow:
    window = MainWindow()
    window.settings = AppSettings(
        QSettings(os.path.join(_tmp, "settings.ini"), QSettings.IniFormat)
    )
    window.show()
    _APP.processEvents()
    return window


def _pump(condition, timeout: float = 30.0) -> bool:
    """Pump until *condition()* holds (event-driven, never a fixed sleep)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
    return condition()


def _drained(window) -> bool:
    return (
        not window.__dict__.get("_open_decode_pending")
        and window.__dict__.get("_open_decode_in_flight") is None
        and window.__dict__.get("_pending_decode_item") is None
    )


def _preflight_case(window) -> None:
    """The exact run_case() body from the aborting test."""
    good1 = _raw("good1.raw")
    good2 = _raw("good2.raw")
    bad = _raw("bad.raw")
    _remember(window, good1)
    _remember(window, good2)
    # Unsupported format for RAW -> a preflight failure that never reaches the
    # async error slot (the q1 path that advances the queue).
    window.settings.remember_file_params(
        bad, DecodeOptions(image_type="RAW", format_name="YUYV", width=8, height=8),
    )
    window._open_paths([good1, bad, good2], source="explicit")
    if not _pump(lambda: _drained(window)):
        print("WARN: batch never drained", flush=True)
    decoded = sum(1 for item in window.items if item.current_display is not None)
    print(f"  decoded={decoded}/{len(window.items)}", flush=True)


def _teardown(window) -> None:
    _pump(lambda: _drained(window), timeout=30)
    window.close()
    _APP.processEvents()
    gc.collect()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: diag_thread_abort.py <experiment>", flush=True)
        return 2
    experiment = argv[1]
    print(f"== {experiment} ==", flush=True)
    print(f"python {sys.version.split()[0]}  platform {sys.platform}", flush=True)

    if experiment == "single-test":
        window = _new_window()
        _preflight_case(window)
        print("  case done", flush=True)
        _teardown(window)
        print("  OK", flush=True)

    elif experiment == "single-test-gc-off":
        # Hypothesis: the abort happens because the cyclic GC frees a QThread
        # whose only reference is its own finished->deleteLater cycle.
        gc.disable()
        window = _new_window()
        _preflight_case(window)
        print("  case done", flush=True)
        _teardown(window)
        gc.enable()
        print("  OK", flush=True)

    elif experiment == "single-test-thread-hooks":
        # Hypothesis: name the exact QThread that dies.  Records every
        # QThread.__init__ and prints the live set around the abort.
        from PyQt5.QtCore import QThread

        created: list = []
        original_init = QThread.__init__

        def tracking_init(self, *args, **kwargs):
            created.append(self)
            return original_init(self, *args, **kwargs)

        QThread.__init__ = tracking_init
        window = _new_window()
        _preflight_case(window)
        print(f"  QThreads created: {len(created)}", flush=True)
        for index, thread in enumerate(created):
            try:
                alive = thread.isRunning()
            except RuntimeError:
                alive = "deleted"
            print(f"    [{index}] id={id(thread)} running={alive}", flush=True)
        _teardown(window)
        print("  OK", flush=True)

    elif experiment in ("file-only", "file-only-preceded"):
        # Hypothesis: the abort needs a *previous* test's leaked QThread to be
        # collectable when the current test allocates.  Run the real test file
        # (or a couple of files) through unittest on this runner.
        import unittest

        suite = unittest.TestSuite()
        if experiment == "file-only-preceded":
            for name in ("test_app_logic", "test_ui_interactions",
                         "test_open_close_perf", "test_params_memory_pin"):
                suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
        suite.addTests(
            unittest.defaultTestLoader.loadTestsFromName("test_batch_open_and_chrome")
        )
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        print(f"  tests run={result.testsRun} failures={len(result.failures)} "
              f"errors={len(result.errors)}", flush=True)
        return 0 if result.wasSuccessful() else 1

    elif experiment == "faithful-runcase":
        # Hypothesis: the abort needs `close_all_items()` from a *previous*
        # run_case to strand a running thread, which the next case's decode
        # setup then collects.  Mirrors the test body exactly, one window.
        window = _new_window()
        for step in range(4):
            print(f"  -- run_case {step} --", flush=True)
            _preflight_case(window)
            window.close_all_items()
            _APP.processEvents()
            print(f"  close_all_items {step} done", flush=True)
        _teardown(window)
        print("  OK", flush=True)

    elif experiment == "three-tests":
        # Hypothesis: single runs pass, but consecutive windows abort (state
        # carried across cases, e.g. a held thread from the previous window).
        for round_index in range(3):
            print(f"  -- round {round_index} --", flush=True)
            window = _new_window()
            _preflight_case(window)
            _teardown(window)
            print(f"  round {round_index} done", flush=True)
            gc.collect()
        print("  OK", flush=True)

    else:
        print(f"unknown experiment: {experiment}", flush=True)
        return 2

    print("SURVIVED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
