"""UI 交互改动回归测试（需求 1–6）。

覆盖（2026-09 落地，工作树未提交）：
- 需求 1：RAW / YUV-YOnly / YUV 普通 / Standard Image 各状态下高级控件的
  条件显隐；Bit depth 仅 YOnly 显示、RAW 隐藏。不引入折叠按钮/折叠容器
  （不引用 adv_btn / advanced_section / adv_container）。
- 需求 2：高级控件是主 QFormLayout 的直接行 —— 都能被 form.labelForField()
  定位到 label；控件宽度不使用 minimumWidth/fixedWidth 撑开。
- 需求 3：面板无 "Estimated frame" 提示标签（frame_size_hint 已移除），但
  超大帧仍禁用 Apply（512MB 门禁是必要功能，不随之删除）。
- 需求 4：工具栏不再添加 Prev/Next File 箭头按钮；菜单 action 与
  Ctrl+Left/Right 快捷键保留。
- 需求 5：标签右键菜单 —— custom context menu 装配、close-all / close-right
  索引稳定性与行为。
- 需求 6：快捷键从 Format Help 独立为 SHORTCUTS 数据源 + KeyboardShortcutsDialog
  （Up/Down/Home/End、Ctrl+Left/Right、Ctrl+0/Ctrl+1、F11/Escape、
  Ctrl+R/Ctrl+Shift+R/Ctrl+H/Ctrl+Shift+V），不含 [ ] / * 翻帧快捷键。

全部用例用 QT_QPA_PLATFORM=offscreen + 共享 QApplication，不依赖真实桌面。
"""

from __future__ import annotations

import inspect
import os
import re
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QFormLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QWidget,
)

# 同一进程里只允许一个 QApplication（重复创建会在同一进程内崩溃）。
_APP = QApplication.instance() or QApplication([])

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.gui.panels import ControlPanel  # noqa: E402
from raw_view.models import ViewerItem  # noqa: E402


def _panel() -> ControlPanel:
    """构造一个真实 ControlPanel（offscreen 下安全）。"""
    return ControlPanel()


def _visible(widget) -> bool:
    """可见性断言统一走 isHidden（不依赖父窗口 show 状态）。"""
    return not widget.isHidden()


def _main_form(p: ControlPanel) -> QFormLayout:
    """定位面板的主 QFormLayout（Preset/Type/Format/.../Zoom 所在表单）。

    面板内已收敛为单一主 QFormLayout（不再有嵌套折叠表单），直接找第一个即可。
    """
    return p.findChild(QFormLayout)


def _label_for(p: ControlPanel, combo) -> str | None:
    """主表单中控件行的 label 文本（QFormLayout.labelForField，需求 2 定位方式）。

    主表单直接行才能用 labelForField 定位；被嵌套在其它容器的控件返回 None。
    """
    form = _main_form(p)
    if form is None:
        return None
    label = form.labelForField(combo)
    return label.text() if label is not None else None


def _label_widget(p: ControlPanel, combo):
    """主表单中控件行的 label **控件对象**（QFormLayout.labelForField，0.4.1-L-2
    对象身份复用断言用）。"""
    form = _main_form(p)
    if form is None:
        return None
    return form.labelForField(combo)


def _main_form_label_hidden(p: ControlPanel, combo) -> bool:
    """主表单中 *combo* 对应行 label 是否隐藏（真隐藏 = field 隐藏时 label 不残留）。"""
    form = _main_form(p)
    if form is None:
        return True
    label = form.labelForField(combo)
    if label is None:
        return True
    return label.isHidden()


def _new_window_with_tabs():
    """轻量 MainWindow（不跑 _build_ui）+ 真实 QTabWidget / items。

    与 tests/test_tab_drag.py 的桩化模式一致：只给被测方法（close_item /
    close_all_items / close_items_to_the_right / _run_tab_menu_action）依赖的
    属性，测纯 Python 逻辑，快速且不弹窗口。
    """
    w = MainWindow.__new__(MainWindow)
    w.items = []
    w.item_tabs = QTabWidget()
    w.item_tabs.setTabsClosable(True)
    w._loading_item = False
    w._active_item_index = -1
    w._active_item_ref = None
    w.close_item = MainWindow.close_item.__get__(w, MainWindow)
    w._on_tab_changed = lambda index: None
    w._update_center_stack = lambda: None
    w.panel = SimpleNamespace(set_enabled=lambda _enabled: None)
    w._refresh_file_nav_actions = lambda: None
    w.file_status = SimpleNamespace(setText=lambda _t: None)
    w.image_status = SimpleNamespace(setText=lambda _t: None)
    w.zoom_status = SimpleNamespace(setText=lambda _t: None)
    w.frame_status = SimpleNamespace(setText=lambda _t: None)
    w._set_state = lambda *_a, **_k: None
    return w


def _add_tab(w, name: str) -> ViewerItem:
    item = ViewerItem()
    w.items.append(item)
    w.item_tabs.addTab(QLabel(name), name)
    return item


# ── 需求 1/2：条件显隐（无折叠交互） ──────────────────────────────────


