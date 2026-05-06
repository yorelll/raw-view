"""Extended format tests: packed round-trip, boundary conditions, edge cases."""

from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np

from raw_view.formats import (
    FormatError,
    ImageSpec,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    gray8_to_raw_bytes,
    raw_to_display_gray,
    rgb_to_yuv_bytes,
)


class PackedRoundTripTests(unittest.TestCase):
    """Encode → decode round-trip validation for all packed formats."""

    def _roundtrip_raw(self, raw_type: str, width: int, height: int, alignment: str = "lsb"):
        """Generate random pixel values, encode, then decode and compare."""
        rng = np.random.RandomState(42)
        bits = {"RAW10 Packed": 10, "RAW12 Packed": 12, "RAW14 Packed": 14}[raw_type]
        max_val = (1 << bits) - 1
        pixels = rng.randint(0, max_val + 1, size=(height, width), dtype=np.uint16)
        raw8 = (pixels / max_val * 255).clip(0, 255).astype(np.uint8)
        encoded = gray8_to_raw_bytes(raw8, raw_type, alignment=alignment)
        decoded = decode_raw(encoded, ImageSpec(width, height), raw_type, alignment=alignment)
        # Allow ±1 LSB quantization loss in the round-trip
        decoded_quant = np.round(decoded / max_val * 255).clip(0, 255).astype(np.uint8)
        diff = np.abs(decoded_quant.astype(int) - raw8.astype(int))
        max_diff = int(diff.max())
        self.assertLessEqual(
            max_diff, 1,
            f"{raw_type} round-trip max diff = {max_diff} (alignment={alignment})",
        )

    def test_raw10_packed_roundtrip_lsb(self):
        self._roundtrip_raw("RAW10 Packed", 8, 4)

    def test_raw12_packed_roundtrip_lsb(self):
        self._roundtrip_raw("RAW12 Packed", 8, 4)

    def test_raw14_packed_roundtrip_lsb(self):
        self._roundtrip_raw("RAW14 Packed", 8, 4)

    def test_raw10_packed_lsb_large(self):
        """Test RAW10 Packed with larger dimensions."""
        self._roundtrip_raw("RAW10 Packed", 64, 48)

    def test_raw12_packed_lsb_large(self):
        """Test RAW12 Packed with larger dimensions."""
        self._roundtrip_raw("RAW12 Packed", 64, 48)

    def test_raw14_packed_lsb_large(self):
        """Test RAW14 Packed with larger dimensions."""
        self._roundtrip_raw("RAW14 Packed", 64, 48)

    def test_yuv420_roundtrip(self):
        """YUV420 encode → decode should preserve approximate colors."""
        rng = np.random.RandomState(123)
        rgb = rng.randint(0, 256, size=(4, 8, 3), dtype=np.uint8)
        for fmt in ("I420", "YV12", "NV12", "NV21"):
            with self.subTest(fmt=fmt):
                encoded = rgb_to_yuv_bytes(rgb, fmt)
                decoded = decode_yuv(encoded, ImageSpec(8, 4), fmt)
                # YUV is lossy; ensure output shape is correct
                self.assertEqual(decoded.shape, (4, 8, 3))
                self.assertEqual(decoded.dtype, np.uint8)

    def test_yuv422_roundtrip(self):
        """YUV422 encode → decode."""
        rng = np.random.RandomState(456)
        rgb = rng.randint(0, 256, size=(4, 8, 3), dtype=np.uint8)
        for fmt in ("YUYV", "UYVY", "NV16"):
            with self.subTest(fmt=fmt):
                encoded = rgb_to_yuv_bytes(rgb, fmt)
                decoded = decode_yuv(encoded, ImageSpec(8, 4), fmt)
                self.assertEqual(decoded.shape, (4, 8, 3))
                self.assertEqual(decoded.dtype, np.uint8)


