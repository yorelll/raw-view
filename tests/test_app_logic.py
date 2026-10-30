"""Tests for core decode/data-model/main-window logic (group A fixes).

Covers, without spinning up the full GUI:

  * M-1  — ``DecodeOptions`` carries preview_mode / bayer_pattern and the
           panel <-> item sync helpers round-trip them.
  * H-2  — ``decode_current`` reads only the current frame's byte interval
           instead of slurping the whole file (and size-mismatch gating).
  * H-1  — generation-counter stale-result dropping in the async decode path.
  * M-2  — error messages include file/frame/offset context.
  * M-4  — frame-nav bar colours come from the theme palette (models.py),
           framenav.py no longer hard-codes dark colours.
  * M-10 — directory drag-drop scans are filtered to supported extensions and
           >N files triggers a "too many" flag.
  * L-2  — ``ViewerItem.rotation_angle`` dead field removed.

Requires a Qt platform plugin; we force the offscreen platform so the module
imports and widget construction work headless.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest import mock

import numpy as np

# unittest discovery does not load pytest's conftest.py.  Establish its owned
# test INI before any raw_view import; under pytest this reuses conftest's path.
from _settings_isolation import (
    ensure_test_settings_file,
    isolated_qt_material_resources,
)
from _qt_cleanup import destroy_widget  # noqa: E402

ensure_test_settings_file()
# Per-process qt_material icon tree (concurrent pytest processes otherwise
# delete each other's generated SVGs).
isolated_qt_material_resources()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

# The offscreen platform lets us construct plain Qt widgets (FrameNavBar) and
# bare MainWindow instances without opening a window.
_APP = QApplication.instance() or QApplication([])

from raw_view.gui import app as app_module  # noqa: E402
from raw_view.gui.app import (  # noqa: E402
    SUPPORTED_EXTENSIONS,
    MainWindow,
    _is_dangerous_extension,
    _is_supported_file,
    _looks_binary_candidate,
    _scan_directory,
    handle_drop_paths,
)
from raw_view.gui.framenav import FrameNavBar  # noqa: E402
from raw_view.gui.dialogs.preset import PresetManagerDialog  # noqa: E402
from raw_view.models import (  # noqa: E402
    BAYER_PATTERNS,
    THEME_PALETTES,
    DecodeOptions,
    PRESET_CATEGORY_ALL,
    PRESET_CATEGORY_RAW,
    PRESET_CATEGORY_STANDARD,
    PRESET_CATEGORY_YUV,
    AppSettings,
    SensorPreset,
    ViewerItem,
    build_ui_stylesheet,
    filter_sensor_presets_by_category,
    is_supported_preset_format,
    is_supported_preset_format_for_type,
    normalize_preset_image_type,
    search_sensor_presets,
)


def _new_window() -> MainWindow:
    """Create a bare MainWindow Python object without running its build.

    ``MainWindow.__new__`` creates the Qt wrapper without calling ``__init__``
    (which would build menus/toolbars/panels). The methods under test
    (decode_current, _on_decode_*, _save/_load_panel_to_item) only touch plain
    Python attributes plus a side-effect-free stub panel, keeping the tests
    hermetic and fast.
    """
    w = MainWindow.__new__(MainWindow)
    w.state_status = SimpleNamespace(setText=lambda _t: None)
    return w


class _StubPanel:
    """Minimal stand-in for ControlPanel: get/set_values round-trip a dict."""

    def __init__(self, values: dict | None = None) -> None:
        self.values = {
            "image_type": "RAW",
            "format_name": "RAW8",
            "width": 2,
            "height": 2,
            "alignment": "msb",
            "endianness": "little",
            "offset": 0,
            "preview_mode": "Bayer Color",
            "bayer_pattern": "RGGB",
        }
        if values:
            self.values.update(values)

    def get_values(self) -> dict:
        return dict(self.values)

    def set_values(self, **kwargs) -> None:
        self.values.update(kwargs)

    def reset_preset_selection(self) -> None:
        pass

    def set_zoom_percent(self, percent: int) -> None:
        pass


class _FakeUrl:
    """Mime-url stand-in exposing only ``toLocalFile()``."""

    def __init__(self, path: str) -> None:
        self._path = path

    def toLocalFile(self) -> str:
        return self._path


# ── Sensor preset format filters ─────────────────────────────────────────


class _PresetSettings:
    """Small AppSettings-compatible preset source for dialog/window tests."""

    def __init__(self, presets, usage=None):
        self.sensor_presets = list(presets)
        self.preset_usage = dict(usage or {})
        self.preset_category = PRESET_CATEGORY_ALL
        self.usage_writes: list[str] = []
        self.usage_renames: list[tuple[str, str]] = []

    def get_sensor_preset(self, name):
        return next((p for p in self.sensor_presets if p.name == name), None)

    def replace_sensor_presets(self, presets):
        self.sensor_presets = list(presets)
        self.prune_preset_usage()

    def record_preset_use(self, name):
        self.usage_writes.append(name)
        record = self.preset_usage.setdefault(name, {"last_used": 0, "count": 0})
        record["last_used"] = record.get("last_used", 0) + 1
        record["count"] = record.get("count", 0) + 1

    def prune_preset_usage(self, valid_names=None):
        valid = {p.name for p in self.sensor_presets} if valid_names is None else set(valid_names)
        self.preset_usage = {
            name: record for name, record in self.preset_usage.items() if name in valid
        }

    def rename_preset_usage(self, old, new):
        self.usage_renames.append((old, new))
        if old in self.preset_usage:
            self.preset_usage[new] = self.preset_usage.pop(old)


class AppSettingsIsolationTests(unittest.TestCase):
    """Test and production settings selection use explicit, safe contracts."""

    @staticmethod
    def _settings_probe_script() -> str:
        return (
            "import os, unittest\n"
            "from PyQt5.QtCore import QSettings\n"
            "from raw_view.models import AppSettings\n"
            "settings = AppSettings()\n"
            "store = settings._store\n"
            "print('|'.join((str(int(store.format())), store.organizationName(), "
            "store.applicationName(), store.fileName())))\n"
        )

    def test_unittest_import_does_not_switch_production_to_temp_ini(self):
        """An embedded production host may import unittest before raw_view."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env.pop("RAW_VIEW_TEST_SETTINGS_FILE", None)
        env.pop("PYTHONPATH", None)
        proc = subprocess.run(
            [sys.executable, "-c", self._settings_probe_script()],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        settings_format, organization, application, filename = proc.stdout.strip().split("|", 3)
        self.assertEqual(settings_format, str(int(QSettings.NativeFormat)))
        self.assertEqual(organization, "yorelll")
        self.assertEqual(application, "raw-view")
        self.assertNotIn("raw-view-test-settings-", filename)
        self.assertNotIn("raw-view-unittest-settings-", filename)
        self.assertNotIn("raw-view-pytest-settings-", filename)

    def test_explicit_test_environment_uses_owned_ini_store(self):
        isolated_path = os.environ["RAW_VIEW_TEST_SETTINGS_FILE"]
        settings = AppSettings()
        self.assertEqual(settings._store.format(), QSettings.IniFormat)
        self.assertEqual(os.path.normcase(settings._store.fileName()), os.path.normcase(isolated_path))

    def test_malformed_test_environment_falls_back_to_native_without_writing(self):
        with mock.patch.dict(
            os.environ,
            {"RAW_VIEW_TEST_SETTINGS_FILE": "not-a-real-parent/settings.ini"},
            clear=False,
        ):
            settings = AppSettings()
        self.assertEqual(settings._store.format(), QSettings.NativeFormat)
        self.assertEqual(settings._store.organizationName(), "yorelll")
        self.assertEqual(settings._store.applicationName(), "raw-view")

    def test_injected_store_wins_over_test_environment(self):
        temp_dir = tempfile.mkdtemp(prefix="rv-settings-injected-")
        try:
            injected_path = os.path.join(temp_dir, "injected.ini")
            injected_store = QSettings(injected_path, QSettings.IniFormat)
            settings = AppSettings(injected_store)
            settings.save_sensor_preset(SensorPreset(name="test", format_name="RAW12"))
            injected_store.sync()
            self.assertIs(settings._store, injected_store)
            self.assertEqual([p.name for p in settings.sensor_presets], ["test"])
            self.assertTrue(os.path.isfile(injected_path))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class PresetUsageSettingsTests(unittest.TestCase):
    """Recent-use and browse-category persistence stays isolated and robust."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rv-usage-")
        self.settings = AppSettings(
            QSettings(os.path.join(self.tmp, "settings.ini"), QSettings.IniFormat)
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_usage_round_trip_and_corruption_tolerance(self):
        self.settings.save_sensor_preset(SensorPreset(name="a", image_type="RAW", format_name="RAW8"))
        self.settings.record_preset_use("a", timestamp=100.0)
        self.settings.record_preset_use("a", timestamp=200.0)
        usage = self.settings.preset_usage
        self.assertEqual(usage["a"], {"last_used": 200.0, "count": 2})
        self.settings._store.setValue("presets/usage", "{not json")
        self.assertEqual(self.settings.preset_usage, {})
        self.settings._store.setValue("presets/usage", '{"a": {"last_used": "bad"}}')
        self.assertEqual(self.settings.preset_usage, {})

    def test_delete_prunes_usage_and_rename_preserves_it(self):
        for name in ("old", "keep"):
            self.settings.save_sensor_preset(
                SensorPreset(name=name, image_type="RAW", format_name="RAW8")
            )
        self.settings.record_preset_use("old", timestamp=10.0)
        self.settings.record_preset_use("keep", timestamp=20.0)
        self.settings.rename_preset_usage("old", "new")
        self.assertEqual(set(self.settings.preset_usage), {"new", "keep"})
        # Mirror what the manager does on Save: the renamed record replaces the
        # original list, and pruning keeps only names that still exist.
        self.settings.replace_sensor_presets([
            SensorPreset(name="new", image_type="RAW", format_name="RAW8"),
            SensorPreset(name="keep", image_type="RAW", format_name="RAW8"),
        ])
        self.assertEqual(set(self.settings.preset_usage), {"new", "keep"})
        self.settings.delete_sensor_preset("new")
        self.assertEqual(set(self.settings.preset_usage), {"keep"})

    def test_replace_prunes_unknown_usage_and_browse_category_is_safe(self):
        self.settings.record_preset_use("ghost", timestamp=5.0)
        self.settings.replace_sensor_presets([
            SensorPreset(name="real", image_type="RAW", format_name="RAW8"),
        ])
        self.assertEqual(self.settings.preset_usage, {})
        self.assertEqual(self.settings.preset_category, PRESET_CATEGORY_ALL)
        self.settings.preset_category = PRESET_CATEGORY_YUV
        self.assertEqual(self.settings.preset_category, PRESET_CATEGORY_YUV)
        self.settings._store.setValue("presets/browse_category", "bogus")
        self.assertEqual(self.settings.preset_category, PRESET_CATEGORY_ALL)

    def test_recent_first_browse_sort(self):
        from raw_view.models import sort_presets_for_browse
        presets = [
            SensorPreset(name="Zulu", image_type="RAW", format_name="RAW8"),
            SensorPreset(name="alpha", image_type="RAW", format_name="RAW8"),
            SensorPreset(name="beta", image_type="RAW", format_name="RAW8"),
        ]
        ordered = sort_presets_for_browse(
            presets, {"beta": {"last_used": 50.0}, "Zulu": {"last_used": 90.0}},
        )
        self.assertEqual([p.name for p in ordered], ["Zulu", "beta", "alpha"])


class PersistentPresetSearchPathTests(unittest.TestCase):
    """Saved records -> MainWindow selection-only refresh path."""

    def test_saved_preset_is_selectable_from_injected_store(self):
        temp_dir = tempfile.mkdtemp(prefix="rv-preset-search-")
        try:
            isolated = AppSettings(
                QSettings(os.path.join(temp_dir, "settings.ini"), QSettings.IniFormat)
            )
            isolated.save_sensor_preset(
                SensorPreset(name="test", image_type="RAW", format_name="RAW12")
            )
            w = MainWindow()
            try:
                w.settings = isolated
                w._refresh_preset_combo(current="test")
                index = w.panel.preset_combo.findData("test")
                self.assertGreaterEqual(index, 0)
                selected: list[str] = []
                w.panel.presetSelected.connect(selected.append)
                w.panel.preset_combo.setCurrentIndex(index)
                w.panel._on_preset_activated(index)
                self.assertEqual(selected, ["test"])
                # Usage was persisted because this was a real selection signal.
                self.assertEqual(isolated.preset_usage["test"]["count"], 1)
            finally:
                destroy_widget(w)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class PresetFilterModelTests(unittest.TestCase):
    def setUp(self):
        self.presets = [
            SensorPreset(name="Alpha RAW8", image_type="RAW", format_name="RAW8"),
            SensorPreset(name="beta RAW12", image_type="RAW", format_name="RAW12"),
            SensorPreset(name="Gamma YUYV", image_type="YUV", format_name="YUYV"),
            SensorPreset(name="Imported custom", image_type="RAW", format_name="VendorRAW"),
        ]

    def test_category_filter_is_coarse_and_hides_internal_malformed_values(self):
        self.assertEqual(
            [p.name for p in filter_sensor_presets_by_category(self.presets, PRESET_CATEGORY_ALL)],
            [p.name for p in self.presets],
        )
        self.assertEqual(
            [p.name for p in filter_sensor_presets_by_category(self.presets, PRESET_CATEGORY_RAW)],
            ["Alpha RAW8", "beta RAW12"],
        )
        self.assertEqual(
            [p.name for p in filter_sensor_presets_by_category(self.presets, PRESET_CATEGORY_YUV)],
            ["Gamma YUYV"],
        )
        self.assertEqual(
            filter_sensor_presets_by_category(self.presets, PRESET_CATEGORY_STANDARD), [],
        )
        # Unknown VendorRAW remains visible under All/search but cannot become a
        # misleading decoder-format filter entry.
        self.assertIn("Imported custom", [
            p.name for p in search_sensor_presets(self.presets, "custom")
        ])

    def test_name_search_is_case_insensitive_across_all_categories(self):
        self.assertEqual(
            [p.name for p in search_sensor_presets(self.presets, "BETA")],
            ["beta RAW12"],
        )
        self.assertEqual(
            [p.name for p in search_sensor_presets(self.presets, "GAMMA")],
            ["Gamma YUYV"],
        )

    def test_bracketed_structured_search_supports_aliases_and_casefolding(self):
        presets = self.presets + [
            SensorPreset(name="testpreset", image_type="YUV", format_name="YUYV"),
            SensorPreset(name="testpreset raw", image_type="RAW", format_name="RAW10 Packed"),
        ]
        self.assertEqual(
            [p.name for p in search_sensor_presets(presets, "<type:yuv>testpreset")],
            ["testpreset"],
        )
        self.assertEqual(
            [p.name for p in search_sensor_presets(presets, "<T:YUV>TESTPRESET")],
            ["testpreset"],
        )
        self.assertEqual(
            [p.name for p in search_sensor_presets(presets, "<t:raw + f:raw10 packed>testpreset")],
            ["testpreset raw"],
        )
        self.assertEqual(
            [p.name for p in search_sensor_presets(
                presets, "< type : raw+format : raw10packed > TESTPRESET"
            )],
            ["testpreset raw"],
        )

    def test_structured_search_ands_name_and_fails_closed_for_invalid_controls(self):
        presets = self.presets + [
            SensorPreset(name="testpreset", image_type="YUV", format_name="YUYV"),
            SensorPreset(name="legacy test", image_type="RAW", format_name="VendorRAW"),
        ]
        self.assertEqual(
            [p.name for p in search_sensor_presets(presets, "<type:yuv>test")],
            ["testpreset"],
        )
        # Brackets are mandatory: this is a literal name query, not a filter.
        self.assertEqual(search_sensor_presets(presets, "type:yuv"), [])
        for invalid in (
            "<type:camera>test", "<type:standard>test", "<bit:12>test",
            "<type:yuv + type:raw>test", "<type:yuv +>test",
            "<type:yuv>test<format:yuyv>", "<type:yuv",
        ):
            with self.subTest(query=invalid):
                self.assertEqual(search_sensor_presets(presets, invalid), [])
        # Explicit format still safely finds legacy identifiers without making
        # them a category or making their decoder path valid.
        self.assertEqual(
            [p.name for p in search_sensor_presets(presets, "<f:vendorraw>legacy")],
            ["legacy test"],
        )

    def test_type_aware_format_validation_rejects_cross_type_pairs(self):
        for image_type, format_name in (
            ("RAW", "YUYV"),
            ("YUV", "RAW8"),
            ("RAW", "N/A"),
            ("Standard Image", "RAW8"),
            ("Unknown", "RAW8"),
            ("RAW", "VendorRAW"),
        ):
            with self.subTest(image_type=image_type, format_name=format_name):
                self.assertFalse(
                    is_supported_preset_format_for_type(image_type, format_name),
                )
        for image_type, format_name in (
            ("RAW", "RAW8"),
            ("RAW", "RAW14 Packed"),
            ("YUV", "YUYV"),
            ("YUV", "YOnly12"),
            ("Standard Image", "N/A"),
        ):
            with self.subTest(image_type=image_type, format_name=format_name):
                self.assertTrue(
                    is_supported_preset_format_for_type(image_type, format_name),
                )


class LegacyPresetCompatibilityTests(unittest.TestCase):
    """Unknown/non-string stored formats survive filtering, editing, and loading."""

    def _dialog(self, preset):
        return PresetManagerDialog(_PresetSettings([preset]))

    def test_from_dict_normalizes_non_string_format_values_and_filters_safely(self):
        for raw, expected in ((12, "12"), (None, ""), ([], "")):
            with self.subTest(raw=raw):
                preset = SensorPreset.from_dict({"name": "bad legacy", "format_name": raw})
                self.assertEqual(preset.format_name, expected)
                # Malformed values remain safely searchable but never become a
                # category option or a decoder-format filter label.
                self.assertEqual(search_sensor_presets([preset], "bad"), [preset])
        direct_bad = SensorPreset(name="direct bad", format_name=12)
        self.assertEqual(search_sensor_presets([direct_bad], "direct"), [direct_bad])
        self.assertEqual(SensorPreset.from_dict(None).format_name, "RAW12")

    def test_from_dict_normalizes_non_string_image_type_values(self):
        for raw, expected in ((12, "12"), (None, ""), ([], ""), ({}, "")):
            with self.subTest(raw=raw):
                preset = SensorPreset.from_dict({"name": "bad type", "image_type": raw})
                self.assertEqual(preset.image_type, expected)
                self.assertEqual(normalize_preset_image_type(raw), expected)
                self.assertFalse(
                    is_supported_preset_format_for_type(preset.image_type, preset.format_name),
                )

    def test_manager_unknown_image_type_loads_and_round_trips_without_typeerror(self):
        settings = _PresetSettings([
            SensorPreset(name="bad type", image_type="12", format_name="RAW8", width=111),
        ])
        dlg = PresetManagerDialog(settings)
        try:
            self.assertEqual(dlg.type_combo.currentText(), "12")
            self.assertEqual(dlg.format_combo.currentText(), "RAW8")
            dlg.width_spin.setValue(222)
            dlg._on_save_clicked()
            self.assertEqual(settings.sensor_presets[0].image_type, "12")
            self.assertEqual(settings.sensor_presets[0].format_name, "RAW8")
            self.assertEqual(settings.sensor_presets[0].width, 222)
        finally:
            destroy_widget(dlg)

    def test_manager_unknown_format_round_trips_after_width_rename_and_save(self):
        settings = _PresetSettings([
            SensorPreset(name="legacy", image_type="RAW", format_name="VendorRAW", width=111),
        ])
        dlg = PresetManagerDialog(settings)
        try:
            self.assertEqual(dlg.format_combo.currentText(), "VendorRAW")
            dlg.width_spin.setValue(222)
            self.assertEqual(dlg._presets[0].format_name, "VendorRAW")
            dlg._presets[0].name = "legacy renamed"  # rename model path
            dlg._on_save_clicked()
            self.assertEqual(settings.sensor_presets[0].name, "legacy renamed")
            self.assertEqual(settings.sensor_presets[0].format_name, "VendorRAW")
            self.assertEqual(settings.sensor_presets[0].width, 222)
            # Export/import representation is still the literal original value.
            payload = settings.sensor_presets[0].to_dict()
            restored = SensorPreset.from_dict(payload)
            self.assertEqual(restored.format_name, "VendorRAW")
        finally:
            destroy_widget(dlg)


class PresetManagerFormatFilterTests(unittest.TestCase):
    def setUp(self):
        self.settings = _PresetSettings([
            SensorPreset(name="Alpha RAW8", image_type="RAW", format_name="RAW8"),
            SensorPreset(name="beta RAW12", image_type="RAW", format_name="RAW12"),
            SensorPreset(name="Gamma YUYV", image_type="YUV", format_name="YUYV"),
        ])
        self.dlg = PresetManagerDialog(self.settings)

    def tearDown(self):
        destroy_widget(self.dlg)

    def _names(self):
        return [self.dlg.list_widget.item(i).text() for i in range(self.dlg.list_widget.count())]

    def test_search_ignores_category_and_empty_browse_uses_category(self):
        choices = [self.dlg.category_filter_combo.itemText(i)
                   for i in range(self.dlg.category_filter_combo.count())]
        self.assertEqual(choices, ["All", "RAW", "YUV", "Standard images"])
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_RAW)
        self.assertEqual(self._names(), ["Alpha RAW8", "beta RAW12"])
        self.dlg.search_edit.setText("GAMMA")
        self.assertEqual(self._names(), ["Gamma YUYV"])
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_STANDARD)
        self.assertEqual(self._names(), ["Gamma YUYV"], "search remains cross-category")
        self.dlg.search_edit.clear()
        self.assertEqual(self._names(), [])
        self.assertEqual(self.dlg._current_index, -1)
        self.assertFalse(self.dlg.delete_btn.isEnabled())

    def test_manager_supports_space_and_plus_structured_search(self):
        self.settings.sensor_presets.append(
            SensorPreset(name="testpreset", image_type="YUV", format_name="YUYV")
        )
        self.dlg._presets = list(self.settings.sensor_presets)
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_RAW)
        for query in ("<type:yuv>testpreset", "<t:yuv + f:yuyv>testpreset"):
            with self.subTest(query=query):
                self.dlg.search_edit.setText(query)
                self.assertEqual(self._names(), ["testpreset"])
        # A bare condition block intentionally lists every YUV/YUYV preset.
        self.dlg.search_edit.setText("<type:yuv f:yuyv>")
        self.assertEqual(self._names(), ["Gamma YUYV", "testpreset"])
        self.dlg.search_edit.setText("<f:raw10 packed>testpreset")
        self.assertEqual(self._names(), [])

    def test_manager_sorts_after_filter_and_keeps_canonical_mapping(self):
        self.settings.sensor_presets.extend([
            SensorPreset(name="zeta yuv", image_type="YUV", format_name="NV12"),
            SensorPreset(name="中文预设", image_type="YUV", format_name="NV12"),
            SensorPreset(name="Alpha yuv", image_type="YUV", format_name="NV12"),
        ])
        self.dlg._presets = list(self.settings.sensor_presets)
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_YUV)
        # Category browse (no search): Latin A-Z, Chinese afterwards by pinyin.
        self.assertEqual(self._names(), ["Alpha yuv", "Gamma YUYV", "zeta yuv", "中文预设"])
        row = self._names().index("zeta yuv")
        self.dlg.list_widget.setCurrentRow(row)
        source = self.dlg._current_index
        self.assertEqual(self.dlg._presets[source].name, "zeta yuv")
        self.dlg.width_spin.setValue(1234)
        self.assertEqual(self.dlg._presets[source].width, 1234)
        self.assertEqual(
            next(p for p in self.dlg._presets if p.name == "zeta yuv").width, 1234,
        )

    def test_editing_format_refreshes_category_rows_and_source_mapping(self):
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_RAW)
        self.assertEqual(self._names(), ["Alpha RAW8", "beta RAW12"])
        row = self._names().index("beta RAW12")
        self.dlg.list_widget.setCurrentRow(row)
        self.assertEqual(self.dlg._current_index, 1)  # canonical source index
        self.dlg.format_combo.setCurrentText("RAW8")
        self.assertIn("beta RAW12", self._names())
        beta = next(p for p in self.dlg._presets if p.name == "beta RAW12")
        self.assertEqual(beta.format_name, "RAW8")

    def test_add_and_import_style_refresh_keep_active_category_safe(self):
        self.dlg.category_filter_combo.setCurrentText(PRESET_CATEGORY_YUV)
        self.assertEqual(self._names(), ["Gamma YUYV"])
        self.dlg._presets.append(
            SensorPreset(name="New NV12", image_type="YUV", format_name="NV12")
        )
        self.dlg._refresh_list(select_index=len(self.dlg._presets) - 1)
        self.assertEqual(self._names(), ["Gamma YUYV", "New NV12"])
        self.assertEqual(self.dlg._current_index, 3)

    def test_search_hint_tooltip_and_first_focus_hint(self):
        self.assertIn("<t:yuv f:yuyv>", self.dlg.search_edit.toolTip())
        self.assertIn("<type:raw format:raw10 packed>name", self.dlg.search_edit.toolTip())
        self.assertIn("name", self.dlg.search_edit.toolTip())
        from PyQt5.QtCore import QEvent
        from PyQt5.QtGui import QFocusEvent
        with mock.patch("raw_view.gui.dialogs.preset.QToolTip.showText") as show:
            self.dlg.eventFilter(self.dlg.search_edit, QFocusEvent(QEvent.FocusIn))
            self.dlg.eventFilter(self.dlg.search_edit, QFocusEvent(QEvent.FocusIn))
        self.assertEqual(show.call_count, 1, "focus hint must show only once")


class MainWindowPresetWorkflowTests(unittest.TestCase):
    """Main-window preset selection: fill values, record usage, never auto-Apply."""

    def setUp(self):
        self.w = MainWindow()
        self.w.settings = _PresetSettings([
            SensorPreset(name="raw10test", image_type="RAW", format_name="RAW10 Packed", width=64),
            SensorPreset(name="yuvpreset", image_type="YUV", format_name="YUYV", width=32),
        ])
        self.w._refresh_preset_combo()
        self.applied: list[object] = []
        self.w._on_apply_clicked = lambda *a, **k: self.applied.append((a, k))
        self.w._current_item = lambda: None

    def tearDown(self):
        destroy_widget(self.w)

    def test_user_selection_fills_values_records_usage_and_never_applies(self):
        index = self.w.panel.preset_combo.findData("raw10test")
        self.assertGreaterEqual(index, 0)
        self.w.panel.preset_combo.setCurrentIndex(index)
        self.w.panel._on_preset_activated(index)
        _APP.processEvents()
        self.assertEqual(self.w.panel.format_combo.currentText(), "RAW10 Packed")
        self.assertEqual(self.w.panel.width_spin.value(), 64)
        self.assertEqual(self.w.settings.usage_writes, ["raw10test"])
        self.assertEqual(self.applied, [])
        # Usage reorder is deferred out of the selection call stack (P1-1), so
        # the rows are untouched until the combo stops being navigated.  The
        # model contains real presets only (the empty state is a painted hint),
        # so the selected preset is found by identity rather than a fixed index.
        combo = self.w.panel.preset_combo
        self.assertEqual(combo.currentData(), "raw10test")
        self.assertEqual(
            [combo.itemText(i) for i in range(combo.count())],
            ["raw10test", "yuvpreset"],
        )
        self.assertEqual(combo.currentIndex(), combo.findData("raw10test"))
        # Programmatic activation with no combo focus/navigation may apply the
        # deferred reorder immediately; the identity must still be preserved.
        from PyQt5.QtWidgets import QLineEdit
        other = QLineEdit(self.w)
        other.show()
        other.setFocus()
        _APP.processEvents()
        self.assertEqual(self.w.panel.preset_combo.currentData(), "raw10test")
        self.assertFalse(self.w.panel._preset_reorder_pending)

    def test_programmatic_refresh_does_not_record_usage(self):
        self.w._refresh_preset_combo(current="yuvpreset")
        self.assertEqual(self.w.settings.usage_writes, [])
        self.assertEqual(self.w.panel.preset_combo.currentData(), "yuvpreset")

    def test_filter_change_persists_category_via_main_window(self):
        self.w.panel._on_preset_filter_action(
            self.w.panel._preset_filter_actions[PRESET_CATEGORY_YUV]
        )
        self.assertEqual(self.w.settings.preset_category, PRESET_CATEGORY_YUV)
        self.assertEqual(
            [self.w.panel.preset_combo.itemText(i)
             for i in range(self.w.panel.preset_combo.count())
             if self.w.panel.preset_combo.itemData(i)],
            ["yuvpreset"],
        )


# ── Save Display default filename ─────────────────────────────────────────


class MainWindowLegacyPresetRefreshTests(unittest.TestCase):
    """Manager refresh must not silently replace an active deleted legacy format."""

    def test_deleted_active_legacy_preset_keeps_panel_item_until_explicit_choice(self):
        root = tempfile.mkdtemp(prefix="rv-legacy-refresh-")
        try:
            path = os.path.join(root, "frame.raw")
            with open(path, "wb") as f:
                f.write(b"\x00" * 64)
            w = MainWindow()
            try:
                item = ViewerItem()
                item.options.file_path = path
                item.options.image_type = "RAW"
                item.options.format_name = "VendorA"
                item.options.width = item.options.height = 2
                # The panel/item state is real; pin the current-item accessor
                # so this integration test need not construct a visual tab page.
                w._current_item = lambda: item
                w.panel.set_enabled(True)
                w.panel.set_values(image_type="RAW", format_name="VendorA", width=2, height=2)
                self.assertEqual(w.panel.format_combo.currentText(), "VendorA")
                self.assertFalse(w.panel.apply_btn.isEnabled())
                states = []
                w._set_state = lambda *args: states.append(args)
                w.settings = _PresetSettings([])
                w._refresh_preset_combo()  # manager deletion / refresh path
                self.assertEqual(w.panel.format_combo.currentText(), "VendorA")
                self.assertFalse(w.panel.apply_btn.isEnabled())
                self.assertEqual(item.options.format_name, "VendorA")
                self.assertEqual(states, [], "refresh must not mark dirty/change state")
                # Ctrl+A / button guard cannot run Apply while legacy is disabled.
                w._start_async_decode = mock.Mock()
                with mock.patch.object(app_module.QMessageBox, "critical"):
                    w._on_apply_clicked()
                w._start_async_decode.assert_not_called()
                self.assertEqual(item.options.format_name, "VendorA")
                # An explicit legal choice removes VendorA and then Apply writes
                # RAW8 intentionally (not through Qt's implicit fallback).
                w.panel.set_format("RAW8")
                self.assertEqual(w.panel.format_combo.findText("VendorA"), -1)
                self.assertTrue(w.panel.apply_btn.isEnabled())
                w._save_panel_to_item(item)
                self.assertEqual(item.options.format_name, "RAW8")
            finally:
                destroy_widget(w)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class SaveDisplayDefaultPathTests(unittest.TestCase):
    def _window_for(self, file_path: str):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = file_path
        item.current_display = np.zeros((2, 2, 3), dtype=np.uint8)
        w._current_item = lambda: item
        w.settings = SimpleNamespace(save_dpi=300)
        w._set_state = lambda *_a, **_k: None
        return w, item

    def test_save_dialog_receives_source_directory_and_stem_png(self):
        cases = [
            ("/captures/Y8I_0x0b.bin", "/captures/Y8I_0x0b.png"),
            ("/测试/307测试图切debug out短曝.png", "/测试/307测试图切debug out短曝.png"),
            ("/captures/capture.raw.bin", "/captures/capture.raw.png"),
            ("/captures/output", "/captures/output.png"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                w, _ = self._window_for(source)
                with mock.patch.object(app_module.QFileDialog, "getSaveFileName", return_value=("", "")) as dialog:
                    w.save_display()
                # Qt uses native path separators on Windows; the source
                # directory/stem contract is separator-agnostic.
                self.assertEqual(dialog.call_args.args[2], os.path.normpath(expected))

    def test_save_dialog_empty_source_uses_usable_fallback(self):
        w, _ = self._window_for("")
        with mock.patch.object(app_module.QFileDialog, "getSaveFileName", return_value=("", "")) as dialog:
            w.save_display()
        self.assertEqual(dialog.call_args.args[2], "image.png")

    def test_jpeg_filter_keeps_existing_bare_name_completion(self):
        w, _ = self._window_for("/captures/input.bin")
        saved = []

        class _Qimg:
            def setDotsPerMeterX(self, _value):
                pass

            def setDotsPerMeterY(self, _value):
                pass

            def save(self, path, fmt):
                saved.append((path, fmt))
                return True

        w._qimage_from_rgb = lambda _img: _Qimg()
        with mock.patch.object(
            app_module.QFileDialog,
            "getSaveFileName",
            return_value=("/exports/chosen", "JPEG (*.jpg *.jpeg)"),
        ):
            w.save_display()
        self.assertEqual(saved, [("/exports/chosen.jpg", "JPEG")])


# ── Initial automatic decode errors ───────────────────────────────────────


class InitialDecodeErrorTests(unittest.TestCase):
    def _window_with_pending(self, generation: int, show_error: bool):
        w = _new_window()
        item = ViewerItem()
        w._decode_generation = generation
        w._pending_decode_item = item
        w._pending_decode_show_error = show_error
        w._current_item = lambda: item
        states = []
        w._set_state = lambda *args: states.append(args)
        return w, item, states

    def test_open_item_decode_true_starts_silent_initial_decode(self):
        """Direct initial open passes show_error=False without needing a real worker."""
        root = tempfile.mkdtemp(prefix="rv-initial-open-")
        try:
            path = os.path.join(root, "small.raw")
            with open(path, "wb") as f:
                f.write(b"\x00" * 8)
            w = MainWindow()
            captured = []
            w.decode_current = lambda *a, **kw: captured.append((a, kw))
            try:
                w._open_item(path, decode=True)
                self.assertEqual(captured, [((), {"show_error": False})])
            finally:
                destroy_widget(w)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_silent_async_worker_error_updates_state_without_modal(self):
        w, _item, states = self._window_with_pending(1, show_error=False)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w._on_decode_error(1, "data too short")
        critical.assert_not_called()
        self.assertEqual(states[-1], ("Decode failed", "error"))
        self.assertIsNone(w._pending_decode_item)

    def test_explicit_async_worker_error_still_shows_modal(self):
        w, _item, states = self._window_with_pending(1, show_error=True)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w._on_decode_error(1, "data too short")
        critical.assert_called_once_with(w, "Decode Failed", "data too short")
        self.assertEqual(states[-1], ("Decode failed", "error"))

    def test_stale_generation_cannot_cross_contaminate_error_intent(self):
        w, item, _states = self._window_with_pending(2, show_error=True)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            # First request was silent (generation 1); its late error is stale
            # and must not consume/mute generation 2's explicit error.
            w._on_decode_error(1, "old silent error")
            critical.assert_not_called()
            self.assertIs(w._pending_decode_item, item)
            w._on_decode_error(2, "new explicit error")
            critical.assert_called_once_with(w, "Decode Failed", "new explicit error")

        # Reverse order: a stale explicit error must not pop a dialog while the
        # current silent initial request remains quiet.
        w, item, _states = self._window_with_pending(4, show_error=False)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w._on_decode_error(3, "old explicit error")
            w._on_decode_error(4, "new silent error")
            critical.assert_not_called()
            self.assertIsNone(w._pending_decode_item)

    def test_standard_image_initial_silent_then_explicit_error(self):
        item = ViewerItem()
        item.options.file_path = "bad.png"
        item.options.image_type = "Standard Image"
        opts = item.options
        w = _new_window()
        states = []
        w._set_state = lambda *args: states.append(args)
        with mock.patch.object(app_module, "load_bgr_image", side_effect=ValueError("bad image")), \
             mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w._decode_standard_image(item, opts, show_error=False)
            critical.assert_not_called()
            self.assertEqual(states[-1], ("Decode failed", "error"))
            w._decode_standard_image(item, opts, show_error=True)
            critical.assert_called_once_with(w, "Decode Failed", "bad image")

    def test_decode_current_forwards_explicit_intent_to_worker_start(self):
        root = tempfile.mkdtemp(prefix="rv-decode-intent-")
        try:
            path = os.path.join(root, "tiny.raw")
            with open(path, "wb") as f:
                f.write(b"\x00")
            item = ViewerItem()
            item.options.file_path = path
            item.options.image_type = "RAW"
            item.options.format_name = "RAW8"
            item.options.width = item.options.height = 2
            w = _new_window()
            w.panel = _StubPanel({"width": 2, "height": 2})
            w._current_item = lambda: item
            captured = []
            w._start_async_decode = lambda *args, **kwargs: captured.append((args, kwargs))
            w.decode_current(show_error=False)
            args, kwargs = captured[-1]
            self.assertFalse(args[5], "show_error is forwarded positionally")
            self.assertFalse(kwargs["user_apply"], "decode_current is not Apply")
            w._warn_size_mismatch = lambda *_args: True
            w.decode_current(warn_mismatch=True, show_error=True)
            args, kwargs = captured[-1]
            self.assertTrue(args[5])
            self.assertFalse(kwargs["user_apply"], "direct call did not set Apply")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ── M-1: DecodeOptions preview/bayer fields ──────────────────────────────


class DecodeOptionsPersistenceTests(unittest.TestCase):
    def test_defaults(self):
        opts = DecodeOptions()
        self.assertEqual(opts.preview_mode, "Bayer Color")
        self.assertEqual(opts.bayer_pattern, "RGGB")
        self.assertIn(opts.preview_mode, {"Bayer Color", "Grayscale"})
        self.assertIn(opts.bayer_pattern, BAYER_PATTERNS)

    def test_round_trip_via_dataclass(self):
        opts = DecodeOptions(
            file_path="/tmp/a.raw",
            image_type="RAW",
            format_name="RAW12",
            width=1920,
            height=1080,
            alignment="lsb",
            endianness="big",
            offset=99,
            preview_mode="Grayscale",
            bayer_pattern="BGGR",
        )
        restored = DecodeOptions(**asdict(opts))
        self.assertEqual(restored, opts)
        self.assertEqual(restored.preview_mode, "Grayscale")
        self.assertEqual(restored.bayer_pattern, "BGGR")

    def test_panel_item_sync_persists_preview_bayer(self):
        """_save_panel_to_item / _load_item_to_panel round-trip the new fields."""
        w = _new_window()
        w.panel = _StubPanel({"preview_mode": "Grayscale", "bayer_pattern": "GBRG"})
        w._loading_item = False
        w.zoom_status = SimpleNamespace(setText=lambda _t: None)

        item = ViewerItem()
        w._save_panel_to_item(item)
        self.assertEqual(item.options.preview_mode, "Grayscale")
        self.assertEqual(item.options.bayer_pattern, "GBRG")

        # Mutate the item away from the panel, then load the item back onto
        # the panel — the panel must reflect the item again.
        item.options.preview_mode = "Bayer Color"
        item.options.bayer_pattern = "RGGB"
        w._load_item_to_panel(item)
        self.assertEqual(w.panel.values["preview_mode"], "Bayer Color")
        self.assertEqual(w.panel.values["bayer_pattern"], "RGGB")


# ── H-2: on-demand (per-frame) file reads ────────────────────────────────


class DecodePreflightInvalidationTests(unittest.TestCase):
    """Every valid decode request invalidates old worker UI effects before preflight."""

    def _window_with_old_worker(self, path: str):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = path
        item.options.image_type = "RAW"
        item.options.format_name = "RAW32"
        item.options.width = item.options.height = 65_535
        w.panel = _StubPanel({
            "image_type": "RAW", "format_name": "RAW32",
            "width": 65_535, "height": 65_535,
        })
        w._current_item = lambda: item
        w._decode_generation = 41
        w._pending_decode_item = item
        w._pending_decode_show_error = False
        w._thread = object()
        w._worker = object()
        w._pending_cache_key = "old-key"
        states = []
        w._set_state = lambda *args: states.append(args)
        return w, item, states

    def _assert_old_signals_are_stale(self, w, item, old_result, states_before):
        w._on_decode_finished(41, old_result)
        w._on_decode_error(41, "late old worker error")
        self.assertEqual(states_before, states_before[:], "late worker must not alter failure state")
        self.assertIsNone(item.current_display)

    def test_frame_too_large_preflight_invalidates_old_silent_worker(self):
        path = os.path.join(tempfile.gettempdir(), "rv-preflight-too-large.raw")
        with open(path, "wb") as f:
            f.write(b"\x00")
        try:
            w, item, states = self._window_with_old_worker(path)
            old_result = SimpleNamespace(
                display_array=np.ones((2, 2), dtype=np.uint8), qimage=None,
                width=2, height=2, format_name="RAW8",
            )
            with mock.patch.object(app_module.QMessageBox, "critical") as critical:
                w.decode_current(warn_mismatch=True, show_error=True)
                critical.assert_called_once()
            self.assertGreater(w._decode_generation, 41)
            self.assertIsNone(w._pending_decode_item)
            self.assertEqual(states[-1], ("Decode rejected: frame too large", "error"))
            states_before = list(states)
            w._on_decode_finished(41, old_result)
            with mock.patch.object(app_module.QMessageBox, "critical") as critical:
                w._on_decode_error(41, "late old worker error")
                critical.assert_not_called()
            self.assertEqual(states, states_before)
            self.assertIsNone(item.current_display)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_mismatch_cancel_preflight_invalidates_old_worker(self):
        path = os.path.join(tempfile.gettempdir(), "rv-preflight-mismatch.raw")
        with open(path, "wb") as f:
            f.write(b"\x00")
        try:
            w, item, states = self._window_with_old_worker(path)
            # Small but non-huge RAW8 forces the size-mismatch prompt path.
            item.options.format_name = "RAW8"
            item.options.width = item.options.height = 2
            w.panel.values.update({"format_name": "RAW8", "width": 2, "height": 2})
            w._warn_size_mismatch = lambda *_args: False
            old_result = SimpleNamespace(
                display_array=np.ones((2, 2), dtype=np.uint8), qimage=None,
                width=2, height=2, format_name="RAW8",
            )
            w.decode_current(warn_mismatch=True, show_error=True)
            self.assertGreater(w._decode_generation, 41)
            self.assertIsNone(w._pending_decode_item)
            states_before = list(states)
            w._on_decode_finished(41, old_result)
            with mock.patch.object(app_module.QMessageBox, "critical") as critical:
                w._on_decode_error(41, "late old error")
                critical.assert_not_called()
            self.assertEqual(states, states_before)
            self.assertIsNone(item.current_display)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class DecodeTypeFormatDefenceTests(unittest.TestCase):
    """Runtime decode rejects legacy image_type/format mismatches even if UI is bypassed."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-type-format-")
        self._path = os.path.join(self._tmp, "frame.bin")
        with open(self._path, "wb") as f:
            f.write(b"\x00" * 64)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_mismatch(self, image_type: str, format_name: str):
        item = ViewerItem()
        item.options.file_path = self._path
        w = _new_window()
        w.panel = _StubPanel({
            "image_type": image_type,
            "format_name": format_name,
            "width": 2,
            "height": 2,
        })
        w._current_item = lambda: item
        w._start_async_decode = mock.Mock()
        states = []
        w._set_state = lambda *args: states.append(args)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w.decode_current(show_error=True)
        return item, w, states, critical

    def test_raw_yuyv_is_rejected_before_worker_decode(self):
        item, w, states, critical = self._run_mismatch("RAW", "YUYV")
        w._start_async_decode.assert_not_called()
        critical.assert_called_once()
        self.assertIn("Unsupported format 'YUYV' for RAW", critical.call_args.args[2])
        self.assertEqual(states[-1], ("Decode failed: unsupported format", "error"))
        self.assertEqual(item.options.format_name, "YUYV")

    def test_all_cross_type_pairs_are_defensively_rejected(self):
        for image_type, format_name in (
            ("YUV", "RAW8"),
            ("RAW", "N/A"),
            ("Standard Image", "RAW8"),
        ):
            with self.subTest(image_type=image_type, format_name=format_name):
                _item, w, states, critical = self._run_mismatch(image_type, format_name)
                w._start_async_decode.assert_not_called()
                critical.assert_called_once()
                self.assertIn(f"Unsupported format '{format_name}' for {image_type}",
                              critical.call_args.args[2])
                self.assertEqual(states[-1], ("Decode failed: unsupported format", "error"))

    def test_silent_initial_mismatch_updates_state_without_modal(self):
        item = ViewerItem()
        item.options.file_path = self._path
        w = _new_window()
        w.panel = _StubPanel({"image_type": "RAW", "format_name": "YUYV"})
        w._current_item = lambda: item
        w._start_async_decode = mock.Mock()
        states = []
        w._set_state = lambda *args: states.append(args)
        with mock.patch.object(app_module.QMessageBox, "critical") as critical:
            w.decode_current(show_error=False)
        critical.assert_not_called()
        w._start_async_decode.assert_not_called()
        self.assertEqual(states[-1], ("Decode failed: unsupported format", "error"))


class OnDemandReadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-on-demand-")
        self.path = os.path.join(self._tmp, "frames.raw")
        # 10 frames x 4 bytes; frame i contains the byte value i repeated.
        with open(self.path, "wb") as f:
            f.write(b"".join(bytes([i]) * 4 for i in range(10)))
        self.w = _new_window()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_remaining_bytes_helper(self):
        self.assertEqual(self.w._remaining_bytes(self.path, 0), 40)
        self.assertEqual(self.w._remaining_bytes(self.path, 8), 32)
        self.assertEqual(self.w._remaining_bytes(self.path, 100), 0)
        self.assertIsNone(self.w._remaining_bytes(os.path.join(self._tmp, "nope.raw"), 0))

    def test_read_frame_data_reads_only_requested_slice(self):
        self.assertEqual(self.w._read_frame_data(self.path, 0, 4), b"\x00\x00\x00\x00")
        self.assertEqual(self.w._read_frame_data(self.path, 8, 4), b"\x02\x02\x02\x02")
        self.assertEqual(self.w._read_frame_data(self.path, 36, 4), b"\x09\x09\x09\x09")
        # beyond EOF returns what's left, not an error
        self.assertEqual(self.w._read_frame_data(self.path, 38, 4), b"\x09\x09")

    def test_decode_current_reads_only_current_frame(self):
        """decode_current must hand the worker exactly one frame slice."""
        item = ViewerItem()
        item.options.file_path = self.path
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 0
        item.current_frame = 2  # third frame → byte offset 8

        captured: list = []
        w = _new_window()
        w.panel = _StubPanel()
        w._current_item = lambda: item
        w._start_async_decode = lambda *args, **kwargs: captured.append((args, kwargs))

        w.decode_current()

        self.assertEqual(len(captured), 1)
        args, kwargs = captured[0]
        data, dec_item, opts, eff, cache_key, show_error = args
        self.assertFalse(kwargs["user_apply"], "non-Apply decode must not remember")
        # 4 bytes for one frame, not the whole 40-byte file
        self.assertEqual(data, b"\x02\x02\x02\x02")
        self.assertEqual(len(data), 4)
        self.assertEqual(eff, 8)
        self.assertIs(dec_item, item)
        self.assertIs(opts, item.options)
        self.assertIsInstance(cache_key, str)
        self.assertTrue(show_error)

    def test_decode_current_frame_offset_with_base_offset(self):
        item = ViewerItem()
        item.options.file_path = self.path
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 4  # skip one frame at the start
        item.current_frame = 1  # effective offset: 4 + 1*4 = 8

        captured: list = []
        # The panel carries the base offset — decode_current saves the panel
        # into the item before computing the effective offset.
        w = _new_window()
        w.panel = _StubPanel({"offset": 4})
        w._current_item = lambda: item
        w._start_async_decode = lambda *args, **kwargs: captured.append((args, kwargs))

        w.decode_current()

        self.assertEqual(len(captured), 1)
        args, _kwargs = captured[0]
        data, _item, _opts, eff, cache_key, show_error = args
        self.assertEqual(eff, 8)
        self.assertEqual(data, b"\x02\x02\x02\x02")
        self.assertIsInstance(cache_key, str)
        self.assertTrue(show_error)

    def test_apply_warn_mismatch_gates_decode(self):
        """Only Apply pops the mismatch dialog; declining aborts the decode."""
        truncated = os.path.join(self._tmp, "truncated.raw")
        with open(truncated, "wb") as f:
            f.write(b"\x00\x00\x00")  # 3 bytes < one 4-byte frame

        item = ViewerItem()
        item.options.file_path = truncated
        item.options.image_type = "RAW"
        item.options.format_name = "RAW8"
        item.options.width = 2
        item.options.height = 2
        item.options.offset = 0
        item.current_frame = 0

        captured: list = []
        w = _new_window()
        w.panel = _StubPanel()
        w._current_item = lambda: item
        w._start_async_decode = lambda *args, **kwargs: captured.append((args, kwargs))

        # User declines → nothing decoded
        w._warn_size_mismatch = lambda *_: False
        w.decode_current(warn_mismatch=True)
        self.assertEqual(captured, [])

        # User accepts → decode proceeds with the 3 available bytes
        w._warn_size_mismatch = lambda *_: True
        w.decode_current(warn_mismatch=True)
        self.assertEqual(len(captured), 1)
        self.assertEqual(len(captured[0][0][0]), 3)


