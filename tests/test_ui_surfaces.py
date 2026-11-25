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
from PyQt5.QtCore import QPoint, QRect, QSettings, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QIcon  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAction,
    QApplication,
    QPushButton,
    QTableWidgetItem,
    QTextBrowser,
    QToolBar,
    QToolButton,
)

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


# ---------------------------------------------------------------------------
# Shared render helpers for 0.6.2 batch 2 (T-5 ... T-9)
# ---------------------------------------------------------------------------


def _max_channel_diff(a: QColor, b: QColor) -> int:
    """Largest per-channel difference; the plan's 40/255 pixel threshold."""
    return max(
        abs(a.red() - b.red()),
        abs(a.green() - b.green()),
        abs(a.blue() - b.blue()),
    )


def _most_common_inset(image, widget, ancestor, inset: int = 4) -> str:
    """Dominant colour inside *widget*, ignoring its border and corners."""
    rect = widget.rect()
    origin = widget.mapTo(ancestor, QPoint(0, 0))
    counts: Counter = Counter()
    step_x = max(1, (rect.width() - 2 * inset) // 8)
    step_y = max(1, (rect.height() - 2 * inset) // 4)
    for x in range(inset, rect.width() - inset, step_x):
        for y in range(inset, rect.height() - inset, step_y):
            counts[image.pixelColor(origin + QPoint(x, y)).name().lower()] += 1
    return counts.most_common(1)[0][0]


def _diff_pixels(before, after, widget, ancestor) -> int:
    """Pixels inside *widget* whose RGB changed by >=40/255 in any channel."""
    rect = widget.rect()
    origin = widget.mapTo(ancestor, QPoint(0, 0))
    changed = 0
    for x in range(rect.width()):
        for y in range(rect.height()):
            point = origin + QPoint(x, y)
            if _max_channel_diff(
                before.pixelColor(point), after.pixelColor(point)
            ) >= 40:
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# T-5 -- keyboard focus ring visibility and size invariance (H-1)
# ---------------------------------------------------------------------------


class FocusRingTests(_SurfaceTestBase):
    """T-5: every button family paints a visible ring without changing size."""

    @staticmethod
    def _toolbar_buttons(window) -> list:
        toolbar = window.findChild(QToolBar, "mainToolbar")
        buttons = []
        for action in toolbar.actions():
            widget = toolbar.widgetForAction(action)
            if isinstance(widget, QToolButton):
                buttons.append(widget)
        return buttons

    def test_t5_focus_ring_is_visible_and_keeps_size(self):
        from raw_view.gui.app import MainWindow
        from raw_view.gui.dialogs import SettingsDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                self.settings.ui_font_size = 13

                dialog = self._track(SettingsDialog(self.settings))
                window = self._track(MainWindow())
                window.settings = self.settings
                window.resize(1180, 800)
                window._apply_theme()
                dialog.show()
                window.show()
                # The empty-state panel disables every control; enable it so the
                # real panel Apply button (accentButton[applyPrimary]) can take
                # keyboard focus for the solid-button ring check.
                window.panel.set_enabled(True)
                self._settle()

                # M-2: the delivered 30px/34px static heights must survive the
                # 2px focus border (Qt min/max-height excludes the border).
                save_btn = dialog.findChild(QPushButton, "accentButton")
                self.assertIsNotNone(save_btn)
                self.assertEqual(
                    save_btn.height(), 30,
                    f"{theme}: 静止 accentButton 高度必须是 30，实测 "
                    f"{save_btn.height()}",
                )
                self.assertEqual(
                    dialog.template_reset_btn.height(), 30,
                    f"{theme}: 静止 secondaryButton 高度必须是 30，实测 "
                    f"{dialog.template_reset_btn.height()}",
                )
                self.assertEqual(
                    window.panel.apply_btn.height(), 34,
                    f"{theme}: 静止 applyPrimary 高度必须是 34，实测 "
                    f"{window.panel.apply_btn.height()}",
                )

                tool_buttons = self._toolbar_buttons(window)
                self.assertGreaterEqual(
                    len(tool_buttons), 2, f"{theme}: 工具栏至少要有两个 QToolButton"
                )
                apply_btn = window.panel.apply_btn
                self.assertEqual(apply_btn.objectName(), "accentButton")
                self.assertTrue(
                    apply_btn.isEnabled(), f"{theme}: 面板 Apply 按钮必须可聚焦"
                )
                cases = (
                    (
                        "accentButton(applyPrimary)",
                        apply_btn,
                        window,
                        window.panel.width_spin,
                    ),
                    (
                        "secondaryButton",
                        dialog.template_reset_btn,
                        dialog,
                        dialog.template_edit,
                    ),
                    (
                        "infoButton",
                        dialog.template_help_icon,
                        dialog,
                        dialog.template_edit,
                    ),
                    ("QToolButton", tool_buttons[0], window, tool_buttons[1]),
                )
                try:
                    for label, button, ancestor, neutral in cases:
                        APP.setActiveWindow(ancestor)
                        ancestor.activateWindow()
                        ancestor.raise_()
                        neutral.setFocus(Qt.TabFocusReason)
                        self._settle()
                        self.assertFalse(
                            button.hasFocus(),
                            f"{theme}/{label}: 取样前必须处于未聚焦态",
                        )
                        before = ancestor.grab().toImage()
                        size_before = button.size()

                        button.setFocus(Qt.TabFocusReason)
                        self._settle()
                        self.assertTrue(
                            button.hasFocus(),
                            f"{theme}/{label}: setFocus 后必须真正获得键盘焦点",
                        )
                        after = ancestor.grab().toImage()
                        size_after = button.size()

                        changed = _diff_pixels(before, after, button, ancestor)
                        self.assertGreaterEqual(
                            changed, 40,
                            f"{theme}/{label}: 聚焦前后按钮区域只有 {changed} 个"
                            "像素变化（要求 >=40），焦点环不可见",
                        )
                        self.assertEqual(
                            size_before, size_after,
                            f"{theme}/{label}: 聚焦改变了按钮尺寸 "
                            f"{size_before} -> {size_after}",
                        )
                finally:
                    for widget in (dialog, window):
                        if widget in self._widgets:
                            self._widgets.remove(widget)
                        destroy_widget(widget)


    def test_t5b_every_visible_pushbutton_paints_focus(self):
        """H-1 r2: no QPushButton family (named or generic) is left out."""
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

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                self.settings.ui_font_size = 13
                dialogs = {
                    "Batch": self._track(BatchConvertDialog(self.settings)),
                    "FourCC": self._track(FourCCDialog(self.settings)),
                    "Help": self._track(HelpDialog()),
                    "About": self._track(AboutDialog()),
                    "Keyboard": self._track(KeyboardShortcutsDialog()),
                    "Settings": self._track(SettingsDialog(self.settings)),
                    "Preset": self._track(PresetManagerDialog(self.settings)),
                    "Convert": self._track(ConvertDialog(self.settings)),
                }
                try:
                    for name, dialog in dialogs.items():
                        dialog.show()
                        APP.setActiveWindow(dialog)
                        dialog.activateWindow()
                        dialog.raise_()
                        self._settle()
                        for button in dialog.findChildren(QPushButton):
                            if not button.isVisibleTo(dialog):
                                continue
                            if not button.isEnabled():
                                continue
                            label = (
                                button.objectName()
                                or button.text()
                                or type(button).__name__
                            )
                            button.clearFocus()
                            self._settle()
                            self.assertFalse(
                                button.hasFocus(),
                                f"{theme}/{name}/{label}: 取样前必须未聚焦",
                            )
                            before = dialog.grab().toImage()
                            size_before = button.size()
                            button.setFocus(Qt.TabFocusReason)
                            self._settle()
                            self.assertTrue(
                                button.hasFocus(),
                                f"{theme}/{name}/{label}: 无法获得键盘焦点",
                            )
                            after = dialog.grab().toImage()
                            size_after = button.size()
                            changed = _diff_pixels(
                                before, after, button, dialog
                            )
                            self.assertGreaterEqual(
                                changed, 40,
                                f"{theme}/{name}/{label}: 聚焦前后只有 "
                                f"{changed} 像素变化（要求 >=40）",
                            )
                            self.assertEqual(
                                size_before, size_after,
                                f"{theme}/{name}/{label}: 聚焦改变了尺寸 "
                                f"{size_before} -> {size_after}",
                            )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)


# ---------------------------------------------------------------------------
# T-6 -- input-control border contrast (H-3)
# ---------------------------------------------------------------------------


class InputBorderContrastTests(_SurfaceTestBase):
    """T-6: input borders reach 3:1 against both the field and its surface."""

    def test_t6_input_borders_are_at_least_3_to_1(self):
        from raw_view.gui.dialogs import SettingsDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                palette = THEME_PALETTES[theme]
                expected_border = palette["input_border"].lower()
                expected_inner = palette["input_bg"].lower()

                dialog = self._track(SettingsDialog(self.settings))
                dialog.show()
                APP.setActiveWindow(dialog)
                dialog.activateWindow()
                dialog.raise_()
                # Park focus on a button so no input paints its accent focus
                # border while we sample the idle boundary.
                save_btn = dialog.findChild(QPushButton, "accentButton")
                save_btn.setFocus(Qt.TabFocusReason)
                self._settle()

                try:
                    image = dialog.grab().toImage()
                    surface = _pixel(
                        image, dialog, dialog, 3, dialog.size().height() // 2
                    )
                    self.assertEqual(
                        surface, palette["main_bg"].lower(),
                        f"{theme}: 取样表面必须是 main_bg，实测 {surface}",
                    )

                    for name in ("theme_combo", "dpi_spin", "template_edit"):
                        widget = getattr(dialog, name)
                        self.assertFalse(
                            widget.hasFocus(), f"{theme}/{name}: 取样时必须未聚焦"
                        )
                        inner = _most_common_inset(image, widget, dialog)
                        self.assertEqual(
                            inner, expected_inner,
                            f"{theme}/{name}: 控件内部底色必须是 input_bg，"
                            f"实测 {inner}",
                        )
                        origin = widget.mapTo(dialog, QPoint(0, 0))
                        width, height = widget.size().width(), widget.size().height()
                        samples = (
                            (width // 2, 0),
                            (width // 2, height - 1),
                            (0, height // 2),
                            (width - 1, height // 2),
                        )
                        for x, y in samples:
                            border = image.pixelColor(
                                origin + QPoint(x, y)
                            ).name().lower()
                            self.assertEqual(
                                border, expected_border,
                                f"{theme}/{name}@({x},{y}): 边界色必须是 "
                                f"input_border，实测 {border}",
                            )
                            for bg_label, bg in (
                                ("控件底", inner),
                                ("所在表面", surface),
                            ):
                                ratio = _contrast_ratio(border, bg)
                                self.assertGreaterEqual(
                                    ratio, 3.0,
                                    f"{theme}/{name}@({x},{y}): 边界 {border} "
                                    f"vs {bg_label} {bg} = {ratio:.2f}:1，"
                                    "低于 WCAG 1.4.11 的 3:1",
                                )
                finally:
                    if dialog in self._widgets:
                        self._widgets.remove(dialog)
                    destroy_widget(dialog)


# ---------------------------------------------------------------------------
# T-7 -- disabled drop-down arrow (H-4)
# ---------------------------------------------------------------------------


class DisabledArrowTests(_SurfaceTestBase):
    """T-7: disabled combos show a faded arrow; enabled arrows are stronger."""

    @staticmethod
    def _arrow_stats(window, combo):
        """Return (background, max channel delta, max contrast) in arrow area."""
        image = window.grab().toImage()
        origin = combo.mapTo(window, QPoint(0, 0))
        width, height = combo.size().width(), combo.size().height()
        # Right-hand drop-down box, excluding the outer border and the
        # left-hand divider: the arrow is centred in this band.
        colours = [
            image.pixelColor(origin + QPoint(x, y))
            for x in range(width - 22, width - 3)
            for y in range(4, height - 4)
        ]
        counts: Counter = Counter(c.name().lower() for c in colours)
        bg_name = counts.most_common(1)[0][0]
        bg = next(c for c in colours if c.name().lower() == bg_name)
        max_delta = max(_max_channel_diff(c, bg) for c in colours)
        max_ratio = max(
            _contrast_ratio(c.name().lower(), bg_name) for c in colours
        )
        return bg_name, max_delta, max_ratio

    def test_t7_disabled_combo_arrow_is_visible_but_fainter(self):
        from raw_view.gui.app import MainWindow

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

                combo = window.panel.type_combo
                try:
                    self.assertFalse(
                        combo.isEnabled(),
                        f"{theme}: 空态面板的 type_combo 必须处于禁用态",
                    )
                    disabled_bg, disabled_delta, disabled_ratio = self._arrow_stats(
                        window, combo
                    )
                    self.assertGreaterEqual(
                        disabled_delta, 40,
                        f"{theme}: 禁用 combo 的下拉区没有箭头像素"
                        f"（与背景的最大差仅 {disabled_delta}/255）",
                    )

                    combo.setEnabled(True)
                    self._settle()
                    enabled_bg, enabled_delta, enabled_ratio = self._arrow_stats(
                        window, combo
                    )
                    combo.setEnabled(False)
                    self._settle()

                    self.assertGreaterEqual(
                        enabled_delta, 40,
                        f"{theme}: 对比用的启用箭头不可见"
                        f"（最大差 {enabled_delta}/255）",
                    )
                    # "Fainter" = lower contrast against the field.  In the
                    # light theme that is literally a lighter glyph; on a dark
                    # field a faded glyph sits closer to the background, so the
                    # comparable invariant is the contrast ratio (plan 7.1
                    # "明显弱于启用").
                    self.assertLess(
                        disabled_ratio, enabled_ratio,
                        f"{theme}: 禁用箭头对比度 {disabled_ratio:.2f}:1 "
                        f"不低于启用 {enabled_ratio:.2f}:1"
                        f"（背景 {disabled_bg}/{enabled_bg}）",
                    )
                finally:
                    if window in self._widgets:
                        self._widgets.remove(window)
                    destroy_widget(window)


# ---------------------------------------------------------------------------
# T-8 -- icon colour tokens (M-9)
# ---------------------------------------------------------------------------


class IconTokenTests(_SurfaceTestBase):
    """T-8: toolbar glyphs use icon_color / icon_disabled_color per theme."""

    # Plan 3.1 enabled-icon contrast on panel_bg; +/-0.01 guards token drift.
    _EXPECTED_ENABLED_RATIO = {"light": 5.09, "dark": 5.89}

    @staticmethod
    def _dominant_colour(icon: QIcon, mode) -> str:
        image = icon.pixmap(16, 16, mode).toImage()
        counts: Counter = Counter()
        for x in range(16):
            for y in range(16):
                colour = image.pixelColor(x, y)
                if colour.alpha() > 200:
                    counts[colour.name().lower()] += 1
        return counts.most_common(1)[0][0]

    @staticmethod
    def _window_icon_sources(window) -> dict:
        """Every themed action/icon in the main window (toolbar, menus, panel)."""
        sources = {}
        for action in window.findChildren(QAction):
            if action.property("iconName"):
                sources[f"action:{action.text()}"] = action.icon()
        panel = window.panel
        for attr in (
            "preset_save_action",
            "preset_manage_action",
            "apply_to_all_action",
            "forget_remembered_action",
            "pin_toggle_action",
        ):
            if hasattr(panel, attr):
                sources[f"panel.{attr}"] = getattr(panel, attr).icon()
        sources["panel.filter"] = panel.preset_filter_btn.icon()
        return sources

    def _assert_icon_colour(self, sources, expected: str, context: str) -> None:
        expected = expected.lower()
        self.assertTrue(sources, f"{context}: 没有采集到任何图标")
        for label, icon in sources.items():
            actual = self._dominant_colour(icon, QIcon.Normal)
            self.assertEqual(
                actual, expected,
                f"{context}/{label}: 图标主色必须是 {expected}，实测 {actual}",
            )

    def test_t8_icon_colours_follow_tokens_and_theme(self):
        from raw_view.gui.app import MainWindow

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                self.settings.ui_font_size = 13
                palette = THEME_PALETTES[theme]
                window = self._track(MainWindow())
                window.settings = self.settings
                window.resize(1180, 800)
                window._apply_theme()
                window.show()
                self._settle()
                try:
                    action = window.open_action
                    enabled = self._dominant_colour(
                        action.icon(), QIcon.Normal
                    )
                    disabled = self._dominant_colour(
                        action.icon(), QIcon.Disabled
                    )
                    self.assertEqual(
                        enabled, palette["icon_color"].lower(),
                        f"{theme}: 启用图标主色必须是 icon_color，实测 {enabled}",
                    )
                    self.assertEqual(
                        disabled, palette["icon_disabled_color"].lower(),
                        f"{theme}: 禁用图标主色必须是 icon_disabled_color，"
                        f"实测 {disabled}",
                    )
                    enabled_ratio = _contrast_ratio(
                        enabled, palette["panel_bg"]
                    )
                    disabled_ratio = _contrast_ratio(
                        disabled, palette["panel_bg"]
                    )
                    self.assertAlmostEqual(
                        enabled_ratio,
                        self._EXPECTED_ENABLED_RATIO[theme],
                        delta=0.01,
                        msg=f"{theme}: 启用图标对比度期望 "
                        f"{self._EXPECTED_ENABLED_RATIO[theme]}+/-0.01，"
                        f"实测 {enabled_ratio:.4f}",
                    )
                    self.assertLess(
                        disabled_ratio, enabled_ratio,
                        f"{theme}: 禁用图标对比度 {disabled_ratio:.2f}:1 "
                        f"必须严格低于启用 {enabled_ratio:.2f}:1",
                    )

                    # A live theme switch must regenerate the very same action
                    # icon in the other theme's colour (no stale blue).
                    other = "dark" if theme == "light" else "light"
                    window.settings.ui_theme = other
                    window._apply_theme()
                    self._settle()
                    switched = self._dominant_colour(
                        window.open_action.icon(), QIcon.Normal
                    )
                    self.assertEqual(
                        switched, THEME_PALETTES[other]["icon_color"].lower(),
                        f"{theme}->{other}: 切换主题后 action 图标未跟随"
                        f"（实测 {switched}）",
                    )
                    self.assertNotEqual(
                        switched, enabled,
                        f"{theme}->{other}: 图标颜色在两主题下必须不同",
                    )

                    # L-3 / M-1: every themed icon in the main window (toolbar,
                    # menus and the panel context-menu actions) must follow the
                    # new theme after the live switch.
                    self._assert_icon_colour(
                        self._window_icon_sources(window),
                        THEME_PALETTES[other]["icon_color"],
                        f"{theme}->{other} 主窗口",
                    )

                    extra_widgets = []
                    try:
                        # The live-switch block above changed
                        # self.settings.ui_theme to *other* (same object as
                        # window.settings).  Fresh dialogs must be constructed
                        # for the iteration's theme even though the current
                        # app stylesheet is the other one.
                        self.settings.ui_theme = theme
                        from raw_view.gui.dialogs import (
                            ConvertDialog,
                            PresetManagerDialog,
                            SettingsDialog,
                        )
                        from raw_view.gui.panels import ControlPanel
                        from raw_view.gui.widgets.variant_selector import (
                            VariantSelector,
                        )

                        # Fresh construction with an explicit theme: no prior
                        # _apply_theme call, the app palette still says light.
                        panel = ControlPanel(theme=theme)
                        extra_widgets.append(panel)
                        fresh_sources = {
                            "filter": panel.preset_filter_btn.icon(),
                            "preset_save": panel.preset_save_action.icon(),
                            "preset_manage": panel.preset_manage_action.icon(),
                            "apply_to_all": panel.apply_to_all_action.icon(),
                            "forget": panel.forget_remembered_action.icon(),
                            "pin_toggle": panel.pin_toggle_action.icon(),
                        }
                        self._assert_icon_colour(
                            fresh_sources, palette["icon_color"],
                            f"{theme} fresh ControlPanel",
                        )

                        selector = VariantSelector(theme=theme)
                        extra_widgets.append(selector)
                        info_buttons = selector.findChildren(
                            QPushButton, "variantInfoButton"
                        )
                        self.assertTrue(
                            info_buttons,
                            f"{theme}: VariantSelector 缺少 variantInfoButton",
                        )
                        self._assert_icon_colour(
                            {
                                f"variantInfo{i}": button.icon()
                                for i, button in enumerate(info_buttons)
                            },
                            palette["icon_color"],
                            f"{theme} fresh VariantSelector",
                        )
                        selector.apply_theme(other)
                        self._assert_icon_colour(
                            {
                                f"variantInfo{i}": button.icon()
                                for i, button in enumerate(info_buttons)
                            },
                            THEME_PALETTES[other]["icon_color"],
                            f"{theme}->{other} VariantSelector",
                        )

                        convert = ConvertDialog(self.settings)
                        extra_widgets.append(convert)
                        convert_info = convert.findChildren(
                            QPushButton, "variantInfoButton"
                        )
                        self.assertTrue(
                            convert_info,
                            f"{theme}: ConvertDialog 缺少 variantInfoButton",
                        )
                        self._assert_icon_colour(
                            {
                                f"convertInfo{i}": button.icon()
                                for i, button in enumerate(convert_info)
                            },
                            palette["icon_color"],
                            f"{theme} fresh ConvertDialog",
                        )

                        preset = PresetManagerDialog(self.settings)
                        extra_widgets.append(preset)
                        self._assert_icon_colour(
                            {
                                "preset.add": preset.add_btn.icon(),
                                "preset.delete": preset.delete_btn.icon(),
                            },
                            palette["icon_color"],
                            f"{theme} fresh PresetManager",
                        )

                        settings_dialog = SettingsDialog(self.settings)
                        extra_widgets.append(settings_dialog)
                        settings_sources = {
                            "settings.templateHelp": (
                                settings_dialog.template_help_icon.icon()
                            ),
                        }
                        variant_help = settings_dialog.findChild(
                            QPushButton, "variantHelp"
                        )
                        self.assertIsNotNone(variant_help)
                        settings_sources["settings.variantHelp"] = variant_help.icon()
                        self._assert_icon_colour(
                            settings_sources, palette["icon_color"],
                            f"{theme} fresh SettingsDialog",
                        )
                    finally:
                        for widget in extra_widgets:
                            destroy_widget(widget)

                    if theme == "dark":
                        # M-1: dark non-toolbar glyphs must not regress below
                        # the old main-baseline contrast (5.055:1).
                        self.assertGreaterEqual(
                            _contrast_ratio(
                                palette["icon_color"], palette["panel_bg"]
                            ),
                            5.05,
                            "dark icon_color 对 panel_bg 低于 main 基线 5.05:1",
                        )
                finally:
                    if window in self._widgets:
                        self._widgets.remove(window)
                    destroy_widget(window)


# ---------------------------------------------------------------------------
# T-9 -- Batch table alternating rows (B1-L2)
# ---------------------------------------------------------------------------


class BatchZebraTests(_SurfaceTestBase):
    """T-9: Batch rows alternate panel_bg / main_bg in both themes."""

    def test_t9_batch_table_has_alternating_rows(self):
        from raw_view.gui.dialogs import BatchConvertDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                palette = THEME_PALETTES[theme]
                panel_bg = palette["panel_bg"].lower()
                main_bg = palette["main_bg"].lower()

                dialog = self._track(BatchConvertDialog(self.settings))
                table = dialog._file_table
                # Two rows are enough to prove the alternation and both fit
                # the dialog's default height (a clipped row samples the body
                # colour, not its own stripe).
                table.setRowCount(2)
                for row in range(2):
                    for column in range(4):
                        table.setItem(
                            row, column, QTableWidgetItem(f"r{row}c{column}")
                        )
                dialog.show()
                self._settle()
                try:
                    self.assertTrue(
                        table.alternatingRowColors(),
                        f"{theme}: Batch 表格必须开启交替行",
                    )
                    image = dialog.grab().toImage()
                    viewport = table.viewport()
                    row_colours = []
                    for row in range(2):
                        rect = table.visualItemRect(table.item(row, 0))
                        x = rect.right() - 6
                        y = rect.center().y()
                        counts: Counter = Counter()
                        for dx in (-6, -4, -2):
                            point = viewport.mapTo(
                                dialog, QPoint(x + dx, y)
                            )
                            counts[
                                image.pixelColor(point).name().lower()
                            ] += 1
                        row_colours.append(counts.most_common(1)[0][0])
                    self.assertEqual(
                        row_colours[0], panel_bg,
                        f"{theme}: Batch 首行必须是 panel_bg，"
                        f"实测 {row_colours}",
                    )
                    self.assertEqual(
                        row_colours[1], main_bg,
                        f"{theme}: Batch 交替行必须是 main_bg，"
                        f"实测 {row_colours}",
                    )
                finally:
                    if dialog in self._widgets:
                        self._widgets.remove(dialog)
                    destroy_widget(dialog)
