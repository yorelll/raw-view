import unittest

import numpy as np

from raw_view.converter import bayer8_to_rgb, bgr_to_bayer8


class BayerConversionTests(unittest.TestCase):
    def test_bgr_to_bayer8_rggb_layout(self):
        bgr = np.array(
            [
                [[10, 20, 30], [11, 21, 31]],
                [[12, 22, 32], [13, 23, 33]],
            ],
            dtype=np.uint8,
        )
        out = bgr_to_bayer8(bgr, 2, 2, pattern="RGGB")
        self.assertEqual(out.tolist(), [[30, 21], [22, 13]])

    def test_bayer8_to_rgb_returns_color_image(self):
        bayer = np.array(
            [
                [255, 0, 255, 0],
                [0, 255, 0, 255],
                [255, 0, 255, 0],
                [0, 255, 0, 255],
            ],
            dtype=np.uint8,
        )
        rgb = bayer8_to_rgb(bayer, pattern="RGGB")
        self.assertEqual(rgb.shape, (4, 4, 3))
        self.assertEqual(rgb.dtype, np.uint8)
        self.assertFalse(np.array_equal(rgb[:, :, 0], rgb[:, :, 1]) and np.array_equal(rgb[:, :, 1], rgb[:, :, 2]))


if __name__ == "__main__":
    unittest.main()
