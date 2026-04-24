import unittest

from raw_view.gui import build_default_output_path, dpi_to_dots_per_meter


class GuiHelperTests(unittest.TestCase):
    def test_build_default_output_path_raw(self):
        path = build_default_output_path("/tmp/input/sample.png", "RAW", "out")
        self.assertEqual(path, "/tmp/input/out/sample.raw")

    def test_build_default_output_path_yuv(self):
        path = build_default_output_path("/tmp/input/sample.jpg", "YUV", "output")
        self.assertEqual(path, "/tmp/input/output/sample.yuv")

    def test_dpi_to_dots_per_meter(self):
        self.assertEqual(dpi_to_dots_per_meter(254), 10000)


if __name__ == "__main__":
    unittest.main()
