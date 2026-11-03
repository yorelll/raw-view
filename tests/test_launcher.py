"""Launcher routing tests (``raw_view/launcher.py``).

Two layers, matching the module's own split:

* :func:`decide_target` / :func:`gui_files` are pure functions over an argv
  list, so the routing table is pinned exhaustively here -- no packaging needed.
* :func:`run` is exercised against a *source* checkout, where the CLI child is
  ``python -m raw_view``.  That keeps the forwarding path (exit codes, argv
  passthrough, no argument rewriting) under test without building a bundle.

The Windows console-handle dance (``AttachConsole`` / handle wrapping) is only
meaningful in a packaged build; it is verified by the end-to-end packaging
checklist, not here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _settings_isolation import (
    ensure_test_settings_file,
    isolated_qt_material_resources,
)

ensure_test_settings_file()
isolated_qt_material_resources()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

from raw_view import launcher  # noqa: E402


class DecideTargetTests(unittest.TestCase):
    """The routing table from the module docstring, one case per row."""

    def test_no_arguments_is_gui(self):
        self.assertEqual(launcher.decide_target([]), "gui")

    def test_bare_view_is_gui(self):
        self.assertEqual(launcher.decide_target(["view"]), "gui")

    def test_view_with_files_is_gui(self):
        self.assertEqual(
            launcher.decide_target(["view", "a.raw", "b.raw"]), "gui"
        )

    def test_view_with_input_option_is_cli(self):
        self.assertEqual(
            launcher.decide_target(["view", "-i", "a.raw"]), "cli"
        )

    def test_view_with_any_cli_option_is_cli(self):
        for option in sorted(launcher._CLI_OPTIONS):
            with self.subTest(option=option):
                self.assertEqual(
                    launcher.decide_target(["view", option, "x"]), "cli"
                )

    def test_view_with_equals_form_is_cli(self):
        self.assertEqual(launcher.decide_target(["view", "--width=800"]), "cli")

    def test_view_with_unknown_option_is_cli_not_gui(self):
        """A typo'd flag must never launch a blocking window."""
        self.assertEqual(launcher.decide_target(["view", "--widht", "800"]), "cli")
        self.assertEqual(launcher.decide_target(["--width", "800"]), "cli")

    def test_convert_is_cli(self):
        self.assertEqual(
            launcher.decide_target(["convert", "-i", "a.png"]), "cli"
        )

    def test_batch_is_cli(self):
        self.assertEqual(launcher.decide_target(["batch", "-b", "s.json"]), "cli")

    def test_print_and_exit_flags_are_cli(self):
        for flag in ("-h", "--help", "--version", "--batch-help"):
            with self.subTest(flag=flag):
                self.assertEqual(launcher.decide_target([flag]), "cli")

    def test_print_flags_win_over_a_mode(self):
        self.assertEqual(launcher.decide_target(["view", "--help"]), "cli")

    def test_bare_filename_is_cli(self):
        """Product decision: no implicit `view <file>` rewriting."""
        self.assertEqual(launcher.decide_target(["a.raw"]), "cli")
        self.assertEqual(launcher.decide_target(["a.raw", "b.raw"]), "cli")

    def test_every_cli_option_alone_routes_to_cli(self):
        for option in sorted(launcher._CLI_OPTIONS):
            with self.subTest(option=option):
                self.assertEqual(launcher.decide_target([option]), "cli")

    def test_decide_target_is_total(self):
        """Every input maps to one of the two targets (never None/exception)."""
        samples = [
            [], ["view"], ["convert"], ["batch"], ["-h"], ["--version"],
            ["weird"], ["-x"], ["view", "f"], ["view", "-i", "f"],
            ["--input-type", "yuv"], ["--input-yuv-type", "NV16"],
            ["view", "--offset", "4"], ["view", "--input-alignment=lsb"],
        ]
        for argv in samples:
            with self.subTest(argv=argv):
                self.assertIn(launcher.decide_target(argv), {"gui", "cli"})


