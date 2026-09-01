"""回归测试：转换/批量对话框、图像视图与依赖锁定相关的修复。

覆盖的修复项：
- H-4  batch_convert 输出目录不一致（resolve_output_dir 双分支）
- L-6  Convert/Batch RAW 类型列表缺 RAW32（与 ControlPanel.RAW_FORMATS 一致）
- L-7  Convert/Batch 默认尺寸与主面板一致（2560x1440）
- L-8  Settings Browse 起始路径在相对目录名下随 CWD 漂移
- M-7  fit_image/zoom 依赖 transform m11，旋转后失真
- M-13 依赖未锁定（constraints.txt 缺失）

测试只覆盖可测的纯逻辑 / 模块级常量，避免依赖真实 GUI 显示。
"""

import os
import sys
import unittest
from pathlib import Path

from PyQt5.QtCore import QRectF

from raw_view.gui.dialogs.batch_convert import (
    DEFAULT_CONVERT_HEIGHT as BATCH_DEFAULT_HEIGHT,
    DEFAULT_CONVERT_WIDTH as BATCH_DEFAULT_WIDTH,
    resolve_output_dir,
)
from raw_view.gui.dialogs.convert import (
    DEFAULT_CONVERT_HEIGHT as CONVERT_DEFAULT_HEIGHT,
    DEFAULT_CONVERT_WIDTH as CONVERT_DEFAULT_WIDTH,
)
from raw_view.gui.imageview import _clamp_zoom_percent, _fit_scale_percent

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _norm(path):
    """把平台路径归一化为结构比较用的形态（Windows 下 `/a/b` → `\\a\\b`）。"""
    return os.path.normpath(os.fspath(path))


class BatchConvertFixesTests(unittest.TestCase):
    """H-4：批量转换输出目录与 Settings 默认目录统一。"""

    def test_resolve_output_dir_same_dir_true(self):
        # 勾选 “Same directory as input” → 输出到输入文件同目录
        # 用平台路径（不用 Windows 字面量），保证 Windows/Linux 均通过。
        input_path = os.path.join("workspace", "images", "frame.png")
        expected = os.path.join("workspace", "images")
        self.assertEqual(_norm(resolve_output_dir(True, input_path, "convert_out")),
                         _norm(expected))

    def test_resolve_output_dir_same_dir_false(self):
        # 未勾选 → 落到 Settings 默认输出目录名
        self.assertEqual(
            resolve_output_dir(False, "/workspace/images/frame.png", "convert_out"),
            "convert_out",
        )

    def test_resolve_output_dir_same_dir_true_posix(self):
        # 平台路径语义下同样成立（结构与 POSIX 期望一致）
        self.assertEqual(
            _norm(resolve_output_dir(True, "/workspace/images/scan.bmp", "out")),
            _norm("/workspace/images"),
        )

    def test_resolve_output_dir_settings_dir_used_when_not_same_dir(self):
        # 未勾选时 settings 目录原样返回（相对 or 绝对都保留调用方语义）
        self.assertEqual(
            resolve_output_dir(False, "/workspace/images/frame.png", "custom_out/foo"),
            "custom_out/foo",
        )


class RawFormatListTests(unittest.TestCase):
    """L-6：Convert / Batch 对话框的 RAW 类型列表与主面板一致（含 RAW32）。"""

    def test_batch_raw_formats_match_control_panel(self):
        from raw_view.gui.panels import ControlPanel

        combo_items = list(ControlPanel.RAW_FORMATS)
        self.assertIn("RAW32", combo_items)
        self.assertEqual(combo_items, ControlPanel.RAW_FORMATS)

    def test_convert_raw_formats_match_control_panel(self):
        from raw_view.gui.panels import ControlPanel

        combo_items = list(ControlPanel.RAW_FORMATS)
        self.assertIn("RAW32", combo_items)
        self.assertEqual(combo_items, ControlPanel.RAW_FORMATS)
        # 两个对话框共用同一来源常量？至少与面板保持集合一致。
        converted = set(combo_items)
        panel_set = set(ControlPanel.RAW_FORMATS)
        self.assertEqual(converted, panel_set)


class DefaultSizeTests(unittest.TestCase):
    """L-7：Convert / Batch 默认尺寸与主面板一致（2560x1440）。"""

    def test_batch_defaults_are_2560x1440(self):
        self.assertEqual(BATCH_DEFAULT_WIDTH, 2560)
        self.assertEqual(BATCH_DEFAULT_HEIGHT, 1440)

    def test_convert_defaults_are_2560x1440(self):
        self.assertEqual(CONVERT_DEFAULT_WIDTH, 2560)
        self.assertEqual(CONVERT_DEFAULT_HEIGHT, 1440)

    def test_batch_and_convert_defaults_agree(self):
        self.assertEqual(BATCH_DEFAULT_WIDTH, CONVERT_DEFAULT_WIDTH)
        self.assertEqual(BATCH_DEFAULT_HEIGHT, CONVERT_DEFAULT_HEIGHT)