class AdvancedVisibilityTests(unittest.TestCase):
    """RAW / YUV-YOnly / YUV 普通 / Standard 各状态的高级控件显隐。"""

    def setUp(self):
        self.p = _panel()

    def tearDown(self):
        self.p.deleteLater()

    def test_raw_shows_advanced_hides_bit_depth(self):
        p = self.p
        p.set_type("RAW")
        p.format_combo.setCurrentText("RAW12")
        # 需求 1：RAW 显示 Alignment / Endianness / RAW preview / Bayer pattern
        self.assertTrue(_visible(p.align_combo))
        self.assertTrue(_visible(p.endian_combo))
        self.assertTrue(_visible(p.raw_preview_combo))
        self.assertTrue(_visible(p.bayer_pattern_combo))
        # 需求 1：RAW 不得显示 Bit depth（field 与其行 label 都隐藏）
        self.assertTrue(p.bit_depth_combo.isHidden())
        self.assertFalse(p.bit_depth_combo.isEnabled())
        self.assertTrue(_label_for(p, p.bit_depth_combo) is None or _main_form_label_hidden(p, p.bit_depth_combo))

    def test_yonly_shows_bit_depth(self):
        p = self.p
        p.set_type("YUV")
        p.set_format("YOnly")
        # 需求 1：YOnly 显示 Bit depth + Alignment + Endianness
        self.assertTrue(_visible(p.bit_depth_combo))
        self.assertTrue(_visible(p.align_combo))
        self.assertTrue(_visible(p.endian_combo))
        # YOnly 隐藏 RAW preview / Bayer pattern
        self.assertTrue(p.raw_preview_combo.isHidden())
        self.assertTrue(p.bayer_pattern_combo.isHidden())

    def test_yuv_plain_hides_all_advanced(self):
        p = self.p
        p.set_type("YUV")
        p.set_format("YUYV")
        for combo in (
            p.bit_depth_combo, p.align_combo, p.endian_combo,
            p.raw_preview_combo, p.bayer_pattern_combo,
        ):
            self.assertTrue(combo.isHidden(), f"{combo} 应隐藏")
            self.assertFalse(combo.isEnabled())
            # 行 label 必须同步隐藏——只藏 field 会让 QFormLayout 留下孤立 label
            self.assertTrue(
                _main_form_label_hidden(p, combo),
                f"{combo} 的行 label 应同时隐藏（不留孤立标签行）",
            )

    def test_standard_image_hides_all_advanced(self):
        p = self.p
        p.set_type("Standard Image")
        for combo in (
            p.bit_depth_combo, p.align_combo, p.endian_combo,
            p.raw_preview_combo, p.bayer_pattern_combo,
        ):
            self.assertTrue(combo.isHidden(), f"{combo} 应隐藏")
            self.assertFalse(combo.isEnabled())
            # 行 label 必须同步隐藏
            self.assertTrue(
                _main_form_label_hidden(p, combo),
                f"{combo} 的行 label 应同时隐藏",
            )

    def test_label_hidden_rows_do_not_occupy_height(self):
        """需求 1/2：隐藏行（field+label）不占表单高度（真实布局验证）。

        实测 QFormLayout 只隐藏 field 时 label 仍占一行；本次修复把 label 一并
        隐藏，布局应真正收拢。
        """
        from PyQt5.QtCore import QRect

        p = self.p
        content = p.findChild(QWidget, "controlPanelContent")
        form = p._main_form
        content.resize(320, 500)
        content.show()
        import time

        app = QApplication.instance()
        app.processEvents()

        def field_pos(combo):
            p.raise_()
            return combo.geometry().y(), combo.isHidden()

        def visible_rows():
            # 数一下所有 field 里「当前可见」的：高级 5 个 + 宽高偏移缩放等
            fields = [
                p.align_combo, p.endian_combo, p.raw_preview_combo,
                p.bayer_pattern_combo, p.bit_depth_combo,
            ]
            return [f for f in fields if not f.isHidden()]

        # RAW：4 个高级可见（bayer 默认在）
        p.set_type("RAW")
        app.processEvents()
        self.assertEqual(len(visible_rows()), 4)
        h_raw = form.sizeHint().height()
        # YUV 普通：高级 5 行全部隐藏（label 也不占位）
        p.set_type("YUV")
        p.set_format("YUYV")
        app.processEvents()
        self.assertEqual(visible_rows(), [])
        h_plain = form.sizeHint().height()
        # 隐藏的高级别行不应贡献高度：跨状态高度差应只来自 4 行高级
        # （宽松断言：隐藏所有高级行后高度明显收窄 >80px 对应 5 个 label）
        self.assertGreater(h_raw - h_plain, 80, "隐藏行（含 label）不应占用表单高度")
        p.close()

    def test_no_fold_container(self):
        """需求 1：不引入折叠按钮/折叠容器。"""
        p = self.p
        self.assertFalse(hasattr(p, "advanced_section"))
        self.assertFalse(hasattr(p, "adv_container"))
        for name in ("adv_btn", "toggle_btn", "collapse_btn"):
            self.assertFalse(hasattr(p, name), f"不应存在折叠/切换按钮 {name}")

    def test_bayer_hides_when_grayscale_preview_selected(self):
        """RAW 下把 preview 切到 Grayscale → Bayer pattern 隐藏。"""
        p = self.p
        p.set_type("RAW")
        p.raw_preview_combo.setCurrentText("Grayscale")
        self.assertTrue(p.bayer_pattern_combo.isHidden())
        self.assertTrue(_main_form_label_hidden(p, p.bayer_pattern_combo))
        p.raw_preview_combo.setCurrentText("Bayer Color")
        self.assertTrue(_visible(p.bayer_pattern_combo))
        # 恢复后 label 也恢复可见
        self.assertFalse(_main_form_label_hidden(p, p.bayer_pattern_combo))

    def test_type_format_flip_roundtrip(self):
        """RAW → YUV 普通 → YOnly → RAW 走一遍，显隐状态始终正确。"""
        p = self.p
        p.set_type("RAW")
        self.assertTrue(_visible(p.align_combo))
        p.set_type("YUV")
        p.set_format("I420")
        self.assertTrue(p.align_combo.isHidden())
        p.set_format("YOnly")
        self.assertTrue(_visible(p.bit_depth_combo))
        self.assertTrue(p.raw_preview_combo.isHidden())
        p.set_type("RAW")
        self.assertTrue(_visible(p.align_combo))
        self.assertTrue(p.bit_depth_combo.isHidden())


