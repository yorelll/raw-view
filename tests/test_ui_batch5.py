"""0.6.5 batch 5 — status-bar split, toolbar density, empty-state recents.

Contract: ``docs/ui-review/ui-fix-plan-0.6.5-batch5.md`` §4 (r2: the state
label accepts up to 24 px from the right edge so the native size grip stays).
Every visual measurement runs in **both** themes at the default 13 px UI font
(V-2 also shrinks the window to 700 px, the narrow layout the report
describes).

Teardown goes through ``destroy_widget`` and the L3 widget-leak guard, per
``CLAUDE.md`` §4.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from _settings_isolation import ensure_test_settings_file  # noqa: E402

ensure_test_settings_file()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qt_cleanup import (  # noqa: E402
    begin_widget_leak_check,
    destroy_widget,
    end_widget_leak_check,
)
from PyQt5.QtCore import QEvent, QSettings, QSize  # noqa: E402
from PyQt5.QtGui import QColor, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QListWidget,
    QPushButton,
    QSizeGrip,
    QToolBar,
    QToolButton,
)

from raw_view.models import (  # noqa: E402
    AppSettings,
    MATERIAL_EXTRA,
    THEME_PALETTES,
    THEME_XML,
    build_ui_stylesheet,
)

APP = QApplication.instance() or QApplication([])
_THEMES = ("light", "dark")


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


class _Batch5TestBase(unittest.TestCase):
    """Isolated settings + tracked widgets + L3 leak guard."""

    def setUp(self):
        super().setUp()
        begin_widget_leak_check()
        self._widgets: list = []
        self._tmp = tempfile.mkdtemp(prefix="rv-batch5-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.addCleanup(APP.setStyleSheet, APP.styleSheet())
        self.settings = AppSettings(
            QSettings(
                os.path.join(self._tmp, "settings.ini"), QSettings.IniFormat
            )
        )
        self.settings.ui_theme = "light"
        self.settings.ui_font_size = 13

    def tearDown(self):
        while self._widgets:
            destroy_widget(self._widgets.pop())
        end_widget_leak_check(f"{type(self).__name__}.{self._testMethodName}")

    def _track(self, widget):
        self._widgets.append(widget)
        return widget

    def _settle(self):
        APP.processEvents()

    def _window(self, theme: str, width: int = 1200, height: int = 800):
        from raw_view.gui.app import MainWindow

        _apply_product_theme(theme)
        self.settings.ui_theme = theme
        self.settings.ui_font_size = 13
        window = self._track(MainWindow())
        window.settings = self.settings
        window._apply_theme()
        window.resize(width, height)
        window.show()
        self._settle()
        return window

    @staticmethod
    def _toolbar(window) -> QToolBar:
        toolbar = window.findChild(QToolBar, "mainToolbar")
        assert toolbar is not None, "main toolbar missing"
        return toolbar

    def _toolbar_buttons(self, window) -> list:
        toolbar = self._toolbar(window)
        buttons = []
        for action in toolbar.actions():
            widget = toolbar.widgetForAction(action)
            if isinstance(widget, QToolButton):
                buttons.append(widget)
        return buttons


# ---------------------------------------------------------------------------
# V-1 — file info sits on the left, decode state on the right
# ---------------------------------------------------------------------------


class StatusBarSplitTests(_Batch5TestBase):
    def test_v1_file_left_state_right_with_space_between(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                window.file_status.setText(
                    "File: sample_frame.raw (12,345 bytes)"
                )
                window.file_status.setToolTip("/shots/sample_frame.raw")
                window._set_state("Decoded 1200x800", "ok")
                self._settle()

                bar = window.status
                file_label = window.file_status
                state_label = window.state_status
                self.assertLessEqual(
                    file_label.x(), 12,
                    f"{theme}: 文件标签左边缘 x={file_label.x()} > 12",
                )
                # The native size grip sits in the bottom-right corner; the
                # state label ends just before it.  24 px is the accepted
                # margin (r2: keeping the affordance beats a 12 px margin).
                self.assertTrue(
                    bar.isSizeGripEnabled(),
                    f"{theme}: 原生 size grip 被关闭",
                )
                grips = [
                    grip
                    for grip in bar.findChildren(QSizeGrip)
                    if grip.isVisible()
                ]
                self.assertTrue(
                    grips, f"{theme}: 原生 size grip 不可见"
                )
                state_right_gap = bar.width() - (
                    state_label.x() + state_label.width()
                )
                self.assertLessEqual(
                    state_right_gap, 24,
                    f"{theme}: 状态标签右边缘距状态栏右边缘 "
                    f"{state_right_gap}px > 24",
                )
                # The visible text ends well before the state label: the two
                # regions are separated, not stacked on the right.
                text_width = QFontMetrics(
                    file_label.font()
                ).horizontalAdvance(file_label.full_text())
                space_between = state_label.x() - (file_label.x() + text_width)
                self.assertGreater(
                    space_between, 100,
                    f"{theme}: 文件文字与状态文字之间只有 {space_between}px",
                )
                self.assertEqual(
                    state_label.elided_text(), "Decoded 1200x800",
                    f"{theme}: 宽窗口下状态文字应完整",
                )


# ---------------------------------------------------------------------------
# V-2 — a narrow window elides the path first, never the state text
# ---------------------------------------------------------------------------


class NarrowStatusTests(_Batch5TestBase):
    def test_v2_narrow_window_elides_file_keeps_state_text(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                window.resize(700, 700)
                self._settle()
                self.assertEqual(
                    window.width(), 700,
                    f"{theme}: 13px 字体下窗口应能缩到 700 宽",
                )

                long_name = "very_long_frame_name_" * 8 + ".raw"
                window.file_status.setText(
                    f"File: {long_name} (12,345 bytes)"
                )
                window._set_state("Decoded 1200x800", "ok")
                self._settle()

                file_label = window.file_status
                state_label = window.state_status
                self.assertGreater(
                    file_label.sizeHint().width(), file_label.width(),
                    f"{theme}: 长路径自然宽未超过标签宽，测试前提不成立",
                )
                self.assertNotEqual(
                    file_label.text(), file_label.full_text(),
                    f"{theme}: 窄窗口下文件路径必须被省略",
                )
                self.assertLess(
                    len(file_label.text()), len(file_label.full_text()),
                    f"{theme}: 省略后的路径没有变短：{file_label.text()!r}",
                )

                self.assertEqual(
                    state_label.elided_text(), "Decoded 1200x800",
                    f"{theme}: 文件路径先省略，状态文字必须完整可见",
                )
                full_state = QFontMetrics(
                    state_label.font()
                ).horizontalAdvance("● Decoded 1200x800")
                self.assertGreaterEqual(
                    state_label.contentsRect().width(), full_state,
                    f"{theme}: 状态标签可用宽度 "
                    f"{state_label.contentsRect().width()} < 全文字宽 "
                    f"{full_state}",
                )
                self.assertTrue(state_label.isVisible())


# ---------------------------------------------------------------------------
# V-3 / V-4 / V-5 — toolbar density (icon 24 px, gap >= 4, height <= 44)
# ---------------------------------------------------------------------------


class ToolbarDensityTests(_Batch5TestBase):
    def test_v3_every_toolbar_icon_is_24px(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                toolbar = self._toolbar(window)
                buttons = self._toolbar_buttons(window)
                self.assertEqual(
                    len(buttons), 6,
                    f"{theme}: 工具栏按钮数量应为 6，实测 {len(buttons)}",
                )
                self.assertEqual(toolbar.iconSize(), QSize(24, 24))
                for button in buttons:
                    self.assertEqual(
                        button.iconSize(), QSize(24, 24),
                        f"{theme}/{button.toolTip()}: 图标尺寸 "
                        f"{button.iconSize()} != 24x24",
                    )

    def test_v4_neighbouring_buttons_keep_a_4px_gap(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                buttons = self._toolbar_buttons(window)
                self.assertGreaterEqual(len(buttons), 2)
                for left, right in zip(buttons, buttons[1:]):
                    gap = right.x() - (left.x() + left.width())
                    self.assertGreaterEqual(
                        gap, 4,
                        f"{theme}: {left.toolTip()} 与 {right.toolTip()} "
                        f"之间只有 {gap}px",
                    )

    def test_v5_toolbar_height_fits_the_44px_budget(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                toolbar = self._toolbar(window)
                self.assertLessEqual(
                    toolbar.height(), 44,
                    f"{theme}: 工具栏高度 {toolbar.height()} > 44",
                )


# ---------------------------------------------------------------------------
# V-6 — hovering a toolbar button must not squeeze its glyph
# ---------------------------------------------------------------------------


def _colour_bbox(image, colour: str, tolerance: int = 60):
    """Bounding box of pixels within *tolerance* of an ``#RRGGBB`` colour."""
    target = QColor(colour)
    xs: list[int] = []
    ys: list[int] = []
    for x in range(image.width()):
        for y in range(image.height()):
            pixel = image.pixelColor(x, y)
            if (
                abs(pixel.red() - target.red())
                + abs(pixel.green() - target.green())
                + abs(pixel.blue() - target.blue())
                <= tolerance
            ):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, len(xs))


