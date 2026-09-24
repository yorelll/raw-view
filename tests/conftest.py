"""Pytest-wide isolation for real-user QSettings.

Every test process gets an INI-backed temporary settings file before raw_view is
imported.  AppSettings honours this test-only environment path, so GUI tests
cannot mutate HKCU\\Software\\yorelll\\raw-view (including sensor presets).
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
from _settings_isolation import adopt_pytest_settings_file  # noqa: E402

adopt_pytest_settings_file(_SETTINGS_FILE)