class GuiFilesTests(unittest.TestCase):
    """The GUI receives file paths only -- the ``view`` sub-command is dropped."""

    def test_strips_leading_view(self):
        self.assertEqual(launcher.gui_files(["view", "a.raw"]), ["a.raw"])

    def test_keeps_files_without_mode(self):
        self.assertEqual(launcher.gui_files(["a.raw", "b.png"]), ["a.raw", "b.png"])

    def test_empty(self):
        self.assertEqual(launcher.gui_files([]), [])

    def test_only_strips_the_first_view(self):
        self.assertEqual(
            launcher.gui_files(["view", "view"]), ["view"]
        )


class ForwardingTests(unittest.TestCase):
    """``run()`` in a source checkout forwards to ``python -m raw_view``."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-launcher-")

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_version_is_forwarded_and_exit_zero(self):
        rc = launcher.run(["--version"])
        self.assertEqual(rc, 0)

    def test_bare_filename_reaches_argparse_and_exits_2(self):
        """No rewriting: argparse sees `a.raw` and reports usage."""
        rc = launcher.run([os.path.join(self._tmp, "a.raw")])
        self.assertEqual(rc, 2, "裸文件名必须由 argparse 报 usage（rc=2）")

    def test_unknown_option_exits_2(self):
        self.assertEqual(launcher.run(["--width", "800"]), 2)

    def test_cli_command_uses_python_module_in_source_checkout(self):
        command = launcher._cli_command(["--version"])
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[1:3], ["-m", "raw_view.cli_entry"])
        self.assertEqual(command[3:], ["--version"])

    def test_cli_command_uses_sibling_exe_when_frozen(self):
        original = getattr(sys, "frozen", None)
        sys.frozen = True  # type: ignore[attr-defined]
        try:
            command = launcher._cli_command(["--version"])
        finally:
            if original is None:
                del sys.frozen  # type: ignore[attr-defined]
            else:
                sys.frozen = original  # type: ignore[attr-defined]
        self.assertTrue(command[0].endswith(launcher.CLI_EXE))
        self.assertEqual(command[1:], ["--version"])

    def test_convert_is_forwarded_to_cli(self):
        """A convert invocation really runs (exit 1 for a missing input, not 0)."""
        rc = launcher.run([
            "convert", "-i", os.path.join(self._tmp, "missing.png"),
            "-o", os.path.join(self._tmp, "o.raw"),
        ])
        self.assertEqual(rc, 1, "缺失输入应走 CLI 并 exit 1")


class ConsoleHandleTests(unittest.TestCase):
    """The wrap-then-attach ordering is documented and guarded in one place."""

    def test_wrap_returns_none_without_a_console(self):
        """Under pytest there is no Windows console handle to wrap."""
        if sys.platform != "win32":
            self.skipTest("Windows-only API")
        # 0 / -1 are the documented "no console" values; the helper must return
        # None rather than raising or producing a broken stream.
        from unittest import mock

        with mock.patch.object(
            launcher, "_kernel32"
        ) as kernel:
            kernel.return_value.GetStdHandle.return_value = 0
            self.assertIsNone(launcher._wrap_std_handle(-11))

    def test_forward_wraps_before_attaching(self):
        """Ordering regression guard: attach must not pre-empt the inherited handles."""
        from unittest import mock

        calls: list[str] = []

        def fake_wrap(index):
            calls.append(f"wrap{index}")
            # Pretend a redirected stdout exists, so attach is never attempted.
            return object() if index == -11 else None

        with mock.patch.object(launcher, "_wrap_std_handle", side_effect=fake_wrap), \
             mock.patch.object(launcher, "_kernel32") as kernel, \
             mock.patch.object(launcher.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 7
            if sys.platform != "win32":
                self.skipTest("Windows-only path")
            rc = launcher._forward(["--version"])
        self.assertEqual(rc, 7)
        self.assertEqual(calls[:3], ["wrap-10", "wrap-11", "wrap-12"])
        kernel.return_value.AttachConsole.assert_not_called()
        # argv must reach the child unmodified.
        self.assertEqual(run_mock.call_args[0][0][-1], "--version")


if __name__ == "__main__":
    unittest.main()
