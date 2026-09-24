"""Owned temporary QSettings setup shared by pytest and unittest discovery.

This module is test-side plumbing only.  It deliberately overwrites any shell
``RAW_VIEW_TEST_SETTINGS_FILE`` value so a test run cannot select an external
settings file.  Its temporary directory belongs to the current test process,
therefore cleanup cannot affect user settings or another xdist worker.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile


_SETTINGS_FILE: str | None = None


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