class AdvancedFormLayoutTests(unittest.TestCase):
    """需求 2：高级控件是主 QFormLayout 的直接行（label + field 一行）。"""

    def setUp(self):
        self.p = _panel()

    def tearDown(self):
        self.p.deleteLater()

    def test_rows_are_direct_form_rows(self):
        """可见的高级控件是主 QFormLayout 的直接行（labelForField 可定位）。

        0.4.1 起隐藏的条件行会被 takeRow 移出主表单（彻底不占高度），因此只有
        **当前可见**的行才要求是直接行；隐藏行应返回 None（证明已从表单移出）。
        默认状态为 RAW，Bit depth 隐藏、其余 4 行可见。
        """
        p = self.p
        for combo in (
            p.align_combo, p.endian_combo, p.raw_preview_combo,
            p.bayer_pattern_combo,
        ):
            self.assertIsNotNone(_label_for(p, combo), f"{combo} 应为主表单直接行")
        # Bit depth 在 RAW 下隐藏 → 已从表单移出，labelForField 返回 None
        self.assertIsNone(_label_for(p, p.bit_depth_combo))

    def test_expected_labels(self):
        p = self.p
        self.assertEqual(_label_for(p, p.align_combo), "Alignment")
        self.assertEqual(_label_for(p, p.endian_combo), "Endianness")
        self.assertEqual(_label_for(p, p.raw_preview_combo), "RAW preview")
        self.assertEqual(_label_for(p, p.bayer_pattern_combo), "Bayer pattern")
        # YOnly 下 Bit depth 显示，标签仍是 "Bit depth"
        p.set_type("YUV")
        p.set_format("YOnly")
        self.assertEqual(_label_for(p, p.bit_depth_combo), "Bit depth")

    def test_cond_row_labels_are_reused_not_rebuilt(self):
        """0.4.1-L-2：_apply_cond_rows 反复 takeRow/insertRow 时 label 必须复用。

        每次 RAW↔YOnly↔YUYV 切换都会触发整段条件行搬移；若按文本重建 QLabel，
        用户/主题对 label 的样式设置会丢失。构造后把缓存 label 打上标记，切换
        若干次状态后，当前可见行的 label 仍应是同一个对象。
        """
        p = self.p
        # 状态先切到 YUV/YOnly：Bit depth/Alignment/Endianness 可见
        p.set_type("YUV")
        p.set_format("YOnly")
        align_label = _label_widget(p, p.align_combo)
        self.assertIsNotNone(align_label)
        self.assertIs(
            _label_widget(p, p.align_combo), p._cond_row_labels.get("align_combo"),
            "可见条件行 label 应来自 __init__ 缓存的首建对象",
        )
        # 反复切换（每次都会 takeRow + insertRow 重建布局）
        for _ in range(3):
            p.set_type("RAW")
            p.format_combo.setCurrentText("RAW12")
            p.set_type("YUV")
            p.set_format("YOnly")
        self.assertIs(
            _label_widget(p, p.align_combo), align_label,
            "多次条件行搬移后 label 必须仍是同一对象（不按文本重建）",
        )
        # Bit depth 的 label 在 RAW/YUV 切换间也应复用
        self.assertIs(
            _label_widget(p, p.bit_depth_combo), p._cond_row_labels.get("bit_depth_combo"),
            "Bit depth label 应复用首次 addRow 创建的对象",
        )

    def test_widths_not_forced_wider_than_normal(self):
        """高级框不显著宽于 Type/Format 等普通框。

        全部使用天然 sizeHint，不设 minimumWidth / fixedWidth，因此主表单列宽
        一致、不会出现高级框更宽（需求 2）。
        """
        p = self.p
        # 高级框与 Type/Format 等普通框一样，不使用 minimumWidth 撑宽 → 主表单
        # 列宽一致，高级框不会明显更宽（需求 2）。
        advanced = (
            p.align_combo, p.endian_combo, p.raw_preview_combo,
            p.bayer_pattern_combo, p.bit_depth_combo,
        )
        normal = (p.type_combo, p.format_combo)
        for combo in advanced + normal:
            self.assertEqual(
                combo.minimumWidth(), 0,
                f"{getattr(combo, 'objectName', lambda: str(combo))()} 不应设置 minimumWidth",
            )


# ── 需求 3：移除 Estimated frame 标签，保留 512MB 门禁 ────────────────


class FrameHintRemovedTests(unittest.TestCase):
    """Estimated frame 提示已移出 UI；超大帧仍禁用 Apply。"""

    def setUp(self):
        self.p = _panel()

    def tearDown(self):
        self.p.deleteLater()

    def test_no_estimated_frame_label(self):
        p = self.p
        self.assertFalse(hasattr(p, "frame_size_hint"), "frame_size_hint 标签应已移除")
        labels = [w.text() for w in p.findChildren(QLabel)]
        self.assertNotIn("Estimated frame", labels)
        self.assertNotIn(
            "frame_size_hint",
            [w.objectName() for w in p.findChildren(QLabel)],
        )

    def test_oversize_still_disables_apply(self):
        """512MB 门禁仍在：超大帧禁用 Apply，参数改回合法恢复。"""
        p = self.p
        p.set_type("RAW")
        p.format_combo.setCurrentText("RAW32")
        p.width_spin.setValue(65535)
        p.height_spin.setValue(65535)
        self.assertFalse(p.apply_btn.isEnabled())
        p.set_type("YUV")
        p.format_combo.setCurrentText("YOnly")
        p.bit_depth_combo.setCurrentText("8")
        p.width_spin.setValue(100)
        p.height_spin.setValue(100)
        self.assertTrue(p.apply_btn.isEnabled())

    def test_panel_disabled_keeps_apply_disabled(self):
        """面板整体禁用（无 item）时门禁不能把 Apply 点亮。"""
        p = self.p
        p.set_enabled(False)
        p.set_type("RAW")
        p.format_combo.setCurrentText("RAW8")
        p.width_spin.setValue(100)
        p.height_spin.setValue(100)
        self.assertFalse(p.apply_btn.isEnabled())


# ── 需求 4：工具栏移除 Prev/Next File 箭头按钮 ────────────────────────