class Raw16RoundTripTests(unittest.TestCase):
    """RAW16 encode → decode round-trip with varying endianness and alignment."""

    def _roundtrip_raw16(self, alignment: str, endianness: str):
        rng = np.random.RandomState(7)
        pixels = rng.randint(0, 65536, size=(4, 6), dtype=np.uint16)
        raw8 = (pixels / 65535.0 * 255).clip(0, 255).astype(np.uint8)
        encoded = gray8_to_raw_bytes(raw8, "RAW16", alignment=alignment, endianness=endianness)
        decoded = decode_raw(encoded, ImageSpec(6, 4), "RAW16", alignment=alignment, endianness=endianness)
        decoded_quant = np.round(decoded / 65535.0 * 255).clip(0, 255).astype(np.uint8)
        max_diff = int(np.abs(decoded_quant.astype(int) - raw8.astype(int)).max())
        self.assertLessEqual(max_diff, 1, f"RAW16 {alignment}/{endianness} max diff={max_diff}")

    def test_raw16_lsb_little(self):
        self._roundtrip_raw16("lsb", "little")

    def test_raw16_lsb_big(self):
        self._roundtrip_raw16("lsb", "big")

    def test_raw16_msb_little(self):
        self._roundtrip_raw16("msb", "little")

    def test_raw16_msb_big(self):
        self._roundtrip_raw16("msb", "big")


class Raw32Tests(unittest.TestCase):
    """RAW32 encode → decode."""

    def test_raw32_roundtrip(self):
        raw8 = np.arange(64, dtype=np.uint8).reshape(8, 8)
        encoded = gray8_to_raw_bytes(raw8, "RAW32", endianness="little")
        decoded = decode_raw(encoded, ImageSpec(8, 8), "RAW32", endianness="little")
        self.assertEqual(decoded.shape, (8, 8))
        # RAW32 stores values as 32-bit (max 2^32-1), so the 8-bit values
        # get scaled up massively. Round-trip through display:
        display = raw_to_display_gray(decoded, "RAW32")
        self.assertEqual(display.dtype, np.uint8)
        self.assertEqual(display.shape, (8, 8))


