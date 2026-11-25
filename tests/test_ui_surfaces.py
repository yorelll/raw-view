"""0.6.1 batch 1 — real-render surface tests for H-2 / H-8 / M-1.

The 0.6.0 review measured the *rendered* pixels (``widget.grab().toImage()``),
not the palette tokens: qt-material plus our overlay is a layered paint stack,
so the only trustworthy background is what the user actually sees.  These
tests follow the same method.

Coverage required by ``docs/ui-review/ui-fix-plan-0.6.1-batch1.md`` §5.3:

* T-1  every dialog window renders ``main_bg`` (H-2).
* T-2  empty state / tab-strip empty area render ``main_bg`` and match the
       16px root margin pixel (H-8).
* T-3  list/table body = ``panel_bg``, header and zebra rows = ``main_bg``
       (M-1).
* T-4  a secondary button really sits on ``main_bg`` in the light theme, so
       ``accent`` regains 4.58:1 (H-2 contrast regression).

Every check runs against both themes; both are asserted explicitly rather
than inferred from one.  Qt teardown uses ``tests/_qt_cleanup.destroy_widget``
(CLAUDE.md §4) — never ``deleteLater()``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from collections import Counter

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_cleanup import (  # noqa: E402
    begin_widget_leak_check,
    destroy_widget,
    end_widget_leak_check,
)
from PyQt5.QtCore import QPoint, QRect, QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication, QTextBrowser  # noqa: E402

from raw_view.models import (  # noqa: E402
    MATERIAL_EXTRA,
    THEME_PALETTES,
    THEME_XML,
    AppSettings,
    build_ui_stylesheet,
)

APP = QApplication.instance() or QApplication([])

_THEMES = ("light", "dark")


# ---------------------------------------------------------------------------
# rendering helpers (real paint stack, never the palette)
# ---------------------------------------------------------------------------


def _apply_product_theme(theme: str, font_size: int = 13) -> None:
    """Mirror ``MainWindow._apply_theme_impl``: qt-material base + overlay."""
    from qt_material import apply_stylesheet

    from raw_view.gui.app import resource_path

    theme_file = resource_path(
        os.path.join("assets", THEME_XML.get(theme, THEME_XML["dark"]))
    )
    apply_stylesheet(
        APP, theme=theme_file, invert_secondary=False, extra=MATERIAL_EXTRA
    )
    APP.setStyleSheet(
        f"{APP.styleSheet()}\n{build_ui_stylesheet(theme, font_size)}"
    )


def _pixel(image, widget, ancestor, x: int, y: int) -> str:
    point = widget.mapTo(ancestor, QPoint(x, y))
    return image.pixelColor(point).name().lower()


def _most_common(image, widget, ancestor, rect: QRect | None = None) -> str:
    rect = rect or widget.rect()
    counts: Counter = Counter()
    step_x = max(1, rect.width() // 24)
    step_y = max(1, rect.height() // 12)
    for x in range(1, rect.width(), step_x):
        for y in range(1, rect.height(), step_y):
            point = widget.mapTo(
                ancestor, QPoint(rect.x() + x, rect.y() + y)
            )
            counts[image.pixelColor(point).name().lower()] += 1
    return counts.most_common(1)[0][0]


def _contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x relative-luminance contrast ratio for two ``#RRGGBB`` colours."""

    def _channel(value: int) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _luminance(color: str) -> float:
        color = color.lstrip("#")
        r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)

    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# shared fixture
# ---------------------------------------------------------------------------


