"""Owned temporary QSettings + qt_material resource setup shared by both harnesses.

This module is test-side plumbing only.  It deliberately overwrites any shell
``RAW_VIEW_TEST_SETTINGS_FILE`` value so a test run cannot select an external
settings file.  Its temporary directories belong to the current test process,
therefore cleanup cannot affect user settings or another xdist worker.

The qt_material half solves a second, independent isolation bug: qt-material
regenerates its icon resources into the *shared* ``~/.qt_material`` directory on
every ``apply_stylesheet()`` call, and ``ResourseGenerator.__init__`` starts with
``shutil.rmtree`` + ``makedirs`` on those sub-folders.  Two pytest processes
running concurrently on one machine therefore delete each other's SVGs and the
other process randomly fails with
``FileNotFoundError: .../primary/branch-open.svg`` (v0.5.2 issue 5).

Redirecting ``qt_material.resources.generate.RESOURCES_PATH`` to a per-process
temporary directory keeps production behaviour untouched (the module default is
still ``~/.qt_material``) while giving every test process its own private tree.
This concerns the resource directory only; it does not touch qt-material's own
``GUI`` flag.  That flag is decided once at import time, and qt-material 2.17
only recognises PySide6/PyQt6/PySide2, so under PyQt5 it is already ``False`` —
pre-existing behaviour, unrelated to and unaffected by this isolation.  The
deferred patch below is still correct hygiene: it keeps our import from racing
the test module's own PyQt import.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile


_SETTINGS_FILE: str | None = None
_RESOURCES_DIR: str | None = None


def adopt_pytest_settings_file(path: str) -> None:
    """Record the file already created and owned by pytest's conftest."""
    global _SETTINGS_FILE
    _SETTINGS_FILE = path


def ensure_test_settings_file() -> str:
    """Install this process's isolated INI path before importing ``raw_view``.

    Pytest calls :func:`adopt_pytest_settings_file` first.  Under unittest
    discovery no conftest is loaded, so this function creates exactly one
    uniquely named directory for that discovery process and removes only that
    directory at interpreter exit.
    """
    global _SETTINGS_FILE
    if _SETTINGS_FILE is None:
        settings_dir = tempfile.mkdtemp(prefix="raw-view-unittest-settings-")
        _SETTINGS_FILE = os.path.join(settings_dir, "settings.ini")
        atexit.register(shutil.rmtree, settings_dir, ignore_errors=True)

    # Do not use setdefault: an inherited shell setting must never direct this
    # test process at another file.
    os.environ["RAW_VIEW_TEST_SETTINGS_FILE"] = _SETTINGS_FILE
    return _SETTINGS_FILE


def isolated_qt_material_resources() -> str:
    """Point qt_material's generated icon tree at a process-private directory.

    The patch is deferred until PyQt is already imported.  That is defensive
    ordering rather than a ``GUI`` fix: qt-material decides its ``GUI`` flag once
    at import time and (2.17) only recognises PySide6/PyQt6/PySide2, so under
    PyQt5 the flag is already ``False`` regardless of import order.  When PyQt is
    not loaded yet the directory is still created and the patch is applied on
    the next call (the pytest fixture and the test modules re-invoke it once Qt
    exists).
    """
    global _RESOURCES_DIR
    if _RESOURCES_DIR is None:
        _RESOURCES_DIR = tempfile.mkdtemp(prefix="raw-view-qt-material-")
        atexit.register(shutil.rmtree, _RESOURCES_DIR, ignore_errors=True)
    resources_dir = _RESOURCES_DIR

    if not any(name in sys.modules for name in ("PyQt5", "PyQt6", "PySide2", "PySide6")):
        return resources_dir  # defer: importing qt_material now would break it

    try:
        import qt_material
        import qt_material.resources.generate as _generate
    except Exception:  # pragma: no cover - qt_material absent in a slim env
        return resources_dir

    _generate.RESOURCES_PATH = resources_dir
    # ``qt_material.RESOURCES_PATH`` is the name some callers import directly;
    # keep both bindings pointing at the same private tree.
    if hasattr(qt_material, "RESOURCES_PATH"):
        qt_material.RESOURCES_PATH = resources_dir
    return resources_dir


def qt_material_resources_dir() -> str | None:
    """The private resource directory, or ``None`` if never installed."""
    return _RESOURCES_DIR
