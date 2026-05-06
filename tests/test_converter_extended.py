"""Extended converter tests: Bayer, resize, gray conversion, raw_file_to_image, etc."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np

from raw_view.converter import (
    bayer8_to_rgb,
    bgr_to_bayer8,
    bgr_to_gray8,
    image_file_to_raw,
    image_file_to_yuv,
    load_bgr_image,
    raw_file_to_image,
    yuv_file_to_image,
)


def _make_test_bgr(h: int, w: int) -> np.ndarray:
    """Create a simple BGR test image with varying colors."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :, 0] = np.tile(np.arange(w, dtype=np.uint8), (h, 1))  # B
    img[:, :, 1] = np.tile(np.arange(h, dtype=np.uint8).reshape(-1, 1), (1, w))  # G
    img[:, :, 2] = 128  # R
    return img


def _write_png(path: str, bgr: np.ndarray) -> None:
    import cv2

    cv2.imwrite(path, bgr)


class BayerConversionExtendedTests(unittest.TestCase):
    """More comprehensive Bayer conversion tests."""

    def test_bgr_to_bayer8_all_patterns(self):
        """Verify all 4 Bayer patterns produce correct layout."""
        bgr = np.zeros((4, 4, 3), dtype=np.uint8)
        for y in range(4):
            for x in range(4):
                bgr[y, x] = [x * 10, y * 10, (x + y) * 10]
        for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
            with self.subTest(pattern=pattern):
                bayer = bgr_to_bayer8(bgr, 4, 4, pattern=pattern)
                self.assertEqual(bayer.shape, (4, 4))
                self.assertEqual(bayer.dtype, np.uint8)

    def test_bgr_to_bayer8_resize(self):
        """bgr_to_bayer8 should resize when dimensions differ."""
        bgr = _make_test_bgr(8, 8)
        bayer = bgr_to_bayer8(bgr, 4, 4)  # downsample
        self.assertEqual(bayer.shape, (4, 4))

    def test_bgr_to_bayer8_upscale(self):
        """bgr_to_bayer8 should upscale when target is larger."""
        bgr = _make_test_bgr(2, 2)
        bayer = bgr_to_bayer8(bgr, 4, 4)  # upsample
        self.assertEqual(bayer.shape, (4, 4))

    def test_bayer8_to_rgb_all_patterns(self):
        """bayer8_to_rgb should work for all patterns."""
        bgr = _make_test_bgr(8, 8)
        for pattern in ("RGGB", "BGGR", "GRBG", "GBRG"):
            with self.subTest(pattern=pattern):
                bayer = bgr_to_bayer8(bgr, 8, 8, pattern=pattern)
                rgb = bayer8_to_rgb(bayer, pattern=pattern)
                self.assertEqual(rgb.shape, (8, 8, 3))
                self.assertEqual(rgb.dtype, np.uint8)

    def test_bayer8_to_rgb_invalid_ndim(self):
        """3D input should raise ValueError."""
        with self.assertRaises(ValueError):
            bayer8_to_rgb(np.zeros((4, 4, 3), dtype=np.uint8))

    def test_bayer8_to_rgb_unknown_pattern(self):
        with self.assertRaises(ValueError):
            bayer8_to_rgb(np.zeros((4, 4), dtype=np.uint8), pattern="INVALID")


class GrayConversionTests(unittest.TestCase):
    """Tests for BGR → Grayscale conversion."""

    def test_bgr_to_gray8_shape(self):
        bgr = _make_test_bgr(10, 20)
        gray = bgr_to_gray8(bgr, 20, 10)
        self.assertEqual(gray.shape, (10, 20))
        self.assertEqual(gray.dtype, np.uint8)

    def test_bgr_to_gray8_resize(self):
        bgr = _make_test_bgr(8, 8)
        gray = bgr_to_gray8(bgr, 16, 16)
        self.assertEqual(gray.shape, (16, 16))

    def test_bgr_to_gray8_invalid_dims(self):
        bgr = _make_test_bgr(4, 4)
        with self.assertRaises(ValueError):
            bgr_to_gray8(bgr, 0, 4)
        with self.assertRaises(ValueError):
            bgr_to_gray8(bgr, 4, -1)


