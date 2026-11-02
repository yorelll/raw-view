"""Flush Qt's deferred-delete queue so test widgets actually get destroyed.

``deleteLater()`` only *posts* a ``QEvent.DeferredDelete``.  Qt runs those
events when the event loop unwinds to depth zero, which is why the familiar
teardown idiom::

    widget.close()
    widget.deleteLater()
    QApplication.processEvents()

does **not** destroy the widget: ``processEvents()`` re-enters the loop without
reaching depth zero.  Every window a test session "closes" therefore stays
alive on ``QApplication``'s object tree.

That leak is quadratic in wall-clock time, not just in memory.  ``MainWindow``
applies the global qt-material stylesheet during construction, and
``QApplication.setStyleSheet()`` re-polishes *every* live widget in the process.
The k-th window built in a session therefore costs O(k) to construct, so a
suite that builds ~100 full windows degenerates from seconds into tens of
minutes.

``destroy_widget()`` closes, schedules and then *flushes* the deletion, keeping
the session's live-widget count flat.

This module is deliberately dependency-free (no pytest import): CI runs
``python -m unittest discover -s tests``, which never loads ``conftest.py``.

Two guards make the rule enforceable instead of merely documented:

* the static guard (``tests/test_qt_cleanup_guard.py``) fails when a test file
  posts ``deleteLater()`` without using :func:`destroy_widget`;
* :func:`assert_no_widget_leak` is the runtime counterpart for the unittest
  path (``conftest.py`` installs the same check for pytest).
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

__all__ = [
    "WIDGET_LEAK_TOLERANCE",
    "assert_no_widget_leak",
    "begin_widget_leak_check",
    "destroy_widget",
    "end_widget_leak_check",
    "flush_deferred_deletes",
    "live_widget_count",
    "widget_leak_growth",
    "widget_leak_message",
]

_LOGGER = logging.getLogger(__name__)

# Slack for widgets a test intentionally shows and lets Qt parent to a
# longer-lived object.  Kept small: a stub tab plus its context menu is 8
# widgets, which a tolerance of 8 would also have accepted, and a real
# ``MainWindow`` is ~144 widgets, so any genuine leak is far above this bound.
WIDGET_LEAK_TOLERANCE = 4


def _ignore_missing_method_error(call) -> None:
    """Run *call*, tolerating objects that are gone or never initialised.

    The tests call cleanup from ``tearDown``/``finally`` blocks, where any of
    these is normal and must not turn into a spurious failure:

    - the C++ object is already destroyed -> ``RuntimeError``;
    - the wrapper is a ``MainWindow.__new__`` stub whose ``__init__`` never ran
      -> ``RuntimeError`` from the Qt base class;
    - the attribute is absent on a lightweight duck-typed stub -> ``TypeError``
      or ``AttributeError``.
    - a widget emitting any other exception from ``close()`` (``ValueError``,
      a custom widget's override, ...).

    Cleanup must never turn a passing test into an error, so every exception is
    contained; it is *logged* rather than swallowed so the cause stays visible
    when diagnosing a flaky teardown.
    """
    try:
        call()
    except Exception:  # noqa: BLE001 - cleanup must not raise
        _LOGGER.debug("ignored exception during widget cleanup", exc_info=True)


def flush_deferred_deletes() -> None:
    """Run Qt's pending deferred deletions now.

    ``processEvents()`` alone does *not* do this; see the module docstring.
    Safe to call repeatedly and safe with no application instance.
    """
    app = QApplication.instance()
    if app is None:
        return
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def destroy_widget(widget, *, close: bool = True) -> None:
    """Close *widget*, schedule its deletion, and flush it immediately.

    Args:
        widget: Any ``QWidget``.  ``None`` and duck-typed stubs are ignored.
        close: Set ``False`` when ``close()`` must not run (for example
            ``closeEvent`` regression tests that assert on a single close
            dispatch), while still flushing the deferred deletion.
    """
    if widget is None:
        return

    if close:
        close_method = getattr(widget, "close", None)
        if close_method is not None:
            _ignore_missing_method_error(close_method)
    delete_later = getattr(widget, "deleteLater", None)
    if delete_later is not None:
        _ignore_missing_method_error(delete_later)

    app = QApplication.instance()
    if app is None:
        return

    # ``qWait`` performs a real event-loop iteration, which is what lets Qt
    # execute the posted DeferredDelete events.  ``qWait(0)`` is enough (the
    # loop only needs to run, not sleep) and costs ~4x less than ``qWait(1)``.
    # The explicit send afterwards is a belt-and-braces second pass for objects
    # posted by child destructors.
    QTest.qWait(0)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


# ---------------------------------------------------------------------------
# Runtime guard (L3)
# ---------------------------------------------------------------------------


def live_widget_count() -> int:
    """Return the number of live widgets, or ``0`` when Qt is not initialised."""
    app = QApplication.instance()
    return 0 if app is None else len(app.allWidgets())


def widget_leak_message(label: str, before: int, after: int, growth: int) -> str:
    """Build the failure text shared by the pytest and unittest guards."""
    return (
        f"{label}: live Qt widgets grew by {growth} "
        f"({before} -> {after}, tolerance {WIDGET_LEAK_TOLERANCE}).\n"
        "The test did not destroy every widget it created.  A leaked MainWindow "
        "keeps ~144 widgets alive and forces app.setStyleSheet() to re-polish all "
        "of them, which makes the suite O(n^2) slower (measured: 58 min -> 37 s).\n"
        "Fix: replace ``w.close(); w.deleteLater(); app.processEvents()`` with\n"
        "    from _qt_cleanup import destroy_widget\n"
        "    destroy_widget(w)\n"
        "(use ``destroy_widget(w, close=False)`` when close() must not run)."
    )


def widget_leak_growth(before: int, after: int) -> int:
    """Growth above the tolerance; a non-positive value means the test is clean."""
    return (after - before) - WIDGET_LEAK_TOLERANCE


def _not_armed_message(label: str) -> str:
    return (
        f"{label}: widget-leak guard was never armed (no baseline).\n"
        "The check cannot tell a clean test from a leaking one, so it must not "
        "pass silently.\n"
        "Fix: call ``begin_widget_leak_check()`` in ``setUp`` -- or, when a base "
        "class already does it, call ``super().setUp()`` as the FIRST line of "
        "your overriding ``setUp``.  A subclass that overrides ``setUp`` without "
        "chaining to ``super()`` silently disables L3 for the whole class."
    )


def assert_no_widget_leak(label: str, before: int | None = None) -> int:
    """Assert the live-widget count did not grow beyond tolerance since *before*.

    Args:
        label: Test identifier used in the failure message.
        before: Baseline captured when the test started.  When omitted it is
            read from the module-level baseline that
            :func:`begin_widget_leak_check` captured, so a shared unittest base
            class can arm the guard from ``setUp``.

    Returns:
        The current live-widget count, so callers can chain a baseline forward.

    Raises:
        AssertionError: When the growth exceeds :data:`WIDGET_LEAK_TOLERANCE`,
            or when no baseline exists (an unarmed guard is a bug in the test,
            not a passing test -- failing loudly is the only way a forgotten
            ``super().setUp()`` cannot silently disable the guard).
    """
    after = live_widget_count()
    if before is None:
        before = _armed_baseline
    if before is None:
        raise AssertionError(_not_armed_message(label))
    if widget_leak_growth(before, after) > 0:
        raise AssertionError(widget_leak_message(label, before, after, after - before))
    return after


# Module-level baseline so a shared unittest base class can arm the guard in
# ``setUp`` without storing state on every subclass.  ``None`` disables it.
_armed_baseline: int | None = None


def begin_widget_leak_check() -> int:
    """Capture the live-widget count and arm :func:`assert_no_widget_leak`.

    Returns the captured baseline so a caller may keep its own copy.
    """
    global _armed_baseline
    _armed_baseline = live_widget_count()
    return _armed_baseline


def end_widget_leak_check(label: str) -> int:
    """Verify the armed baseline still holds and disarm the check."""
    global _armed_baseline
    baseline, _armed_baseline = _armed_baseline, None
    return assert_no_widget_leak(label, baseline)