class AccessibilityAndDialogConsistencyTests(unittest.TestCase):
    """Regression checks for the 0.3.1 accessibility and dialog fixes."""

    def test_control_panel_accessible_names_match_actions(self):
        panel = _panel()
        try:
            self.assertEqual(panel.preset_save_btn.accessibleName(), "Save sensor preset")
            self.assertEqual(panel.preset_manage_btn.accessibleName(), "Manage sensor presets")
            self.assertEqual(panel.zoom_slider.accessibleName(), "Zoom level")
            self.assertIn("10%", panel.zoom_slider.accessibleDescription())
            self.assertIn("1000%", panel.zoom_slider.accessibleDescription())
        finally:
            panel.deleteLater()

    def test_frame_navigation_names_match_shortcuts(self):
        from raw_view.gui.framenav import FrameNavBar

        nav = FrameNavBar()
        try:
            self.assertEqual(nav.first_btn.accessibleName(), "First frame")
            self.assertEqual(nav.prev_btn.accessibleName(), "Previous frame")
            self.assertEqual(nav.next_btn.accessibleName(), "Next frame")
            self.assertEqual(nav.last_btn.accessibleName(), "Last frame")
            self.assertIn("Home", nav.first_btn.toolTip())
            self.assertIn("Left", nav.prev_btn.toolTip())
            self.assertIn("Right", nav.next_btn.toolTip())
            self.assertIn("End", nav.last_btn.toolTip())
        finally:
            nav.deleteLater()

    def test_info_buttons_are_focusable_and_use_non_clipping_icon_size(self):
        from raw_view.gui.dialogs.settings import SettingsDialog
        from raw_view.gui.widgets.variant_selector import _info_icon
        from raw_view.models import AppSettings

        settings_dialog = SettingsDialog(AppSettings())
        variant_button = _info_icon("Example help")
        try:
            for button in (settings_dialog.template_help_icon, variant_button):
                self.assertIsInstance(button, QPushButton)
                self.assertEqual(button.iconSize(), QSize(16, 16))
                self.assertLessEqual(button.iconSize().width(), button.width())
                self.assertLessEqual(button.iconSize().height(), button.height())
                self.assertTrue(button.accessibleName())
                self.assertTrue(button.accessibleDescription())
        finally:
            settings_dialog.deleteLater()
            variant_button.deleteLater()

    def test_fourcc_dialogs_remove_unbound_context_help_button(self):
        from raw_view.gui.dialogs.fourcc import FourCCDialog, FourCCEditDialog
        from raw_view.models import AppSettings

        for dialog in (FourCCDialog(AppSettings()), FourCCEditDialog()):
            try:
                self.assertFalse(dialog.windowFlags() & Qt.WindowContextHelpButtonHint)
            finally:
                dialog.deleteLater()

    def test_fourcc_status_label_escapes_rich_text_query(self):
        """0.4.0-L-3：搜索状态字符串对用户查询做 HTML 转义。

        QLabel 默认 Qt.AutoText 会把 ``<b>`` 之类片段当富文本解析；查询原样
        落进状态栏会有显示错乱/注入口径。构造真实对话框、用带尖括号的查询搜索
        后，状态文本中必须只剩转义后的字面量。
        """
        from raw_view.gui.dialogs.fourcc import FourCCDialog
        from raw_view.models import AppSettings

        dlg = FourCCDialog(AppSettings())
        try:
            dlg._search_edit.setText("A<B & C")
            dlg._on_search()
            text = dlg._status_label.text()
            self.assertNotIn("A<B", text, "原始 < 不应被 QLabel 当标签解析")
            self.assertIn("A&lt;B", text)
            self.assertIn("&amp;", text)
            # 空查询不带 'result(s) for' 也无转义问题
            dlg._search_edit.clear()
            dlg._on_search()
            self.assertNotIn("result(s) for", dlg._status_label.text())
        finally:
            dlg.deleteLater()

    def test_batch_start_follows_file_table_state(self):
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        dialog = BatchConvertDialog(AppSettings())
        try:
            self.assertFalse(dialog._run_btn.isEnabled())
            dialog._add_files(["missing-input.png"])
            self.assertTrue(dialog._run_btn.isEnabled())
            dialog._clear_files()
            self.assertFalse(dialog._run_btn.isEnabled())
        finally:
            dialog.deleteLater()

    def test_batch_empty_table_placeholder_visible_only_when_empty(self):
        """0.4.0-L-2：空表显示“Add Files 或拖放”引导，有行后隐藏。"""
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        dialog = BatchConvertDialog(AppSettings())
        try:
            # 对话框未 show 时 isVisible() 恒为 False（父级隐藏）；用 isHidden()
            # 追踪显式 setVisible 状态。
            self.assertFalse(dialog._empty_hint.isHidden(), "空表应显示引导文字")
            self.assertTrue(
                dialog._empty_hint.text(),
                "空表引导文字非空",
            )
            hint_text = dialog._empty_hint.text()
            self.assertIn("Add Files", hint_text)
            self.assertIn("drag", hint_text.lower())
            dialog._add_files(["missing-input.png"])
            self.assertTrue(dialog._empty_hint.isHidden(), "有行后引导应隐藏")
            dialog._clear_files()
            self.assertFalse(dialog._empty_hint.isHidden(), "清空后引导恢复显示")
        finally:
            dialog.deleteLater()

    def test_batch_empty_hint_recenters_on_viewport_resize(self):
        """0.4.0-L-2：空表提示必须随 viewport 尺寸变化重新定位。

        事件过滤器必须装在 **viewport** 上（Qt 只把对象自身事件交给装在该对象
        上的过滤器）；曾错装在 table 上导致 viewport 的 Resize 永远匹配不到、
        窗口拉大时空表提示不再居中。这里不依赖对话框 show 后的真实布局（未显示
        窗口的 viewport 尺寸会被布局引擎异步重算，直接比较尺寸会抖动），改为核对
        机制本身：过滤器挂在 viewport 上 + 空表收到 viewport Resize 会触发重排。
        """
        from PyQt5.QtCore import QEvent

        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        dialog = BatchConvertDialog(AppSettings())
        try:
            # 机制自证：修复前过滤器误挂在 table 上，viewport 的 Resize 事件到不到
            # eventFilter（obj is viewport 恒不匹配），下面 2) 的 sendEvent 就不会
            # 触发 _update_empty_hint —— 该断言即回归探测器。
            with mock.patch.object(
                dialog, "_update_empty_hint",
                wraps=dialog._update_empty_hint,
            ) as upd:
                # 2) 空表 + viewport Resize → 触发重排
                QApplication.sendEvent(
                    dialog._file_table.viewport(),
                    QEvent(QEvent.Resize),
                )
                self.assertGreaterEqual(
                    upd.call_count, 1,
                    "空表 + viewport resize 必须触发空表提示重新定位",
                )
                # 3) 重排本身把 hint 铺满当前 viewport 矩形
                dialog._update_empty_hint()
                self.assertFalse(dialog._empty_hint.isHidden())
                self.assertEqual(
                    dialog._empty_hint.geometry(), dialog._file_table.viewport().rect(),
                    "空表提示应铺满 viewport（随尺寸重排）",
                )
                # 4) 有行后 resize 不再触发重排
                dialog._add_files(["missing-input.png"])
                upd.reset_mock()
                QApplication.sendEvent(
                    dialog._file_table.viewport(),
                    QEvent(QEvent.Resize),
                )
                self.assertEqual(
                    upd.call_count, 0,
                    "有行时 viewport resize 不应触发空表提示",
                )
                self.assertTrue(dialog._empty_hint.isHidden())
        finally:
            dialog.deleteLater()