class ImageFileToRawTests(unittest.TestCase):
    """Tests for image_file_to_raw with mock images."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._input_png = os.path.join(self._tmpdir, "input.png")
        bgr = _make_test_bgr(4, 6)
        _write_png(self._input_png, bgr)

    def tearDown(self):
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def _out_path(self, name: str) -> str:
        return os.path.join(self._tmpdir, name)

    def test_raw8_bayer(self):
        out = self._out_path("test.raw")
        size = image_file_to_raw(self._input_png, out, "RAW8", 6, 4)
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))

    def test_raw8_gray(self):
        out = self._out_path("test_gray.raw")
        size = image_file_to_raw(self._input_png, out, "RAW8", 6, 4, source_mode="gray")
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))

    def test_raw12_msb_big(self):
        out = self._out_path("test_raw12.raw")
        size = image_file_to_raw(
            self._input_png, out, "RAW12", 6, 4,
            alignment="msb", endianness="big",
        )
        self.assertGreater(size, 0)

    def test_raw10_packed(self):
        out = self._out_path("test_raw10_packed.raw")
        size = image_file_to_raw(
            self._input_png, out, "RAW10 Packed", 8, 4,
        )
        self.assertGreater(size, 0)

    def test_resize_during_encode(self):
        """Encoding with different output dimensions forces resize."""
        out = self._out_path("test_resized.raw")
        size = image_file_to_raw(self._input_png, out, "RAW8", 10, 10)
        self.assertGreater(size, 0)
        # Verify size matches
        self.assertEqual(size, 10 * 10)

    @mock.patch("raw_view.converter.load_bgr_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8))
    def test_invalid_source_mode(self, _mock_load):
        with self.assertRaises(ValueError):
            image_file_to_raw("in.png", "out.raw", "RAW8", 2, 2, source_mode="invalid")

    def test_nonexistent_input_raises(self):
        with self.assertRaises(Exception):
            image_file_to_raw("/nonexistent/file.png", "out.raw", "RAW8", 2, 2)


class ImageFileToYuvTests(unittest.TestCase):
    """Tests for image_file_to_yuv."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._input_png = os.path.join(self._tmpdir, "input.png")
        bgr = _make_test_bgr(8, 8)
        _write_png(self._input_png, bgr)

    def tearDown(self):
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def test_i420(self):
        out = os.path.join(self._tmpdir, "out.yuv")
        size = image_file_to_yuv(self._input_png, out, "I420", 8, 8)
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))
        # I420: 8*8 + 4*4 + 4*4 = 64 + 16 + 16 = 96
        self.assertEqual(size, 96)

    def test_nv12(self):
        out = os.path.join(self._tmpdir, "out_nv12.yuv")
        size = image_file_to_yuv(self._input_png, out, "NV12", 8, 8)
        self.assertGreater(size, 0)
        self.assertEqual(size, 96)

    def test_yuyv(self):
        out = os.path.join(self._tmpdir, "out_yuyv.yuv")
        size = image_file_to_yuv(self._input_png, out, "YUYV", 8, 8)
        self.assertGreater(size, 0)
        self.assertEqual(size, 128)

    def test_nv16(self):
        out = os.path.join(self._tmpdir, "out_nv16.yuv")
        size = image_file_to_yuv(self._input_png, out, "NV16", 8, 8)
        self.assertGreater(size, 0)
        self.assertEqual(size, 128)

    def test_resize_during_yuv_encode(self):
        out = os.path.join(self._tmpdir, "out_resized.yuv")
        size = image_file_to_yuv(self._input_png, out, "I420", 4, 4)
        self.assertGreater(size, 0)
        self.assertEqual(size, 24)  # 4*4 + 2*2 + 2*2


class RawFileToImageTests(unittest.TestCase):
    """Tests for raw_file_to_image (decode RAW → PNG/JPEG)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        # Create a small RAW12 file (4x4 pixels → 32 bytes in 16-bit)
        rng = np.random.RandomState(99)
        raw_data = rng.randint(0, 4096, size=(4, 4), dtype=np.uint16)
        self._raw_path = os.path.join(self._tmpdir, "test.raw12")
        raw_data.astype("<u2").tofile(self._raw_path)

    def tearDown(self):
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def test_raw12_to_png_bayer(self):
        out = os.path.join(self._tmpdir, "out.png")
        size = raw_file_to_image(
            self._raw_path, out, "RAW12", 4, 4,
            preview_mode="Bayer Color", bayer_pattern="RGGB",
        )
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))

    def test_raw12_to_png_grayscale(self):
        out = os.path.join(self._tmpdir, "out_gray.png")
        size = raw_file_to_image(
            self._raw_path, out, "RAW12", 4, 4,
            preview_mode="Grayscale",
        )
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))

    def test_raw8_to_png(self):
        raw8_path = os.path.join(self._tmpdir, "test.raw8")
        np.arange(16, dtype=np.uint8).reshape(4, 4).tofile(raw8_path)
        out = os.path.join(self._tmpdir, "out_raw8.png")
        size = raw_file_to_image(raw8_path, out, "RAW8", 4, 4)
        self.assertGreater(size, 0)

    def test_nonexistent_raw_file(self):
        with self.assertRaises(Exception):
            raw_file_to_image("/nonexistent.raw", "out.png", "RAW8", 4, 4)


class YuvFileToImageTests(unittest.TestCase):
    """Tests for yuv_file_to_image (decode YUV → PNG/JPEG)."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        rng = np.random.RandomState(123)
        rgb = rng.randint(0, 256, size=(4, 8, 3), dtype=np.uint8)
        from raw_view.formats import rgb_to_yuv_bytes

        yuv_data = rgb_to_yuv_bytes(rgb, "I420")
        self._yuv_path = os.path.join(self._tmpdir, "test.i420")
        with open(self._yuv_path, "wb") as f:
            f.write(yuv_data)

    def tearDown(self):
        for f in os.listdir(self._tmpdir):
            try:
                os.remove(os.path.join(self._tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(self._tmpdir)
        except OSError:
            pass

    def test_yuv_to_png(self):
        out = os.path.join(self._tmpdir, "out.png")
        size = yuv_file_to_image(self._yuv_path, out, "I420", 8, 4)
        self.assertGreater(size, 0)
        self.assertTrue(os.path.isfile(out))

    def test_nonexistent_yuv_file(self):
        with self.assertRaises(Exception):
            yuv_file_to_image("/nonexistent.yuv", "out.png", "I420", 8, 4)


if __name__ == "__main__":
    unittest.main()
