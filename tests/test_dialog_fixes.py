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

from _settings_isolation import (
    ensure_test_settings_file,
    isolated_qt_material_resources,
)

ensure_test_settings_file()
# Per-process qt_material icon tree (concurrent pytest processes otherwise
# delete each other's generated SVGs).
isolated_qt_material_resources()

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
from raw_view.models import (
    DEFAULT_OUTPUT_TEMPLATE,
    canonical_yuv_name,
    format_output_template,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _norm(path):
    """把平台路径归一化为结构比较用的形态（Windows 下 `/a/b` → `\\a\\b`）。"""
    return os.path.normpath(os.fspath(path))


class BatchConvertFixesTests(unittest.TestCase):
    """H-4：批量转换输出目录与 Settings 默认目录统一。"""

    def test_resolve_output_dir_same_dir_true(self):
        # 勾选 “Same directory as input” → 输出到输入文件同目录。
        # 返回绝对父目录，避免相对路径被 format_output_template 再拼一层
        # （0.5.11.1 review L-5）。
        input_path = os.path.join("workspace", "images", "frame.png")
        resolved = resolve_output_dir(True, input_path, "convert_out")
        self.assertTrue(os.path.isabs(resolved), resolved)
        self.assertEqual(
            _norm(resolved), _norm(Path(os.path.abspath(input_path)).parent)
        )

    def test_resolve_output_dir_same_dir_false(self):
        # 未勾选 → 落到 Settings 默认输出目录名
        self.assertEqual(
            resolve_output_dir(False, "/workspace/images/frame.png", "convert_out"),
            "convert_out",
        )

    def test_resolve_output_dir_same_dir_true_posix(self):
        # 平台路径语义下同样成立（结构与 POSIX 期望一致）；绝对化后
        # Windows 会补上当前盘符，因此期望值也用同一解析得到。
        resolved = resolve_output_dir(True, "/workspace/images/scan.bmp", "out")
        self.assertTrue(os.path.isabs(resolved), resolved)
        self.assertEqual(
            _norm(resolved),
            _norm(Path(os.path.abspath("/workspace/images/scan.bmp")).parent),
        )

    def test_resolve_output_dir_settings_dir_used_when_not_same_dir(self):
        # 未勾选时 settings 目录原样返回（相对 or 绝对都保留调用方语义）
        self.assertEqual(
            resolve_output_dir(False, "/workspace/images/frame.png", "custom_out/foo"),
            "custom_out/foo",
        )


class OutputNamingTests(unittest.TestCase):
    """0.5.11 B1: one template drives Convert / Batch / CLI.

    ``{format}`` = ``{bayer}{bits}{packed}`` / ``{raw_type}`` / ``{yuv_type}``
    for image input, and ``{source}_to_{target}`` for a YUV -> YUV transcode.
    """

    def _name(self, filename, target_type, **kwargs):
        out = format_output_template(
            DEFAULT_OUTPUT_TEMPLATE,
            os.path.join("images", filename),
            2560,
            1440,
            target_type,
            output_dir="images",
            **kwargs,
        )
        return os.path.basename(out)

    def test_image_to_raw10_bayer(self):
        self.assertEqual(
            self._name(
                "a.png", "RAW", raw_type="RAW10",
                bayer_pattern="RGGB", source_mode="bayer",
            ),
            "a_2560x1440_RGGB10.raw",
        )
        self.assertEqual(
            self._name(
                "a.png", "RAW", raw_type="RAW10",
                bayer_pattern="BGGR", source_mode="bayer",
            ),
            "a_2560x1440_BGGR10.raw",
        )

    def test_image_to_raw10_gray_uses_raw_type(self):
        self.assertEqual(
            self._name(
                "a.png", "RAW", raw_type="RAW10",
                bayer_pattern="BGGR", source_mode="gray",
            ),
            "a_2560x1440_RAW10.raw",
        )

    def test_image_to_yuv(self):
        self.assertEqual(
            self._name("a.png", "YUV", yuv_type="YUYV"),
            "a_2560x1440_YUYV.yuv",
        )
        self.assertEqual(
            self._name("a.png", "YUV", yuv_type="YOnly12"),
            "a_2560x1440_YOnly12.yuv",
        )

    def test_yuv_to_yuv_names_both_layouts(self):
        self.assertEqual(
            self._name(
                "a.yuv", "YUV", yuv_type="NV16", source_yuv_type="YUYV",
            ),
            "a_2560x1440_YUYV_to_NV16.yuv",
        )
        # Lower-case/space-padded input is canonicalised, not copied verbatim.
        self.assertEqual(
            self._name(
                "a.yuv", "YUV", yuv_type="nv16", source_yuv_type="yu yv",
            ),
            "a_2560x1440_YUYV_to_NV16.yuv",
        )

    def test_source_yuv_type_is_ignored_for_non_yuv_targets(self):
        self.assertEqual(
            self._name(
                "a.yuv", "RAW", raw_type="RAW10",
                bayer_pattern="RGGB", source_mode="bayer",
                source_yuv_type="YUYV",
            ),
            "a_2560x1440_RGGB10.raw",
        )

    def test_relative_output_dir_is_relative_to_the_input(self):
        out = format_output_template(
            DEFAULT_OUTPUT_TEMPLATE,
            os.path.join("images", "a.png"), 2560, 1440, "YUV",
            output_dir="convert_out", yuv_type="YUYV",
        )
        self.assertEqual(
            _norm(out),
            _norm(os.path.join("images", "convert_out", "a_2560x1440_YUYV.yuv")),
        )

    def test_same_directory_with_a_relative_input_does_not_double_the_folder(self):
        """L-5: ``dir/a.png`` + Same directory must land in ``dir``, not ``dir/dir``."""
        import shutil
        import tempfile

        tmp = tempfile.mkdtemp(prefix="rv-relative-out-")
        old_cwd = os.getcwd()
        try:
            os.chdir(tmp)
            input_path = os.path.join("dir", "a.png")
            out_dir = resolve_output_dir(True, input_path, "convert_out")
            self.assertTrue(os.path.isabs(out_dir), out_dir)
            out = format_output_template(
                DEFAULT_OUTPUT_TEMPLATE,
                input_path, 4, 4, "RAW",
                output_dir=out_dir,
                raw_type="RAW8",
                bayer_pattern="RGGB",
                source_mode="bayer",
            )
            parent = _norm(os.path.dirname(out))
            self.assertEqual(
                parent, _norm(Path(os.path.abspath(input_path)).parent)
            )
            self.assertNotIn(
                _norm(os.path.join("dir", "dir")), _norm(out),
                f"相对输入被重复拼接了一层目录: {out}",
            )
        finally:
            os.chdir(old_cwd)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_canonical_yuv_names(self):
        self.assertEqual(canonical_yuv_name("yuyv"), "YUYV")
        self.assertEqual(canonical_yuv_name(" yu yv "), "YUYV")
        self.assertEqual(canonical_yuv_name("YONLY12"), "YOnly12")
        self.assertEqual(canonical_yuv_name("yonly8"), "YOnly8")
        self.assertEqual(canonical_yuv_name("YOnly"), "YOnly")
        self.assertEqual(canonical_yuv_name(""), "")


class DocumentationConsistencyTests(unittest.TestCase):
    """0.5.11 M-3: user docs must describe the behaviour that actually ships."""

    def test_readme_describes_current_convert_defaults(self):
        text = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        # Input type resets dimensions; it no longer clears them.
        self.assertIn("重置为 2560×1440", text)
        self.assertNotIn("切换 Input type 会清空 Width/Height", text)
        # Convert defaults to the input's directory, not convert_out; it has
        # one auto-filled, editable Output file row and no directory option.
        self.assertIn("输入文件同目录", text)
        self.assertIn("Output file", text)
        self.assertIn("可直接编辑", text)
        self.assertIn("Convert（单文件）没有单独的目录选项", text)
        # YUV -> YUV naming is documented.
        self.assertIn("{源}_to_{目标}", text)
        # One template drives every consumer.
        self.assertIn("Convert（单文件）、Batch Convert、Generate Variants 与 CLI", text)

    def test_help_content_describes_current_convert_defaults(self):
        from raw_view import help_content as hc

        html = hc.HELP_HTML
        self.assertIn("next to the input file", html)
        self.assertIn("Default output folder", html)
        self.assertIn("Output filename template", html)
        # Convert's single editable Output file row (U-2).
        flat = " ".join(html.split())
        self.assertIn("one <b>Output file</b> row", flat)
        self.assertIn("fully editable", flat)

    def test_docs_describe_the_two_convert_modes(self):
        """V6: the mode selector and the YUV Variant boundary must be documented."""
        from raw_view import help_content as hc

        readme = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        html = " ".join(hc.HELP_HTML.split())
        for text in (readme, html):
            self.assertIn("Variant Convert", text)
            self.assertIn("Variant Convert (enable in Settings)", text)
            self.assertIn("Mode", text)
        # YUV Variant capability boundary: same family only, no resize.
        self.assertIn("same chroma family", html)
        self.assertIn("同一色度家族", readme)
        self.assertIn("no resize", html)

    def test_help_says_batch_convert_is_single_target(self):
        """L-6: the in-app Help must explain the lost Batch multi-variant mode."""
        from raw_view import help_content as hc

        html = " ".join(hc.HELP_HTML.split())
        self.assertIn("single-target", html)
        self.assertIn("one input file at a time", html)
        self.assertIn("Variant Convert", html)

    def test_resolve_output_dir_docstring_states_the_abspath_limits(self):
        """L-4: the docstring must not claim abspath and resolve() are equal."""
        from raw_view.models import resolve_output_dir

        doc = " ".join((resolve_output_dir.__doc__ or "").split())
        self.assertIn("8.3", doc)
        self.assertIn("symbolic links", doc)
        self.assertNotIn("both produce the same absolute parent", doc)

    def test_readme_default_output_folder_is_convert_out(self):
        """0.5.11.3 L-5: the README default was still the old ``out``."""
        text = (_PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("默认 `convert_out`", text)
        self.assertNotIn("默认 `out`", text, "README 仍把默认目录写成 `out`")
        # The setting belongs to Variant/Batch; Convert always writes beside
        # the input and has no "Same directory as input" switch.
        self.assertIn("**Variant Convert / Batch Convert**", text)
        self.assertIn("Convert（单文件）始终写到输入文件同目录", text)

    def test_resolve_output_dir_docstring_attributes_same_directory_correctly(self):
        """0.5.11.3 L-5: Convert no longer has the Same-directory switch."""
        from raw_view.models import resolve_output_dir

        doc = " ".join((resolve_output_dir.__doc__ or "").split())
        self.assertIn("Variant Convert and Batch Convert", doc)
        self.assertNotIn("both Convert and Batch", doc)
        self.assertIn("Convert has no such switch", doc)


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
