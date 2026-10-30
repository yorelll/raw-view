"""Pytest-wide isolation for real-user QSettings, plus the widget-leak guard.

Every test process gets an INI-backed temporary settings file before raw_view is
imported.  AppSettings honours this test-only environment path, so GUI tests
cannot mutate HKCU\\Software\\yorelll\\raw-view (including sensor presets).

The second half of this module is the runtime half of the teardown rule (see
``_qt_cleanup``): a test that leaves widgets alive is failed at teardown rather
than silently making the rest of the session quadratically slower.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_SETTINGS_DIR = tempfile.mkdtemp(prefix="raw-view-pytest-settings-")
_SETTINGS_FILE = os.path.join(_SETTINGS_DIR, "settings.ini")
# Assign rather than setdefault: an inherited shell value must not redirect a
# test run to an unrelated INI file.
os.environ["RAW_VIEW_TEST_SETTINGS_FILE"] = _SETTINGS_FILE
atexit.register(shutil.rmtree, _SETTINGS_DIR, ignore_errors=True)

# This module is loaded before pytest imports test modules.  Make its owned
# path available to unittest-style test modules too, without creating a second
# resource in the pytest process.
from _settings_isolation import (  # noqa: E402
    adopt_pytest_settings_file,
    isolated_qt_material_resources,
)

adopt_pytest_settings_file(_SETTINGS_FILE)
# qt-material regenerates its SVG icon tree (rmtree + makedirs) into the shared
# ~/.qt_material on every apply_stylesheet().  Concurrent pytest processes on one
# machine thereby delete each other's files and fail with a random
# FileNotFoundError; give each process a private resource tree instead.  The
# directory is created now and the module patch is (re)applied by the autouse
# fixture below, i.e. only once PyQt has been imported by the test module.
isolated_qt_material_resources()


# ---------------------------------------------------------------------------
# L3: runtime widget-leak guard (pytest path)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_qt_material_resources():
    """Re-assert the per-process qt_material tree once PyQt is imported."""
    isolated_qt_material_resources()
    yield


@pytest.fixture(autouse=True)
def _assert_no_widget_leak():
    """Fail a test whose live Qt widget count grew beyond tolerance.

    The check runs after the test function *and* its fixtures/tearDown have
    finished, so a correctly cleaned-up GUI test measures 0 growth.  Tests that
    legitimately create widgets without destroying them are the ones this guard
    is meant to catch (each leaked ``MainWindow`` is ~144 widgets and makes the
    next ``setStyleSheet()`` re-polish all of them).

    Skipped automatically when Qt is not initialised, so non-GUI tests and
    platforms without PyQt5 pay nothing.
    """
    try:
        from _qt_cleanup import (
            WIDGET_LEAK_TOLERANCE,  # noqa: F401  (documents the bound)
            live_widget_count,
            widget_leak_growth,
            widget_leak_message,
        )
    except Exception:  # pragma: no cover - PyQt5 unavailable in this env
        yield
        return

    try:
        before = live_widget_count()
    except Exception:  # pragma: no cover - defensive, Qt torn down mid-run
        yield
        return

    yield

    try:
        after = live_widget_count()
    except Exception:  # pragma: no cover - defensive
        return

    if widget_leak_growth(before, after) > 0:
        pytest.fail(
            widget_leak_message("widget leak", before, after, after - before),
            pytrace=False,
        )

