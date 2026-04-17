import unittest

import numpy as np

from raw_view.formats import (
    ImageSpec,
    decode_raw,
    decode_yuv,
    rgb_to_yuv_bytes,
)


class RawPackedTests(unittest.TestCase):
    def test_raw10_packed_decode(self):
        p0, p1, p2, p3 = 0x001, 0x155, 0x2AA, 0x3FF
        b0 = p0 & 0xFF
        b1 = p1 & 0xFF
        b2 = p2 & 0xFF
        b3 = p3 & 0xFF
        b4 = ((p0 >> 8) & 0x03) | (((p1 >> 8) & 0x03) << 2) | (((p2 >> 8) & 0x03) << 4) | (((p3 >> 8) & 0x03) << 6)
        data = bytes([b0, b1, b2, b3, b4])
        out = decode_raw(data, ImageSpec(4, 1), "RAW10 Packed", alignment="lsb")
        self.assertEqual(out.tolist(), [[p0, p1, p2, p3]])

    def test_raw12_packed_manual_example(self):
        p0, p1 = 0x123, 0xABC
        b0 = p0 & 0xFF
        b1 = ((p0 >> 8) & 0x0F) | ((p1 & 0x0F) << 4)
        b2 = (p1 >> 4) & 0xFF
        out = decode_raw(bytes([b0, b1, b2]), ImageSpec(2, 1), "RAW12 Packed", alignment="lsb")
        self.assertEqual(int(out[0, 0]), p0)
        self.assertEqual(int(out[0, 1]), p1)

    def test_raw14_packed_manual_example(self):
        p0, p1, p2, p3 = 0x0001, 0x1234, 0x2AAA, 0x3FFF
        b0 = p0 & 0xFF
        b1 = p1 & 0xFF
        b2 = p2 & 0xFF
        b3 = p3 & 0xFF
        b4 = ((p0 >> 8) & 0x3F) | (((p1 >> 8) & 0x03) << 6)
        b5 = (((p1 >> 10) & 0x0F) << 4) | ((p2 >> 10) & 0x0F)
        b6 = (((p2 >> 8) & 0x03) << 6) | ((p3 >> 8) & 0x3F)
        out = decode_raw(bytes([b0, b1, b2, b3, b4, b5, b6]), ImageSpec(4, 1), "RAW14 Packed", alignment="lsb")
        self.assertEqual(out.tolist(), [[p0, p1, p2, p3]])


class AlignTests(unittest.TestCase):
    def test_raw10_aligned_16bit(self):
        raw10 = np.array([1, 0x155], dtype=np.uint16)
        lsb = raw10.astype("<u2").tobytes()
        msb = (raw10 << 6).astype("<u2").tobytes()
        out_lsb = decode_raw(lsb, ImageSpec(2, 1), "RAW10", alignment="lsb")
        out_msb = decode_raw(msb, ImageSpec(2, 1), "RAW10", alignment="msb")
        self.assertEqual(out_lsb.tolist(), [[1, 0x155]])
        self.assertEqual(out_msb.tolist(), [[1, 0x155]])


class YuvTests(unittest.TestCase):
    def test_yuv420_i420_decode_shape(self):
        rgb = np.array(
            [
                [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]],
                [[10, 20, 30], [40, 50, 60], [70, 80, 90], [100, 110, 120]],
            ],
            dtype=np.uint8,
        )
        yuv = rgb_to_yuv_bytes(rgb, "I420")
        out = decode_yuv(yuv, ImageSpec(4, 2), "I420")
        self.assertEqual(out.shape, (2, 4, 3))


if __name__ == "__main__":
    unittest.main()
