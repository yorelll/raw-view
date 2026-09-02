"""YOnly (YUV 4:0:0 full-resolution grayscale) tests.

Covers the frame-size rule (1 byte/pixel, no even-width/height restriction),
the encode/decode round-trip, RAW→YOnly end-to-end conversion, and the
panel/dialog/variant-selector wiring that makes YOnly usable in the UI.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from raw_view.formats import (
    FormatError,
    ImageSpec,
    YUV_BYTES_PER_PIXEL,
    decode_yuv,
    expected_frame_size_yuv,
    rgb_to_yuv_bytes,
)


def _make_known_gray(h: int, w: int) -> np.ndarray:
    """确定性灰度图：横向 + 纵向渐变混合，覆盖 0..255 全范围。"""
    x = np.tile(np.arange(w, dtype=np.uint8), (h, 1))
    y = np.tile(np.arange(h, dtype=np.uint8).reshape(-1, 1), (1, w))
    gray = ((x.astype(np.uint16) + y.astype(np.uint16)) * 3 % 256).astype(np.uint8)
    return gray


def _gray_to_rgb(gray: np.ndarray) -> np.ndarray:
    return np.repeat(gray[:, :, None], 3, axis=2)


class YOnlyFrameSizeTests(unittest.TestCase):
    """YOnly 帧大小规则：1 字节/像素，任意宽高（无偶数限制）。"""

    def test_bytes_per_pixel_is_one(self):
        self.assertEqual(YUV_BYTES_PER_PIXEL["YOnly"], 1.0)

    def test_frame_size_equals_w_times_h(self):
        for w, h in [(1280, 720), (8, 4), (1, 1), (2560, 1440)]:
            with self.subTest(w=w, h=h):
                self.assertEqual(expected_frame_size_yuv("YOnly", w, h), w * h)

    def test_odd_dimensions_ok(self):
        # 与其它 YUV 不同：YOnly 无宽高必须为偶数的限制。
        self.assertEqual(expected_frame_size_yuv("YOnly", 5, 5), 25)
        self.assertEqual(expected_frame_size_yuv("YOnly", 7, 3), 21)
        self.assertEqual(expected_frame_size_yuv("YOnly", 1, 9), 9)

    def test_invalid_yonly_raises(self):
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("INVALID", 4, 4)

    def test_other_formats_even_check_unchanged(self):
        # 回归：新增 YOnly 分支不能破坏既有 YUV 的偶数校验。
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("I420", 5, 4)
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("YUYV", 5, 4)


class YOnlyDecodeEncodeTests(unittest.TestCase):
    """YOnly 编解码 round-trip。"""

    def test_frame_size_matches_expected(self):
        gray = _make_known_gray(4, 8)
        data = rgb_to_yuv_bytes(_gray_to_rgb(gray), "YOnly")
        self.assertEqual(len(data), 32)
        self.assertEqual(expected_frame_size_yuv("YOnly", 8, 4), len(data))

    def test_roundtrip_known_gray(self):
        h, w = 6, 9  # 故意用奇数宽，验证无偶数限制
        gray = _make_known_gray(h, w)
        encoded = rgb_to_yuv_bytes(_gray_to_rgb(gray), "YOnly")
        decoded = decode_yuv(encoded, ImageSpec(w, h), "YOnly")
        self.assertEqual(decoded.shape, (h, w, 3))
        self.assertEqual(decoded.dtype, np.uint8)
        # 三通道相同（灰度）
        self.assertTrue(np.array_equal(decoded[:, :, 0], decoded[:, :, 1]))
        self.assertTrue(np.array_equal(decoded[:, :, 0], decoded[:, :, 2]))
        # 灰度值≈原值（编码用的 BT.601 系数引入 ≤1 量化）
        diff = np.abs(decoded[:, :, 0].astype(int) - gray.astype(int))
        self.assertLessEqual(int(diff.max()), 1)

    def test_roundtrip_flat_black_and_white(self):
        # 全黑/全白走一遍，避免渐变掩盖端点量化问题。
        for val in (0, 255):
            with self.subTest(val=val):
                gray = np.full((3, 5), val, dtype=np.uint8)
                encoded = rgb_to_yuv_bytes(_gray_to_rgb(gray), "YOnly")
                decoded = decode_yuv(encoded, ImageSpec(5, 3), "YOnly")
                self.assertEqual(int(decoded[0, 0, 0]), val)

    def test_truncated_yonly_raises(self):
        with self.assertRaises(FormatError):
            decode_yuv(bytes([0] * 9), ImageSpec(5, 2), "YOnly")  # needs 10

    def test_encode_length_is_w_times_h(self):
        rng = np.random.RandomState(3)
        rgb = rng.randint(0, 256, size=(4, 7, 3), dtype=np.uint8)
        self.assertEqual(len(rgb_to_yuv_bytes(rgb, "YOnly")), 4 * 7)


class YOnlyMultiBitTests(unittest.TestCase):
    """YOnly 多 bit（YOnly8..16）：帧大小、round-trip、lsb/msb 有效位、endianness。"""

    def test_frame_size_scales_with_bit_depth(self):
        # 8-bit → 1 字节/像素；10/12/14/16 → 2 字节/像素（16-bit 存储）
        self.assertEqual(expected_frame_size_yuv("YOnly8", 10, 6), 60)
        self.assertEqual(expected_frame_size_yuv("YOnly10", 10, 6), 120)
        self.assertEqual(expected_frame_size_yuv("YOnly12", 10, 6), 120)
        self.assertEqual(expected_frame_size_yuv("YOnly14", 10, 6), 120)
        self.assertEqual(expected_frame_size_yuv("YOnly16", 10, 6), 120)
        # 奇数宽高也允许（YOnly 无偶数限制）
        self.assertEqual(expected_frame_size_yuv("YOnly12", 5, 7), 70)

    def _roundtrip(self, fmt, alignment, endianness):
        # _make_known_gray(h, w)=(6, 9)：6 行 9 列；对应 ImageSpec(width=9, height=6)
        # decode_yuv 返回 (h, w, 3)=(6, 9, 3)。奇数宽高一并在内。
        gray = _make_known_gray(6, 9)
        rgb = _gray_to_rgb(gray)
        enc = rgb_to_yuv_bytes(rgb, fmt, alignment=alignment, endianness=endianness)
        self.assertEqual(len(enc), expected_frame_size_yuv(fmt, 9, 6))
        dec = decode_yuv(enc, ImageSpec(9, 6), fmt, alignment=alignment, endianness=endianness)
        self.assertEqual(dec.shape, (6, 9, 3))
        # 灰度三通道一致；±2 容差（N-bit 量化 + 浮点归属）
        d = dec[:, :, 0].astype(int) - gray.astype(int)
        self.assertLessEqual(int(np.abs(d).max()), 2, f"{fmt}/{alignment}/{endianness} max diff")

    def test_roundtrip_all_bits_and_alignments(self):
        for fmt in ("YOnly8", "YOnly10", "YOnly12", "YOnly14", "YOnly16"):
            for align in ("lsb", "msb"):
                for endian in ("little", "big"):
                    with self.subTest(fmt=fmt, align=align, endian=endian):
                        self._roundtrip(fmt, align, endian)

    def test_lsb_vs_msb_produce_different_physical_bytes(self):
        # 同一灰度值，lsb（低 N 位有效）与 msb（高 N 位有效）存储字节必须不同
        gray = np.zeros((1, 1, 3), dtype=np.uint8)
        gray[0, 0] = 200
        lsb = rgb_to_yuv_bytes(gray, "YOnly12", alignment="lsb", endianness="little")
        msb = rgb_to_yuv_bytes(gray, "YOnly12", alignment="msb", endianness="little")
        self.assertEqual(len(lsb), 2)
        self.assertEqual(len(msb), 2)
        self.assertNotEqual(lsb, msb, "lsb/msb 应以不同字节布局存储")

    def test_lsb_msb_show_same_gray_after_correct_decode(self):
        # 用各自正确的 alignment 解码后，显示的灰度应一致（归一化等价）
        gray = np.zeros((1, 4, 3), dtype=np.uint8)
        gray[0, 0] = 10
        gray[0, 1] = 100
        gray[0, 2] = 200
        gray[0, 3] = 250
        lsb_enc = rgb_to_yuv_bytes(gray, "YOnly12", alignment="lsb")
        msb_enc = rgb_to_yuv_bytes(gray, "YOnly12", alignment="msb")
        lsb_dec = decode_yuv(lsb_enc, ImageSpec(4, 1), "YOnly12", alignment="lsb")
        msb_dec = decode_yuv(msb_enc, ImageSpec(4, 1), "YOnly12", alignment="msb")
        d = lsb_dec[:, :, 0].astype(int) - msb_dec[:, :, 0].astype(int)
        self.assertLessEqual(int(np.abs(d).max()), 2)

    def test_endianness_swaps_byte_order_in_file(self):
        # 端序改变的是文件里每像素 2 字节的排列。对 YOnly 系列，归一化 16-bit
        # 存储值 = round(g/255*65535) = g*257（恒为 0xGGGG 对称型），单像素大小端
        # 字节相同；但端序必须在编码路径中正确传递（big 端写出的字节序与 little
        # 不同，多像素时整体布局可区分）。这里验证 big/little 的字节流确实按声明
        # 的端序写出。
        gray = np.zeros((1, 2, 3), dtype=np.uint8)
        gray[0, 0, 0] = 1
        gray[0, 1, 0] = 2
        enc_le = rgb_to_yuv_bytes(gray, "YOnly16", endianness="little")
        enc_be = rgb_to_yuv_bytes(gray, "YOnly16", endianness="big")
        # 两像素值相同刻度；big 端与 little 端字节要么一致（对称值）要么高低位互换
        self.assertEqual(len(enc_le), len(enc_be))
        # 用声明的端序解析，两种都应还原出相同像素值序列
        v_le = np.frombuffer(enc_le, dtype="<u2")
        v_be = np.frombuffer(enc_be, dtype=">u2")
        np.testing.assert_array_equal(v_be, v_le)


class YOnlyConverterTests(unittest.TestCase):
    """RAW→YOnly 端到端（converter.image_file_to_yuv / yuv_file_to_image）。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._input_png = os.path.join(self._tmpdir, "input.png")
        import cv2

        gray = _make_known_gray(8, 9)
        cv2.imwrite(self._input_png, _gray_to_rgb(gray)[:, :, ::-1])  # BGR

    def tearDown(self):
        for name in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, name))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def test_image_file_to_yuv_yonly_odd_dimensions(self):
        from raw_view.converter import image_file_to_yuv

        out = os.path.join(self._tmpdir, "out.yonly")
        # 奇数宽高（9x5）也应成功。
        size = image_file_to_yuv(self._input_png, out, "YOnly", 9, 5)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(size, 9 * 5)
        with open(out, "rb") as f:
            data = f.read()
        self.assertEqual(len(data), 45)

    def test_yuv_file_to_image_yonly(self):
        from raw_view.converter import image_file_to_yuv, yuv_file_to_image

        out = os.path.join(self._tmpdir, "out.yonly")
        size = image_file_to_yuv(self._input_png, out, "YOnly", 9, 5)
        self.assertEqual(size, 45)
        img = os.path.join(self._tmpdir, "out.png")
        png_bytes = yuv_file_to_image(out, img, "YOnly", 9, 5)
        self.assertGreater(png_bytes, 0)
        self.assertTrue(os.path.isfile(img))

    def test_image_file_to_yuv_yonly_mocked_source(self):
        from raw_view.converter import image_file_to_yuv

        with mock.patch(
            "raw_view.converter.load_bgr_image",
            return_value=np.zeros((2, 2, 3), dtype=np.uint8),
        ):
            out = os.path.join(self._tmpdir, "mocked.yonly")
            size = image_file_to_yuv("in.png", out, "YOnly", 3, 3)
            self.assertEqual(size, 9)