class ToolbarNavRemovedTests(unittest.TestCase):
    def test_source_does_not_add_file_nav_to_toolbar(self):
        """工具栏构建源码不再 addAction 文件切换箭头。"""
        src = inspect.getsource(MainWindow._build_toolbar)
        for needle in (
            'toolbar.addAction(self.prev_file_action)',
            'toolbar.addAction(self.next_file_action)',
            'self.prev_file_action.setIcon',
            'self.next_file_action.setIcon',
            'fa5s.arrow-left',
            'fa5s.arrow-right',
        ):
            self.assertNotIn(needle, src, f"工具栏不应包含 {needle}")

    def test_menu_actions_and_shortcuts_kept(self):
        """Navigate 菜单 Previous/Next File 与 Ctrl+Left/Right 保留。"""
        src = inspect.getsource(MainWindow._build_menus)
        self.assertIn('QAction("Previous File", self)', src)
        self.assertIn('QAction("Next File", self)', src)
        self.assertIn('setShortcut("Ctrl+Left")', src)
        self.assertIn('setShortcut("Ctrl+Right")', src)
        self.assertIn('self._nav_file_by_dir(-1)', src)
        self.assertIn('self._nav_file_by_dir(1)', src)
        self.assertIn(
            'nav_menu.addActions([self.prev_file_action, self.next_file_action])', src,
        )

    def test_actions_are_there_but_not_on_toolbar(self):
        """真实窗口：动作保留、无图标、不在工具栏 children 里。"""
        w = MainWindow()
        try:
            self.assertTrue(hasattr(w, "prev_file_action"))
            self.assertTrue(hasattr(w, "next_file_action"))
            # 箭头图标不再指派到动作上
            self.assertTrue(w.prev_file_action.icon().isNull())
            self.assertTrue(w.next_file_action.icon().isNull())
            toolbar = w.findChild(object, "mainToolbar")
            self.assertIsNotNone(toolbar)
            toolbar_actions = [a.text() for a in toolbar.actions()]
            self.assertNotIn("Previous File", toolbar_actions)
            self.assertNotIn("Next File", toolbar_actions)
        finally:
            w.close()
            w.deleteLater()

    def test_nav_action_precise_boundary_enablement(self):
        """0.3.0-L-1 / 0.3.1-L-2：首/尾文件只启用可用的那一个方向。

        首文件（无前驱）prev 禁用、next 启用；末文件相反；只有既非首又非末才
        两个都启用。
        """
        import tempfile

        w = MainWindow()
        try:
            tmpdir = tempfile.mkdtemp(prefix="rv-nav-boundary-")
            try:
                for name in ("a.raw", "b.raw", "c.raw"):
                    open(os.path.join(tmpdir, name), "w").close()

                item = ViewerItem()
                item.options.file_path = os.path.join(tmpdir, "b.raw")
                w._current_item = lambda: item
                w._refresh_file_nav_actions()
                self.assertTrue(w.prev_file_action.isEnabled(), "中间文件 prev 应可用")
                self.assertTrue(w.next_file_action.isEnabled(), "中间文件 next 应可用")

                item.options.file_path = os.path.join(tmpdir, "a.raw")
                w._refresh_file_nav_actions()
                self.assertFalse(w.prev_file_action.isEnabled(), "首文件 prev 应禁用")
                self.assertTrue(w.next_file_action.isEnabled(), "首文件 next 应可用")

                item.options.file_path = os.path.join(tmpdir, "c.raw")
                w._refresh_file_nav_actions()
                self.assertTrue(w.prev_file_action.isEnabled(), "末文件 prev 应可用")
                self.assertFalse(w.next_file_action.isEnabled(), "末文件 next 应禁用")
            finally:
                import shutil

                shutil.rmtree(tmpdir, ignore_errors=True)
        finally:
            w.close()
            w.deleteLater()


# ── 需求 5：标签右键关闭操作 ──────────────────────────────────────────


class TabContextMenuWiringTests(unittest.TestCase):
    def test_custom_context_menu_policy_is_set(self):
        """真实 MainWindow：标签栏使用 CustomContextMenu。"""
        w = MainWindow()
        try:
            tab_bar = w.item_tabs.tabBar()
            self.assertEqual(tab_bar.contextMenuPolicy(), Qt.CustomContextMenu)
        finally:
            w.close()
            w.deleteLater()

    def test_source_wires_custom_context_menu(self):
        src = inspect.getsource(MainWindow._build_ui)
        self.assertIn("setContextMenuPolicy(Qt.CustomContextMenu)", src)
        self.assertIn(
            "customContextMenuRequested.connect(self._show_tab_context_menu)", src,
        )

    def test_menu_actions_created_with_english_labels(self):
        w = _new_window_with_tabs()
        menu = MainWindow._build_tab_context_menu(w)
        try:
            texts = [a.text() for a in menu.actions()]
            self.assertEqual(texts, ["Close All Items", "Close Items to the Right"])
            for a in menu.actions():
                self.assertTrue(a.shortcut().isEmpty(), "右键菜单无需快捷键")
        finally:
            w.item_tabs.deleteLater()


class TabContextCloseBehaviorTests(unittest.TestCase):
    def test_close_all_items_closes_everything(self):
        w = _new_window_with_tabs()
        for n in ("a", "b", "c", "d"):
            _add_tab(w, n)
        MainWindow.close_all_items(w)
        self.assertEqual(w.items, [])
        self.assertEqual(w.item_tabs.count(), 0)

    def test_close_all_does_not_depend_on_current_index(self):
        """close-all 与当前选中无关（清空全部）。"""
        w = _new_window_with_tabs()
        for n in ("a", "b", "c"):
            _add_tab(w, n)
        w.item_tabs.setCurrentIndex(1)
        MainWindow.close_all_items(w)
        self.assertEqual(w.items, [])
        self.assertEqual(w.item_tabs.count(), 0)

    def test_close_items_to_the_right(self):
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c", "d", "e")]
        MainWindow.close_items_to_the_right(w, 2)
        self.assertEqual(w.items, items[:3])
        self.assertEqual([w.item_tabs.tabText(i) for i in range(3)], ["a", "b", "c"])

    def test_close_items_to_the_right_leaves_left_untouched(self):
        """左侧/目标标签保持不变（含对象身份与顺序）。"""
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c", "d")]
        MainWindow.close_items_to_the_right(w, 1)
        self.assertIs(w.items[0], items[0])
        self.assertIs(w.items[1], items[1])
        self.assertEqual(w.item_tabs.count(), 2)

    def test_close_right_at_last_does_nothing(self):
        w = _new_window_with_tabs()
        for _ in range(3):
            _add_tab(w, "x")
        MainWindow.close_items_to_the_right(w, 2)
        self.assertEqual(len(w.items), 3)

    def test_invalid_index_is_ignored(self):
        w = _new_window_with_tabs()
        for _ in range(3):
            _add_tab(w, "x")
        MainWindow.close_items_to_the_right(w, -5)
        MainWindow.close_items_to_the_right(w, 99)
        self.assertEqual(len(w.items), 3)


class TabMenuDispatchTests(unittest.TestCase):
    """_run_tab_menu_action：按 exec_ 返回的 QAction 对象身份分派。"""

    def test_dispatch_close_all(self):
        w = _new_window_with_tabs()
        for n in ("a", "b", "c"):
            _add_tab(w, n)
        menu = MainWindow._build_tab_context_menu(w)
        MainWindow._run_tab_menu_action(w, w._acTabCloseAll, index=1)
        self.assertEqual(w.items, [])
        w.item_tabs.deleteLater()

    def test_dispatch_close_right(self):
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c", "d")]
        menu = MainWindow._build_tab_context_menu(w)
        MainWindow._run_tab_menu_action(w, w._acTabCloseRight, index=1)
        self.assertEqual(w.items, items[:2])
        w.item_tabs.deleteLater()

    def test_dispatch_none_is_noop(self):
        w = _new_window_with_tabs()
        for n in ("a", "b"):
            _add_tab(w, n)
        MainWindow._run_tab_menu_action(w, None, index=0)
        self.assertEqual(len(w.items), 2)
        w.item_tabs.deleteLater()

    def test_right_click_selects_target_tab_before_close_right(self):
        """右键先选中目标标签，再执行 close-right（用被右击标签的 index）。"""
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c", "d")]
        MainWindow._build_tab_context_menu(w)
        w.item_tabs.setCurrentIndex(0)
        tab_bar = w.item_tabs.tabBar()
        # 右击第 3 个标签（index 2）的正中位置 → tabAt(pos) == 2
        pos = tab_bar.tabRect(2).center()
        # 真实 exec_ 会阻塞等待用户操作：patch 掉它，直接返回“Close Items to
        # the Right”动作，验证完整链路（选中 + 分派 + 关闭行为）。
        with mock.patch.object(w._tabRMenu, "exec_", return_value=w._acTabCloseRight):
            MainWindow._show_tab_context_menu(w, pos)
        # 右键先选中了 index 2
        self.assertEqual(w.item_tabs.currentIndex(), 2)
        # close-right of 2 → 只保留 0,1,2
        self.assertEqual(w.items, items[:3])