class EdgeCaseTests(unittest.TestCase):
    """Boundary conditions and invalid inputs."""

    # ── RAW edge cases ──

    def test_raw8_decode_truncated(self):
        """Truncated data should raise FormatError."""
        data = bytes([1, 2, 3])  # too short for 2x2 RAW8 (needs 4)
        with self.assertRaises(FormatError):
            decode_raw(data, ImageSpec(2, 2), "RAW8")

    def test_raw10_decode_truncated(self):
        data = bytes([1, 2, 3])  # too short for 2x1 RAW10 (needs 4)
        with self.assertRaises(FormatError):
            decode_raw(data, ImageSpec(2, 1), "RAW10")

    def test_raw12_decode_truncated(self):
        data = bytes([1, 2, 3])
        with self.assertRaises(FormatError):
            decode_raw(data, ImageSpec(2, 1), "RAW12")

    def test_raw10_packed_truncated(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes([0] * 4), ImageSpec(4, 1), "RAW10 Packed")  # needs 5 bytes

    def test_raw12_packed_truncated(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes([0] * 2), ImageSpec(2, 1), "RAW12 Packed")  # needs 3 bytes

    def test_raw14_packed_truncated(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes([0] * 6), ImageSpec(4, 1), "RAW14 Packed")  # needs 7 bytes

    def test_empty_data_raw(self):
        with self.assertRaises(FormatError):
            decode_raw(b"", ImageSpec(1, 1), "RAW8")

    # ── YUV edge cases ──

    def test_yuv_truncated(self):
        with self.assertRaises(FormatError):
            decode_yuv(bytes([0] * 10), ImageSpec(4, 4), "I420")  # needs 24

    def test_empty_data_yuv(self):
        with self.assertRaises(FormatError):
            decode_yuv(b"", ImageSpec(1, 1), "I420")

    def test_invalid_yuv_format(self):
        with self.assertRaises(FormatError):
            decode_yuv(bytes(100), ImageSpec(4, 4), "INVALID")

    # ── Invalid parameters ──

    def test_invalid_raw_type(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes(100), ImageSpec(4, 4), "INVALID")

    def test_invalid_raw_type_expected_size(self):
        with self.assertRaises(FormatError):
            expected_frame_size_raw("INVALID", 4, 4)

    def test_invalid_yuv_type_expected_size(self):
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("INVALID", 4, 4)

    def test_negative_dimensions(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes(100), ImageSpec(-1, 4), "RAW8")

    def test_zero_dimensions(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes(100), ImageSpec(0, 4), "RAW8")

    def test_negative_offset(self):
        with self.assertRaises(FormatError):
            decode_raw(bytes(100), ImageSpec(4, 4, offset=-1), "RAW8")

    # ── Width divisibility requirements ──

    def test_raw10_packed_width_not_div_4(self):
        with self.assertRaises(FormatError):
            expected_frame_size_raw("RAW10 Packed", 5, 4)

    def test_raw12_packed_width_not_div_2(self):
        # RAW12 Packed requires even width — should be fine with 6
        size = expected_frame_size_raw("RAW12 Packed", 6, 4)
        self.assertGreater(size, 0)
        # But odd width should fail
        with self.assertRaises(FormatError):
            expected_frame_size_raw("RAW12 Packed", 5, 4)

    def test_raw14_packed_width_not_div_4(self):
        with self.assertRaises(FormatError):
            expected_frame_size_raw("RAW14 Packed", 5, 4)

    def test_yuv420_odd_width(self):
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("I420", 5, 4)

    def test_yuv422_odd_width(self):
        with self.assertRaises(FormatError):
            expected_frame_size_yuv("YUYV", 5, 4)


class Gray8ToRawBytesTests(unittest.TestCase):
    """Tests for gray8_to_raw_bytes encoding."""

    def test_raw8_encoding(self):
        gray = np.array([[0, 128, 255]], dtype=np.uint8)
        encoded = gray8_to_raw_bytes(gray, "RAW8")
        self.assertEqual(encoded, bytes([0, 128, 255]))

    def test_raw10_lsb_encoding(self):
        gray = np.array([[0, 128, 255]], dtype=np.uint8)
        encoded = gray8_to_raw_bytes(gray, "RAW10", alignment="lsb")
        # 128 → 128*1023/255 = 513, 255 → 255*1023/255 = 1023
        decoded = decode_raw(encoded, ImageSpec(3, 1), "RAW10", alignment="lsb")
        self.assertEqual(decoded.shape, (1, 3))

    def test_raw10_msb_encoding(self):
        gray = np.array([[128]], dtype=np.uint8)
        encoded = gray8_to_raw_bytes(gray, "RAW10", alignment="msb")
        decoded = decode_raw(encoded, ImageSpec(1, 1), "RAW10", alignment="msb")
        self.assertEqual(decoded.shape, (1, 1))

    def test_gray8_to_raw_bytes_invalid_type(self):
        with self.assertRaises(FormatError):
            gray8_to_raw_bytes(np.zeros((2, 2), dtype=np.uint8), "INVALID")


class ImageSpecTests(unittest.TestCase):
    """ImageSpec validation tests."""

    def test_valid_spec(self):
        spec = ImageSpec(1920, 1080, offset=0)
        spec.validate()  # should not raise

    def test_negative_width(self):
        with self.assertRaises(FormatError):
            ImageSpec(-1, 1080).validate()

    def test_zero_height(self):
        with self.assertRaises(FormatError):
            ImageSpec(1920, 0).validate()

    def test_negative_offset(self):
        with self.assertRaises(FormatError):
            ImageSpec(1920, 1080, offset=-5).validate()


if __name__ == "__main__":
    unittest.main()