# ── H-1: generation-counter stale-result dropping ────────────────────────


class GenerationGuardTests(unittest.TestCase):
    def test_should_apply_accepts_current_generation_and_item(self):
        w = _new_window()
        item_a = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertTrue(w._should_apply_decode(3, item_a))

    def test_should_apply_rejects_stale_and_future_generations(self):
        w = _new_window()
        item_a = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertFalse(w._should_apply_decode(2, item_a))  # stale
        self.assertFalse(w._should_apply_decode(4, item_a))  # future
        self.assertFalse(w._should_apply_decode(3, None))    # no item

    def test_should_apply_rejects_different_item(self):
        w = _new_window()
        item_a = ViewerItem()
        item_b = ViewerItem()
        w._decode_generation = 3
        w._pending_decode_item = item_a
        self.assertFalse(w._should_apply_decode(3, item_b))  # other tab

    def test_stale_finished_result_is_dropped(self):
        """A late result from an abandoned decode must not touch the item."""
        w = _new_window()
        item = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = item
        w._current_item = lambda: item

        applied: list = []
        w._on_decode_success = lambda *args, **kwargs: applied.append((args, kwargs))
        fake = SimpleNamespace(
            display_array=None, qimage=None, width=12, height=34, format_name="RAW8"
        )

        # Latest generation + same item → applied.  The width/height are now
        # committed by _on_decode_success (stubbed here), so assert that the
        # result is forwarded instead of the item being mutated eagerly.
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)
        args, kwargs = applied[0]
        self.assertEqual(args[2:4], (12, 34))

        # A newer decode started (generation bumped); the old worker's late
        # result carrying gen=1 must be silently discarded.
        w._decode_generation = 2
        w._pending_decode_item = item
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1, "stale generation result applied")

    def test_stale_error_is_dropped(self):
        w = _new_window()
        item = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = item
        w._current_item = lambda: item

        with mock.patch.object(app_module, "QMessageBox") as mb:
            w._on_decode_error(1, "boom")
            mb.critical.assert_called_once()
            # now stale
            w._decode_generation = 2
            w._on_decode_error(1, "boom late")
            self.assertEqual(mb.critical.call_count, 1)

    def test_finished_for_different_item_is_dropped(self):
        w = _new_window()
        shown = ViewerItem()
        w._decode_generation = 1
        w._pending_decode_item = shown
        w._current_item = lambda: shown

        applied: list = []
        w._on_decode_success = lambda *args, **kwargs: applied.append((args, kwargs))
        fake = SimpleNamespace(
            display_array=None, qimage=None, width=1, height=1, format_name="RAW8"
        )
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)

        # Switch to a different tab while the worker is still running.
        other = ViewerItem()
        w._current_item = lambda: other
        # The result was for `shown`, which is no longer visible -- dropped.
        w._on_decode_finished(1, fake)
        self.assertEqual(len(applied), 1)