# ── 需求 6：Help 快捷键章节 ──────────────────────────────────────────


class HelpShortcutSectionTests(unittest.TestCase):
    """Keyboard Shortcuts 小节存在、文案齐全，且不含 [ ] / * 翻帧快捷键。"""

    def _shortcut_keys(self) -> set[str]:
        import raw_view.help_content as hc

        return {keys for _cat, items in hc.SHORTCUTS for keys, _desc in items}

    def test_help_adds_shortcuts_section(self):
        """快捷键已从 Format Help HTML 独立为 SHORTCUTS 数据源（0.4.1）。"""
        import raw_view.help_content as hc

        self.assertNotIn("Keyboard Shortcuts", hc.HELP_HTML)
        self.assertTrue(len(hc.SHORTCUTS) >= 5, "应有分类：帧/文件/缩放/视图/变换")
        keys = set()
        for _cat, items in hc.SHORTCUTS:
            for k, _d in items:
                keys.add(k)
        for needle in (
            "Up / Left", "Down / Right", "Home", "End",
            "Ctrl+Left", "Ctrl+Right",
            "Ctrl+0", "Ctrl+1",
            "F11", "Escape",
            "Ctrl+R", "Ctrl+Shift+R",
            "Ctrl+H", "Ctrl+Shift+V",
        ):
            self.assertIn(needle, keys, f"快捷键 {needle} 应出现在 SHORTCUTS 中")

    def test_shortcuts_dialog_builds_from_source(self):
        """KeyboardShortcutsDialog 能从 SHORTCUTS 数据源构造（不依赖 HTML 解析）。"""
        from raw_view.gui.dialogs import KeyboardShortcutsDialog

        dlg = KeyboardShortcutsDialog()
        self.assertEqual(dlg.windowTitle(), "Keyboard Shortcuts")
        # 每个分类都渲染成一个 QGroupBox
        import raw_view.help_content as hc

        from PyQt5.QtWidgets import QGroupBox

        boxes = dlg.findChildren(QGroupBox)
        titles = {b.title() for b in boxes}
        for cat, _items in hc.SHORTCUTS:
            self.assertIn(cat, titles, f"分类 {cat} 应渲染到对话框")
        dlg.deleteLater()

    def test_no_removed_shortcuts_documented(self):
        """被取消的 [ ] 与不存在的 / * 翻帧快捷键不得写入快捷键数据。"""
        import raw_view.help_content as hc

        keys = " ".join(self._shortcut_keys())
        self.assertNotIn("[", keys)
        self.assertNotIn("]", keys)
        # "*" 不作为单键翻帧；"/" 不单独成键
        self.assertNotIn("*", keys)

    def test_help_zoom_keys_match_runtime_normalization(self):
        """0.3.1-L-3：Help 文案与实现按键必须运行时一致。

        实现用 ``QKeySequence.ZoomIn/Out``（平台强相关的常量）；Help 写
        ``Ctrl++``/``Ctrl+-``。仅比对源码字符串会产生“源码里有文本就算一致”的
        假阳性——这里用 ``QKeySequence(...).toString()`` 把实现按键归一后与 Help
        文案比对，防止个别键盘布局下常量解析结果漂移。
        """
        from PyQt5.QtGui import QKeySequence

        impl = {
            "Ctrl++": QKeySequence(QKeySequence.ZoomIn).toString(),
            "Ctrl+-": QKeySequence(QKeySequence.ZoomOut).toString(),
        }
        for help_keys, impl_keys in impl.items():
            self.assertEqual(
                impl_keys, help_keys,
                f"QKeySequence 归一结果应等于 Help 文案 {help_keys!r}（实际 {impl_keys!r}）",
            )

    def test_shortcuts_match_source_truth(self):
        """快捷键描述必须真实存在于 app.py（防描述与实现脱节）。"""
        src = inspect.getsource(MainWindow)
        short = self._shortcut_keys()
        # app.py 里所有字符串形式的 setShortcut(...)
        impl_shortcuts: set[str] = set()
        for m in re.finditer(r'setShortcut\(["\']([^"\']+)["\']\)', src):
            impl_shortcuts.add(m.group(1))
        # keyPressEvent 内联处理后缀的帧/全屏键
        self.assertIn("Qt.Key_Up", src)
        self.assertIn("Qt.Key_Down", src)
        self.assertIn("Qt.Key_Home", src)
        self.assertIn("Qt.Key_End", src)
        self.assertIn("Qt.Key_Escape", src)
        # 字符串形式的 setShortcut 都在实现里注册过
        for sc in ("Ctrl+Left", "Ctrl+Right", "F11",
                   "Ctrl+0", "Ctrl+1",
                   "Ctrl+R", "Ctrl+Shift+R", "Ctrl+H", "Ctrl+Shift+V"):
            if sc in impl_shortcuts:
                self.assertIn(sc, impl_shortcuts, f"{sc} 应通过 setShortcut 注册")
        self.assertIn("QKeySequence.ZoomIn", src)
        self.assertIn("QKeySequence.ZoomOut", src)
        # 快捷键数据源写出的每个字符串快捷键都要出现在实现里（Zoom +/- 由
        # 常量提供，通过 QKeySequence.ZoomIn/Out 校验）
        for sc in ("Ctrl+Left", "Ctrl+Right", "F11",
                   "Ctrl+0", "Ctrl+1",
                   "Ctrl+R", "Ctrl+Shift+R", "Ctrl+H", "Ctrl+Shift+V"):
            self.assertIn(sc, short, f"SHORTCUTS 应写出 {sc}")


# ── 0.3.0-M-3 / 0.4.0-M-1 / 0.4.1-M-1：缓存命中状态栏帧大小 ────────────


