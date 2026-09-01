import os
import subprocess
import sys
import unittest
import unittest.mock as mock
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


class CLIFixTests(unittest.TestCase):
    """Regression tests for CLI robustness fixes.

    Covers the ``_make_utf8_stdio`` codepage fix — the CLI must not crash with
    UnicodeEncodeError when stdout is bound to a narrow single-byte codepage
    (cp1252 / GBK), as happened on GitHub-Actions Windows runners.
    """

    def test_batch_help_prints_on_cp1252_stdout(self):
        """``main()`` must survive a cp1252 (strict) stdout and print the help.

        Before the fix, ``--batch-help`` raised ``UnicodeEncodeError`` because
        the help text contains box-drawing arrows not representable in cp1252.

        Note: pytest replaces ``sys.stdin`` with a ``DontReadFromInput`` object
        that has no ``reconfigure`` method, so this test must swap in narrow
        stand-ins for stdin/stdout/stderr (and restore them after) instead of
        only patching stdout/stderr.
        """
        from raw_view.__main__ import _make_utf8_stdio

        class _Narrow:
            """A stand-in stream bound to a narrow single-byte codepage."""

            encoding = "cp1252"

            def reconfigure(self, **kw):
                self.encoding = kw.get("encoding", "utf-8")

            def write(self, s):
                # A utf-8 stream can encode anything; a cp1252 one could not.
                s.encode(self.encoding, "strict")

            def flush(self):
                pass

        # 0.2.1-L-1：宿主环境若注入 PYTHONIOENCODING=utf-8，_make_utf8_stdio 会
        # 按产品契约直接返回（“已显式配置 UTF-8 → 不重配”），使本测试的 stand-in
        # 转换断言失去前提。这里显式清空该变量，保证测试不随宿主环境漂移。
        with mock.patch.dict(os.environ, {"PYTHONIOENCODING": ""}, clear=False):
            old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
            narrow_in, narrow_out, narrow_err = _Narrow(), _Narrow(), _Narrow()
            sys.stdin, sys.stdout, sys.stderr = narrow_in, narrow_out, narrow_err
            try:
                _make_utf8_stdio()  # must redirect stdin/stdout/stderr to UTF-8
                self.assertEqual(narrow_in.encoding, "utf-8")
                self.assertEqual(narrow_out.encoding, "utf-8")
                self.assertEqual(narrow_err.encoding, "utf-8")
                # The Unicode arrows that crashed CI now encode cleanly.
                narrow_out.write("─◀ → OK\n")
                narrow_out.write("format → {input_stem}\n")
                narrow_out.flush()
            finally:
                sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err

    def test_command_does_not_crash_as_module_subprocess(self):
        """Full end-to-end: ``python -m raw_view --batch-help`` exits 0."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run(
            [sys.executable, "-m", "raw_view", "--batch-help"],
            cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertIn("Batch JSON format", proc.stdout.decode("utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