class ToolbarHoverTests(_Batch5TestBase):
    def test_v6_hover_does_not_narrow_the_icon(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                button = self._toolbar_buttons(window)[0]
                palette = THEME_PALETTES[theme]

                before = button.grab().toImage()
                bbox_before = _colour_bbox(before, palette["icon_color"])
                self.assertIsNotNone(
                    bbox_before, f"{theme}: 静止态找不到图标像素"
                )
                self.assertIsNone(
                    _colour_bbox(before, palette["accent_light"], 10),
                    f"{theme}: 静止态不应有 hover 背景",
                )

                QApplication.sendEvent(button, QEvent(QEvent.Enter))
                button.update()
                self._settle()
                self.assertTrue(
                    button.underMouse(), f"{theme}: 合成 Enter 未进入 hover"
                )
                after = button.grab().toImage()
                bbox_after = _colour_bbox(after, palette["icon_color"])
                hover_bg = _colour_bbox(after, palette["accent_light"], 10)
                self.assertIsNotNone(
                    hover_bg, f"{theme}: hover 后未出现 accent_light 背景"
                )
                QApplication.sendEvent(button, QEvent(QEvent.Leave))
                button.update()
                self._settle()
                self.assertFalse(button.underMouse())

                self.assertIsNotNone(bbox_after)
                self.assertLessEqual(
                    abs(bbox_after[0] - bbox_before[0]), 1,
                    f"{theme}: hover 前后图标外接框宽差 "
                    f"{abs(bbox_after[0] - bbox_before[0])}px > 1",
                )
                self.assertLessEqual(
                    abs(bbox_after[1] - bbox_before[1]), 1,
                    f"{theme}: hover 前后图标外接框高差 "
                    f"{abs(bbox_after[1] - bbox_before[1])}px > 1",
                )


# ---------------------------------------------------------------------------
# V-7 — the empty state no longer lists recent files
# ---------------------------------------------------------------------------


class EmptyStateRecentsRemovedTests(_Batch5TestBase):
    def test_v7_empty_state_has_no_recent_list_but_keeps_open(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                empty = window.empty_state
                self.assertTrue(empty.isVisible(), f"{theme}: 空态未显示")
                self.assertFalse(
                    empty.findChildren(QListWidget),
                    f"{theme}: 空态里仍有承载最近文件的 QListWidget",
                )
                for removed in (
                    "_empty_recent_list",
                    "_empty_recent_label",
                    "_refresh_empty_recent_list",
                    "_on_empty_recent_clicked",
                ):
                    self.assertFalse(
                        hasattr(window, removed),
                        f"{theme}: {removed} 应随 L-1 回退一起删除",
                    )
                titles = [
                    label.text()
                    for label in empty.findChildren(QLabel)
                ]
                self.assertIn("No image loaded", titles)
                self.assertNotIn("Recent files", titles)
                self.assertTrue(
                    any(
                        label.accessibleName() == "Bayer colour mark"
                        for label in empty.findChildren(QLabel)
                    ),
                    f"{theme}: Bayer 标记丢失",
                )
                open_buttons = [
                    button
                    for button in empty.findChildren(QPushButton)
                    if button.text().startswith("Open File")
                ]
                self.assertTrue(
                    open_buttons, f"{theme}: Open File… 按钮丢失"
                )
                self.assertTrue(
                    open_buttons[0].isVisible(),
                    f"{theme}: Open File… 按钮不可见",
                )


# ---------------------------------------------------------------------------
# V-8 — the File → Recent Files menu still opens entries
# ---------------------------------------------------------------------------


class FileMenuRecentTests(_Batch5TestBase):
    def test_v8_file_menu_recent_submenu_still_opens(self):
        paths = [
            os.path.join(self._tmp, "recent_a.raw"),
            os.path.join(self._tmp, "recent_b.raw"),
        ]
        for path in paths:
            self.settings.add_recent_file(path)
        # ``add_recent_file`` puts the newest entry first.
        expected = list(reversed(paths))
        for theme in _THEMES:
            with self.subTest(theme=theme):
                window = self._window(theme)
                window._refresh_recent_files_menu()
                self._settle()

                menu = window.recent_menu
                self.assertEqual(menu.title(), "Recent Files")
                self.assertEqual(
                    [action.text() for action in menu.actions()], expected,
                    f"{theme}: 菜单条目应为最新在前 {expected}",
                )
                self.assertFalse(
                    menu.isEmpty(), f"{theme}: Recent Files 子菜单为空"
                )

                with patch.object(
                    type(window), "_open_recent_file", autospec=True
                ) as opener:
                    menu.actions()[0].trigger()
                    self._settle()
                opener.assert_called_once_with(window, expected[0])