# ── M-2: error messages carry decode context ─────────────────────────────


class ErrorContextTests(unittest.TestCase):
    def test_describe_source_mentions_file_frame_offset(self):
        w = _new_window()
        worker = app_module.DecodeWorker()
        from raw_view.formats import ImageSpec

        worker._data = b"\x00" * 4
        # spec.offset 固定为 0（data 已是按 source offset 切出的帧）；
        # 真实文件内 offset 独立记在 _source_offset 供错误信息定位。
        worker._spec = ImageSpec(2, 2, offset=0)
        worker._format_name = "RAW8"
        worker._file_path = os.path.join("d", "sensor", "cap.bin")
        worker._frame_index = 2
        worker._source_offset = 8
        text = worker._describe_source()
        self.assertIn("cap.bin", text)
        self.assertIn("frame 2", text)
        self.assertIn("offset=8", text)

    def test_error_signal_carries_generation_and_context(self):
        w = _new_window()
        from raw_view.formats import ImageSpec

        worker = app_module.DecodeWorker()
        errors: list = []

        def _on_error(gen, msg):
            errors.append((gen, msg))

        worker.error.connect(_on_error)
        worker._data = b"\x00\x00"  # too short for a 4-byte RAW8 frame
        worker._spec = ImageSpec(2, 2, offset=0)
        worker._format_name = "RAW8"
        worker._generation = 7
        worker._file_path = "cap.bin"
        worker._frame_index = 0
        worker.run()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0][0], 7)
        self.assertIn("cap.bin", errors[0][1])