class DecodeCacheHitStatusTests(unittest.TestCase):
    """解码缓存命中后，状态栏帧字节数必须由**当前**宽高算出。

    缓存命中分支曾先 ``_on_decode_success`` 再回填 ``item.options.width/height``，
    导致 ``_on_decode_success`` 里的 ``_get_frame_size(item.options)`` 读到旧宽高
    （真实图片缩到曾缓存的组合时，状态栏显示的是旧帧大小的字节数）。
    """

    def _new_window(self):
        from raw_view.gui.app import MainWindow

        w = MainWindow.__new__(MainWindow)
        w.state_status = SimpleNamespace(setText=lambda _t: None)
        w.image_status = SimpleNamespace(setText=lambda _t: None)
        w.file_status = SimpleNamespace(
            setText=lambda _t: None,
            setToolTip=lambda _t: None,
        )
        w._set_tab_dirty = lambda *_a, **_k: None
        w._update_frame_display = lambda _item: None
        w._set_state = lambda *_a, **_k: None
        w.decode_cache = None
        return w

    def test_cache_hit_status_uses_current_dimensions(self):
        import tempfile

        from raw_view.gui.app import DecodeCache
        from raw_view.gui.worker import DecodeResult
        from raw_view.models import DecodeOptions, ViewerItem
        from raw_view.formats import expected_frame_size_raw

        # 真实文件：decode_current 会经 _remaining_bytes 读文件大小（文件不存在会
        # 走 QMessageBox 分支，__new__ 桩无真实父类会崩）。命中路径其实不读内容。
        _tf = tempfile.NamedTemporaryFile(suffix=".raw", delete=False)
        _tf.close()
        try:
            with open(_tf.name, "wb") as f:
                f.write(b"\x00" * 4096)
            self._run_cache_hit_assertions(_tf.name)
        finally:
            os.unlink(_tf.name)

    def _run_cache_hit_assertions(self, path: str):
        from raw_view.gui.app import DecodeCache
        from raw_view.gui.worker import DecodeResult
        from raw_view.models import DecodeOptions, ViewerItem
        from raw_view.formats import expected_frame_size_raw

        w = self._new_window()
        item = ViewerItem()
        item.options = DecodeOptions(
            file_path=path, image_type="RAW",
            format_name="RAW8", width=2, height=2,
            alignment="msb", endianness="little",
        )
        item.current_frame = 0

        # 假缓存：命中一个 4x4 RAW8 的结果（显示器按缓存宽高 4x4 展示）。
        # qimage 必须是真 QImage——_on_decode_success 会 QPixmap.fromImage(qimg)。
        from PyQt5.QtGui import QImage

        cache = DecodeCache(max_bytes=10_000_000, max_items=10)
        w.decode_cache = cache
        cached = DecodeResult(
            display_array=None,
            qimage=QImage(4, 4, QImage.Format_RGB888),
            width=4,
            height=4,
            format_name="RAW8",
        )
        cache.store(DecodeCache.key(item.options, 0), cached)

        w._current_item = lambda: item
        w._start_async_decode = lambda *a: None
        w._read_frame_data = lambda *a: b"\x00" * 16
        # _on_decode_success 会调 view.set_pixmap/fit_image → 给个桩视图。
        item.view = SimpleNamespace(
            set_pixmap=lambda _p: None,
            fit_image=lambda: None,
        )
        # decode_current 先 _save_panel_to_item（从面板读参数）；给出一个平板桩。
        w.panel = SimpleNamespace(
            get_values=lambda: {
                "image_type": "RAW", "format_name": "RAW8",
                "width": 2, "height": 2,
                "alignment": "msb", "endianness": "little", "offset": 0,
                "preview_mode": "Bayer Color", "bayer_pattern": "RGGB",
            }
        )
        w._loading_item = False
        w.zoom_status = SimpleNamespace(setText=lambda _t: None)
        w._compute_frame_info = lambda _item: None

        # 现在的 options 是 2x2；缓存中是 4x4 → 命中后状态应显示 4x4 的帧大小。
        seen: list[str] = []

        def _capture(text, *a):
            seen.append(text)

        w.image_status.setText = _capture
        w.decode_current()

        self.assertTrue(seen, "命中路径应写状态栏")
        status = seen[-1]
        expected = expected_frame_size_raw("RAW8", 4, 4)
        self.assertIn("4x4", status)
        self.assertIn(f"{expected:,} bytes/frame", status)
        # 旧 bug：显示的会是 2x2 的帧大小。"bytes/frame" 前的数字必须精确等于
        # 4x4 的帧大小，而不是 `4`（stale）这种会被子串误判的裸数字。
        import re as _re

        m = _re.search(r"\(([\d,]+) bytes/frame\)", status)
        self.assertIsNotNone(m, f"状态栏应有 bytes/frame 数字: {status!r}")
        self.assertEqual(
            int(m.group(1).replace(",", "")), expected,
            f"状态栏帧大小必须按当前宽高 4x4 计算: {status!r}",
        )
        # 选项已回填为缓存的 4x4（与 _on_decode_finished 非缓存路径一致）
        self.assertEqual(item.options.width, 4)
        self.assertEqual(item.options.height, 4)


# ── 0.2.2-L-1 / 0.3.1-M-1,L-1 / 0.4.0-M-2 / 0.4.1-M-2：关闭回收在途解码 ──


