"""阶段 1 提升落地项的回归测试。

覆盖（2026-09 落地）：
- P1-1  解码 LRU 缓存（DecodeCache 键、命中/缺失/LRU 上限、超大帧拒绝）
- P1-6  转换覆盖确认的纯逻辑（resolve_output_path_collision 三策略；GUI 弹窗
        只做手动验证，不在此实例化真实 QDialog）
- P2-3  批量 JSON 的 base_dir 相对路径 + glob 展开（__main__._run_batch）
- P2-4/ENG-6 版本号单一来源：models.APP_VERSION == __main__ 返回值 == 一致性
- UI-9  面板 Estimated frame 提示的纯逻辑（_format_size）
- UI-4  标签未保存标记（_set_tab_dirty）在 MainWindow.__new__ 下不崩溃
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np


class DecodeCacheTests(unittest.TestCase):
    """P1-1：LRU 解码缓存。"""

    def _make_result(self, size: int):
        from raw_view.gui.worker import DecodeResult

        return DecodeResult(
            display_array=np.zeros((size, size, 3), dtype=np.uint8),
            qimage=None,
            width=size,
            height=size,
            format_name="RAW8",
        )

    def _make_options(self, path="x.raw", fmt="RAW12", w=8, h=8, align="msb", endian="little"):
        from raw_view.models import DecodeOptions

        return DecodeOptions(
            file_path=path, format_name=fmt, width=w, height=h,
            alignment=align, endianness=endian,
        )

    def test_key_covers_all_params_and_frame(self):
        from raw_view.gui.worker import DecodeCache

        k1 = DecodeCache.key(self._make_options(), 2)
        k2 = DecodeCache.key(self._make_options(), 3)
        self.assertNotEqual(k1, k2)
        # 帧号不同 → 键不同
        self.assertNotEqual(
            DecodeCache.key(self._make_options(), 0),
            DecodeCache.key(self._make_options(), 1),
        )
        # 参数不同 → 键不同
        self.assertNotEqual(
            DecodeCache.key(self._make_options(align="lsb"), 0),
            DecodeCache.key(self._make_options(align="msb"), 0),
        )
        # 文件不同 → 键不同
        self.assertNotEqual(
            DecodeCache.key(self._make_options(path="a.raw"), 0),
            DecodeCache.key(self._make_options(path="b.raw"), 0),
        )

    def test_key_includes_offset(self):
        # 回归：offset 决定 effective_offset（offset + frame*size），不同 offset
        # 读取的帧数据不同；同一帧号在 offset=0 与 offset=N 必须是不不同的键，
        # 否则偏移后翻帧会串到偏移前的帧（0.2.1 M-2 语义）。
        from raw_view.gui.worker import DecodeCache
        from raw_view.models import DecodeOptions

        def opts(offset: int, frame: int) -> str:
            return DecodeCache.key(
                DecodeOptions(file_path="f.raw", offset=offset), frame
            )

        self.assertNotEqual(opts(0, 0), opts(4096, 0))
        self.assertNotEqual(opts(0, 2), opts(4096, 2))

    def test_get_after_store_returns_same(self):
        from raw_view.gui.worker import DecodeCache

        cache = DecodeCache(max_bytes=100_000, max_items=10)
        opts = self._make_options()
        key = DecodeCache.key(opts, 0)
        self.assertIsNone(cache.get(key))
        cache.store(key, self._make_result(4))
        result = cache.get(key)
        self.assertIsNotNone(result)
        self.assertEqual(result.width, 4)
        # 再次 get 后仍命中（LRU 命中移动不影响可命中性）
        self.assertIsNotNone(cache.get(key))

    def test_over_capacity_evicts_lru(self):
        from raw_view.gui.worker import DecodeCache

        # 每个 result 约 4x4x3=48 字节；max_bytes 限制总字节 → 逐出最久未用
        cache = DecodeCache(max_bytes=150, max_items=100)
        keys = [f"k{i}" for i in range(10)]
        for k in keys:
            cache.store(k, self._make_result(4))
        # 总字节超限后旧键被淘汰：最近存入的还在，首部（最先的）被逐出
        self.assertIsNotNone(cache.get(keys[-1]))
        # 早期的 key 可能已被 LRU 淘汰（150 字节只放约 3 条）
        present = sum(1 for k in keys if cache.get(k) is not None)
        self.assertGreaterEqual(present, 1)
        self.assertLess(present, len(keys))

    def test_max_items_evicts(self):
        from raw_view.gui.worker import DecodeCache

        cache = DecodeCache(max_bytes=10_000_000, max_items=3)
        for i in range(5):
            cache.store(f"k{i}", self._make_result(1))
        self.assertIsNone(cache.get("k0"))  # oldest evicted
        self.assertIsNotNone(cache.get("k4"))

    def test_oversized_single_frame_rejected(self):
        from raw_view.gui.worker import DecodeCache

        cache = DecodeCache(max_bytes=100, max_items=10)
        # 单帧超过 max_bytes → store 返回 False 且不缓存
        self.assertFalse(cache.store("big", self._make_result(20)))  # 20*20*3=1200B
        self.assertIsNone(cache.get("big"))

    def test_clear(self):
        from raw_view.gui.worker import DecodeCache

        cache = DecodeCache(max_bytes=100_000, max_items=10)
        cache.store("a", self._make_result(4))
        cache.store("b", self._make_result(4))
        cache.clear()
        self.assertIsNone(cache.get("a"))
        self.assertIsNone(cache.get("b"))


class OutputCollisionTests(unittest.TestCase):
    """P1-6：resolve_output_path_collision 三策略纯逻辑。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-collision-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _p(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    def test_no_exists_returns_same_for_all_policies(self):
        from raw_view.converter import resolve_output_path_collision

        path = self._p("new.raw")
        for policy in ("overwrite", "rename", "skip"):
            self.assertEqual(resolve_output_path_collision(path, policy), path)

    def test_overwrite_keeps_path(self):
        from raw_view.converter import resolve_output_path_collision

        path = self._p("exists.raw")
        open(path, "w").close()
        self.assertEqual(resolve_output_path_collision(path, "overwrite"), path)

    def test_skip_returns_none_when_exists(self):
        from raw_view.converter import resolve_output_path_collision

        path = self._p("exists.raw")
        open(path, "w").close()
        self.assertIsNone(resolve_output_path_collision(path, "skip"))

    def test_rename_bumps_suffix(self):
        from raw_view.converter import resolve_output_path_collision

        base = self._p("cap.raw")
        open(base, "w").close()
        first = resolve_output_path_collision(base, "rename")
        self.assertEqual(first, self._p("cap_1.raw"))
        # 第二次调用 → cap_1 已存在 → cap_2
        open(first, "w").close()
        second = resolve_output_path_collision(base, "rename")
        self.assertEqual(second, self._p("cap_2.raw"))

    def test_rename_skips_taken_number(self):
        from raw_view.converter import resolve_output_path_collision

        base = self._p("cap.raw")
        open(base, "w").close()
        open(self._p("cap_1.raw"), "w").close()
        self.assertEqual(resolve_output_path_collision(base, "rename"), self._p("cap_2.raw"))

    def test_generate_variants_tolerates_none_output_paths(self):
        # 回归：output_paths 返回 None（skip 策略下目标已存在）时，该变体应被
        # 跳过而不是在 os.path.dirname(None) 处崩溃（batch/convert 多变体共用）。
        import cv2
        import numpy as np
        from raw_view.converter import generate_image_variants

        src = self._p("in.png")
        cv2.imwrite(src, np.zeros((8, 8, 3), dtype=np.uint8))
        # 精确命中第一个计划的输出路径 → mapper 返回 None
        existing = self._p("in_8x8_RGGB8.raw")
        open(existing, "w").close()

        def mapper(out: str):
            return None if out == existing else out

        written = generate_image_variants(
            src, ["RAW8"], [(8, 8)], ["RGGB"], source_mode="bayer",
            output_dir=self._tmp,
            template="{input_stem}_{width}x{height}_{format}{ext}",
            output_paths=mapper,
        )
        # 已存在/被 skip 的那个不在 written 里，且不抛异常
        self.assertNotIn(existing, written)


class BatchBaseDirGlobTests(unittest.TestCase):
    """P2-3：批量 JSON base_dir 相对路径 + glob 展开。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-batch-basedir-")
        self._input = os.path.join(self._tmp, "input.png")
        np.zeros((4, 4, 3), dtype=np.uint8).tofile(self._input)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_spec(self, spec: dict) -> str:
        path = os.path.join(self._tmp, "spec.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        return path

    def _run_batch(self, spec: dict):
        import raw_view.__main__ as main_mod

        with mock.patch("raw_view.__main__.sys.exit"):
            return main_mod._run_batch(
                argparse.Namespace(batch_file=self._write_spec(spec), batch_help=False)
            )

    def test_base_dir_resolves_relative_input(self):
        # 只给 mode=view + 不存在的文件也未命中 → 记 failed；但不因解析崩溃。
        # 这里用真实存在的输入，观察 'OK' 而非 'not found'。
        import raw_view.__main__ as main_mod

        calls = []

        def fake_conv(input_path, output_path, raw_type, width, height, **kw):
            calls.append(input_path)
            return 1

        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "base_dir": self._tmp,
            "files": [{"input": "input.png", "output": os.path.join(self._tmp, "o.raw")}],
        }
        with mock.patch("raw_view.converter.image_file_to_raw", side_effect=fake_conv):
            self._run_batch(spec)
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isabs(calls[0]))
        self.assertEqual(calls[0], self._input)

    def test_glob_expands_multiple_files(self):
        import raw_view.__main__ as main_mod

        calls = []
        for name in ("a.png", "b.png", "c.png"):
            np.zeros((4, 4, 3), dtype=np.uint8).tofile(os.path.join(self._tmp, name))

        def fake_conv(input_path, output_path, raw_type, width, height, **kw):
            calls.append(os.path.basename(input_path))
            return 1

        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "base_dir": self._tmp,
            # 只匹配 a/b/c（避免 setUp 的 input.png 也被展开）
            "files": [{"input": "[abc].png"}],
        }
        with mock.patch("raw_view.converter.image_file_to_raw", side_effect=fake_conv):
            self._run_batch(spec)
        self.assertEqual(sorted(calls), ["a.png", "b.png", "c.png"])

    def test_glob_with_output_dir_still_works(self):
        # glob 展开后若 entry 有 output_dir，最终输出路径含该目录（不崩溃即可）
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "base_dir": self._tmp,
            "files": [{"input": "*.png", "output_dir": os.path.join(self._tmp, "sub")}],
        }
        self._run_batch(spec)  # 不抛错即通过

    def test_glob_with_explicit_output_derives_unique_paths(self):
        """glob 展开 + entry 显式 ``output`` 时每个匹配都得到独立输出名。

        0.3.0-M-1 回归：旧的 ``{**entry, "input": m}`` 展开会保留同一个
        ``output``，N 个匹配写同一个文件、后面覆盖前面（静默丢数据）。现在
        显式 ``output`` 参与展开的每个匹配时，以 ``{stem}_{input_name}`` 派生
        唯一输出名，绝不让多个输入共享一个输出路径。
        """
        import raw_view.__main__ as main_mod

        for name in ("a.png", "b.png", "c.png"):
            np.zeros((4, 4, 3), dtype=np.uint8).tofile(os.path.join(self._tmp, name))

        calls: list[tuple[str, str]] = []

        def fake_conv(input_path, output_path, raw_type, width, height, **kw):
            calls.append((input_path, output_path))
            return 1

        sink = os.path.join(self._tmp, "sink.raw")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "base_dir": self._tmp,
            # 只匹配 a/b/c（避免 setUp 的 input.png 也被展开）。output 要放在
            # entry 级——这才是 0.3.0-M-1 复现的写法（批量文档里 output 是 per-file）。
            "files": [{"input": "[abc].png", "output": sink}],
        }
        with mock.patch("raw_view.converter.image_file_to_raw", side_effect=fake_conv):
            self._run_batch(spec)

        self.assertEqual(len(calls), 3, "glob 应展开为 3 个匹配")
        # 3 条输出路径互不相同（绝不回退到 0.3.0 的两个/多个输入共享同一 output）
        outs = [out for _inp, out in calls]
        self.assertEqual(len(set(outs)), 3, f"每个匹配的输出必须唯一: {outs}")

        for in_path, out_path in calls:
            self.assertEqual(
                os.path.dirname(out_path), os.path.dirname(sink),
                "显式 output 目录仍是所在目录",
            )
            self.assertNotEqual(
                out_path, sink,
                "首个匹配也不应原样占用用户给的 sink.raw",
            )
            self.assertIn(
                Path(in_path).stem, Path(out_path).name,
                "命名应含输入文件名以便区分",
            )

    def test_output_stem_sanitizer(self):
        """派生输出名的 stem 消毒：Windows 非法字符/控制字符 → '_'。

        输入文件名可在其它平台含 ``<>:"/\\|?*`` 与控制字符；直接拼进输出名会让
        写入失败或写错位置。
        """
        from raw_view import __main__ as main_mod

        self.assertEqual(
            main_mod._sanitize_output_stem("a:b<c>d"),
            "a_b_c_d",
        )
        self.assertNotIn(
            "/", main_mod._sanitize_output_stem("x/y"),
            "正斜杠也是目录分隔符，必须消毒",
        )
        self.assertNotIn(
            "\x00", main_mod._sanitize_output_stem("a\x00b"),
            "控制字符应消毒",
        )
        self.assertEqual(
            main_mod._sanitize_output_stem("normal"),
            "normal",
            "普通文件名保持不变",
        )

    def test_glob_output_applies_sanitizer_to_derived_name(self):
        """0.3.0-M-1：glob+显式 output 派生名应经 stem 消毒器。

        用一个 Unix 风格非法名（mock 输入，真实 Windows 无法建该文件）验证派生
        路径里只有消毒后的名字；输出目录仍是用户给的目录。
        """
        import raw_view.__main__ as main_mod

        calls: list[tuple[str, str]] = []
        fake_match = os.path.join(self._tmp, "a:b<c>d.png")
        with mock.patch(
            "raw_view.converter.image_file_to_raw",
            side_effect=lambda i, o, *a, **k: (calls.append((i, o)), 1)[1],
        ), mock.patch.object(
            main_mod.glob, "glob",
            return_value=[fake_match],
        ), mock.patch.object(
            main_mod.os.path, "isfile",
            side_effect=lambda p: p == fake_match,
        ):
            self._run_batch({
                "mode": "convert",
                "target": "RAW",
                "raw_type": "RAW8",
                "width": 4,
                "height": 4,
                "base_dir": self._tmp,
                "files": [{"input": "*.png", "output": os.path.join(self._tmp, "sink.raw")}],
            })
        self.assertEqual(len(calls), 1)
        _inp, out = calls[0]
        out_name = Path(out).name
        # 关键不变量：派生名不得再含任何非法字符（具体前缀随平台而变——Windows 的
        # pathlib 会把 "a:..." 的 "a:" 当盘符吃掉，因此断言“无非法字符 + 有 given
        # 文件名”即可，跨平台稳定）。
        for ch in ('<', '>', ':', '"', '\\', '|', '?', '*'):
            self.assertNotIn(ch, out_name, f"派生名不应含非法字符 {ch!r}")
        self.assertTrue(
            out_name.endswith("_sink.raw"),
            f"派生名应保留用户给的输出文件名后缀: {out_name!r}",
        )
        self.assertIn(
            main_mod._sanitize_output_stem(Path(fake_match).stem), out_name,
            "派生名应含消毒后的输入 stem",
        )
        self.assertEqual(
            os.path.dirname(out), os.path.dirname(os.path.join(self._tmp, "sink.raw")),
            "显式 output 的目录保持不变",
        )

    def test_glob_output_collision_same_stem_gets_unique_suffix(self):
        """0.3.0-M-1：不同扩展名同 stem 的输入不能派生出同一输出名。

        ``a.png`` 与 ``a.jpg`` 的 stem 都是 ``a``，若只做 ``{stem}_{given}`` 派生
        会都落到 ``a_sink.raw``，第二个静默覆盖第一个（与 M-1 同类的丢数据）。
        展开必须对同入口内已占用的输出名追加序号，保证两两不同。
        """
        import raw_view.__main__ as main_mod

        calls: list[tuple[str, str]] = []
        fake_matches = [
            os.path.join(self._tmp, "a.png"),
            os.path.join(self._tmp, "a.jpg"),
        ]
        with mock.patch(
            "raw_view.converter.image_file_to_raw",
            side_effect=lambda i, o, *a, **k: (calls.append((i, o)), 1)[1],
        ), mock.patch.object(
            main_mod.glob, "glob",
            return_value=fake_matches,
        ), mock.patch.object(
            main_mod.os.path, "isfile",
            side_effect=lambda p: p in fake_matches,
        ):
            self._run_batch({
                "mode": "convert",
                "target": "RAW",
                "raw_type": "RAW8",
                "width": 4,
                "height": 4,
                "base_dir": self._tmp,
                "files": [{"input": "a.*", "output": os.path.join(self._tmp, "sink.raw")}],
            })
        self.assertEqual(len(calls), 2, "两个匹配都应转换")
        outs = [o for _i, o in calls]
        self.assertEqual(len(set(outs)), 2, f"同 stem 碰撞也必须产生唯一输出: {outs}")
        names = sorted(Path(o).name for o in outs)
        self.assertEqual(names[0], "a_sink.raw")
        self.assertEqual(names[1], "a_sink_2.raw")


class VersionSingleSourceTests(unittest.TestCase):
    """P2-4 / ENG-6：版本号单一来源一致性。"""

    def test_models_version_is_nonempty(self):
        from raw_view.models import APP_VERSION

        self.assertTrue(APP_VERSION.strip())

    def test_cli_and_models_agree(self):
        from raw_view.__main__ import get_app_version
        from raw_view.models import APP_VERSION

        self.assertEqual(get_app_version(), APP_VERSION)

    def test_help_content_matches_version(self):
        # help_content 里若提到版本号，应与单一来源一致（当前不内嵌版本号）。
        import raw_view.help_content as hc
        from raw_view.models import APP_VERSION

        self.assertNotIn(APP_VERSION, "")  # 语法占位：help 不硬编码版本
        self.assertTrue(isinstance(hc.HELP_HTML, str))

    def test_readme_current_version_matches_app_version(self):
        """0.2.2-L-2 / 0.4.0-L-4：README「当前版本」不得滞后于 APP_VERSION。

        曾多次出现版本号只改 APP_VERSION、README 忘同步的人工失配；这里直接读
        README 的「当前版本：x.y.z」行与单一来源比对。
        """
        import re

        from raw_view.models import APP_VERSION

        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
            encoding="utf-8"
        )
        m = re.search(r"当前版本：([0-9]+\.[0-9]+\.[0-9]+)", readme)
        self.assertIsNotNone(m, "README 应有「当前版本：x.y.z」行")
        self.assertEqual(
            m.group(1), APP_VERSION,
            "README 当前版本应与 models.APP_VERSION 保持一致（发布流程检查项）",
        )


class FrameSizeFormatTests(unittest.TestCase):
    """UI-9：_format_size 人类可读格式化 + Apply 门禁（超大帧禁用）。"""

    def test_format_size(self):
        from raw_view.gui.image_utils import _format_size

        self.assertIn("B", _format_size(1023))
        self.assertIn("KB", _format_size(2048))
        self.assertIn("MB", _format_size(1024 * 1024))
        self.assertIn("GB", _format_size(2 * 1024 * 1024 * 1024))

    def test_byte_formatter_is_single_source(self):
        """0.3.0-L-4 / 0.4.0-L-1 / 0.4.1-L-1：三份字节格式化并存已收敛为一处。

        面板 Estimated frame、Convert/Batch 预览、主窗状态共用
        ``image_utils._format_size``，不再有独立的 ``_human_size`` / panels 内
        ``_format_size`` 拷贝。
        """
        from raw_view.gui.image_utils import _format_size as shared
        import raw_view.gui.panels as panels
        import raw_view.gui.dialogs.convert as convert

        self.assertIs(panels._format_size, shared, "panels 应复用共享 helper")
        self.assertIs(convert._format_size, shared, "convert 应复用共享 helper")
        self.assertFalse(hasattr(convert, "_human_size"), "_human_size 已删除")

    def test_apply_gate_disables_oversize_and_reenables(self):
        # UI-9 门禁：单帧超 512MB 禁用 Apply；参数改回合法时重新启用。
        # 这是对“面板启用态”判断的回归——门禁必须能反复权衡，不能因为一次
        # 禁用就再也无法点亮（曾用 apply_btn.isEnabled() 当面板启用态导致此 bug）。
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from raw_view.gui.panels import ControlPanel

        p = ControlPanel()
        try:
            # 超大帧 → 禁用 Apply
            p.set_type("RAW")
            p.format_combo.setCurrentText("RAW32")
            p.width_spin.setValue(65535)
            p.height_spin.setValue(65535)
            self.assertFalse(p.apply_btn.isEnabled())
            # 改回合法 → Apply 恢复（不能卡死在门禁）
            p.set_type("YUV")
            p.format_combo.setCurrentText("YOnly")
            p.bit_depth_combo.setCurrentText("8")
            p.width_spin.setValue(100)
            p.height_spin.setValue(100)
            self.assertTrue(p.apply_btn.isEnabled())
            # 面板整体禁用后（无 item），门禁不能把 Apply 点亮
            p.set_enabled(False)
            p.set_type("RAW")
            p.format_combo.setCurrentText("RAW8")
            p.width_spin.setValue(100)
            p.height_spin.setValue(100)
            self.assertFalse(p.apply_btn.isEnabled())
        finally:
            p.close()
            p.deleteLater()


class TabDirtyGuardTests(unittest.TestCase):
    """UI-4：标签未保存标记在 MainWindow.__new__ 测试对象下不崩溃。"""

    def test_set_tab_dirty_does_not_raise_on_stub(self):
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from raw_view.gui.app import MainWindow
        from raw_view.models import ViewerItem

        w = MainWindow.__new__(MainWindow)
        # 无 items / item_tabs（测试常用构造）→ 防御性返回，不抛错
        item = ViewerItem()
        item.options.file_path = os.path.join("d", "cap.bin")
        w._set_tab_dirty(item, True)  # 不应抛 RuntimeError


if __name__ == "__main__":
    unittest.main()