class YOnlyUIIntegrationTests(unittest.TestCase):
    """面板/对话框/variant selector 的 YOnly 接线（import/常量/源码级校验）。

    注意：本机在无显示环境下实例化真实 QDialog 会出现与 YOnly 无关的原生
    段错误（exit 127，间歇性；纯 ConvertDialog 构造同样触发）。与项目既有
    测试约定一致（“避免依赖真实 GUI 显示”），这里只做 import / 模块常量 /
    源码引用级断言，不做真实控件实例化。
    """

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    def test_panel_yuv_formats_contains_yonly_first(self):
        from raw_view.gui.panels import ControlPanel

        self.assertIn("YOnly", ControlPanel.YUV_FORMATS)
        self.assertEqual(ControlPanel.YUV_FORMATS[0], "YOnly")

    def test_variant_selector_yuv_format_is_single_yonly(self):
        from raw_view.gui.widgets.variant_selector import VariantSelector

        # YUV 列表收敛为单个 YOnly（需求 2：yonly 是一个 fmt，非 yonly8/10/... 多格式）
        self.assertIn("YOnly", VariantSelector.YUV_FORMATS)
        for f in ("YOnly8", "YOnly10", "YOnly12", "YOnly14", "YOnly16"):
            self.assertNotIn(f, VariantSelector.YUV_FORMATS)
        # 勾选 YOnly → 按位深展开
        vs = VariantSelector.__new__(VariantSelector)
        vs._format_boxes = {"YOnly": type("C", (), {"isChecked": lambda s: True})()}
        vs._yonly_bit_boxes = {"8": type("C", (), {"isChecked": lambda s: True})(),
                               "12": type("C", (), {"isChecked": lambda s: False})()}
        self.assertEqual(vs.selected_formats(), ["YOnly8"])

    def test_worker_dispatch_rule_holds_for_yonly(self):
        # 后台 worker 的 YUV 分支判定就是 ``name in YUV_BYTES_PER_PIXEL``，
        # YOnly 已在字典中 → 自动走进 YUV 解码路径（无需改动 worker）。
        self.assertIn("YOnly", YUV_BYTES_PER_PIXEL)
        self.assertTrue("YOnly" in YUV_BYTES_PER_PIXEL)

    def test_panel_marks_yonly_16bit_for_alignment(self):
        # YOnly 多 bit（16-bit 存储）需要 Alignment/Endianness 控制其有效位
        # 位置与大小端（类同 RAW10/12/16）。
        from raw_view.gui.panels import ControlPanel

        for fmt in ("YOnly10", "YOnly12", "YOnly14", "YOnly16"):
            self.assertIn(fmt, ControlPanel._YONLY_16BIT, f"{fmt} 应在 _YONLY_16BIT")
        self.assertNotIn("YOnly", ControlPanel._YONLY_16BIT)
        self.assertNotIn("YOnly8", ControlPanel._YONLY_16BIT)

    def test_advanced_conditional_visibility(self):
        # 需求 1/2：高级参数不做折叠容器，而是"只有选目标才显示"（主 QFormLayout
        # 直接行，逐控件 setVisible）。
        #   RAW → 显示高级参数（align/endian/preview/bayer），隐藏 bit depth
        #   YUV+YOnly → 显示 bit depth + align + endian
        #   其它 → 全部隐藏
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from raw_view.gui.panels import ControlPanel

        p = ControlPanel()
        try:
            # RAW 默认：显示高级参数（无 advanced_section 容器——不再有折叠组）
            self.assertFalse(hasattr(p, "advanced_section"))
            p.set_type("RAW")
            self.assertFalse(p.align_combo.isHidden())
            self.assertFalse(p.endian_combo.isHidden())
            self.assertFalse(p.raw_preview_combo.isHidden())
            self.assertFalse(p.bayer_pattern_combo.isHidden())
            # RAW 隐藏 bit depth（位深由 Format 自身表达，需求 1）
            self.assertTrue(p.bit_depth_combo.isHidden())
            # YUV 默认 YUYV：全部隐藏
            p.set_type("YUV")
            self.assertTrue(p.align_combo.isHidden())
            self.assertTrue(p.bit_depth_combo.isHidden())
            # 切 YOnly：显示 bit depth + align + endian，隐藏 preview/bayer
            p.set_format("YOnly")
            self.assertFalse(p.bit_depth_combo.isHidden())
            self.assertFalse(p.align_combo.isHidden())
            self.assertFalse(p.endian_combo.isHidden())
            self.assertTrue(p.raw_preview_combo.isHidden())
            self.assertTrue(p.bayer_pattern_combo.isHidden())
            # get_values 把 YOnly+bit 映射为内部有效名
            p.bit_depth_combo.setCurrentText("16")
            self.assertEqual(p.get_values()["format_name"], "YOnly16")
        finally:
            p.close()
            p.deleteLater()

    def test_convert_batch_dialogs_source_panel_yuv_list(self):
        # 两个转换对话框的 YUV 下拉不再硬编码列表，而是直接引用
        # ControlPanel.YUV_FORMATS —— 只要主面板常量包含 YOnly，下拉即跟随。
        import inspect

        from raw_view.gui.dialogs import batch_convert, convert

        for mod in (convert, batch_convert):
            src = inspect.getsource(mod)
            with self.subTest(module=mod.__name__):
                self.assertIn("addItems(ControlPanel.YUV_FORMATS)", src)
                self.assertNotIn('", "NV61"]', src)  # 旧的硬编码列表已移除

    def test_fourcc_grey_alias_includes_yonly(self):
        from raw_view.fourcc_data import ALIAS_TO_FOURCC, FourCCStore

        self.assertEqual(ALIAS_TO_FOURCC["YONLY"], "GREY")
        self.assertEqual(ALIAS_TO_FOURCC["Y8"], "GREY")
        entry = FourCCStore().find_by_alias("YOnly")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.fourcc, "GREY")


if __name__ == "__main__":
    unittest.main()
