"""0.6.3 batch 3 — font-size / layout acceptance tests (V-1 … V-7).

Contract: ``docs/ui-review/ui-fix-plan-0.6.3-batch3.md`` §7.  Every check runs
against both themes and the five UI font sizes the Settings dialog allows
(10/13/16/20/24), because the 0.6.0 defects only appeared at the upper half of
that range (H-6/H-7).

Qt teardown uses ``tests/_qt_cleanup.destroy_widget`` (CLAUDE.md §4); the L3
widget-leak guard is armed in ``setUp`` because ``unittest discover`` does not
load ``conftest.py``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_cleanup import (  # noqa: E402
    begin_widget_leak_check,
    destroy_widget,
    end_widget_leak_check,
)
from PyQt5.QtCore import QPoint, QSettings  # noqa: E402
from PyQt5.QtGui import QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QSpinBox,
)

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.gui.panels import ControlPanel  # noqa: E402
from raw_view.models import (  # noqa: E402
    AppSettings,
    DecodeOptions,
    SensorPreset,
)

APP = QApplication.instance() or QApplication([])

THEMES = ("light", "dark")
SIZES = (10, 13, 16, 20, 24)
#: Field chrome assumed by the acceptance rule: 8 px padding per side, 1 px
#: border per side, plus the 24 px combo drop-down.
PADDING = 8 * 2 + 1 * 2
DROPDOWN = 24


def _pump(rounds: int = 6) -> None:
    for _ in range(rounds):
        APP.processEvents()


class _LayoutFixture(unittest.TestCase):
    """Temp dir + isolated INI + a real, shown MainWindow with one RAW item."""

    def setUp(self):
        begin_widget_leak_check()
        self.tmp = tempfile.mkdtemp(prefix="rv-layout-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = AppSettings(
            QSettings(os.path.join(self.tmp, "settings.ini"), QSettings.IniFormat)
        )
        self.w = MainWindow()
        self.w.settings = self.settings
        self.w.resize(1200, 700)
        self.w.show()
        _pump()
        self.raw_path = os.path.join(self.tmp, "frame.raw")
        with open(self.raw_path, "wb") as handle:
            handle.write(b"\x00" * 4096)
        self.w._open_item(self.raw_path, decode=False)
        _pump()

    def tearDown(self):
        try:
            destroy_widget(self.w)
        finally:
            end_widget_leak_check(f"{type(self).__name__}.{self._testMethodName}")

    # ── helpers ──────────────────────────────────────────────────────

    def _apply(self, theme: str, size: int) -> None:
        self.settings.ui_theme = theme
        self.settings.ui_font_size = size
        self.w._apply_theme()
        _pump()

    def _panel(self) -> ControlPanel:
        return self.w.panel

    def _strip(self):
        return self.w.items[0].readout_strip


class PanelWidthTests(_LayoutFixture):
    """V-1 / V-2: the panel never scrolls sideways and fields keep their text."""

    def test_v1_no_horizontal_scrollbar_at_any_font(self):
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    scroll = self._panel().scroll_area
                    self.assertFalse(
                        scroll.horizontalScrollBar().isVisible(),
                        f"{theme}/{size}: panel must never show a horizontal "
                        "scrollbar",
                    )

    @staticmethod
    def _longest_text(field) -> str:
        """Longest possible value per the V-2 contract."""
        if isinstance(field, QComboBox):
            if field.count() == 0:
                return ""
            return max(
                (field.itemText(i) for i in range(field.count())),
                key=lambda text: QFontMetrics(field.font()).horizontalAdvance(text),
            )
        if isinstance(field, QSpinBox):
            return f"{field.prefix()}{field.maximum()}{field.suffix()}"
        return field.text()

    def test_v2_every_field_fits_its_longest_value(self):
        """V-2: every QComboBox/QSpinBox (zoom + preset included), longest value."""
        panel = self._panel()
        fields = panel.findChildren(QComboBox) + panel.findChildren(QSpinBox)
        self.assertTrue(fields)
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    for field in fields:
                        if field.isHidden():
                            continue
                        text = self._longest_text(field)
                        if not text:
                            continue
                        padding = PADDING + DROPDOWN
                        advance = QFontMetrics(field.font()).horizontalAdvance(
                            text
                        )
                        self.assertLessEqual(
                            advance,
                            field.width() - padding,
                            f"{theme}/{size}: {field.objectName() or type(field).__name__}"
                            f" longest value {text!r} is clipped "
                            f"({advance} > {field.width() - padding})",
                        )


class WindowMinimumWidthTests(_LayoutFixture):
    """V-3: no status/readout label may drag the window beyond 1280 px."""

    def test_v3_window_minimum_width_at_24px(self):
        for theme in THEMES:
            with self.subTest(theme=theme):
                self._apply(theme, 24)
                minimum = self.w.minimumSizeHint().width()
                self.assertLessEqual(
                    minimum, 1280,
                    f"{theme}: 24px window minimum width is {minimum}px",
                )
                # The status bar must not be what decides it either.
                self.assertLessEqual(
                    self.w.status.minimumSizeHint().width(),
                    minimum,
                )


class StatusBarCompositionTests(_LayoutFixture):
    """V-4: exactly two permanent labels, neither imposing a minimum width."""

    def test_v4_status_bar_has_two_elidable_labels(self):
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    labels = self.w.status.findChildren(QLabel)
                    self.assertEqual(
                        len(labels), 2,
                        "status bar keeps only the path and the state label",
                    )
                    self.assertEqual(self.w.file_status.minimumWidth(), 0)
                    self.assertEqual(self.w.state_status.minimumWidth(), 0)
                    for removed in ("image_status", "frame_status", "zoom_status"):
                        self.assertFalse(
                            hasattr(self.w, removed),
                            f"{removed} must no longer exist (M-5)",
                        )


class StateStatusElisionTests(_LayoutFixture):
    """L-1: the state label elides visually while keeping its full text."""

    def test_state_text_elided_on_paint_but_text_stays_full(self):
        long_text = "Decoded " + "0123456789" * 5
        self._apply("light", 13)
        self.w._set_state(long_text, "ok")
        label = self.w.state_status
        # Callers read .text(): it must keep the full HTML state string.
        self.assertIn(long_text, label.text())

        # Shrink to the window's own minimum so the bar is narrower than the
        # status text, then check the paint path really uses elision.
        self.w.resize(self.w.minimumSizeHint().width(), 700)
        _pump()
        metrics = QFontMetrics(label.font())
        rect = label.contentsRect()
        self.assertGreater(
            metrics.horizontalAdvance("● " + long_text), rect.width(),
            "test setup: the status text must be wider than the label",
        )
        elided = label.elided_text()
        self.assertNotEqual(elided, long_text)
        self.assertLess(len(elided), len(long_text),
                        f"paint text was not shortened: {elided!r}")
        available = rect.width() - metrics.horizontalAdvance("● ")
        self.assertLessEqual(
            metrics.horizontalAdvance(elided), available + 1,
            "elided text must fit the label's paint area",
        )

        calls: list[int] = []
        original = label.elided_text
        label.elided_text = lambda: calls.append(1) or original()
        label.repaint()
        _pump(2)
        self.assertTrue(calls, "paintEvent must go through the elision path")
        del label.elided_text

        # Widening the window restores the full text.
        self.w.resize(1600, 700)
        _pump()
        self.assertEqual(label.elided_text(), long_text)
        pixmap = label.grab()
        self.assertFalse(pixmap.isNull())


class PresetNameElisionTests(_LayoutFixture):
    """V-2 follow-up: long preset names elide with an ellipsis + full tooltip."""

    LONG_27 = "IMX477 12bit 4000x3000 msb"
    LONG_47 = "IMX477 12bit 4000x3000 msb with an extremely long suffix"
    SHORT = "Short"

    def _select(self, name: str):
        panel = self._panel()
        panel.set_presets([SensorPreset(name=name)], current=name)
        _pump()
        return panel.preset_combo

    def test_long_names_elide_and_tooltip_keeps_full_name(self):
        self._apply("light", 24)
        for name in (self.LONG_27, self.LONG_47):
            with self.subTest(name=name):
                combo = self._select(name)
                self.assertEqual(combo.currentText(), name)
                self.assertEqual(combo.toolTip(), name)
                metrics = QFontMetrics(combo.font())
                rect = combo._text_rect()
                self.assertGreater(
                    metrics.horizontalAdvance(name), rect.width(),
                    "test setup: the name must overflow the capped selector",
                )
                shown = combo.display_text()
                self.assertNotEqual(shown, name)
                self.assertLess(len(shown), len(name))
                self.assertLessEqual(
                    metrics.horizontalAdvance(shown), rect.width() + 1,
                    f"elided text must fit the field: {shown!r}",
                )
                # The real fonts render "…"; glyph-less fallback fonts measure
                # it as zero width and Qt then truncates without the glyph.
                if metrics.horizontalAdvance("…") > 0:
                    self.assertTrue(shown.endswith("…"), shown)

                calls: list[int] = []
                original = combo.display_text
                combo.display_text = lambda: calls.append(1) or original()
                combo.repaint()
                _pump(2)
                self.assertTrue(calls, "paintEvent must use display_text()")
                del combo.display_text

    def test_short_name_is_not_elided(self):
        self._apply("light", 24)
        combo = self._select(self.SHORT)
        self.assertEqual(combo.display_text(), self.SHORT)
        self.assertEqual(combo.toolTip(), self.SHORT)

    def test_late_reorder_on_placeholder_clears_tooltip(self):
        """r4: a deferred re-order must not leave the old preset name in the tooltip."""
        self._apply("light", 24)
        panel = self._panel()
        combo = self._select(self.LONG_27)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(combo.toolTip(), self.LONG_27)

        # User arrows back to the empty state; the signal resets the tooltip.
        combo.setCurrentIndex(-1)
        _pump()
        self.assertEqual(combo.currentIndex(), -1)
        self.assertEqual(combo.toolTip(), ControlPanel._PRESET_TOOLTIP)

        # A late recent-use re-order rebuilds the model (restores the old
        # selection internally) and then puts the box back on the placeholder.
        panel._preset_reorder_pending = True
        panel._maybe_apply_preset_reorder()
        _pump()
        self.assertEqual(combo.currentIndex(), -1)
        self.assertIsNone(combo.currentData())
        self.assertEqual(
            combo.toolTip(), ControlPanel._PRESET_TOOLTIP,
            "placeholder must not keep the previous preset name in the tooltip",
        )

    def test_tooltip_restored_when_selection_is_cleared(self):
        self._apply("light", 24)
        combo = self._select(self.LONG_27)
        self.assertEqual(combo.toolTip(), self.LONG_27)
        self._panel().reset_preset_selection()
        _pump()
        self.assertEqual(combo.currentIndex(), -1)
        self.assertEqual(combo.toolTip(), ControlPanel._PRESET_TOOLTIP)


class ReadoutStripHeightTests(_LayoutFixture):
    """V-5: the readout height follows the font and never clips its labels."""

    def test_v5_strip_taller_than_its_labels(self):
        strip = self._strip()
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    natural = max(
                        label.sizeHint().height()
                        for label in (strip._format, strip._detail, strip._frame)
                    )
                    self.assertGreaterEqual(
                        strip.height(), natural + 4,
                        f"{theme}/{size}: strip {strip.height()}px leaves no "
                        f"vertical room for a {natural}px label",
                    )


class FrameCounterAnchorTests(_LayoutFixture):
    """V-6: the frame counter keeps the same left edge across content kinds."""

    def test_v6_frame_x_is_stable(self):
        strip = self._strip()
        contents = {
            "RAW": DecodeOptions(
                file_path=self.raw_path, image_type="RAW",
                format_name="RAW12", width=2560, height=1440,
            ),
            "YUV": DecodeOptions(
                file_path=self.raw_path, image_type="YUV",
                format_name="NV12", width=2560, height=1440,
            ),
            "Standard Image": DecodeOptions(
                file_path=self.raw_path, image_type="Standard Image",
                format_name="N/A", width=2560, height=1440,
            ),
        }
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    positions = []
                    for options in contents.values():
                        strip.set_data(options, 7_372_800, 0, 2)
                        _pump(2)
                        positions.append(
                            strip._frame.mapTo(strip, QPoint(0, 0)).x()
                        )
                    spread = max(positions) - min(positions)
                    self.assertLessEqual(
                        spread, 2,
                        f"{theme}/{size}: frame counter jumped {spread}px "
                        f"({positions})",
                    )


class GroupHierarchyTests(_LayoutFixture):
    """V-7: group headings outrank field labels and every group is separated."""

    def test_v7_section_labels_bigger_and_three_dividers(self):
        panel = self._panel()
        for theme in THEMES:
            for size in SIZES:
                with self.subTest(theme=theme, size=size):
                    self._apply(theme, size)
                    sections = panel.findChildren(QLabel, "sectionLabel")
                    form_labels = [
                        label for label in panel.findChildren(QLabel)
                        if label.property("role") == "formLabel"
                    ]
                    self.assertTrue(sections)
                    self.assertTrue(form_labels)
                    section_px = sections[0].font().pixelSize()
                    form_px = form_labels[0].font().pixelSize()
                    self.assertGreater(
                        section_px, form_px,
                        f"{theme}/{size}: section {section_px}px is not larger "
                        f"than field label {form_px}px",
                    )
                    dividers = panel.findChildren(QFrame, "groupDivider")
                    self.assertEqual(
                        len(dividers), 3,
                        f"{theme}/{size}: expected 3 group dividers, got "
                        f"{len(dividers)}",
                    )


if __name__ == "__main__":
    unittest.main()