# ── M-4: frame-nav theming lives in the palette ──────────────────────────


class FramenavThemingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_palettes_define_nav_button_colors(self):
        for theme in ("light", "dark"):
            p = THEME_PALETTES[theme]
            for key in (
                "nav_button_bg", "nav_button_border", "nav_button_text",
                "nav_button_hover_bg", "nav_button_pressed_bg",
                "nav_button_disabled_bg", "nav_button_disabled_text",
            ):
                self.assertIn(key, p, f"{key} missing from {theme} palette")

    def test_stylesheets_embed_theme_specific_nav_colors(self):
        for theme in ("light", "dark"):
            sheet = build_ui_stylesheet(theme, 13)
            p = THEME_PALETTES[theme]
            self.assertIn("QWidget#frameNavBar QPushButton", sheet)
            self.assertIn(p["nav_button_bg"], sheet)
            self.assertIn(p["nav_button_text"], sheet)
            self.assertIn(":hover", sheet)
            self.assertIn(":pressed", sheet)
            self.assertIn(":disabled", sheet)
            # light text must not leak into the dark stylesheet and vice-versa
            if theme == "light":
                self.assertNotIn("#2A2D4A", sheet)
            else:
                self.assertIn("#2A2D4A", sheet)

    def test_framenav_no_longer_sets_hardcoded_stylesheet(self):
        nav = FrameNavBar()
        self.assertEqual(nav.styleSheet(), "")
        # layout/metrics are preserved
        self.assertEqual(nav.first_btn.width(), 36)
        self.assertEqual(nav.first_btn.height(), 32)


