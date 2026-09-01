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

    def test_variant_selector_yuv_formats_follows_dict(self):
        from raw_view.gui.widgets.variant_selector import VariantSelector

        self.assertEqual(
            VariantSelector.YUV_FORMATS, list(YUV_BYTES_PER_PIXEL.keys())
        )
        self.assertEqual(VariantSelector.YUV_FORMATS[0], "YOnly")
        self.assertIn("YOnly", VariantSelector.YUV_FORMATS)

    def test_worker_dispatch_rule_holds_for_yonly(self):
        # 后台 worker 的 YUV 分支判定就是 ``name in YUV_BYTES_PER_PIXEL``，
        # YOnly 已在字典中 → 自动走进 YUV 解码路径（无需改动 worker）。
        self.assertIn("YOnly", YUV_BYTES_PER_PIXEL)
        self.assertTrue("YOnly" in YUV_BYTES_PER_PIXEL)

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
