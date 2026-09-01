"""Tests for tab drag-reorder (QTabWidget.setMovable) and items <-> tab sync.

The app lets the user drag tabs by their name text to reorder them. Qt 5.15
supports this natively via ``setMovable(True)``, but moving a tab does NOT
touch ``self.items`` — the ``tabBar().tabMoved`` handler (:meth:`MainWindow.
_on_tab_moved`) must reorder ``self.items`` to match the visual order, or
close_item / decode_current / _current_item would hit the wrong item.

Requires a Qt platform plugin; we force the offscreen platform so imports and
widget construction work headless. A single QApplication is shared across this
module (creating several QApplication instances in one process asserts).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLabel, QTabWidget  # noqa: E402

# 在整个模块里共享同一个 QApplication（重复创建会在同一进程内崩溃）。
_APP = QApplication.instance() or QApplication([])

from raw_view.gui.app import MainWindow  # noqa: E402
from raw_view.models import ViewerItem  # noqa: E402


def _new_window() -> MainWindow:
    """不含 _build_ui 的轻量 MainWindow（仅提供被测方法依赖的属性）。

    与 test_app_logic 的 ``_new_window`` 同一思路：不构造菜单/面板，只给
    出 items、item_tabs、_loading_item 与几个空同步桩，便于在纯 Python 层
    直接验证拖拽重排逻辑。
    """
    w = MainWindow.__new__(MainWindow)
    w.items = []
    w.item_tabs = QTabWidget()
    w.item_tabs.setTabsClosable(True)
    w._loading_item = False
    w._active_item_index = -1
    w._load_item_to_panel = lambda item: None
    w._sync_status_from_item = lambda item: None
    w._save_panel_to_item = lambda item: None
    w.panel = SimpleNamespace(set_enabled=lambda _enabled: None, _sync_type_enabled=lambda: None)
    return w


def _add_tab(w: MainWindow, name: str) -> tuple[ViewerItem, "object"]:
    """追加一个标签，返回它对应的 (item, page widget)。items 与 tab 保持同步。"""
    item = ViewerItem()
    tab = QLabel(name)
    w.items.append(item)
    w.item_tabs.addTab(tab, name)
    return item, tab


def _tab_widgets(w: MainWindow) -> list[object]:
    """按标签视觉顺序返回每个 tab 的 page widget。"""
    return [w.item_tabs.widget(i) for i in range(w.item_tabs.count())]


# ── _reorder_items（纯方法，不依赖真实信号） ──────────────────────────────


class ReorderItemsTests(unittest.TestCase):
    def test_pop_insert_semantics(self):
        """再排序必须等价于 insert(to, pop(from)) —— 顺序一致性是解码/关闭的前提。"""
        w = _new_window()
        items = [_add_tab(w, "a")[0] for _ in range(4)]

        # index 3 -> index 1
        w._reorder_items(3, 1)
        self.assertEqual(w.items, [items[0], items[3], items[1], items[2]])

        # index 0 -> index 3（移至末尾）
        w._reorder_items(0, 3)
        self.assertEqual(w.items, [items[3], items[1], items[2], items[0]])

        # index 2 -> index 0（移至开头）
        w._reorder_items(2, 0)
        self.assertEqual(w.items, [items[2], items[3], items[1], items[0]])

    def test_keeps_item_identity(self):
        """每个 item 仍是同一个 Python 对象，只是位置变了（拖拽不改数据）。"""
        w = _new_window()
        items = [_add_tab(w, "a")[0] for _ in range(3)]
        w._reorder_items(2, 0)
        self.assertIs(w.items[0], items[2])
        self.assertIs(w.items[1], items[0])
        self.assertEqual(set(map(id, w.items)), set(map(id, items)))

    def test_out_of_range_is_ignored(self):
        """越界索引直接忽略，列表保持原样。"""
        w = _new_window()
        items = [_add_tab(w, "a")[0] for _ in range(2)]
        w._reorder_items(-1, 0)
        w._reorder_items(0, 5)
        w._reorder_items(2, 1)
        w._reorder_items(1, -3)
        self.assertEqual(w.items, items)


# ── 拖拽后 items / _current_item / close 的一致性 ─────────────────────────


class TabMovedSyncTests(unittest.TestCase):
    def test_items_match_tab_widget_order_after_move(self):
        """每次 moveTab + _on_tab_moved 后，第 i 个 item 必须对应第 i 个 widget。

        这是拖拽排序正确性的核心不变量：close_item / decode / current 全部
        依赖 items[i] 与第 i 个标签一一对应。
        """
        w = _new_window()
        entries = [_add_tab(w, n) for n in ("a", "b", "c", "d")]
        items = [e[0] for e in entries]
        widgets = [e[1] for e in entries]

        def widget_of(it):
            return widgets[next(i for i, o in enumerate(items) if o is it)]

        for frm, to in [(2, 0), (3, 1), (0, 3), (1, 3)]:
            w.item_tabs.tabBar().moveTab(frm, to)
            w._reorder_items(frm, to)  # tabBar().tabMoved 触发同一逻辑
            for i, it in enumerate(w.items):
                self.assertIs(w.item_tabs.widget(i), widget_of(it))

    def test_current_item_unaffected_by_reordering(self):
        """拖拽选中的标签时，选中的 item 不会变（用户拖的是顺序不是选中）。"""
        w = _new_window()
        entries = [_add_tab(w, n) for n in ("a", "b", "c", "d")]
        items = [e[0] for e in entries]

        # 当前选中 index 2（items[2]），把它拖到开头
        w.item_tabs.setCurrentIndex(2)
        selected = w.items[2]
        w.item_tabs.tabBar().moveTab(2, 0)
        w._reorder_items(2, 0)

        # 选中的 page widget 跟随拖动项：currentIndex() 指向新位置 0
        self.assertIs(w.item_tabs.currentWidget(), w.item_tabs.widget(0))
        # items[0] 就是原先选中的 item；_current_item() 靠 currentIndex() 命中它
        self.assertIs(w.items[0], selected)
        self.assertIs(w._current_item(), selected)

    def test_close_item_index_consistency_after_move(self):
        """close_item(index) / items[index] 与 tab 视觉顺序在拖拽后仍一致。"""
        w = _new_window()
        entries = [_add_tab(w, n) for n in ("a", "b", "c")]
        items = [e[0] for e in entries]

        w._reorder_items(2, 0)
        # 视觉第 0 个标签 == 最初的 items[2]；视觉第 1 个标签 == 最初的 items[0]
        self.assertEqual(w.items, [items[2], items[0], items[1]])
        # 关闭视觉上第 1 个标签所对应的索引，应命中 items[1] == 最初的 items[0]
        self.assertIs(w.items[1], items[0])


# ── setMovable 装配（源码/运行时双重校验） ────────────────────────────────


class SetMovableWiringTests(unittest.TestCase):
    def test_tab_widget_is_movable(self):
        """setMovable(True) 必须在 _build_ui 中被调用（运行时校验）。"""
        w = MainWindow()
        try:
            self.assertTrue(w.item_tabs.isMovable())
        finally:
            w.close()
            w.deleteLater()

    def test_tab_close_button_not_always_shown(self):
        """关闭按钮不长期显示，避免占用名称区域影响拖动（点击名称区域拖动）。"""
        w = MainWindow()
        try:
            self.assertFalse(w.item_tabs.tabBar().tabsClosable())
        finally:
            w.close()
            w.deleteLater()

    def test_source_contains_setmovable_and_tab_moved_connection(self):
        """源码层面：_build_ui 显式启用 setMovable，并连接了 tabMoved 处理器。"""
        import inspect

        src = inspect.getsource(MainWindow._build_ui)
        self.assertIn("setMovable(True)", src)
        self.assertIn(".tabBar().tabMoved.connect(self._on_tab_moved)", src)
        self.assertIn("def _on_tab_moved", inspect.getsource(MainWindow))


# ── 真实 MainWindow 端到端 ───────────────────────────────────────────────


class RealWindowTabOrderTests(unittest.TestCase):
    def test_move_tab_syncs_items_in_real_window(self):
        """真实 MainWindow 上 moveTab 后 items 与标签顺序一致、当前项不变。

        ``_open_item`` 用假文件名（不存在的路径）只建标签、不触发解码，
        因此可以走完整的 _build_ui 装配路径，验证真实信号连接。
        """
        w = MainWindow()
        tmpdir = tempfile.mkdtemp(prefix="rv-tabdrag-")
        try:
            names = ("a.png", "b.png", "c.png", "d.png")
            paths = []
            for name in names:
                path = os.path.join(tmpdir, name)
                with open(path, "wb") as f:
                    f.write(b"\x00")
                paths.append(path)
                w._open_item(path, decode=False)
            self.assertEqual([w.item_tabs.tabText(i) for i in range(4)],
                             list(names))

            # 选中 index 2 并把它拖到开头
            w.item_tabs.setCurrentIndex(2)
            moved = w.items[2]
            w.item_tabs.tabBar().moveTab(2, 0)

            # tabMoved -> _on_tab_moved 在真实连接下同步了 items
            self.assertEqual([os.path.basename(it.options.file_path) for it in w.items],
                             ["c.png", "a.png", "b.png", "d.png"])
            # 选中的 item 不变，且 _current_item() 通过 currentIndex() 命中它
            self.assertIs(w.items[0], moved)
            self.assertIs(w._current_item(), moved)
        finally:
            w.close()
            w.deleteLater()
            _APP.processEvents()
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