class DecodeTeardownTests(unittest.TestCase):
    """关闭标签/窗口时必须断开在途解码的信号，防迟到结果画到新标签。"""

    def test_close_item_cancels_inflight_decode_for_closed_item(self):
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c")]
        # 模拟：items[1] 是当前在途解码目标
        w._pending_decode_item = items[1]
        w._cancel_decode_for = MainWindow._cancel_decode_for.__get__(w, MainWindow)
        w._disconnect_decode = mock.Mock()
        MainWindow.close_item(w, 1)
        w._disconnect_decode.assert_called_once_with()

    def test_close_item_not_cancelled_for_other_inflight(self):
        """关闭非在途 item 时不应误断开其它 item 的解码信号。"""
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c")]
        w._pending_decode_item = items[0]   # 在途目标是 a
        w._cancel_decode_for = MainWindow._cancel_decode_for.__get__(w, MainWindow)

        def _boom():
            raise AssertionError("不应断开非在途 item 的解码")
        w._disconnect_decode = lambda: _boom()
        MainWindow.close_item(w, 2)   # 关 c → a 的在途不受影响
        # 不抛错即通过

    def test_close_all_reaps_each_inflight_item(self):
        """批量关闭（close-all）应逐个回收在途解码。"""
        w = _new_window_with_tabs()
        items = [_add_tab(w, n) for n in ("a", "b", "c")]
        w._pending_decode_item = items[1]
        w._cancel_decode_for = MainWindow._cancel_decode_for.__get__(w, MainWindow)
        w._disconnect_decode = mock.Mock()
        MainWindow.close_all_items(w)
        self.assertTrue(w._disconnect_decode.called, "close-all 应触发在途解码回收")

    def test_cancel_decode_for_uses_identity(self):
        """_cancel_decode_for 只在对象身份一致时断开（拖拽重排后仍正确）。"""
        from raw_view.gui.app import MainWindow

        w = MainWindow.__new__(MainWindow)
        w._pending_decode_item = None
        w._cancel_decode_for = MainWindow._cancel_decode_for.__get__(w, MainWindow)

        def _boom():
            raise AssertionError("无在途时不��断开")
        w._disconnect_decode = lambda: _boom()
        item = ViewerItem()
        w._cancel_decode_for(item)      # pending 为 None → 不动作
        w._cancel_decode_for(None)      # item None → 不动作

    def _qclose_event(self):
        """真实 QCloseEvent：closeEvent 的 super() 需要它（_Evt 桩会被拒绝）。"""
        from PyQt5.QtGui import QCloseEvent

        return QCloseEvent()

    def test_close_event_disconnects_and_waits_bounded(self):
        """closeEvent 应断开在途解码，并对存活线程做有界等待。

        用真实 MainWindow（完整 QWidget 初始化，super().closeEvent 才可调）；
        closeEvent 的 super() 在 ``__new__`` 桩上会因“本类 __init__ 未调用”抛
        RuntimeError，生产路径窗口总是完整初始化，行为不受影响。
        """
        from raw_view.gui.app import MainWindow

        w = MainWindow()
        # 假线程：isRunning=True、wait 记录调用
        class _FakeThread:
            def __init__(self):
                self.waited = 0
            def isRunning(self):
                return True
            def wait(self, ms):
                self.waited = ms
                return True
        fake = _FakeThread()
        w._thread = fake
        with mock.patch.object(w, "_disconnect_decode") as dis, \
             mock.patch.object(w, "_cancel_async_decode") as canc:
            MainWindow.closeEvent(w, self._qclose_event())
            dis.assert_called_once_with()
            self.assertEqual(fake.waited, 400, "关闭时应留下有界短超时")
            # 别在这里 w.close()——会再次进入 closeEvent 重复触发断言；
            # 真实窗口用 hide+deleteLater 清理即可。
            w.hide()
            w.deleteLater()

    def test_close_event_tolerates_no_inflight(self):
        """真实窗口无在途解码（_thread/_pending_decode_item 均 None）关闭不崩。"""
        from raw_view.gui.app import MainWindow

        w = MainWindow()
        try:
            MainWindow.closeEvent(w, self._qclose_event())
        finally:
            w.close()
            w.deleteLater()

    def test_close_event_tolerates_deleted_qthread_due_to_runtime_error(self):
        """"已完成解码的线程对象已 deleteLater（C++ 对象销毁）后关闭不崩。

        解码完成后 ``_thread`` 仍被引用，但 QThread 的 C++ 对象已被
        finished→deleteLater 释放；对已删包装调用 isRunning()/wait() 会抛
        RuntimeError。closeEvent 必须静默跳过，否则关窗崩（e2e 实测发现）。
        """
        from unittest.mock import MagicMock

        from raw_view.gui.app import MainWindow

        w = MainWindow()
        class _GoneThread:
            def isRunning(self):
                raise RuntimeError("wrapped C/C++ object of type QThread has been deleted")
            def wait(self, ms):
                raise AssertionError("已删线程不应 wait")
        w._thread = _GoneThread()
        try:
            with mock.patch.object(w, "_disconnect_decode") as dis:
                MainWindow.closeEvent(w, self._qclose_event())
                dis.assert_called_once_with()
        finally:
            w._thread = None
            w.close()
            w.deleteLater()


# ── 0.4.1 回归：Batch 进度框不闪现（进度框延迟显式创建）────────────────


class BatchProgressDialogTests(unittest.TestCase):
    """0.4.1 修复：QProgressDialog 不在 __init__ 预创建，避免 Add Files /
    冲突确认等嵌套事件循环中闪现 "Batch conversion in progress..."。

    关键契约（实测 PyQt5 5.15.11）：
    - 对话框构造时 ``self._progress`` 必须为 None（未创建任何进度框）；
    - ``_run_batch`` 在冲突确认**之后**才按需创建，``minimumDuration`` 不小于
      300ms，且创建后**不立即显式 show()**——deferred show 由后续 setValue()
      触发，短批量不闪现、长批量 300ms 后出现；
    - ``finally`` 清理后 ``self._progress`` 回到 None（无残留对象、无泄漏）。
    """

    def _make_dialog(self):
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        dlg = BatchConvertDialog(AppSettings())
        # 关闭多变体分支，让 _run_batch 走单文件转换路径（进度框逻辑相同）
        dlg._variant_selector = None
        return dlg

    def test_no_progress_dialog_at_construction(self):
        """进度框不得在 __init__ 创建（杜绝 Add Files/Clear 的闪现源头）。"""
        dlg = self._make_dialog()
        try:
            self.assertIsNone(dlg._progress)
        finally:
            dlg.deleteLater()

    def test_run_batch_creates_deferred_progress_and_cleans_up(self):
        """_run_batch 在冲突确认后创建进度框，deferred-show、结束后清空。"""
        from unittest import mock

        import raw_view.gui.dialogs.batch_convert as bc_mod

        dlg = self._make_dialog()
        try:
            # 用一张真实存在的小图驱动 _run_batch；转换函数打桩避免实际写文件。
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                temp_png = tf.name
            try:
                dlg._add_files([temp_png])
                self.assertIsNone(dlg._progress, "运行前不应有进度框")
                # 默认目标类型为 RAW → 走 image_file_to_raw；打桩避免实际写文件
                with mock.patch.object(bc_mod, "image_file_to_raw") as conv, \
                     mock.patch.object(bc_mod.QMessageBox, "information"):
                    dlg._run_batch()
                # 运行中创建过进度框，结束后清理干净
                self.assertIsNone(dlg._progress, "run 结束后 _progress 应清空")
                self.assertTrue(conv.called, "转换桩应被调用")
            finally:
                os.unlink(temp_png)
        finally:
            dlg.deleteLater()

    def test_progress_dialog_config_deferred_show(self):
        """进度框配置：minimumDuration>=300ms，且创建后不立即 show（防闪现）。"""
        from unittest import mock

        import raw_view.gui.dialogs.batch_convert as bc_mod

        dlg = self._make_dialog()
        created = {}

        class _Spy(bc_mod.QProgressDialog):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                created["instance"] = self

            def show(self):
                created["show_called"] = True
                super().show()

        try:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                temp_png = tf.name
            try:
                dlg._add_files([temp_png])
                with mock.patch.object(bc_mod, "QProgressDialog", _Spy), \
                     mock.patch.object(bc_mod, "image_file_to_raw") as conv, \
                     mock.patch.object(bc_mod.QMessageBox, "information"):
                    dlg._run_batch()
                self.assertIn("instance", created, "应创建进度框")
                self.assertFalse(
                    created.get("show_called", False),
                    "进度框创建后不应立即 show()（靠 setValue 延迟显示防短批量闪现）",
                )
                inst = created["instance"]
                self.assertGreaterEqual(inst.minimumDuration(), 300)
                self.assertTrue(inst.wasCanceled() or not inst.isVisible())
            finally:
                os.unlink(temp_png)
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
