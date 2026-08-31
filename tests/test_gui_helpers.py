import os
import unittest
from pathlib import PurePosixPath

from raw_view.models import (
    THEME_PALETTES,
    add_recent_file_entry,
    build_ui_stylesheet,
    build_default_output_path,
    dpi_to_dots_per_meter,
    normalize_recent_files,
    normalize_ui_theme,
)


def _assert_same_posix_path(test_case, actual_path, expected_posix: str):
    """Compare a platform-native path against a POSIX-style expectation.

    On Windows ``Path('/tmp/input/sample.png')`` normalises to a drive-less
    ``\\tmp\\input\\...``, so comparing raw strings against a POSIX literal
    fails. We normalise both sides with ``os.path.normpath`` (which maps the
    native separator) so the comparison is structure-based and cross-platform,
    without hard-coding forward slashes in the assertions.
    """
    actual_norm = os.path.normpath(os.fspath(actual_path))
    expected_norm = os.path.normpath(expected_posix.replace("/", os.sep))
    test_case.assertEqual(actual_norm, expected_norm)


# Keys in THEME_PALETTES whose values should appear in the stylesheet
_STYLESHEET_PALETTE_KEYS = {
    "main_bg", "text_color", "panel_bg", "border_color",
    "input_bg", "button_bg", "button_hover_bg", "button_text_color",
    "accent", "accent_light", "text_secondary",
}


class GuiHelperTests(unittest.TestCase):
    def test_build_default_output_path_raw(self):
        path = build_default_output_path("/tmp/input/sample.png", "RAW", "out")
        _assert_same_posix_path(self, path, "/tmp/input/out/sample.raw")

    def test_build_default_output_path_yuv(self):
        path = build_default_output_path("/tmp/input/sample.jpg", "YUV", "output")
        _assert_same_posix_path(self, path, "/tmp/input/output/sample.yuv")

    def test_dpi_to_dots_per_meter(self):
        self.assertEqual(dpi_to_dots_per_meter(254), 10000)

    def test_dpi_to_dots_per_meter_bounds(self):
        self.assertEqual(dpi_to_dots_per_meter(0), 39)
        self.assertEqual(dpi_to_dots_per_meter(-100), 39)
        self.assertEqual(dpi_to_dots_per_meter(2400), 94488)

    def test_build_default_output_path_edge_cases(self):
        self.assertEqual(build_default_output_path("", "RAW", "out"), "")
        _assert_same_posix_path(
            self,
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
        self.assertEqual(normalize_ui_theme(""), "light")
        self.assertEqual(normalize_ui_theme("unknown"), "light")
        self.assertEqual(normalize_ui_theme(None), "light")

    def test_build_ui_stylesheet_light(self):
        stylesheet = build_ui_stylesheet("light", 13)
        self.assertIn("font-size: 13px;", stylesheet)
        for key in _STYLESHEET_PALETTE_KEYS:
            self.assertIn(THEME_PALETTES["light"][key], stylesheet)

    def test_build_ui_stylesheet_dark(self):
        stylesheet = build_ui_stylesheet("dark", 15)
        self.assertIn("font-size: 15px;", stylesheet)
        for key in _STYLESHEET_PALETTE_KEYS:
            self.assertIn(THEME_PALETTES["dark"][key], stylesheet)


if __name__ == "__main__":
    unittest.main()