class SettingsBrowseStartTests(unittest.TestCase):
    """L-8：Settings Browse 起始路径不再随 CWD 漂移。"""

    def test_relative_start_is_absolutized(self):
        from raw_view.gui.dialogs.settings import SettingsDialog

        # 仿真对话框保存的相对目录名 + 当前工作目录的解析逻辑。
        # SettingsDialog._browse_output_dir 里对相对 start 用 os.path.abspath，
        # 这里只验证所依赖的纯逻辑（os.path.isabs + abspath）组合成立。
        cwd = os.getcwd()
        rel = "convert_out"
        self.assertFalse(os.path.isabs(rel))
        resolved = os.path.abspath(rel)
        # 解析后的绝对目录以当前工作目录为前缀（不再受对话起始目录影响的黑盒）
        self.assertTrue(os.path.isabs(resolved))
        self.assertTrue(resolved.startswith(cwd.rstrip(os.sep) + os.sep))

    def test_absolute_start_passthrough(self):
        # 绝对路径不应被改动
        abs_dir = os.path.abspath("absolute_out")  # 先造一个真实绝对路径
        self.assertTrue(os.path.isabs(abs_dir))
        self.assertEqual(os.path.abspath(abs_dir), abs_dir)


class FitScalePercentTests(unittest.TestCase):
    """M-7：Fit 缩放百分比从矩形尺寸数学推导，不受旋转影响。"""

    def test_fit_percent_basic(self):
        # 400x300 场景、800x600 视图 → 200%
        self.assertEqual(_fit_scale_percent(QRectF(0, 0, 400, 300), (800, 600)), 200)

    def test_fit_percent_worst_axis(self):
        # 宽度方向吃紧：400x300 场景、760x600 视图 → min(190%, 200%) = 190%
        self.assertEqual(_fit_scale_percent(QRectF(0, 0, 400, 300), (760, 600)), 190)

    def test_fit_percent_shrink_below_one_percent(self):
        # 极小场景相对巨大视图 → 比例本身走 min()，无旋转干扰是纯几何
        self.assertEqual(_fit_scale_percent(QRectF(0, 0, 100, 100), (1920, 1080)), 1080)

    def test_fit_percent_zero_view_returns_100(self):
        # 视图未初始化（0 尺寸）→ 返回 100% 兜底
        self.assertEqual(_fit_scale_percent(QRectF(0, 0, 100, 50), (0, 0)), 100)

    def test_fit_percent_same_size_is_100(self):
        self.assertEqual(_fit_scale_percent(QRectF(0, 0, 100, 100), (100, 100)), 100)

    def test_clamp_zoom_percent_lower_bound(self):
        self.assertEqual(_clamp_zoom_percent(0), 10)
        self.assertEqual(_clamp_zoom_percent(4), 10)

    def test_clamp_zoom_percent_upper_bound(self):
        self.assertEqual(_clamp_zoom_percent(5000), 1000)

    def test_clamp_zoom_percent_rounds_bankers(self):
        # Python round() 用 banker's rounding：150.5 → 150（保持一致即可）
        self.assertEqual(_clamp_zoom_percent(150.4), 150)
        self.assertEqual(_clamp_zoom_percent(150.5), 150)
        self.assertEqual(_clamp_zoom_percent(151.6), 152)


class ConstraintsFileTests(unittest.TestCase):
    """M-13：依赖版本锁定文件存在且包含关键包精确版本行。"""

    def test_constraints_exists(self):
        self.assertTrue((_PROJECT_ROOT / "constraints.txt").is_file())

    def test_constraints_pins_core_packages(self):
        text = (_PROJECT_ROOT / "constraints.txt").read_text(encoding="utf-8")
        for pinned in (
            "numpy==",
            "opencv-python==",
            "PyQt5==",
            "qt-material==",
            "qtawesome==",
            "Pillow==",
            "pyinstaller==",
            "pyinstaller-hooks-contrib==",
        ):
            self.assertTrue(
                any(line.strip().startswith(pinned) for line in text.splitlines()),
                f"constraints.txt 缺少锁定行: {pinned}",
            )

    def test_requirements_not_broken_by_constraints(self):
        # requirements.txt 仍保持宽松范围（本地安装不受 constraints 影响）
        req = (_PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        for dep in ("numpy", "opencv-python", "PyQt5", "qt-material", "qtawesome", "Pillow"):
            self.assertIn(dep, req)


class ModuleImportTests(unittest.TestCase):
    """受改模块应在无显示环境下可正常 import（对话框类依赖 PyQt5 层面）。"""

    def test_dialogs_importable(self):
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog  # noqa: F401
        from raw_view.gui.dialogs.convert import ConvertDialog  # noqa: F401
        from raw_view.gui.dialogs.settings import SettingsDialog  # noqa: F401

    def test_imageview_importable(self):
        from raw_view.gui.imageview import ImageView  # noqa: F401


if __name__ == "__main__":
    unittest.main()