# ── M-10: directory drag-drop scanning ───────────────────────────────────


class DirectoryDropTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-drop-")
        self.root = os.path.join(self._tmp, "folder")
        os.makedirs(os.path.join(self.root, "sub"))
        for name in ("a.raw", "b.png", "notes.txt"):
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(b"x")
        with open(os.path.join(self.root, "sub", "c.yuv"), "wb") as f:
            f.write(b"x")
        # unsupported nested file must be skipped too
        with open(os.path.join(self.root, "sub", "cache.tmp"), "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_supported_extension_set(self):
        for ext in (".png", ".jpg", ".jpeg", ".bmp", ".yuv", ".nv12", ".raw", ".bin"):
            self.assertIn(ext, SUPPORTED_EXTENSIONS)
        self.assertNotIn(".txt", SUPPORTED_EXTENSIONS)

    def test_is_supported_file(self):
        self.assertTrue(_is_supported_file("x.RAW"))
        self.assertTrue(_is_supported_file("x.jpeg"))
        self.assertFalse(_is_supported_file("x.txt"))

    def test_is_supported_file_no_extension(self):
        """无后缀文件 = 支持（二进制 RAW 候选）。"""
        self.assertTrue(_is_supported_file("output"))
        self.assertTrue(_is_supported_file("raw_pic/frame0001"))
        self.assertFalse(_is_supported_file("output.txt"))
        self.assertFalse(_is_supported_file("output.exe"))
        self.assertFalse(_is_supported_file("output.dll"))

    def test_scan_directory_filters_extensions(self):
        found = _scan_directory(self.root)
        names = {os.path.basename(p) for p in found}
        self.assertEqual(names, {"a.raw", "b.png", "c.yuv"})

    def test_handle_drop_paths_directory(self):
        files, too_many = handle_drop_paths([_FakeUrl(self.root)])
        names = {os.path.basename(p) for p in files}
        self.assertEqual(names, {"a.raw", "b.png", "c.yuv"})
        self.assertFalse(too_many)

    def test_handle_drop_paths_direct_unsupported_file_is_skipped(self):
        files, _ = handle_drop_paths([_FakeUrl(os.path.join(self.root, "notes.txt"))])
        self.assertEqual(files, [])

    def test_handle_drop_paths_direct_unknown_extension_is_skipped(self):
        exe = os.path.join(self.root, "tool.exe")
        with open(exe, "wb") as f:
            f.write(b"\x00MZbinary")
        files, _ = handle_drop_paths([_FakeUrl(exe)])
        self.assertEqual(files, [])

    def test_handle_drop_paths_too_many_triggers_flag(self):
        dense = os.path.join(self._tmp, "dense")
        os.makedirs(dense)
        for i in range(51):
            with open(os.path.join(dense, f"f{i}.raw"), "wb") as f:
                f.write(b"x")
        files, too_many = handle_drop_paths([_FakeUrl(dense)])
        self.assertEqual(len(files), 51)
        self.assertTrue(too_many)
        files, too_many = handle_drop_paths([_FakeUrl(dense)], max_files=100)
        self.assertFalse(too_many)


# ── 无后缀文件支持（排除乱七八糟后缀 + 与文件夹区分）───────────────────────


class NoExtensionFileTests(unittest.TestCase):
    """无后缀二进制文件纳入、无后缀纯文本排除、无后缀子目录不冒充文件。

    语义：``_is_supported_file`` 对无后缀返回 True（带未知后缀仍为 False），
    目录扫描/同目录导航对无后缀条目额外做二进制嗅探（头部含 NUL）过滤。
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-noext-")
        self.root = os.path.join(self._tmp, "folder")
        os.makedirs(self.root)
        # 无后缀二进制（RAW 候选，头部含 NUL）
        with open(os.path.join(self.root, "output"), "wb") as f:
            f.write(b"\x00\x11\x22rawdata...")
        # 无后缀纯文本（README/LICENSE 不得被当作 RAW 扫入）
        with open(os.path.join(self.root, "README"), "w", encoding="utf-8") as f:
            f.write("hello\nworld\n")
        with open(os.path.join(self.root, "LICENSE"), "w", encoding="utf-8") as f:
            f.write("MIT License\n")
        # 无后缀空文件（嗅探读不到 NUL → 不纳入，安全兜底）
        with open(os.path.join(self.root, "empty"), "wb") as f:
            pass
        # 无后缀子目录（os.listdir 会枚举到同名条目，必须排除）
        os.makedirs(os.path.join(self.root, "subdir_noext"))
        with open(os.path.join(self.root, "subdir_noext", "inside.bin"), "wb") as f:
            f.write(b"x")
        # 常规支持文件与未知后缀文件
        with open(os.path.join(self.root, "a.raw"), "wb") as f:
            f.write(b"\x00x")
        with open(os.path.join(self.root, "b.png"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(self.root, "notes.txt"), "wb") as f:
            f.write(b"x")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_looks_binary_candidate(self):
        self.assertTrue(_looks_binary_candidate(os.path.join(self.root, "output")))
        self.assertFalse(_looks_binary_candidate(os.path.join(self.root, "README")))
        self.assertFalse(_looks_binary_candidate(os.path.join(self.root, "LICENSE")))
        self.assertFalse(_looks_binary_candidate(os.path.join(self.root, "empty")))
        # 嗅探失败（不存在）也不纳入
        self.assertFalse(_looks_binary_candidate(os.path.join(self.root, "missing")))

    def test_looks_binary_candidate_nul_beyond_512_bytes(self):
        """P2-2：NUL 落在 512 字节之后（如 offset 600）也应命中（窗口 4096）。"""
        beyond = os.path.join(self._tmp, "bin_nul600")
        with open(beyond, "wb") as f:
            f.write(b"A" * 600 + b"\x00" + b"B" * 100)
        self.assertTrue(_looks_binary_candidate(beyond))
        # 目录扫描能把它纳入
        self.assertIn("bin_nul600",
                      {os.path.basename(p) for p in _scan_directory(self._tmp)})

    def test_looks_binary_candidate_high_control_char_ratio(self):
        """P2-2：前 4096 无 NUL 但控制字符占比过高 → 判为二进制。"""
        ctrl = os.path.join(self._tmp, "bin_ctrl")
        with open(ctrl, "wb") as f:
            f.write(bytes([0x01, 0x02, 0x03, 0x04]) * 500)
        self.assertTrue(_looks_binary_candidate(ctrl))

    def test_looks_binary_candidate_multilingual_text_excluded(self):
        """P2-2：非打印字符计数不误伤 UTF-8 中文等多行纯文本。"""
        text = os.path.join(self._tmp, "多语言说明")
        with open(text, "w", encoding="utf-8") as f:
            f.write("hello world\n" * 200 + "中文文本需要UTF-8编码\n" * 100)
        self.assertFalse(_looks_binary_candidate(text))

    def test_scan_directory_includes_noext_binary_excludes_text_and_subdir(self):
        found = _scan_directory(self.root)
        names = {os.path.basename(p) for p in found}
        self.assertIn("output", names)          # 无后缀二进制 -> 纳入
        self.assertIn("a.raw", names)
        self.assertIn("b.png", names)
        self.assertIn("inside.bin", names)      # 子目录内正常文件照常递归
        self.assertNotIn("README", names)       # 无后缀纯文本 -> 排除
        self.assertNotIn("LICENSE", names)      # 无后缀纯文本 -> 排除
        self.assertNotIn("empty", names)        # 无后缀空文件 -> 排除（嗅探兜底）
        self.assertNotIn("notes.txt", names)    # 未知后缀 -> 排除
        self.assertNotIn("subdir_noext", names)  # 子目录（即便无后缀）不入文件列表

    def test_handle_drop_paths_directory_includes_noext_binary(self):
        files, too_many = handle_drop_paths([_FakeUrl(self.root)])
        names = {os.path.basename(p) for p in files}
        self.assertIn("output", names)
        self.assertNotIn("README", names)
        self.assertNotIn("LICENSE", names)
        self.assertNotIn("notes.txt", names)
        self.assertFalse(too_many)

    def test_handle_drop_paths_single_noext_file_accepted(self):
        """单文件显式拖放不受嗅探限制：无后缀二进制直接被接受。"""
        files, _ = handle_drop_paths([_FakeUrl(os.path.join(self.root, "output"))])
        self.assertEqual([os.path.basename(p) for p in files], ["output"])

    def test_handle_drop_paths_single_noext_text_is_user_choice(self):
        """单文件显式拖放：用户手选即信任（无后缀纯文本也可打开为 RAW）。"""
        files, _ = handle_drop_paths([_FakeUrl(os.path.join(self.root, "README"))])
        self.assertEqual([os.path.basename(p) for p in files], ["README"])

    def test_handle_drop_paths_directory_with_noext_subdir_only(self):
        """整目录只有无后缀子目录时不会把子目录当文件打开。"""
        only_dir = os.path.join(self._tmp, "only_subdir")
        os.makedirs(os.path.join(only_dir, "some_noext_subdir"))
        files, _ = handle_drop_paths([_FakeUrl(only_dir)])
        self.assertEqual(files, [])


class SameDirItemsNoExtensionTests(unittest.TestCase):
    """_same_dir_items 对无后缀兄弟项：子目录排除、纯文本排除、二进制纳入。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-samedir-")
        for fn in ("a.raw", "c.raw"):
            with open(os.path.join(self._tmp, fn), "wb") as f:
                f.write(b"\x00x")
        with open(os.path.join(self._tmp, "output"), "wb") as f:
            f.write(b"\x00\x11binary")
        with open(os.path.join(self._tmp, "notes"), "w", encoding="utf-8") as f:
            f.write("text note without extension\n")
        os.makedirs(os.path.join(self._tmp, "subdir_noext"))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _window_on(self, path):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = path
        w._current_item = lambda: item
        return w

    def test_same_dir_items_filters_noext_entries(self):
        w = self._window_on(os.path.join(self._tmp, "c.raw"))
        siblings = sorted(w._same_dir_items())
        # 无后缀子目录与纯文本被排除，无后缀二进制 "output" 被纳入。
        self.assertEqual(siblings, ["a.raw", "output"])


class OpenItemGuardTests(unittest.TestCase):
    """_open_item 的 source 区分：auto 未知后缀拒绝；explicit 允许未知后缀、
    仅拒危险可执行后缀并给出 GUI 反馈（绝不静默）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-openitem-")
        self._raw = os.path.join(self._tmp, "frame0001")
        with open(self._raw, "wb") as f:
            f.write(b"\x00" * 64)
        self._exe = os.path.join(self._tmp, "tool.exe")
        with open(self._exe, "wb") as f:
            f.write(b"\x00MZbinary")
        self._txt = os.path.join(self._tmp, "readme.txt")
        with open(self._txt, "wb") as f:
            f.write(b"plain text")
        self._dat = os.path.join(self._tmp, "capture.dat")
        with open(self._dat, "wb") as f:
            f.write(b"\x00" * 64)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_open_item_accepts_noext(self):
        """无后缀文件（即便内容非图片）仍会建标签（按 RAW 默认打开）。"""
        w = MainWindow()
        try:
            w._open_item(self._raw, decode=False)
            self.assertEqual(len(w.items), 1)
            self.assertEqual(w.items[0].options.image_type, "RAW")
        finally:
            destroy_widget(w)

    def test_open_item_auto_rejects_unknown_extension(self):
        """auto 来源（扫描/拖放）：带未知后缀（.exe/.txt）被拒，不建标签。"""
        for path in (self._exe, self._txt, self._dat):
            with self.subTest(path=path):
                w = MainWindow()
                try:
                    w._open_item(path, decode=False, source="auto")
                    self.assertEqual(len(w.items), 0)
                finally:
                    destroy_widget(w)

    def test_open_item_explicit_accepts_unknown_data_extension(self):
        """explicit 来源（打开对话框/启动参数/最近文件）：.dat 等未知后缀按 RAW 打开。"""
        w = MainWindow()
        try:
            w._open_item(self._dat, decode=False, source="explicit")
            self.assertEqual(len(w.items), 1)
            self.assertEqual(w.items[0].options.image_type, "RAW")
        finally:
            destroy_widget(w)

    def test_open_item_explicit_rejects_dangerous_extension_with_feedback(self):
        """explicit 来源：.exe 等可执行后缀被拒，且给出状态栏/GUI 反馈而非静默。"""
        for path in (self._exe,):
            with self.subTest(path=path):
                w = MainWindow()
                status_msgs: list[str] = []
                w.statusBar().showMessage = lambda msg, *a, **k: status_msgs.append(msg)
                try:
                    w._open_item(path, decode=False, source="explicit")
                    self.assertEqual(len(w.items), 0)
                    self.assertTrue(status_msgs, "被拒原因应写入状态栏反馈")
                    self.assertIn("可执行文件", status_msgs[0])
                finally:
                    destroy_widget(w)

    def test_is_dangerous_extension_set(self):
        for ext in (".exe", ".dll", ".sys", ".bat", ".cmd", ".com", ".scr",
                    ".msi", ".ps1", ".vbs", ".ocx"):
            self.assertTrue(_is_dangerous_extension(f"a{ext}"))
            self.assertTrue(_is_dangerous_extension(f"a{ext.upper()}"))
        for ext in (".dat", ".txt", ".raw", ".bin", ".png", ".log"):
            self.assertFalse(_is_dangerous_extension(f"a{ext}"))
        self.assertFalse(_is_dangerous_extension("noext"))


# ── L-2: dead field removed ──────────────────────────────────────────────


class ViewerItemFieldTests(unittest.TestCase):
    def test_rotation_angle_removed(self):
        item = ViewerItem()
        self.assertFalse(hasattr(item, "rotation_angle"))


# ── 多帧回归：data 已按 effective_offset 切片，spec.offset 必须为 0 ───────


class MultiFrameOffsetRegressionTests(unittest.TestCase):
    """Regression for the double-offset bug where frame 1+ reported
    "data too short: need 2x bytes" (H-2 seek-read optimisation sliced the
    frame at effective_offset, but the decode spec still used that offset,
    slicing an already-sliced buffer a second time).
    """

    def _worker_decode(self, data, width, height, offset, format_name="RAW12", frame_index=0):
        """Run a DecodeWorker synchronously (bypassing QThread) and return result/error."""
        from PyQt5.QtCore import QCoreApplication
        app = QCoreApplication.instance() or QCoreApplication([])
        from raw_view.gui.worker import DecodeWorker
        from raw_view.formats import ImageSpec

        outcome = {}
        worker = DecodeWorker()

        def on_finish(_gen, res):
            outcome["ok"] = res

        def on_error(_gen, msg):
            outcome["err"] = msg

        worker.finished.connect(on_finish)
        worker.error.connect(on_error)
        # 修复后的调用契约：data 是已按 offset 读出的单帧，spec.offset 必须为 0
        worker.configure(
            data, ImageSpec(width, height, 0), format_name,
            alignment="msb", endianness="little", preview_mode="Grayscale",
            generation=1, file_path="cap.bin", frame_index=frame_index,
            source_offset=offset,
        )
        worker.run()
        return outcome

    def test_multi_frame_all_frames_decode(self):
        # 用户场景：256x36 RAW12，一帧 18432，5 帧 → 92160 字节
        import tempfile
        w, h, frames = 256, 36, 5
        frame_size = w * h * 2
        fd, path = tempfile.mkstemp(suffix=".raw")
        os.close(fd)
        try:
            with open(path, "wb") as f:
                for fi in range(frames):
                    frame = (np.arange(w * h, dtype=np.uint16).reshape(h, w) * (fi + 1)) & 0x0FFF
                    frame.astype("<u2").tofile(f)
            for fi in range(frames):
                offset = fi * frame_size
                with open(path, "rb") as f:
                    f.seek(offset)
                    data = f.read(frame_size)
                outcome = self._worker_decode(data, w, h, offset, frame_index=fi)
                self.assertNotIn("err", outcome, f"frame {fi} failed: {outcome.get('err')}")
                self.assertEqual(outcome["ok"].width, w)
                self.assertEqual(outcome["ok"].height, h)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_worker_describe_source_shows_real_offset(self):
        # 错误消息应显示真实源 offset（即使 spec.offset=0）
        from raw_view.formats import ImageSpec
        from raw_view.gui.worker import DecodeWorker

        worker = DecodeWorker()
        worker.configure(b"", ImageSpec(4, 4, 0), "RAW12",
                         generation=1, file_path="/tmp/cap.bin",
                         frame_index=3, source_offset=6144)
        desc = worker._describe_source()
        self.assertIn("frame 3", desc)
        self.assertIn("offset=6144", desc)


# ── UI-6: 同目录文件组切换（上一/下一文件）─────────────────────────────


class SameDirNavTests(unittest.TestCase):
    """Regression for the P1-6-style prev/next-file nav: _same_dir_items 排除
    自身，_nav_file_by_dir 不能把「完整有序列表中的 rank」直接当下标（siblings
    少一个元素），否则 "c" 的下一文件会错误跳过、落到错误邻居。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.mkdtemp(prefix="rv-nav-")
        for fn in ("a.raw", "b.raw", "c.raw", "d.raw"):
            open(os.path.join(self._tmp, fn), "w").close()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _nav(self, path: str, delta: int):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = path
        w._current_item = lambda: item
        opened: list[str] = []
        w._open_item = lambda p, decode=True: opened.append(os.path.basename(p))
        w._nav_file_by_dir(delta)
        return opened

    def test_next_from_middle(self):
        base = os.path.join(self._tmp, "c.raw")
        self.assertEqual(self._nav(base, 1), ["d.raw"])
        self.assertEqual(self._nav(base, -1), ["b.raw"])

    def test_boundaries_do_not_wrap(self):
        head = os.path.join(self._tmp, "a.raw")
        tail = os.path.join(self._tmp, "d.raw")
        self.assertEqual(self._nav(head, -1), [])
        self.assertEqual(self._nav(tail, 1), [])
        self.assertEqual(self._nav(head, 1), ["b.raw"])

    def test_same_dir_items_excludes_self(self):
        w = _new_window()
        item = ViewerItem()
        item.options.file_path = os.path.join(self._tmp, "c.raw")
        w._current_item = lambda: item
        self.assertEqual(sorted(w._same_dir_items()), ["a.raw", "b.raw", "d.raw"])


if __name__ == "__main__":
    unittest.main()
