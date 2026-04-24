import unittest
from unittest import mock

import numpy as np

from raw_view.converter import bayer8_to_rgb, bgr_to_bayer8, image_file_to_raw


class BayerConversionTests(unittest.TestCase):
    def test_bgr_to_bayer8_rggb_layout(self):
        bgr = np.array(
            [
                [[10, 20, 30], [11, 21, 31]],
                [[12, 22, 32], [13, 23, 33]],
            ],
            dtype=np.uint8,
        )
        expected = {
            "RGGB": [[30, 21], [22, 13]],
            "BGGR": [[10, 21], [22, 33]],
            "GRBG": [[20, 31], [12, 23]],
            "GBRG": [[20, 11], [32, 23]],
        }
        for pattern, exp in expected.items():
            with self.subTest(pattern=pattern):
                out = bgr_to_bayer8(bgr, 2, 2, pattern=pattern)
                self.assertEqual(out.tolist(), exp)

    def test_bayer8_to_rgb_reconstructs_constant_color(self):
        bgr = np.zeros((4, 4, 3), dtype=np.uint8)
        bgr[:, :, 0] = 50
        bgr[:, :, 1] = 100
        bgr[:, :, 2] = 200
        bayer = bgr_to_bayer8(bgr, 4, 4, pattern="RGGB")
        rgb = bayer8_to_rgb(bayer, pattern="RGGB")
        self.assertEqual(rgb.shape, (4, 4, 3))
        self.assertEqual(rgb.dtype, np.uint8)
        self.assertTrue(np.all(rgb[:, :, 0] == 200))
        self.assertTrue(np.all(rgb[:, :, 1] == 100))
        self.assertTrue(np.all(rgb[:, :, 2] == 50))

    def test_bayer8_to_rgb_invalid_inputs_raise(self):
        with self.assertRaises(ValueError):
            bayer8_to_rgb(np.zeros((2, 2, 1), dtype=np.uint8), pattern="RGGB")
        with self.assertRaises(ValueError):
            bayer8_to_rgb(np.zeros((2, 2), dtype=np.uint8), pattern="INVALID")
        with self.assertRaises(ValueError):
            bayer8_to_rgb(np.zeros((0, 0), dtype=np.uint8), pattern="RGGB")

    def test_image_file_to_raw_invalid_source_mode_raises(self):
        with mock.patch("raw_view.converter.load_bgr_image", return_value=np.zeros((2, 2, 3), dtype=np.uint8)):
            with self.assertRaises(ValueError):
                image_file_to_raw("in.png", "out.raw", "RAW8", 2, 2, source_mode="invalid")


if __name__ == "__main__":
    unittest.main()
