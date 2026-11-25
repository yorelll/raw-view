"""0.6.4 batch 4 — dialog consistency and detail-polish acceptance (V-1…V-14).

Real-render where the requirement is visual (fit scrollbars, stepper pixels,
icon contrast, About wrapping); token/form assertions where the requirement is
structural.  Every measurement runs in **both** themes unless the review
explicitly scopes it to Dark (V-5 disabled contrast, V-11 M-14 was Dark-only).

Teardown goes through ``destroy_widget`` and the L3 widget-leak guard, per
``CLAUDE.md`` §4.
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
from PyQt5.QtGui import (  # noqa: E402
    QColor,
    QFontMetrics,
    QIcon,
    QImage,
    QPixmap,
    QTextLayout,
)
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
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


def _dominant_opaque(icon: QIcon, size, mode=QIcon.Normal) -> str:
    image = icon.pixmap(size, mode).toImage()
    counts: Counter = Counter()
    for x in range(image.width()):
        for y in range(image.height()):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 200:
                counts[colour.name().lower()] += 1
    assert counts, "icon has no opaque pixels"
    return counts.most_common(1)[0][0]


def _alpha_bbox(icon: QIcon, size) -> tuple[int, int]:
    image = icon.pixmap(size, QIcon.Normal).toImage()
    xs, ys = [], []
    for x in range(image.width()):
        for y in range(image.height()):
            if image.pixelColor(x, y).alpha() > 30:
                xs.append(x)
                ys.append(y)
    assert xs, "icon rendered an empty pixmap"
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1)


class _Batch4TestBase(unittest.TestCase):
    """Isolated settings + tracked widgets + L3 leak guard."""

    def setUp(self):
        super().setUp()
        begin_widget_leak_check()
        self._widgets: list = []
        self._tmp = tempfile.mkdtemp(prefix="rv-batch4-")
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


# ---------------------------------------------------------------------------
# V-1 — Fit leaves no scrollbars at 1366x768 and 800x600 (H-5)
# ---------------------------------------------------------------------------


class FitScrollbarTests(_Batch4TestBase):
    @staticmethod
    def _pixmap_2560x1440() -> QPixmap:
        image = QImage(2560, 1440, QImage.Format_RGB888)
        image.fill(QColor("#808080"))
        return QPixmap.fromImage(image)

    def test_v1_fit_has_no_scrollbars_at_both_window_sizes(self):
        from raw_view.gui.app import MainWindow

        path = os.path.join(self._tmp, "big_frame.raw")
        with open(path, "wb") as handle:
            handle.write(b"\x00" * 4096)

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                window = self._track(MainWindow())
                window.settings = self.settings
                window.resize(1366, 768)
                window._apply_theme()
                window.show()
                self._settle()
                window._open_item(path, decode=False)
                self._settle()
                view = window.items[0].view
                view.set_pixmap(self._pixmap_2560x1440())
                view.fit_image()
                self._settle()

                for width, height in ((1366, 768), (800, 600)):
                    window.resize(width, height)
                    self._settle()
                    view.fit_image()
                    self._settle()
                    self.assertFalse(
                        view.horizontalScrollBar().isVisible(),
                        f"{theme}/{width}x{height}: Fit 后仍有横向滚动条",
                    )
                    self.assertFalse(
                        view.verticalScrollBar().isVisible(),
                        f"{theme}/{width}x{height}: Fit 后仍有纵向滚动条",
                    )
                    viewport = view.viewport().contentsRect()
                    mapped = view.mapFromScene(view.sceneRect()).boundingRect()
                    self.assertLessEqual(
                        mapped.width(), viewport.width(),
                        f"{theme}/{width}x{height}: Fit 图像仍比视口宽 "
                        f"{mapped.width()} > {viewport.width()}",
                    )
                    self.assertLessEqual(
                        mapped.height(), viewport.height(),
                        f"{theme}/{width}x{height}: Fit 图像仍比视口高 "
                        f"{mapped.height()} > {viewport.height()}",
                    )
                destroy_widget(window)
                self._widgets.remove(window)

    def test_v1b_refit_after_viewport_shrinks(self):
        """The resize path alone must re-fit (frame nav appears late)."""
        from raw_view.gui.imageview import ImageView

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                view = self._track(ImageView())
                view.resize(956, 526)
                view.show()
                self._settle()
                view.set_pixmap(self._pixmap_2560x1440())
                view.fit_image()
                self._settle()
                first_zoom = view.zoom_percent
                view.resize(760, 420)
                self._settle()
                self.assertLess(
                    view.zoom_percent, first_zoom,
                    f"{theme}: 视口缩小后没有重新 Fit",
                )
                self.assertFalse(view.horizontalScrollBar().isVisible())
                self.assertFalse(view.verticalScrollBar().isVisible())


# ---------------------------------------------------------------------------
# V-2 — one form density (M-2)
# ---------------------------------------------------------------------------


class FormConsistencyTests(_Batch4TestBase):
    def test_v2_all_forms_use_the_shared_subcard_density(self):
        from raw_view.gui.dialogs import (
            BatchConvertDialog,
            ConvertDialog,
            PresetManagerDialog,
            SettingsDialog,
        )

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                dialogs = {
                    "Convert": self._track(ConvertDialog(self.settings)),
                    "Batch": self._track(BatchConvertDialog(self.settings)),
                    "Settings": self._track(SettingsDialog(self.settings)),
                    "Preset": self._track(PresetManagerDialog(self.settings)),
                }
                try:
                    forms = {
                        "Convert": dialogs["Convert"]._form,
                        "Batch": dialogs["Batch"]._form,
                        "Settings": dialogs["Settings"]._form,
                        "Preset": dialogs["Preset"]._cond_form,
                    }
                    for name, dialog in dialogs.items():
                        self.assertTrue(
                            dialog.findChildren(QFrame, "subcard"),
                            f"{theme}/{name}: 表单没有包进 QFrame#subcard",
                        )
                        form = forms[name]
                        self.assertTrue(
                            form.labelAlignment() & Qt.AlignLeft,
                            f"{theme}/{name}: 标签不是左对齐 "
                            f"(alignment={form.labelAlignment()})",
                        )
                        self.assertEqual(
                            form.verticalSpacing(), 6,
                            f"{theme}/{name}: 行距不是 6 "
                            f"(实测 {form.verticalSpacing()})",
                        )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)


# ---------------------------------------------------------------------------
# V-3 / V-4 — one accent primary + a secondary Cancel/Close, Esc closes (M-3)
# ---------------------------------------------------------------------------


class ActionFooterTests(_Batch4TestBase):
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
            "About": AboutDialog(),
            "Help": HelpDialog(),
            "KeyboardShortcuts": KeyboardShortcutsDialog(),
            "FourCC": FourCCDialog(self.settings),
        }

    _PRIMARY = ("Convert", "Batch", "Settings", "Preset", "FourCC")

    def test_v3_exactly_one_visible_accent_primary(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                dialogs = self._dialogs()
                for dialog in dialogs.values():
                    self._track(dialog)
                try:
                    for name, dialog in dialogs.items():
                        dialog.show()
                        self._settle()
                        accents = [
                            button
                            for button in dialog.findChildren(QPushButton)
                            if button.objectName() == "accentButton"
                            and button.isVisibleTo(dialog)
                        ]
                        expected = 1 if name in self._PRIMARY else 0
                        self.assertEqual(
                            len(accents), expected,
                            f"{theme}/{name}: 可见 accentButton 数量应为 "
                            f"{expected}，实测 {len(accents)}",
                        )
                        if name not in self._PRIMARY:
                            continue
                        close_like = [
                            button
                            for button in dialog.findChildren(QPushButton)
                            if button.objectName() == "secondaryButton"
                            and button.isVisibleTo(dialog)
                            and button.text() in ("Cancel", "Close")
                        ]
                        self.assertTrue(
                            close_like,
                            f"{theme}/{name}: 底部没有 secondary Cancel/Close",
                        )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)

    def test_v4_secondary_dismiss_exists_and_esc_closes(self):
        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                dialogs = self._dialogs()
                for dialog in dialogs.values():
                    self._track(dialog)
                try:
                    for name, dialog in dialogs.items():
                        dialog.show()
                        self._settle()
                        dismiss = [
                            button
                            for button in dialog.findChildren(QPushButton)
                            if button.objectName() == "secondaryButton"
                            and button.isVisibleTo(dialog)
                            and button.text() in ("Cancel", "Close")
                        ]
                        self.assertTrue(
                            dismiss,
                            f"{theme}/{name}: 没有可见的 secondary 取消/关闭",
                        )
                        APP.setActiveWindow(dialog)
                        dialog.activateWindow()
                        dialog.raise_()
                        QTest.keyClick(dialog, Qt.Key_Escape)
                        self._settle()
                        self.assertFalse(
                            dialog.isVisible(),
                            f"{theme}/{name}: Esc 未能关闭对话框",
                        )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)


# ---------------------------------------------------------------------------
# V-5 — frame-nav icons are one qtawesome family (M-7)
# ---------------------------------------------------------------------------


class FrameNavIconTests(_Batch4TestBase):
    def test_v5_nav_icons_non_null_same_family_and_proportional(self):
        from raw_view.gui.framenav import FrameNavBar

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                nav = self._track(FrameNavBar(theme=theme))
                nav.show()
                self._settle()
                sizes = set()
                for button in nav._nav_buttons:
                    icon = button.icon()
                    self.assertFalse(
                        icon.isNull(), f"{theme}: 帧导航按钮图标为空"
                    )
                    self.assertEqual(
                        button.text(), "",
                        f"{theme}: 图标按钮不应再带 Unicode 字形文本",
                    )
                    sizes.add((button.iconSize().width(), button.iconSize().height()))
                    bbox_w, bbox_h = _alpha_bbox(icon, button.iconSize())
                    self.assertLessEqual(
                        abs(bbox_w - bbox_h), 2,
                        f"{theme}: 图标像素外接框 {bbox_w}x{bbox_h} 宽高差 >2 px",
                    )
                self.assertEqual(
                    len(sizes), 1,
                    f"{theme}: 四个按钮的图标尺寸不一致：{sizes}",
                )

    def test_v5b_dark_disabled_icon_is_fainter_but_still_2_5_to_1(self):
        from raw_view.gui.framenav import FrameNavBar

        _apply_product_theme("dark")
        nav = self._track(FrameNavBar(theme="dark"))
        nav.show()
        self._settle()
        palette = THEME_PALETTES["dark"]
        for button in nav._nav_buttons:
            size = button.iconSize()
            enabled = _dominant_opaque(button.icon(), size, QIcon.Normal)
            disabled = _dominant_opaque(button.icon(), size, QIcon.Disabled)
            self.assertEqual(
                enabled, palette["nav_button_text"].lower(),
                "Dark: 启用图标未使用 nav_button_text",
            )
            self.assertEqual(
                disabled, palette["nav_button_disabled_text"].lower(),
                "Dark: 禁用图标未使用 nav_button_disabled_text",
            )
            enabled_ratio = _contrast_ratio(
                enabled, palette["nav_button_bg"]
            )
            disabled_ratio = _contrast_ratio(
                disabled, palette["nav_button_disabled_bg"]
            )
            self.assertGreaterEqual(
                disabled_ratio, 2.5,
                f"Dark: 禁用图标对比度 {disabled_ratio:.2f}:1 < 2.5:1",
            )
            self.assertLess(
                disabled_ratio, enabled_ratio,
                f"Dark: 禁用图标 {disabled_ratio:.2f}:1 不低于启用 "
                f"{enabled_ratio:.2f}:1",
            )


# ---------------------------------------------------------------------------
# V-6 — QSpinBox steppers are visible and clickable, height unchanged (M-8)
# ---------------------------------------------------------------------------


class SpinStepperTests(_Batch4TestBase):
    def test_v6_stepper_arrows_click_and_height(self):
        from raw_view.gui.app import MainWindow

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                window = self._track(MainWindow())
                window.settings = self.settings
                window._apply_theme()
                window.show()
                self._settle()

                spin = self._track(QSpinBox())
                spin.setRange(0, 100)
                spin.setValue(5)
                spin.show()
                self._settle()

                reference = self._track(QLineEdit("x"))
                reference.show()
                self._settle()
                self.assertEqual(
                    spin.height(), reference.height(),
                    f"{theme}: 步进器改变了 QSpinBox 高度 "
                    f"({spin.height()} vs {reference.height()})",
                )

                image = spin.grab().toImage()
                background = THEME_PALETTES[theme]["input_bg"].lower()
                arrow_pixels = 0
                for x in range(spin.width() - 16, spin.width()):
                    for y in range(spin.height()):
                        colour = image.pixelColor(x, y)
                        if max(
                            abs(colour.red() - int(background[1:3], 16)),
                            abs(colour.green() - int(background[3:5], 16)),
                            abs(colour.blue() - int(background[5:7], 16)),
                        ) >= 40:
                            arrow_pixels += 1
                self.assertGreaterEqual(
                    arrow_pixels, 10,
                    f"{theme}: QSpinBox 右侧 16 px 没有箭头像素 "
                    f"(仅 {arrow_pixels} 个)",
                )

                before = spin.value()
                QTest.mouseClick(
                    spin, Qt.LeftButton, pos=QPoint(spin.width() - 8, 4)
                )
                self._settle()
                self.assertEqual(
                    spin.value(), before + 1,
                    f"{theme}: 点击 SC_SpinBoxUp 未使值 +1",
                )


# ---------------------------------------------------------------------------
# V-7 — Mode selector ≤260 px and aligned with the field column (M-10)
# ---------------------------------------------------------------------------


class ModeWidthTests(_Batch4TestBase):
    def test_v7_mode_combo_is_narrow_and_aligned(self):
        from raw_view.gui.dialogs import ConvertDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                dialog = self._track(ConvertDialog(self.settings))
                dialog.show()
                self._settle()
                combo = dialog.mode_selector
                self.assertLessEqual(
                    combo.width(), 260,
                    f"{theme}: Mode 下拉宽 {combo.width()} > 260",
                )
                field_left = dialog.input_type.mapTo(
                    dialog, QPoint(0, 0)
                ).x()
                combo_left = combo.mapTo(dialog, QPoint(0, 0)).x()
                self.assertLessEqual(
                    abs(combo_left - field_left), 2,
                    f"{theme}: Mode 左缘 {combo_left} 与字段列 {field_left} "
                    "相差 >2 px",
                )


# ---------------------------------------------------------------------------
# V-8 — Settings width, template visibility and cursor (M-11)
# ---------------------------------------------------------------------------


class SettingsGeometryTests(_Batch4TestBase):
    def test_v8_settings_is_wide_enough_and_starts_at_the_left(self):
        from raw_view.gui.dialogs import SettingsDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                dialog = self._track(SettingsDialog(self.settings))
                dialog.show()
                self._settle()
                self.assertGreaterEqual(
                    dialog.width(), 560,
                    f"{theme}: Settings 默认宽 {dialog.width()} < 560",
                )
                self.assertGreaterEqual(
                    dialog.template_edit.width(), 340,
                    f"{theme}: 模板字段宽 {dialog.template_edit.width()} < 340",
                )
                self.assertEqual(
                    dialog.template_edit.cursorPosition(), 0,
                    f"{theme}: 模板字段初始光标不在左端",
                )


# ---------------------------------------------------------------------------
# V-9 — one product name (M-12)
# ---------------------------------------------------------------------------


class ProductNameTests(_Batch4TestBase):
    def test_v9_window_menu_and_about_agree(self):
        from raw_view.gui.app import MainWindow
        from raw_view.gui.dialogs import AboutDialog

        self.settings.ui_theme = "light"
        window = self._track(MainWindow())
        window.settings = self.settings
        self.assertEqual(
            window.windowTitle(), "raw-view",
            f"窗口标题应为 raw-view，实测 {window.windowTitle()}",
        )
        menu_texts = [
            action.text()
            for action in window.findChildren(type(window.save_action))
        ]
        self.assertIn(
            "About raw-view", menu_texts,
            "Help 菜单项必须使用 raw-view",
        )

        dialog = self._track(AboutDialog())
        dialog.show()
        self._settle()
        title = dialog.findChild(QLabel, "aboutTitle")
        self.assertIsNotNone(title)
        self.assertEqual(
            title.text(), "raw-view",
            f"About 正文标题应为 raw-view，实测 {title.text()}",
        )
        self.assertEqual(
            dialog.windowTitle(), "About raw-view",
            f"About 窗口标题应为 About raw-view，实测 {dialog.windowTitle()}",
        )


# ---------------------------------------------------------------------------
# V-10 — Save is disabled until pixels exist (M-13)
# ---------------------------------------------------------------------------


class SaveActionStateTests(_Batch4TestBase):
    def test_v10_save_disabled_when_empty_and_enabled_with_display(self):
        import numpy as np

        from raw_view.gui.app import MainWindow

        path = os.path.join(self._tmp, "save_state.raw")
        with open(path, "wb") as handle:
            handle.write(b"\x00" * 256)

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                window = self._track(MainWindow())
                window.settings = self.settings
                window._apply_theme()
                window.show()
                self._settle()
                self.assertFalse(
                    window.save_action.isEnabled(),
                    f"{theme}: 空态 Save 必须禁用",
                )

                window._open_item(path, decode=False)
                self._settle()
                self.assertFalse(
                    window.save_action.isEnabled(),
                    f"{theme}: 打开但未解码（无图像）时 Save 必须禁用",
                )
                item = window.items[0]
                item.current_display = np.zeros((4, 4), dtype=np.uint8)
                window._update_save_action_state()
                self.assertTrue(
                    window.save_action.isEnabled(),
                    f"{theme}: 有图像后 Save 必须可用",
                )
                window.close_item(0)
                self._settle()
                self.assertFalse(
                    window.save_action.isEnabled(),
                    f"{theme}: 关闭最后一个标签后 Save 必须重新禁用",
                )
                destroy_widget(window)
                self._widgets.remove(window)


# ---------------------------------------------------------------------------
# V-11 — group titles reach AA in both themes (M-14)
# ---------------------------------------------------------------------------


class GroupTitleContrastTests(_Batch4TestBase):
    def test_v11_groupbox_title_contrast_and_rendered_colour(self):
        from raw_view.gui.dialogs import KeyboardShortcutsDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                palette = THEME_PALETTES[theme]
                ratio = _contrast_ratio(
                    palette["text_secondary"], palette["main_bg"]
                )
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f"{theme}: QGroupBox 标题 text_secondary on main_bg = "
                    f"{ratio:.2f}:1 < 4.5",
                )
                self.assertIn(
                    "QGroupBox::title", APP.styleSheet(),
                    f"{theme}: 样式表缺少 QGroupBox::title 规则",
                )
                self.assertIn(
                    palette["text_secondary"], APP.styleSheet(),
                    f"{theme}: QGroupBox::title 未使用 text_secondary token",
                )

                dialog = self._track(KeyboardShortcutsDialog())
                dialog.show()
                self._settle()
                boxes = dialog.findChildren(QGroupBox)
                self.assertTrue(boxes, f"{theme}: 快捷键对话框没有 QGroupBox")
                # The rendered title must actually contain the token colour,
                # not merely define a rule that never paints.
                image = dialog.grab().toImage()
                origin = boxes[0].mapTo(dialog, QPoint(0, 0))
                expected = palette["text_secondary"].lower()
                painted = {
                    image.pixelColor(
                        origin + QPoint(x, y)
                    ).name().lower()
                    for x in range(4, boxes[0].width() - 4, 1)
                    for y in range(0, 20)
                }
                self.assertIn(
                    expected, painted,
                    f"{theme}: 分组标题未渲染 text_secondary ({expected})",
                )


# ---------------------------------------------------------------------------
# V-12 — recent files in the empty state (L-1)
# ---------------------------------------------------------------------------


class EmptyRecentFilesTests(_Batch4TestBase):
    def test_v12_recent_block_hidden_empty_then_five_clickable(self):
        from unittest.mock import patch

        from raw_view.gui.app import MainWindow

        self.settings.ui_theme = "light"
        window = self._track(MainWindow())
        window.settings = self.settings
        window._apply_theme()
        window.show()
        self._settle()
        window.settings.clear_recent_files()
        window._refresh_recent_files_menu()
        self._settle()
        self.assertFalse(
            window._empty_recent_list.isVisible(),
            "无最近记录时列表必须隐藏",
        )
        self.assertFalse(
            window._empty_recent_label.isVisible(),
            "无最近记录时标题必须隐藏",
        )

        paths = [
            os.path.join(self._tmp, f"recent_{index}.raw")
            for index in range(7)
        ]
        for path in paths:
            window.settings.add_recent_file(path)
        window._refresh_recent_files_menu()
        self._settle()
        self.assertTrue(window._empty_recent_list.isVisible())
        self.assertTrue(window._empty_recent_label.isVisible())
        self.assertLessEqual(
            window._empty_recent_list.count(), 5,
            "最近文件列表最多 5 条",
        )
        newest = window._empty_recent_list.item(0)
        self.assertEqual(
            newest.data(Qt.UserRole), paths[-1],
            "最近文件按最新在前排列",
        )
        with patch.object(
            MainWindow, "_open_recent_file", autospec=True
        ) as opener:
            window._empty_recent_list.itemClicked.emit(newest)
        opener.assert_called_once_with(window, paths[-1])


# ---------------------------------------------------------------------------
# V-13 — one dialog height policy (L-3)
# ---------------------------------------------------------------------------


class DialogHeightTests(_Batch4TestBase):
    def test_v13_every_dialog_is_within_the_screen_cap(self):
        from raw_view.gui.dialogs import (
            AboutDialog,
            BatchConvertDialog,
            ConvertDialog,
            FourCCDialog,
            HelpDialog,
            PresetManagerDialog,
            SettingsDialog,
        )

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                self.settings.ui_theme = theme
                dialogs = {
                    "Convert": ConvertDialog(self.settings),
                    "Batch": BatchConvertDialog(self.settings),
                    "Settings": SettingsDialog(self.settings),
                    "Preset": PresetManagerDialog(self.settings),
                    "Help": HelpDialog(),
                    "About": AboutDialog(),
                    "FourCC": FourCCDialog(self.settings),
                }
                try:
                    for name, dialog in dialogs.items():
                        self._track(dialog)
                        dialog.show()
                        self._settle()
                        screen = dialog.screen() or APP.primaryScreen()
                        available = screen.availableGeometry()
                        cap = min(int(available.height() * 0.85), 720)
                        # ConvertDialog shadows height() with its QSpinBox; use
                        # size(), exactly like the production helper does.
                        height = dialog.size().height()
                        self.assertLessEqual(
                            height, cap,
                            f"{theme}/{name}: 高度 {height} 超过屏幕上限 "
                            f"{cap}",
                        )
                        self.assertLessEqual(
                            height, available.height(),
                            f"{theme}/{name}: 对话框高于屏幕",
                        )
                finally:
                    for dialog in dialogs.values():
                        if dialog in self._widgets:
                            self._widgets.remove(dialog)
                        destroy_widget(dialog)


# ---------------------------------------------------------------------------
# V-14 — About description wrapping (L-4)
# ---------------------------------------------------------------------------


class AboutWrapTests(_Batch4TestBase):
    def test_v14_description_is_left_aligned_fixed_width_no_orphan(self):
        from raw_view.gui.dialogs import AboutDialog

        for theme in _THEMES:
            with self.subTest(theme=theme):
                _apply_product_theme(theme)
                dialog = self._track(AboutDialog())
                dialog.show()
                self._settle()
                labels = [
                    label
                    for label in dialog.findChildren(QLabel)
                    if "RAW / YUV image viewer" in label.text()
                ]
                self.assertTrue(labels, f"{theme}: 找不到 About 描述")
                desc = labels[0]
                self.assertTrue(desc.wordWrap())
                self.assertTrue(
                    desc.alignment() & Qt.AlignLeft,
                    f"{theme}: About 描述没有左对齐",
                )
                self.assertEqual(
                    desc.width(), 320,
                    f"{theme}: About 描述宽度 {desc.width()} != 320",
                )

                metrics = QFontMetrics(desc.font())
                wrapped = metrics.boundingRect(
                    QRect(0, 0, desc.width(), 100000),
                    Qt.TextWordWrap,
                    desc.text(),
                )
                visual_lines = max(1, wrapped.height() // metrics.lineSpacing())
                # Wrap each explicit paragraph the way QLabel does and make
                # sure the short ``(4:0:0)`` fragment never stands alone.
                for paragraph in desc.text().split("\n"):
                    layout = QTextLayout(paragraph, desc.font())
                    layout.beginLayout()
                    lines = []
                    while True:
                        line = layout.createLine()
                        if not line.isValid():
                            break
                        line.setLineWidth(desc.width())
                        lines.append(
                            paragraph[
                                line.textStart():
                                line.textStart() + line.textLength()
                            ]
                        )
                    layout.endLayout()
                    if lines:
                        self.assertNotEqual(
                            lines[-1].strip(), "(4:0:0)",
                            f"{theme}: (4:0:0) 被挤成单独一行：{lines}",
                        )
                self.assertGreaterEqual(
                    desc.height(),
                    metrics.lineSpacing() * max(3, visual_lines - 1),
                    f"{theme}: 描述高度 {desc.height()} 不足以容纳 "
                    f"{visual_lines} 行（QLabel.height 与文本行数核对）",
                )