class _SurfaceTestBase(unittest.TestCase):
    """Isolated settings, a tracked widget list, and the L3 leak guard."""

    def setUp(self):
        begin_widget_leak_check()
        self._widgets: list = []
        self._tmp = tempfile.mkdtemp(prefix="rv-surfaces-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        # Restore the process stylesheet after the widgets are gone; unittest
        # runs tearDown before addCleanup callbacks.
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        self.settings = AppSettings(
            QSettings(os.path.join(self._tmp, "settings.ini"), QSettings.IniFormat)
        )

    def tearDown(self):
        while self._widgets:
            destroy_widget(self._widgets.pop())
        end_widget_leak_check(f"{type(self).__name__}.{self._testMethodName}")

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def _settle(self):
        APP.processEvents()


# ---------------------------------------------------------------------------
# T-1 — every dialog window renders main_bg (H-2)
# ---------------------------------------------------------------------------


class DialogSurfaceTests(_SurfaceTestBase):
    """T-1: the eight dialog families no longer fall back to qt-material grey."""

    def _dialogs(self):
        from raw_view.gui.dialogs import (
            AboutDialog,
            BatchConvertDialog,
            ConvertDialog,
            FourCCDialog,
            HelpDialog,
            KeyboardShortcutsDialog,
            PresetManagerDialog,
            SettingsDialog,
        )

        return {
            "Convert": ConvertDialog(self.settings),
            "Batch": BatchConvertDialog(self.settings),
            "Settings": SettingsDialog(self.settings),
            "Preset": PresetManagerDialog(self.settings),
            "Help": HelpDialog(),
            "About": AboutDialog(),
            "KeyboardShortcuts": KeyboardShortcutsDialog(),
            "FourCC": FourCCDialog(self.settings),
        }

    def test_t1_all_dialog_windows_render_main_bg(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                main_bg = THEME_PALETTES[theme]["main_bg"].lower()
                dialogs = self._dialogs()
                try:
                    for name, dialog in dialogs.items():
                        self._track(dialog)
                        dialog.show()
                        self._settle()
                        image = dialog.grab().toImage()
                        width, height = dialog.size().width(), dialog.size().height()
                        left = _pixel(image, dialog, dialog, 3, height // 2)
                        top = _pixel(image, dialog, dialog, width // 2, 3)
                        self.assertEqual(
                            left, main_bg,
                            f"{theme}/{name}: 对话框左边距底色必须是 main_bg",
                        )
                        self.assertEqual(
                            top, main_bg,
                            f"{theme}/{name}: 对话框上边距底色必须是 main_bg",
                        )
                    # The help browser keeps its card surface (panel_bg) instead
                    # of being flattened by the QDialog rule.
                    help_dialog = dialogs["Help"]
                    browser = help_dialog.findChild(QTextBrowser, "helpBrowser")
                    self.assertIsNotNone(browser, "Help 对话框缺少 helpBrowser")
                    browser.show()
                    self._settle()
                    browser_bg = _most_common(
                        help_dialog.grab().toImage(), browser, help_dialog
                    )
                    self.assertEqual(
                        browser_bg,
                        THEME_PALETTES[theme]["panel_bg"].lower(),
                        f"{theme}: helpBrowser 必须仍为 panel_bg",
                    )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)


# ---------------------------------------------------------------------------
# T-2 — viewer area (empty state + tab strip empty area) = main_bg (H-8)
# ---------------------------------------------------------------------------


class ViewerSurfaceTests(_SurfaceTestBase):
    """T-2: the biggest area of the window matches the 16px root margin."""

    def _open_raw(self, window, path: str) -> None:
        window._open_item(path, decode=False)
        self._settle()

    def test_t2_viewer_and_tab_empty_area_match_root_margin(self):
        from raw_view.gui.app import MainWindow

        raw_path = os.path.join(self._tmp, "surface.raw")
        with open(raw_path, "wb") as handle:
            handle.write(b"\x00" * 256)

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                self.settings.ui_font_size = 13
                window = self._track(MainWindow())
                window.settings = self.settings
                window.resize(1180, 800)
                window._apply_theme()
                window.show()
                self._settle()

                main_bg = THEME_PALETTES[theme]["main_bg"].lower()
                central = window.centralWidget()
                image = window.grab().toImage()

                margin_px = _pixel(image, central, window, 4, 4)
                empty_px = _most_common(image, window.empty_state, window)
                self.assertEqual(
                    empty_px, main_bg,
                    f"{theme}: 空态区域底色必须是 main_bg",
                )

                self._open_raw(window, raw_path)
                image = window.grab().toImage()
                tabs = window.item_tabs
                tab_bar = tabs.tabBar()
                # Right of the tab strip: exposed QTabWidget background.
                right_px = _pixel(
                    image, tabs, window,
                    tabs.size().width() - 6, tab_bar.size().height() // 2,
                )
                # Below the tab bar: the viewer page itself.  The synthetic raw
                # is intentionally not decoded, so the whole image canvas is
                # the page's empty background.
                viewport = window.items[0].view.viewport()
                below_px = _pixel(
                    image, viewport, window,
                    viewport.size().width() // 2, 3,
                )
                for label, value in (
                    ("标签右侧空区", right_px),
                    ("标签下方空区（视图区）", below_px),
                    ("16px 根边距", margin_px),
                ):
                    self.assertEqual(
                        value, main_bg, f"{theme}/{label}: 必须是 main_bg"
                    )
                self.assertEqual(
                    {right_px, below_px, empty_px, margin_px}, {main_bg},
                    f"{theme}: 空态 / 标签空区 / 根边距四处必须完全同色",
                )
                destroy_widget(window)
                self._widgets.remove(window)


# ---------------------------------------------------------------------------
# T-3 — list / table surfaces (M-1)
# ---------------------------------------------------------------------------


class ListTableSurfaceTests(_SurfaceTestBase):
    """T-3: body = panel_bg; header and zebra rows = main_bg."""

    def _fourcc_row_colour(self, dialog, table, row: int) -> str:
        from raw_view.gui.dialogs.fourcc import _COL_DESC

        rect = table.visualItemRect(table.item(row, _COL_DESC))
        viewport = table.viewport()
        x = (
            table.columnViewportPosition(_COL_DESC)
            + table.columnWidth(_COL_DESC) - 8
        )
        y = rect.center().y()
        image = dialog.grab().toImage()
        counts: Counter = Counter()
        for dx in range(-8, 1, 2):
            for dy in range(-3, 4, 3):
                point = viewport.mapTo(dialog, QPoint(x + dx, y + dy))
                counts[image.pixelColor(point).name().lower()] += 1
        return counts.most_common(1)[0][0]

    def test_t3_list_and_table_bodies_headers_and_zebra(self):
        from raw_view.gui.dialogs import (
            BatchConvertDialog,
            FourCCDialog,
            PresetManagerDialog,
        )

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                palette = THEME_PALETTES[theme]
                panel_bg = palette["panel_bg"].lower()
                main_bg = palette["main_bg"].lower()

                preset_dialog = self._track(PresetManagerDialog(self.settings))
                batch_dialog = self._track(BatchConvertDialog(self.settings))
                fourcc_dialog = self._track(FourCCDialog(self.settings))
                for dialog in (preset_dialog, batch_dialog, fourcc_dialog):
                    dialog.show()
                    self._settle()

                preset_bg = _most_common(
                    preset_dialog.grab().toImage(),
                    preset_dialog.list_widget.viewport(),
                    preset_dialog,
                )
                self.assertEqual(
                    preset_bg, panel_bg, f"{theme}: Preset 列表背景必须是 panel_bg"
                )

                batch_table = batch_dialog._file_table
                batch_body = _most_common(
                    batch_dialog.grab().toImage(),
                    batch_table.viewport(),
                    batch_dialog,
                )
                self.assertEqual(
                    batch_body, panel_bg, f"{theme}: Batch 表格体必须是 panel_bg"
                )
                batch_header = _most_common(
                    batch_dialog.grab().toImage(),
                    batch_table.horizontalHeader(),
                    batch_dialog,
                )
                self.assertEqual(
                    batch_header, main_bg, f"{theme}: Batch 表头必须是 main_bg"
                )

                fourcc_table = fourcc_dialog._table
                fourcc_header = _most_common(
                    fourcc_dialog.grab().toImage(),
                    fourcc_table.horizontalHeader(),
                    fourcc_dialog,
                )
                self.assertEqual(
                    fourcc_header, main_bg, f"{theme}: FourCC 表头必须是 main_bg"
                )

                row_colours = [
                    self._fourcc_row_colour(fourcc_dialog, fourcc_table, row)
                    for row in range(4)
                ]
                self.assertEqual(
                    {row_colours[0], row_colours[2]}, {panel_bg},
                    f"{theme}: FourCC 偶数行必须是 panel_bg，实测 {row_colours}",
                )
                self.assertEqual(
                    {row_colours[1], row_colours[3]}, {main_bg},
                    f"{theme}: FourCC 交替行必须是 main_bg，实测 {row_colours}",
                )


# ---------------------------------------------------------------------------
# T-4 — dialog accent contrast regression (H-2)
# ---------------------------------------------------------------------------


class DialogContrastTests(_SurfaceTestBase):
    """T-4: an outlined button's real background is main_bg at AA contrast."""

    # Plan §3.1 expected accent-on-main_bg ratios; ±0.01 guards token drift.
    _EXPECTED_RATIO = {"light": 4.58, "dark": 6.43}

    def test_t4_secondary_button_background_and_accent_contrast(self):
        from raw_view.gui.dialogs import SettingsDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                palette = THEME_PALETTES[theme]
                main_bg = palette["main_bg"].lower()

                dialog = self._track(SettingsDialog(self.settings))
                dialog.show()
                self._settle()
                button = dialog.template_reset_btn
                self.assertEqual(
                    button.objectName(), "secondaryButton",
                    f"{theme}: 被测按钮必须是描边 secondaryButton",
                )
                rendered_bg = _most_common(
                    dialog.grab().toImage(), button, dialog
                )
                self.assertEqual(
                    rendered_bg, main_bg,
                    f"{theme}: 描边按钮的实际绘制背景必须是 main_bg，"
                    f"实测 {rendered_bg}",
                )
                ratio = _contrast_ratio(palette["accent"], rendered_bg)
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f"{theme}: accent on {rendered_bg} = {ratio:.2f}:1 "
                    "低于 WCAG AA",
                )
                self.assertAlmostEqual(
                    ratio, self._EXPECTED_RATIO[theme], delta=0.01,
                    msg=f"{theme}: accent on {rendered_bg} 期望 "
                    f"{self._EXPECTED_RATIO[theme]}±0.01，实测 {ratio:.4f}",
                )
