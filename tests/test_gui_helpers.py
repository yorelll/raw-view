import unittest

from raw_view.gui import (
    add_recent_file_entry,
    build_default_output_path,
    dpi_to_dots_per_meter,
    normalize_recent_files,
    normalize_ui_theme,
)


class GuiHelperTests(unittest.TestCase):
    def test_build_default_output_path_raw(self):
        path = build_default_output_path("/tmp/input/sample.png", "RAW", "out")
        self.assertEqual(path, "/tmp/input/out/sample.raw")

    def test_build_default_output_path_yuv(self):
        path = build_default_output_path("/tmp/input/sample.jpg", "YUV", "output")
        self.assertEqual(path, "/tmp/input/output/sample.yuv")

    def test_dpi_to_dots_per_meter(self):
        self.assertEqual(dpi_to_dots_per_meter(254), 10000)

    def test_dpi_to_dots_per_meter_bounds(self):
        self.assertEqual(dpi_to_dots_per_meter(0), 39)
        self.assertEqual(dpi_to_dots_per_meter(-100), 39)
        self.assertEqual(dpi_to_dots_per_meter(2400), 94488)

    def test_build_default_output_path_edge_cases(self):
        self.assertEqual(build_default_output_path("", "RAW", "out"), "")
        self.assertEqual(
            build_default_output_path("/tmp/input/sample", "RAW", ""),
            "/tmp/input/out/sample.raw",
        )

    def test_normalize_recent_files(self):
        self.assertEqual(
            normalize_recent_files([" /a.raw ", "/b.raw", "/a.raw", ""], max_items=3),
            ["/a.raw", "/b.raw"],
        )

    def test_normalize_recent_files_string(self):
        self.assertEqual(normalize_recent_files(" /a.raw "), ["/a.raw"])

    def test_add_recent_file_entry(self):
        existing = ["/a.raw", "/b.raw", "/c.raw"]
        self.assertEqual(
            add_recent_file_entry(existing, "/b.raw", max_items=3),
            ["/b.raw", "/a.raw", "/c.raw"],
        )

    def test_add_recent_file_entry_empty(self):
        self.assertEqual(add_recent_file_entry(["/a.raw"], "  "), ["/a.raw"])

    def test_normalize_ui_theme(self):
        self.assertEqual(normalize_ui_theme("dark"), "dark")
        self.assertEqual(normalize_ui_theme(" Light "), "light")
        self.assertEqual(normalize_ui_theme("unknown"), "light")
        self.assertEqual(normalize_ui_theme(None), "light")


if __name__ == "__main__":
    unittest.main()
