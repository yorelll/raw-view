"""CLI help system: two-level help, copy-pasteable batch JSON, and the fix for
"a RAW/YUV binary handed to convert's default image input".

Three groups, all end-to-end through ``python -m raw_view`` so they prove what a
user actually sees:

* the level-1 page is a short menu (modes + core options + pointers) instead of
  the flat 23-option table, and every mode page is reachable from it;
* each of the three mode pages prints and contains complete, runnable examples
  (the "generalise from one example" requirement);
* ``--batch-help`` emits three examples that ``json.loads`` accepts verbatim;
* a YUV binary with ``--input-type yuv`` converts to PNG (*the reported bug*),
  and a failed image read explains the RAW/YUV case with runnable commands.

Run: ``python -m pytest tests/test_cli_help.py -q``
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from pathlib import Path

from _settings_isolation import isolated_env

_MAIN_DIR = Path(__file__).resolve().parents[1]


def _unwrapped(text: str) -> str:
    """Join backslash-continued command lines into one line.

    Help wraps long commands with ``\\`` (review L-1: 175-column lines folded
    badly in an 80-column terminal), so assertions about a whole command must
    compare against the joined text rather than one physical line.  Runs of
    whitespace collapse to one space, which is what a reader sees.
    """
    import re

    joined = text.replace("\\\r\n", " ").replace("\\\n", " ")
    return re.sub(r"[ ]{2,}", " ", joined)


#: A help-page table row: two spaces, an option token (``-x``/``--long``), two
#: or more spaces of padding, then the description.  A *continuation* line
#: (indented, no leading ``-``) is deliberately not a row -- ``-i`` has one and
#: it carries no scope column.
_HELP_ROW = re.compile(r"^(  -\S.*?)( {2,})(\S.*)$")

#: A trailing parenthesised annotation on an option row: the ``(view, convert)``
#: scope column, ``(default RAW12)``, ``(convert only)``.  **One** space is
#: accepted, deliberately: the defect being guarded against is a row whose
#: annotation was pushed right by a too-long description, and that row then had
#: a single space in front of it.  Requiring 2+ spaces would make the detector
#: blind to exactly the shape it exists to catch.
_HELP_TAIL = re.compile(r"\s+(\([^()]*\))$")


def _scope_columns(text: str) -> dict[tuple[str, str], list[tuple[int, int]]]:
    """Map ``(section, annotation)`` -> ``[(line number, 1-based column), ...]``.

    Rows are grouped by the section header above them, so different blocks are
    free to line up at different columns (they do: ``CORE OPTIONS`` sits further
    right than ``MODE PAGES``, because its option column is wider).  Within one
    block, the same annotation must start at the same column -- otherwise the
    table reads as if one row were indented differently.

    Why this exists (review of 0.5.8): the ``-o, --output`` row of the main page
    put its ``(view, convert)`` 3 columns right of the other four.  The line
    was only 73 characters wide, so the 100-column guard passed; width is not
    alignment.  Annotations that appear once in a block constrain nothing, so
    only groups of 2+ rows are compared -- see
    ``test_annotation_used_once_is_not_a_constraint``.
    """
    groups: dict[tuple[str, str], list[tuple[int, int]]] = {}
    section = "<top>"
    for number, line in enumerate(text.splitlines(), start=1):
        if line and not line.startswith(" "):
            section = line.strip()
            continue
        if not _HELP_ROW.match(line):
            continue
        tail = _HELP_TAIL.search(line)
        if tail is None:
            continue
        groups.setdefault((section, tail.group(1)), []).append(
            (number, line.index(tail.group(1)) + 1)
        )
    return groups


def _misaligned_scope_columns(text: str) -> list[str]:
    """Human-readable description of every misaligned annotation in *text*."""
    problems = []
    for (section, annotation), positions in _scope_columns(text).items():
        if len(positions) < 2:
            continue
        columns = sorted({column for _, column in positions})
        if len(columns) > 1:
            problems.append(
                f"section {section!r}: {annotation} starts at columns {columns} "
                f"(lines {[number for number, _ in positions]})"
            )
    return problems


def _run_cli(*argv: str, timeout: int = 120,
             cwd: str | None = None,
             env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess and decode its output as UTF-8.

    Bytes (not ``text=True``): the help pages are UTF-8 by contract, but the
    surrounding Windows console may be cp936/cp1252, and ``text=True`` decodes
    with the locale codec -- which is exactly the failure mode those pages guard
    against.

    *cwd* defaults to the repository root (``python -m raw_view`` resolves there);
    pass a temp directory when the command would otherwise create files.  When a
    different cwd is used the repository is added to ``PYTHONPATH`` so the module
    is still importable.  *env_extra* adds variables on top of the isolated
    environment (used to exercise ``RAW_VIEW_LOG_LEVEL``).
    """
    env = isolated_env()
    if env_extra:
        env.update(env_extra)
    if cwd is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(_MAIN_DIR), env.get("PYTHONPATH", "")])
        )
    proc = subprocess.run(
        [sys.executable, "-m", "raw_view", *argv],
        capture_output=True, env=env, cwd=cwd or str(_MAIN_DIR),
        timeout=timeout,
    )
    return subprocess.CompletedProcess(
        proc.args, proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


class LevelOneHelpTests(unittest.TestCase):
    """``-h`` / ``--help`` is a menu, not the flat option table."""

    @classmethod
    def setUpClass(cls):
        cls.proc = _run_cli("--help")
        cls.text = cls.proc.stdout

    def test_exits_zero(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)

    def test_lists_all_modes(self):
        for mode in ("view", "convert", "batch"):
            with self.subTest(mode=mode):
                self.assertIn(mode, self.text)

    def test_points_at_every_mode_page(self):
        for flag in ("--help-view", "--help-convert", "--help-batch"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.text)

    def test_lists_the_core_options_only(self):
        """Core flags are shown; mode-specific ones are not dumped in level 1."""
        for flag in ("-i, --input", "-o, --output", "--width", "--height", "--offset"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.text)
        # These belong to exactly one mode; listing them here is what made the
        # old help unreadable. argparse's generated sections must not contain
        # them either (they are SUPPRESSed).
        for flag in ("--input-type", "--preview-mode", "--batch-file",
                     "--bayer-pattern", "--source-mode"):
            with self.subTest(flag=flag):
                self.assertNotIn(f"  {flag} ", self.text)

    def test_short_and_long_help_agree(self):
        self.assertEqual(_run_cli("-h").stdout, self.text)

    def test_mode_word_selects_that_modes_page(self):
        """`convert --help` prints the *convert* page, not the menu (M-5).

        Asking for a mode's help and being shown the overview makes the user type
        a second command to reach what they already asked for; the bare -h keeps
        the menu.
        """
        expected = {
            "view": "raw-view view -- decode a RAW/YUV binary",
            "convert": "raw-view convert -- encode, transcode, or decode",
            "batch": "raw-view batch -- run many view/convert jobs",
        }
        for mode, first_line in expected.items():
            with self.subTest(mode=mode):
                proc = _run_cli(mode, "--help")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.startswith(first_line), proc.stdout[:80])
                self.assertNotEqual(proc.stdout, self.text)

    def test_mode_word_selects_the_page_for_h_too(self):
        for mode in ("view", "convert", "batch"):
            with self.subTest(mode=mode):
                self.assertEqual(_run_cli(mode, "-h").stdout,
                                 _run_cli(mode, "--help").stdout)

    def test_usage_mentions_the_help_entries(self):
        self.assertIn("--help", self.text.split("MODES")[0])
        self.assertIn("mode word is REQUIRED", self.text)


class OptionCoverageTests(unittest.TestCase):
    """Every option the parser accepts must appear in some help page.

    This is the structural guard the review asked for (H-3 / M-2): it is what
    makes the main page's "the remaining options belong to one mode each"
    verifiable instead of aspirational, and it fails the moment somebody adds an
    option without documenting it.  Mutation check: adding an undocumented
    ``--mutation-probe`` used to leave all 55 tests green; it now fails here.
    """

    #: Aliases that are deliberately documented through their canonical spelling.
    #: Mapping is alias -> the spelling that must appear.
    _ALIAS_OF = {
        "-h": "--help",
        "-i": "--input",
        "-o": "--output",
        "-t": "--target",
        "-b": "--batch-file",
        "--batch-help": "--help-batch",
    }

    @classmethod
    def setUpClass(cls):
        from raw_view.cli_help import batch_help, convert_help, main_help, view_help

        cls.pages = {
            "main": main_help("0.0.0"),
            "view": view_help(),
            "convert": convert_help(),
            "batch": batch_help(),
        }
        cls.all_text = "\n".join(cls.pages.values())

    def _option_strings(self) -> list[str]:
        import argparse

        import raw_view.__main__ as main_mod

        strings = []
        for action in main_mod._build_parser()._actions:
            if isinstance(action, argparse._HelpAction):  # pragma: no cover
                continue
            strings.extend(action.option_strings)
        return strings

    def test_every_option_is_documented_somewhere(self):
        missing = []
        for option in self._option_strings():
            required = self._ALIAS_OF.get(option, option)
            # Match the token, not a prefix: "--input" must not be satisfied by
            # "--input-type" appearing in the text.
            if not re.search(rf"(?<![\w-]){re.escape(required)}(?![\w-])",
                             self.all_text):
                missing.append(option)
        self.assertEqual(
            missing, [],
            "these options are accepted by the parser but appear in no help page: "
            f"{missing}",
        )

    def test_every_option_appears_on_a_mode_page_not_only_the_overview(self):
        """The overview alone is not enough: a user reading a mode page needs it."""
        from raw_view.cli_help import batch_help, convert_help, view_help

        mode_text = "\n".join((view_help(), convert_help(), batch_help()))
        mode_only = {
            "--input-type", "--input-yuv-type", "--input-alignment",
            "--input-endianness", "--input-raw-type", "--raw-type", "--yuv-type",
            "--alignment", "--endianness", "--source-mode", "--bayer-pattern",
            "--preview-mode", "--batch-file", "--target",
        }
        for option in sorted(mode_only):
            with self.subTest(option=option):
                self.assertTrue(
                    re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", mode_text),
                    f"{option} is not mentioned on any mode page",
                )

    def test_main_page_does_not_claim_more_than_it_documents(self):
        """H-3: the old page said "every other option ... is documented on that
        mode's page" and two options were on no page at all.  The claim is now
        narrower and this pins it."""
        main = self.pages["main"]
        self.assertNotIn("every other option belongs to exactly one mode", main)
        self.assertIn("the remaining options belong to one mode each", main)

    def test_core_options_are_not_claimed_for_batch(self):
        """M-1: --width/-height/-offset/-i/-o are not read in batch mode."""
        main = self.pages["main"]
        core = main.split("CORE OPTIONS", 1)[1].split("MODE PAGES", 1)[0]
        self.assertNotIn("accepted by every mode", core)
        self.assertIn("batch ignores these five", core)

    def test_batch_page_documents_the_required_option(self):
        self.assertIn("-b, --batch-file", self.pages["batch"])
        self.assertIn("REQUIRED OPTION", self.pages["batch"])

    def test_input_raw_type_is_documented_as_reserved(self):
        self.assertIn("--input-raw-type", self.pages["convert"])
        self.assertIn("reserved", self.pages["convert"])


class ModeHelpTests(unittest.TestCase):
    """The three level-2 pages: they print, and they teach by example."""

    @classmethod
    def setUpClass(cls):
        cls.view = _run_cli("--help-view")
        cls.convert = _run_cli("--help-convert")
        cls.batch = _run_cli("--help-batch")

    # ── shared shape ──

    def test_all_three_exit_zero(self):
        for name, proc in (("view", self.view), ("convert", self.convert),
                           ("batch", self.batch)):
            with self.subTest(page=name):
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertTrue(proc.stdout.strip())

    def test_each_page_has_usage_and_examples(self):
        for name, proc in (("view", self.view), ("convert", self.convert),
                           ("batch", self.batch)):
            with self.subTest(page=name):
                self.assertIn("USAGE", proc.stdout)
                self.assertIn("EXAMPLE", proc.stdout)

    def test_pages_cross_reference_each_other(self):
        for proc in (self.view, self.convert, self.batch):
            self.assertIn("--help-", proc.stdout)
            self.assertIn("docs/cli.md", proc.stdout)

    # ── view ──

    def test_view_page_covers_both_input_families(self):
        text = self.view.stdout
        for token in ("-t RAW", "-t YUV", "--input-yuv-type",
                      "--raw-type", "--yuv-type", "--alignment", "--endianness",
                      "--bayer-pattern", "--preview-mode", "--offset"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_view_page_has_generalise_notes(self):
        """YVYU from YUYV, YOnly8 from YOnly10, packed RAW variants, like the ask."""
        text = self.view.stdout
        self.assertIn("YVYU", text)
        self.assertIn("YOnly8", text)
        self.assertIn("RAW10 Packed", text)
        self.assertIn("Try next", text)

    def test_view_page_explains_target_direction(self):
        """L-2: view's -t names the INPUT; convert's names the OUTPUT."""
        self.assertIn("-t names what the INPUT is", _unwrapped(self.view.stdout))
        self.assertIn("-t/--target names what you WRITE",
                      _unwrapped(self.convert.stdout))

    def test_view_page_examples_are_runnable_commands(self):
        text = self.view.stdout
        commands = [
            line.strip() for line in text.splitlines()
            if line.strip().startswith("raw-view view -i ")
        ]
        self.assertGreaterEqual(len(commands), 4, "expected several worked examples")
        self.assertTrue(all("-i " in c and "-o " in c for c in commands))

    # ── convert ──

    def test_convert_page_lists_the_three_jobs(self):
        text = self.convert.stdout
        self.assertIn("image -> RAW/YUV", text)
        self.assertIn("YUV -> YUV", text)
        self.assertIn("YUV -> image", text)

    def test_convert_page_has_the_nv16_to_yuyv_example(self):
        # Commands are wrapped with a trailing backslash, so compare the joined
        # text rather than requiring one physical line.
        text = _unwrapped(self.convert.stdout)
        self.assertIn("--input-type yuv --input-yuv-type NV16 --yuv-type YUYV", text)

    def test_convert_page_lists_both_families_and_the_refusals(self):
        text = self.convert.stdout
        for name in ("YUYV", "UYVY", "YVYU", "VYUY", "NV16", "NV61"):
            self.assertIn(name, text)
        for name in ("I420", "YV12", "NV12", "NV21"):
            self.assertIn(name, text)
        self.assertIn("4:2:2 <-> 4:2:0", text)
        self.assertIn("YOnly", text)

    def test_convert_page_documents_the_packed_raw_example(self):
        self.assertIn("RAW10 Packed", self.convert.stdout)
        self.assertIn("RAW12 Packed", self.convert.stdout)

    def test_convert_page_shows_the_yuv_to_png_command(self):
        self.assertIn(
            "--input-type yuv --input-yuv-type YOnly10",
            _unwrapped(self.convert.stdout),
        )

    # ── batch ──

    def test_batch_page_states_json_is_strict(self):
        text = self.batch.stdout
        self.assertIn("strict JSON", text)
        self.assertIn("UTF-8", text)
        self.assertIn("Comments", text)

    def test_batch_page_lists_every_schema_key(self):
        text = self.batch.stdout
        for key in ("mode", "target", "input_type", "input_yuv_type",
                    "input_raw_type", "input_alignment", "input_endianness",
                    "raw_type", "yuv_type", "width", "height", "alignment",
                    "endianness", "source_mode", "bayer_pattern",
                    "preview_mode", "offset", "output_dir", "base_dir",
                    "files", "input", "output"):
            with self.subTest(key=key):
                self.assertIn(key, text)


class BatchJsonExampleTests(unittest.TestCase):
    """``--batch-help`` examples must be valid JSON *as printed*.

    The previous page showed one example full of ``//`` comments, so a user who
    copied it got ``JSONDecodeError: Expecting property name enclosed in double
    quotes`` instead of a batch run.  These tests extract the fenced examples
    from the printed page (not from the module constants) so the assertion is
    about what the user sees.
    """

    _RULE = "-" * 78

    @classmethod
    def setUpClass(cls):
        cls.text = _run_cli("--help-batch").stdout
        cls.examples = cls._extract(cls.text)

    @classmethod
    def _extract(cls, text: str) -> list[str]:
        """Text between the two ``-----`` rules that bracket each example."""
        lines = text.splitlines()
        blocks, current = [], None
        for line in lines:
            if line.strip() and set(line.strip()) == {"-"} and len(line.strip()) >= 20:
                if current is None:
                    current = []
                else:
                    blocks.append("\n".join(current))
                    current = None
                continue
            if current is not None:
                current.append(line)
        return blocks

    def test_at_least_three_examples_printed(self):
        self.assertGreaterEqual(len(self.examples), 3)

    def test_every_example_parses_as_json(self):
        for index, block in enumerate(self.examples, start=1):
            with self.subTest(example=index):
                try:
                    document = json.loads(block)
                except json.JSONDecodeError as exc:  # pragma: no cover - failure path
                    self.fail(f"example {index} is not valid JSON: {exc}\n{block}")
                self.assertIsInstance(document, dict)
                self.assertIn("files", document)

    def test_examples_are_accepted_by_the_batch_validator(self):
        """More than JSON syntax: the real validator must accept them."""
        import raw_view.__main__ as main_mod

        for index, block in enumerate(self.examples, start=1):
            with self.subTest(example=index):
                main_mod._validate_batch_spec(json.loads(block))

    def test_examples_contain_no_json_comment_syntax(self):
        """No ``// ...`` or ``/* ... */`` outside a string value.

        A bare substring check is wrong here: ``"D:/work/frames"`` legitimately
        contains ``//``.  What must not exist is a comment *token between
        values*, which is exactly what makes ``json.loads`` fail -- so this scans
        the parsed document's source outside string literals.
        """
        for index, block in enumerate(self.examples, start=1):
            with self.subTest(example=index):
                in_string = False
                escaped = False
                for position, char in enumerate(block):
                    if in_string:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            in_string = False
                        continue
                    if char == '"':
                        in_string = True
                        continue
                    if block.startswith("//", position) or block.startswith("/*", position):
                        self.fail(
                            f"example {index} has a comment token at offset "
                            f"{position}: {block[position:position + 40]!r}"
                        )

    def test_exported_constants_are_valid_json(self):
        from raw_view.cli_help import BATCH_EXAMPLES

        self.assertGreaterEqual(len(BATCH_EXAMPLES), 3)
        for index, text in enumerate(BATCH_EXAMPLES, start=1):
            with self.subTest(example=index):
                json.loads(text)

    def test_batch_help_alias_matches(self):
        """``--batch-help`` (script compatibility) prints the same page."""
        self.assertEqual(_run_cli("--batch-help").stdout, self.text)

    def test_batch_help_is_reachable_with_a_mode_word(self):
        proc = _run_cli("batch", "--help-batch")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, self.text)


class _YuvFixtureTestCase(unittest.TestCase):
    """Builds real YUV/RAW binaries; no Qt, no GUI, no user settings."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_help_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _path(self, name: str) -> str:
        return os.path.join(self._tmp, name)

    def _write_yonly10(self, name: str, width: int, height: int) -> str:
        """A YOnly10 frame: 10-bit values MSB-aligned in 16-bit little-endian."""
        ramp = np.tile(
            np.linspace(0, 1023, width).astype(np.uint16), (height, 1)
        )
        data = (ramp << 6).astype("<u2")
        path = self._path(name)
        Path(path).write_bytes(data.tobytes())
        return path

    def _write_nv16(self, name: str, width: int, height: int) -> str:
        y = np.tile(np.arange(width, dtype=np.uint8), (height, 1))
        chroma = np.empty((height, width), dtype=np.uint8)
        chroma[:, 0::2] = 128
        chroma[:, 1::2] = 64
        path = self._path(name)
        Path(path).write_bytes(y.tobytes() + chroma.tobytes())
        return path


class YuvInputBugTests(_YuvFixtureTestCase):
    """The reported defect: a YUV binary + --input-type yuv must convert to PNG.

    Repro from the report (1296x736 YOnly10 ``.bin`` -> ``.png``):

        convert -i x.bin -o x.png --target YUV --yuv-type YOnly10 \
                --width 1296 --height 736

    ``--input-type`` was simply missing, but adding it did *not* help: the YUV
    path only knew how to re-arrange YUV bytes, so ``YOnly10 -> YOnly10`` was
    rejected as a no-op and a PNG output name was silently written full of raw
    bytes.  Both halves are covered here.
    """

    WIDTH, HEIGHT = 1296, 736

    def test_exact_reported_command_with_input_type_succeeds(self):
        source = self._write_yonly10("output_0x0b_grey.bin", self.WIDTH, self.HEIGHT)
        out = self._path("out.png")
        proc = _run_cli(
            "convert", "-i", source, "-o", out,
            "--target", "YUV", "--input-type", "yuv",
            "--input-yuv-type", "YOnly10", "--yuv-type", "YOnly10",
            "--width", str(self.WIDTH), "--height", str(self.HEIGHT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(out), proc.stdout)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_yuv_to_image_reports_a_decode_not_a_rearrange(self):
        source = self._write_yonly10("g.bin", 64, 48)
        proc = _run_cli(
            "convert", "-i", source, "-o", self._path("g.png"),
            "--input-type", "yuv", "--input-yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("YUV decode", proc.stdout)

    def test_decode_needs_no_target_and_no_explicit_output_type(self):
        """`-o x.png` says what to do; omitting -t must not be an error."""
        source = self._write_yonly10("g2.bin", 64, 48)
        proc = _run_cli(
            "convert", "-i", source, "-o", self._path("g2.png"),
            "--input-type", "yuv", "--input-yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_decode_honours_crop_alignment_and_endianness(self):
        ramp = np.tile(np.arange(32, dtype=np.uint16), (16, 1))
        # LSB-aligned big-endian YOnly10
        path = self._path("lsbbe.bin")
        Path(path).write_bytes(ramp.astype(">u2").tobytes())
        out = self._path("lsbbe.png")
        proc = _run_cli(
            "convert", "-i", path, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "YOnly10",
            "--input-alignment", "lsb", "--input-endianness", "big",
            "--width", "32", "--height", "16",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(out))

    def test_short_input_is_refused_before_writing(self):
        path = self._path("short.bin")
        Path(path).write_bytes(b"\x00" * 64)
        out = self._path("short.png")
        proc = _run_cli(
            "convert", "-i", path, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("too short", proc.stderr)
        self.assertFalse(os.path.exists(out))

    def test_yuv_to_yuv_still_writes_raw_bytes(self):
        """The new image branch must not hijack the transcode job."""
        source = self._write_nv16("f.nv16", 64, 48)
        out = self._path("f.yuyv")
        proc = _run_cli(
            "convert", "-i", source, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "NV16", "--yuv-type", "YUYV",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(os.path.getsize(out), 64 * 48 * 2)
        with open(out, "rb") as handle:
            self.assertNotEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_same_format_transcode_is_still_a_no_op_error(self):
        """Without an image output name the old rejection still applies."""
        source = self._write_nv16("f2.nv16", 64, 48)
        proc = _run_cli(
            "convert", "-i", source, "-o", self._path("f2.nv16"),
            "--input-type", "yuv", "--input-yuv-type", "NV16", "--yuv-type", "NV16",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-op", proc.stderr)


class ImageReadErrorGuidanceTests(_YuvFixtureTestCase):
    """The improved message for `failed to read image` on a binary input."""

    def _convert_binary_as_image(self, source: str, out_name: str, *extra: str):
        return _run_cli(
            "convert", "-i", source, "-o", self._path(out_name), *extra
        )

    def test_binary_input_gets_actionable_guidance(self):
        source = self._write_yonly10("grey.bin", 1296, 736)
        proc = self._convert_binary_as_image(
            source, "out.png", "--target", "YUV", "--yuv-type", "YOnly10",
            "--width", "1296", "--height", "736",
        )
        self.assertEqual(proc.returncode, 1)
        text = proc.stderr
        # The symptom is still reported verbatim (scripts may match on it).
        self.assertIn("failed to read image", text)
        # ... followed by the diagnosis and copy-pasteable commands.
        self.assertIn("--input-type image", text)
        self.assertIn("--input-type yuv", text)
        self.assertIn("--help-convert", text)
        # The commands echo the user's own paths, size and format.
        self.assertIn("grey.bin", text)
        self.assertIn("YOnly10", text)
        self.assertIn("1296", text)
        self.assertIn("736", text)

    def _suggested_commands(self, stderr: str) -> list[list[str]]:
        """Every runnable command the guidance printed, as argv lists."""
        commands = []
        for line in stderr.splitlines():
            stripped = line.strip()
            if not any(stripped.startswith(p) for p in
                       ("raw-view ", "raw-view.exe ", "raw-view-cli.exe ",
                        "python -m raw_view ")):
                continue
            # shlex, not str.split: the quoted paths contain spaces on Windows.
            argv = shlex.split(stripped)
            if argv[0] == "raw-view":
                argv = argv[1:]                      # packaged build spelling
            elif argv[:3] == ["python", "-m", "raw_view"]:
                argv = argv[3:]                      # source-checkout spelling
            else:  # pragma: no cover - other packaged names
                self.fail(f"unexpected executable in suggestion: {argv[0]}")
            commands.append(argv)
        return commands

    def test_every_suggested_command_actually_works(self):
        """Run EVERY command the guidance prints (review H-1).

        The old test executed only the first suggestion, which is how a transcode
        command that was rejected as ``X -> X is a no-op`` shipped: the guidance
        filled ``--input-yuv-type`` and ``--yuv-type`` from one variable.
        """
        source = self._write_yonly10("grey2.bin", 64, 48)
        proc = self._convert_binary_as_image(
            source, "grey2.png", "--target", "YUV", "--yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        commands = self._suggested_commands(proc.stderr)
        self.assertGreaterEqual(len(commands), 3)
        for argv in commands:
            with self.subTest(command=" ".join(argv)):
                # Run in the temp dir: one suggestion uses a relative output
                # name ("out.yuv"), which must not land in the repository root.
                result = _run_cli(*argv, cwd=self._tmp)
                self.assertEqual(
                    result.returncode, 0,
                    f"guidance suggested a command that fails:\n"
                    f"  {' '.join(argv)}\n  {result.stderr.strip()}",
                )

    def test_transcode_suggestion_uses_a_different_target(self):
        """H-1 directly: --input-yuv-type and --yuv-type must not be equal."""
        source = self._write_yonly10("grey3.bin", 64, 48)
        proc = self._convert_binary_as_image(
            source, "grey3.png", "--target", "YUV", "--yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("-> YOnly10 is a no-op", proc.stderr)
        # YOnly has no legal transcode target, so the whole section is absent.
        self.assertNotIn("another YUV layout", proc.stderr)

    def test_transcode_suggestion_is_present_and_valid_for_a_transcodable_source(self):
        source = self._write_nv16("nv16src.bin", 64, 48)
        proc = self._convert_binary_as_image(
            source, "nv16src.png", "--target", "YUV", "--yuv-type", "NV16",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("another YUV layout", proc.stderr)
        for argv in self._suggested_commands(proc.stderr):
            with self.subTest(command=" ".join(argv)):
                self.assertEqual(
                    _run_cli(*argv, cwd=self._tmp).returncode, 0, " ".join(argv)
                )

    def test_no_suggested_command_repeats_the_source_as_target(self):
        """A no-op pairs check across several source formats."""
        for fmt in ("NV16", "NV61", "I420", "NV12", "YUYV"):
            with self.subTest(fmt=fmt):
                from raw_view.cli_help import _other_yuv_target

                target = _other_yuv_target(fmt)
                self.assertIsNotNone(target, f"{fmt} should have a legal target")
                self.assertNotEqual(target.upper(), fmt.upper())

    def test_broken_image_does_not_get_binary_advice(self):
        path = self._path("broken.png")
        Path(path).write_bytes(b"not an image at all")
        proc = self._convert_binary_as_image(path, "b.raw")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("failed to read image", proc.stderr)
        self.assertNotIn("--input-type yuv", proc.stderr)
        self.assertIn("not a decodable PNG/JPEG/BMP image", proc.stderr)

    def test_unknown_suffix_binary_gets_guidance(self):
        path = self._path("payload.dat")
        Path(path).write_bytes(bytes(range(256)) * 4)
        proc = self._convert_binary_as_image(path, "p.raw")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--input-type yuv", proc.stderr)

    def test_missing_file_keeps_its_own_short_error(self):
        proc = self._convert_binary_as_image(self._path("nope.png"), "n.raw")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("input file not found", proc.stderr)
        self.assertNotIn("--input-type yuv", proc.stderr)

    def test_non_image_errors_are_not_diluted(self):
        """A permissions/encode failure must not print the RAW/YUV lecture."""
        from raw_view.cli_help import image_read_guidance
        from raw_view.cli_help import looks_like_binary

        # A real decode failure on a genuine-looking PNG image (magic present).
        path = self._path("real.png")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.assertTrue(not looks_like_binary(path))
        text = image_read_guidance(path, "o.raw", 8, 8, "failed to read image: x")
        self.assertNotIn("--input-type yuv", text)

    def test_batch_fail_line_carries_a_short_hint(self):
        source = self._write_yonly10("bg.bin", 32, 16)
        spec = {
            "mode": "convert",
            "target": "YUV",
            "yuv_type": "YOnly10",
            "width": 32,
            "height": 16,
            "files": [{"input": source, "output": self._path("bg.yuv")}],
        }
        batch_file = self._path("spec.json")
        Path(batch_file).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_file)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("failed to read image", proc.stdout)
        self.assertIn('"input_type": "yuv"', proc.stdout)


class BatchYuvDecodeTests(_YuvFixtureTestCase):
    """Batch parity: an image output on a YUV convert entry decodes."""

    def test_batch_entry_with_png_output_decodes(self):
        source = self._write_yonly10("b.bin", 64, 48)
        out = self._path("b_dec.png")
        spec = {
            "mode": "convert",
            "target": "YUV",
            "input_type": "yuv",
            "input_yuv_type": "YOnly10",
            "yuv_type": "YOnly10",
            "width": 64,
            "height": 48,
            "files": [{"input": source, "output": out}],
        }
        batch_file = self._path("spec.json")
        Path(batch_file).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_file)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 succeeded", proc.stdout)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")


class LooksLikeBinaryTests(unittest.TestCase):
    """Unit-level table for the classifier behind the guidance."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_bin_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self._tmp, name)
        Path(path).write_bytes(data)
        return path

    def test_raw_yuv_suffixes_are_binary(self):
        from raw_view.cli_help import looks_like_binary

        for suffix in (".bin", ".raw", ".yuv", ".nv12", ".yuyv", ".dat"):
            with self.subTest(suffix=suffix):
                self.assertTrue(looks_like_binary(self._write(f"x{suffix}", b"\x00" * 8)))

    def test_real_image_magic_is_not_binary(self):
        from raw_view.cli_help import looks_like_binary

        for name, magic in (
            ("a.png", b"\x89PNG\r\n\x1a\n"),
            ("a.jpg", b"\xff\xd8\xff\xe0"),
            ("a.bmp", b"BM"),
            ("a.tif", b"II*\x00"),
        ):
            with self.subTest(name=name):
                self.assertFalse(looks_like_binary(self._write(name + ".x", magic)))

    def test_image_suffix_wins_over_content_sniff(self):
        """A ``.png`` full of noise is a broken image, not a YUV dump."""
        from raw_view.cli_help import looks_like_binary

        self.assertFalse(looks_like_binary(self._write("noise.png", bytes(range(64)))))

    def test_empty_file_counts_as_binary(self):
        from raw_view.cli_help import looks_like_binary

        self.assertTrue(looks_like_binary(self._write("empty.dat", b"")))

    def test_missing_file_with_a_binary_suffix_still_answers_true(self):
        """The suffix alone is enough -- the file need not be readable.

        Reaching this code at all means the caller already read successfully
        enough to fail on *content*, so a missing file is not a case the hint
        has to distinguish; a ``.bin`` name is the best signal available.
        """
        from raw_view.cli_help import looks_like_binary

        self.assertTrue(looks_like_binary(os.path.join(self._tmp, "missing.bin")))

    def test_missing_file_with_a_neutral_suffix_answers_false(self):
        from raw_view.cli_help import looks_like_binary

        self.assertFalse(looks_like_binary(os.path.join(self._tmp, "missing.xyz")))


class SuggestedProgTests(unittest.TestCase):
    """The printed commands must name an executable the reader can actually run."""

    def test_source_invocation_suggests_python_dash_m(self):
        from unittest import mock

        from raw_view.cli_help import suggested_prog

        with mock.patch.object(sys, "argv", [".../raw_view/__main__.py"]):
            self.assertEqual(suggested_prog(), "python -m raw_view")

    def test_packaged_build_suggests_its_own_name(self):
        from unittest import mock

        from raw_view.cli_help import suggested_prog

        for argv0, expected in (
            ("C:/bundle/raw-view-cli.exe", "raw-view-cli.exe"),
            ("C:/bundle/raw-view.exe", "raw-view.exe"),
            ("/usr/local/bin/raw-view", "raw-view"),
        ):
            with self.subTest(argv0=argv0):
                with mock.patch.object(sys, "argv", [argv0]):
                    self.assertEqual(suggested_prog(), expected)

    def test_guidance_uses_the_given_prog(self):
        from raw_view.cli_help import image_read_guidance

        text = image_read_guidance(
            "x.bin", "x.png", 8, 8, "failed to read image: x.bin",
            prog="raw-view-cli.exe",
        )
        self.assertIn("raw-view-cli.exe view -i", text)
        self.assertNotIn("python -m raw_view", text)


class HelpIsAsciiTests(unittest.TestCase):
    """Help text must survive a narrow console codepage (cp1252 / GBK)."""

    def test_all_pages_are_ascii(self):
        from raw_view.cli_help import (
            batch_help, convert_help, main_help, view_help,
        )

        pages = {
            "main": main_help("0.0.0"),
            "view": view_help(),
            "convert": convert_help(),
            "batch": batch_help(),
        }
        for name, text in pages.items():
            with self.subTest(page=name):
                try:
                    text.encode("ascii")
                except UnicodeEncodeError as exc:  # pragma: no cover
                    self.fail(f"{name} help has a non-ASCII character: {exc}")

    def test_pages_print_on_a_cp1252_stream(self):
        """Every page encodes under cp1252, the narrowest realistic console."""
        from raw_view.cli_help import (
            batch_help, convert_help, main_help, view_help,
        )

        for name, text in (("main", main_help("0.0.0")), ("view", view_help()),
                           ("convert", convert_help()), ("batch", batch_help())):
            with self.subTest(page=name):
                text.encode("cp1252")

    def test_no_page_has_a_line_over_100_columns(self):
        """Review L-1 (0.5.7): long command lines folded badly in an 80-col
        terminal (one convert line reached 175).  The pages were re-wrapped; a
        guard is what keeps a future edit from re-introducing a 130-column line
        that no test would notice."""
        from raw_view.cli_help import (
            batch_help, convert_help, main_help, view_help,
        )

        pages = {
            "main": main_help("0.0.0"),
            "view": view_help(),
            "convert": convert_help(),
            "batch": batch_help(),
        }
        for name, text in pages.items():
            with self.subTest(page=name):
                over = [
                    (number, len(line))
                    for number, line in enumerate(text.splitlines(), start=1)
                    if len(line) > 100
                ]
                self.assertEqual(over, [], f"{name} help has lines over 100 chars")


class HelpColumnAlignmentTests(unittest.TestCase):
    """Review round 4: within one block, the scope column must be one column.

    The defect this pins: on the main page ``-o, --output PATH`` put its
    ``(view, convert)`` at column 59 while ``-i``/``--width``/``--height``/
    ``--offset`` all sat at 56.  The description
    ``Output file; omitted -> auto-named`` was three characters longer than the
    others and the author moved the *scope* instead of shortening the sentence,
    so the five rows no longer read as one table.

    Why the existing guards missed it: the width guard only looks at how long a
    line is (that row was 73 of 100 columns), and no test compared two rows to
    each other.  Width is not alignment -- a block can be uniformly wide and
    still ragged, or uniformly narrow and ragged in the middle.
    """

    @classmethod
    def setUpClass(cls):
        from raw_view.cli_help import (
            batch_help, convert_help, main_help, view_help,
        )

        cls.pages = {
            "main": main_help("0.0.0"),
            "view": view_help(),
            "convert": convert_help(),
            "batch": batch_help(),
        }

    def test_no_block_has_a_ragged_annotation_column(self):
        for name, text in self.pages.items():
            with self.subTest(page=name):
                self.assertEqual(
                    _misaligned_scope_columns(text), [],
                    f"{name} help has rows whose column does not line up",
                )

    def test_core_options_scope_column_is_identical_for_all_five_rows(self):
        """The exact regression, asserted on the exact block.

        A generic sweep alone would pass if someone deleted the fifth row; this
        fixes the expectation: five core options, one ``(view, convert)`` column.
        """
        block = self.pages["main"].split("CORE OPTIONS", 1)[1].split(
            "MODE PAGES", 1
        )[0]
        rows = [
            line for line in block.splitlines() if "(view, convert)" in line
        ]
        self.assertEqual(len(rows), 5, "expected the five core options")
        columns = {line.index("(view, convert)") + 1 for line in rows}
        self.assertEqual(
            len(columns), 1,
            "the five CORE OPTIONS rows put (view, convert) at different "
            f"columns {sorted(columns)}:\n" + "\n".join(rows),
        )

    def test_input_continuation_line_is_not_treated_as_a_row(self):
        """``-i`` wraps onto a second line that carries no scope.

        The second line is indented under the description column and starts
        with ``(``, so a naive "every line with a parenthesised tail" parser
        would compare it against the real rows and report a phantom
        misalignment -- or, worse, a "fix" that aligned the wrong lines.
        """
        block = self.pages["main"].split("CORE OPTIONS", 1)[1].split(
            "MODE PAGES", 1
        )[0]
        lines = block.splitlines()
        index = next(
            position for position, line in enumerate(lines)
            if line.strip().startswith("-i, --input")
        )
        continuation = lines[index + 1]
        self.assertTrue(continuation.strip().startswith("(a directory is rejected"))
        self.assertIsNone(
            _HELP_ROW.match(continuation),
            "the continuation line must not parse as an option row",
        )

    def test_annotation_used_once_is_not_a_constraint(self):
        """A lone annotation has no column to agree with, so it is not flagged.

        Without this, every re-wrapped prose line ending in parentheses would be
        a false positive, and the sane fix would be to weaken the guard.
        """
        sample = "\n".join([
            "RAW OPTIONS",
            "  --raw-type       RAW8, RAW10, RAW12 (default RAW12)",
            "  --alignment      msb | lsb (default msb)",
        ])
        self.assertEqual(_misaligned_scope_columns(sample), [])

    def test_detector_reports_the_original_defect(self):
        """Mutation check without touching the source: the detector must fire.

        A test that only ever sees correct input proves nothing about the
        detector -- a helper hard-wired to return ``[]`` would also pass the
        sweep above.  This feeds it the 3-column-right row that shipped in 0.5.8.
        """
        sample = "\n".join([
            "CORE OPTIONS",
            "  -i, --input PATH     Input file, exactly one         (view, convert)",
            "  -o, --output PATH    Output file; omitted -> auto-named (view, convert)",
            "  --width N            Frame width                     (view, convert)",
        ])
        problems = _misaligned_scope_columns(sample)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("(view, convert)", problems[0])
        self.assertIn("CORE OPTIONS", problems[0])

    def test_different_blocks_may_use_different_columns(self):
        """Grouping is per block on purpose: the pages are not one grid.

        ``CORE OPTIONS`` (option column 23) and ``MODE PAGES`` (20) legitimately
        line up at different columns on the same page; a page-wide grouping
        would have demanded an unnecessary rewrite of the second block.
        """
        # Same annotation, two blocks: CORE OPTIONS rows are ``-i, --input``
        # (option column 23) and the -h/--version rows in MODE PAGES are
        # ``-h, --help`` (column 20), so one page-wide grouping would demand a
        # rewrite that makes the page worse.
        sample = "\n".join([
            "CORE OPTIONS",
            "  -i, --input PATH     Input file, exactly one        (view, convert)",
            "  --width N            Frame width                    (view, convert)",
            "MODE PAGES",
            "  -h, --help        This page           (view, convert)",
            "  --version         Print the version   (view, convert)",
        ])
        self.assertEqual(_misaligned_scope_columns(sample), [])
        columns = _scope_columns(sample)
        core = {column for _, column in columns[("CORE OPTIONS", "(view, convert)")]}
        mode_pages = {column for _, column in columns[("MODE PAGES", "(view, convert)")]}
        self.assertEqual(len(core), 1)
        self.assertEqual(len(mode_pages), 1)
        self.assertNotEqual(core, mode_pages)


class DirectoryInputIsCoveredEverywhereTests(unittest.TestCase):
    """L-3: every page that documents ``-i`` must say it takes one file only.

    ``view``/``convert`` reject a directory with a batch pointer, but the user
    only meets that error *after* the mistake.  ``--help-batch`` already said
    so; the two pages where ``-i`` is actually typed did not.
    """

    _NOTE = "(a directory is rejected -- use `batch` for a folder)"

    @classmethod
    def setUpClass(cls):
        from raw_view.cli_help import (
            batch_help, convert_help, main_help, view_help,
        )

        cls.pages = {
            "main": main_help("0.0.0"),
            "view": view_help(),
            "convert": convert_help(),
            "batch": batch_help(),
        }

    def test_every_page_with_minus_i_states_the_single_file_rule(self):
        for name in ("main", "view", "convert", "batch"):
            with self.subTest(page=name):
                self.assertIn(self._NOTE, self.pages[name])

    def test_the_note_sits_directly_below_the_input_option(self):
        """The note must be attached to ``-i`` on every page, not to batch prose.

        Three pages saying it three different ways is how help drifts; placing
        it under the option itself is what makes it reachable before the user
        hits the error.
        """
        for name in ("main", "view", "convert"):
            with self.subTest(page=name):
                lines = [line.strip() for line in self.pages[name].splitlines()]
                index = next(
                    position for position, line in enumerate(lines)
                    if line.startswith("-i, --input")
                )
                self.assertEqual(lines[index + 1], self._NOTE)

    def test_batch_folder_example_is_a_valid_entry_object(self):
        """The folder pointer must be copy-pasteable JSON, not a bare string.

        ``"files": ["captures/*.raw"]`` is rejected by the validator
        (``files[0] must be a JSON object, got str``), so the example that tells
        users to use batch would itself fail on the first run.
        """
        import re

        text = self.pages["batch"]
        match = re.search(r'\{"files": \[.*?\]\}', text)
        self.assertIsNotNone(match, "the batch page no longer shows a files example")
        document = json.loads(match.group(0))
        self.assertIsInstance(document["files"][0], dict)
        self.assertIn("input", document["files"][0])


class LauncherRoutingTests(unittest.TestCase):
    """The launcher must send the new help flags to the CLI, not the GUI."""

    def test_new_help_flags_route_to_cli(self):
        from raw_view import launcher

        for flag in ("--help-view", "--help-convert", "--help-batch",
                     "--batch-help", "-h", "--help", "--version"):
            with self.subTest(flag=flag):
                self.assertEqual(launcher.decide_target([flag]), "cli")

    def test_mode_words_do_not_swallow_help_into_the_gui(self):
        from raw_view import launcher

        for argv in (["view", "--help-view"], ["batch", "--help-batch"],
                     ["convert", "--help-convert"]):
            with self.subTest(argv=argv):
                self.assertEqual(launcher.decide_target(argv), "cli")


class ModeWordRequiredTests(unittest.TestCase):
    """H-2: CLI options without a mode word must not launch a blocking GUI."""

    def test_options_that_would_open_the_gui_are_rejected(self):
        """N-1: the guard keys on "would this open the GUI", so these must fire.

        Every case here has no ``-i``, so ``view``'s default would fall through to
        ``_run_gui`` and block the terminal.
        """
        for argv in (["--width", "800"], ["--width=800"], ["-o", "o.raw"],
                     ["-t", "RAW"], ["--yuv-type", "NV16"], ["--offset", "16"],
                     ["-b", "jobs.json"], ["--batch-file", "jobs.json"]):
            with self.subTest(argv=argv):
                proc = _run_cli(*argv, timeout=30)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertIn("a mode word is required", proc.stderr)
                # Never silently start a window: no Qt import output at all.
                self.assertNotIn("qt_material", proc.stderr)

    def test_input_without_a_mode_word_still_decodes(self):
        """N-1 (the false rejection): ``-i`` never opens the GUI.

        ``view`` is the default mode, and ``_run_view_mode`` decodes whenever
        ``-i`` is present.  Judging by "carries CLI options" rejected this
        legitimate, historically working call with exit 2.
        """
        tmp = tempfile.mkdtemp(prefix="rawview_nomode_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        source = os.path.join(tmp, "f.yuv")
        Path(source).write_bytes(bytes(64 * 48 * 2))
        out = os.path.join(tmp, "out.png")
        proc = _run_cli("-i", source, "-o", out, "-t", "YUV", "--yuv-type", "YUYV",
                        "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(out))
        self.assertNotIn("a mode word is required", proc.stderr)

    def test_batch_file_without_a_mode_word_does_not_hang(self):
        """N-1 (the leak): ``-b`` is ignored by the view path -> GUI -> hang.

        The guard's own timeout is the assertion: a regression here blocks the
        caller's terminal, which is exactly what H-2 exists to prevent.
        """
        tmp = tempfile.mkdtemp(prefix="rawview_nob_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proc = _run_cli("-b", os.path.join(tmp, "jobs.json"), timeout=30)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("a mode word is required", proc.stderr)

    def test_valid_mode_word_still_runs(self):
        """A recognised mode word is never rejected as "missing a mode word".

        ``view`` with no ``-i`` opens the GUI on purpose, so it cannot be run
        here; its routing is covered by
        ``launcher.decide_target``. ``convert``/``batch`` fail on their own
        missing input but must not be caught by the mode-word guard.
        """
        for argv in (["convert"], ["batch"]):
            with self.subTest(argv=argv):
                proc = _run_cli(*argv)
                self.assertNotIn("a mode word is required", proc.stderr)

    def test_mode_word_detection_is_positional_and_exact(self):
        from raw_view.__main__ import _mode_word_from_argv

        self.assertEqual(_mode_word_from_argv(["view", "-i", "a.raw"]), "view")
        self.assertEqual(_mode_word_from_argv(["--width", "8", "convert"]), "convert")
        self.assertIsNone(_mode_word_from_argv(["--width", "800"]))
        self.assertIsNone(_mode_word_from_argv(["a.raw"]))
        # An option *value* that happens to look like a mode must not count.
        self.assertIsNone(_mode_word_from_argv(["-i", "view"]))

    def test_print_and_exit_flags_are_allowed_without_a_mode(self):
        for argv in (["--version"], ["--help"], ["--help-view"],
                     ["--help-convert"], ["--help-batch"], ["--batch-help"]):
            with self.subTest(argv=argv):
                proc = _run_cli(*argv)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_options_before_the_mode_word_still_work(self):
        """`--width 64 --height 48 view -i ...` is a long-standing spelling."""
        tmp = tempfile.mkdtemp(prefix="rawview_order_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = os.path.join(tmp, "a.raw")
        Path(src).write_bytes(b"\x00" * (64 * 48))
        proc = _run_cli("--width", "64", "--height", "48", "view", "-i", src,
                        "-o", os.path.join(tmp, "a.png"), "-t", "RAW",
                        "--raw-type", "RAW8")
        self.assertEqual(proc.returncode, 0, proc.stderr)


class DecodeFrameReportingTests(_YuvFixtureTestCase):
    """M-3: `Frames found:` numbers what was found, and a partial tail warns."""

    def test_reports_found_versus_decoded(self):
        src = self._path("three.bin")
        frame = 64 * 48 * 2
        Path(src).write_bytes(b"\x00" * (frame * 3))
        proc = _run_cli("convert", "-i", src, "-o", self._path("three.png"),
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Frames found: 3", proc.stdout)
        self.assertIn("decoding frame 1 only", proc.stdout)

    def test_partial_tail_is_reported_not_silent(self):
        src = self._path("partial.bin")
        frame = 64 * 48 * 2
        Path(src).write_bytes(b"\x00" * (frame * 2) + b"\x00" * 7)
        proc = _run_cli("convert", "-i", src, "-o", self._path("partial.png"),
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Frames found: 2", proc.stdout)
        self.assertIn("trailing 7 byte(s)", proc.stdout)
        self.assertIn("were ignored", proc.stdout)

    def test_exact_multiple_prints_no_warning(self):
        src = self._path("exact.bin")
        Path(src).write_bytes(b"\x00" * (64 * 48 * 2))
        proc = _run_cli("convert", "-i", src, "-o", self._path("exact.png"),
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("trailing", proc.stdout)
        self.assertNotIn("Warning:", proc.stdout)


class ImageOutputSuffixTests(unittest.TestCase):
    """M-6: the decode/re-arrange decision and its two suffix sets stay aligned."""

    def test_converter_writer_and_decode_decision_agree(self):
        """Anything we call an image output must be something we can encode.

        ``_imwrite_extension`` returns ``.png`` for *unsupported* suffixes, so the
        check cannot simply compare to ``.png`` -- that would reject ``.png``
        itself.  Compare against the extension the writer would actually use for
        the same suffix instead: a suffix is only safe if the writer keeps it.
        """
        from raw_view.cli_help import IMAGE_OUTPUT_SUFFIXES
        from raw_view.converter import _IMWRITE_FALLBACK_EXT, _imwrite_extension

        for suffix in sorted(IMAGE_OUTPUT_SUFFIXES):
            with self.subTest(suffix=suffix):
                written = _imwrite_extension(f"out{suffix}")
                if suffix == _IMWRITE_FALLBACK_EXT:
                    self.assertEqual(written, suffix)
                    continue
                self.assertNotEqual(
                    written, _IMWRITE_FALLBACK_EXT,
                    f"{suffix} is treated as an image output but the writer "
                    f"falls back to {_IMWRITE_FALLBACK_EXT}",
                )

    def test_input_suffixes_are_a_superset_of_output_suffixes(self):
        from raw_view.cli_help import IMAGE_OUTPUT_SUFFIXES, IMAGE_SUFFIXES

        self.assertTrue(IMAGE_OUTPUT_SUFFIXES <= IMAGE_SUFFIXES)

    def test_no_second_copy_of_the_suffix_set_exists(self):
        """Regression guard for the drift the review found (6 vs 9 entries)."""
        import raw_view.__main__ as main_mod

        self.assertFalse(
            hasattr(main_mod, "_IMAGE_OUTPUT_SUFFIXES"),
            "the suffix set must live in cli_help only; a second copy already "
            "drifted once",
        )


class NonImageOutputAdviceTests(_YuvFixtureTestCase):
    """M-6: a same-format pair with a non-image output explains the extension."""

    def test_webp_output_gets_extension_advice(self):
        src = self._write_yonly10("w.bin", 64, 48)
        proc = _run_cli("convert", "-i", src, "-o", self._path("out.webp"),
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--yuv-type", "YOnly10", "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not end in a writable image extension", proc.stderr)
        self.assertIn(".png", proc.stderr)

    def test_extensionless_output_gets_the_same_advice(self):
        src = self._write_yonly10("w2.bin", 64, 48)
        proc = _run_cli("convert", "-i", src, "-o", self._path("out"),
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--yuv-type", "YOnly10", "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not end in a writable image extension", proc.stderr)

    def test_png_output_decodes(self):
        src = self._write_yonly10("w3.bin", 64, 48)
        out = self._path("out.png")
        proc = _run_cli("convert", "-i", src, "-o", out,
                        "--input-type", "yuv", "--input-yuv-type", "YOnly10",
                        "--yuv-type", "YOnly10", "--width", "64", "--height", "48")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(out, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")


class DocsCommandTests(_YuvFixtureTestCase):
    """S-4: the shell commands in ``docs/cli.md`` must actually run.

    The manual and the help text are maintained separately (help is English and
    generated, the manual is Chinese prose), so they drift.  Rather than
    duplicating the manual from the help -- which would lose the prose that makes
    it usable -- this executes what the manual tells people to type.
    """

    #: Synopsis lines use *whole-token metavariables* instead of real paths
    #: (``-i INPUT``).  Matching on tokens, not substrings: a real path may
    #: legitimately contain "OUTPUT" (``./OUTPUT/x.png``) and must not be
    #: silently skipped (review N-4).
    _SYNOPSIS_TOKENS = frozenset({
        "INPUT", "OUTPUT", "{RAW|YUV}", "[格式参数]", "[参数]", "[文件…]",
    })

    @classmethod
    def _is_synopsis(cls, argv: list[str]) -> bool:
        """Whether this line is a usage synopsis rather than a real command.

        ``raw-view view -i INPUT -o OUTPUT.jpg`` has no runnable file behind it;
        anything else does, and must be executed.
        """
        return any(token in cls._SYNOPSIS_TOKENS for token in argv)

    @classmethod
    def _commands(cls):
        """Classify the manual's shell commands.

        Returns ``(runnable, synopsis, expected_failures)``:

        * *runnable* -- real commands that must exit 0;
        * *synopsis* -- usage lines with ``INPUT``-style metavariables, not run;
        * *expected_failures* -- the §8 troubleshooting blocks start with the
          command that *demonstrates* a failure, annotated in the manual with a
          ``# Error: ...`` comment.  Those are asserted to fail, which is what
          makes the section's "this is what you will see" claim testable.
        """
        import re

        doc = Path(__file__).resolve().parents[1] / "docs" / "cli.md"
        text = doc.read_text(encoding="utf-8")
        runnable: list[list[str]] = []
        synopsis: list[list[str]] = []
        expected_failures: list[list[str]] = []
        for block in re.findall(r"```bash\n(.*?)```", text, re.S):
            # Join backslash continuations, keeping the comment that follows on
            # the same logical command so "# Error: ..." can be detected.
            joined = block.replace("\\\n", " ")
            lines = [l.strip() for l in joined.splitlines() if l.strip()]
            for index, line in enumerate(lines):
                if not line.startswith("raw-view "):
                    continue
                comment = ""
                if "  #" in line:
                    line, comment = line.split("  #", 1)
                    line = line.strip()
                # The manual also writes the expected output on the *next* line
                # as a bare comment ("# Error: failed to read image: ...").
                if not comment and index + 1 < len(lines):
                    following = lines[index + 1]
                    if following.startswith("#"):
                        comment = following.lstrip("#").strip()
                try:
                    argv = shlex.split(line)[1:]
                except ValueError:  # pragma: no cover - malformed doc line
                    continue
                if cls._is_synopsis(argv):
                    synopsis.append(argv)
                elif comment.lower().startswith("error"):
                    expected_failures.append(argv)
                else:
                    runnable.append(argv)
        return runnable, synopsis, expected_failures

    def test_manual_has_executable_examples(self):
        runnable, _, _ = self._commands()
        self.assertGreaterEqual(len(runnable), 15)

    def test_issue_section_commands_are_covered(self):
        """N-3: §8.1 (the user's original report) must be exercised, not skipped."""
        runnable, _, expected = self._commands()
        joined = [" ".join(argv) for argv in runnable + expected]
        self.assertTrue(
            any("output_0x0b_grey.bin" in line for line in joined),
            "the §8.1 troubleshooting commands must be executed, not skipped",
        )

    #: Byte size of each fixture the manual names, at the resolution the manual
    #: uses for it.  Generous but bounded (~17 MB) so "data too short" can never
    #: masquerade as "the documented command is wrong".
    _FIXTURE_SIZES = {
        "sensor.raw": 1920 * 1080 * 2,        # RAW12 @1920x1080
        "packed.raw": 1920 * 1080 * 5 // 4,   # RAW10 Packed
        "frame.yuv": 1920 * 1080 * 2,         # YUYV @1920x1080
        "grey.bin": 1296 * 736 * 2,           # YOnly10 @1296x736
        "frame.nv12": 1280 * 720 * 3 // 2,    # NV12 @1280x720
        "frame.nv16": 1920 * 1080 * 2,        # NV16 @1920x1080
        "frame.i420": 1920 * 1080 * 3 // 2,   # I420 @1920x1080
    }
    #: Image fixtures the manual names (any size decodes).
    _IMAGE_FIXTURES = ("photo.png", "shot1.png", "shot2.jpg", "shot3.bmp")

    #: Basenames that appear in the manual as *story* paths (§8.1 uses the paths
    #: from the original bug report).  Each is mapped onto a fixture with the
    #: right layout, so those commands are executed rather than skipped -- they
    #: are the most valuable samples in the manual (review N-3).
    _PATH_ALIASES = {
        "output_0x0b_grey.bin": 1296 * 736 * 2,   # YOnly10, from the bug report
        "photo.jpg": 0,                           # image; 0 => written via cv2
        "jobs.json": None,                        # a minimal valid batch spec
    }

    def test_every_manual_command_runs(self):
        import cv2

        for name in self._IMAGE_FIXTURES:
            cv2.imwrite(self._path(name), np.zeros((48, 64, 3), dtype=np.uint8))
        for name, size in self._FIXTURE_SIZES.items():
            Path(self._path(name)).write_bytes(bytes(size))
        for alias, size in self._PATH_ALIASES.items():
            if size is None:
                Path(self._path(alias)).write_text(
                    json.dumps({"mode": "convert", "target": "RAW",
                                "raw_type": "RAW8", "width": 8, "height": 8,
                                "files": []}),
                    encoding="utf-8",
                )
            elif size == 0:
                cv2.imwrite(self._path(alias), np.zeros((48, 64, 3), dtype=np.uint8))
            else:
                Path(self._path(alias)).write_bytes(bytes(size))

        # A `--offset 128` example documents skipping a header, so its input has
        # to actually have one: 128 bytes followed by two whole frames.
        offset_fixture = self._path("framewithheader.nv16")
        Path(offset_fixture).write_bytes(bytes(128) + bytes(1920 * 1080 * 2 * 2))

        known = set(self._IMAGE_FIXTURES) | set(self._FIXTURE_SIZES) | set(self._PATH_ALIASES)
        runnable, _, expected_failures = self._commands()
        failures, executed = [], 0
        for argv in runnable:
            # Locate the token that follows -i/--input: the --offset example means
            # to skip a header of that file.
            input_value_index = None
            for position, arg in enumerate(argv):
                if arg in ("-i", "--input") and position + 1 < len(argv):
                    input_value_index = position + 1
                    break
            use_offset_fixture = input_value_index is not None and "--offset" in argv

            resolved = []
            for position, arg in enumerate(argv):
                basename = os.path.basename(arg)
                if use_offset_fixture and position == input_value_index:
                    resolved.append(offset_fixture)
                elif basename in known:
                    resolved.append(self._path(basename))
                else:
                    resolved.append(arg)
            with self.subTest(command=" ".join(argv)):
                # Run inside the temp dir: the manual's examples use relative
                # output names, which must not land in the repository root.
                result = _run_cli(*resolved, cwd=self._tmp)
                executed += 1
                if result.returncode != 0:
                    failures.append((" ".join(argv),
                                     result.stderr.strip().splitlines()[:1]))
        self.assertEqual(failures, [], f"documented commands fail: {failures}")
        # Everything the extractor classifies as runnable is executed; nothing is
        # silently dropped.  The only lines excluded are whole-token synopses.
        self.assertEqual(executed, len(runnable))

        # The §8 blocks open with the command that *demonstrates* the failure,
        # annotated in the manual with "# Error: ...".  Those must fail -- if one
        # started succeeding, the text explaining it would be wrong.
        self.assertGreaterEqual(len(expected_failures), 1)
        for argv in expected_failures:
            resolved = [
                self._path(os.path.basename(arg))
                if os.path.basename(arg) in known else arg
                for arg in argv
            ]
            with self.subTest(expected_failure=" ".join(argv)):
                result = _run_cli(*resolved, cwd=self._tmp)
                self.assertEqual(
                    result.returncode, 1,
                    f"the manual says this fails but it did not: {' '.join(argv)}",
                )
                self.assertIn("Error:", result.stderr)


class SingleLineTerminalTests(unittest.TestCase):
    """L-3: the terminal shows one `Error:` line; the log keeps the detail."""

    def test_failure_prints_one_error_line(self):
        proc = _run_cli("convert", "-i", "definitely-missing.png", "-o", "o.raw")
        self.assertEqual(proc.returncode, 1)
        lines = [l for l in proc.stderr.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertTrue(lines[0].startswith("Error: "), lines[0])
        self.assertNotIn("ERROR:", proc.stderr)

    def test_success_keeps_info_progress_lines(self):
        """N-2: progress lines are wanted on the terminal.

        They are the only feedback during a long conversion, and the file log is
        not where a user watches a running command.  Raising the console level to
        WARNING to silence the duplicated ERROR also silenced these, which is why
        the filter (not the level) is the right mechanism.
        """
        tmp = tempfile.mkdtemp(prefix="rawview_log_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = os.path.join(tmp, "a.png")
        import cv2

        cv2.imwrite(src, np.zeros((8, 8, 3), dtype=np.uint8))
        proc = _run_cli("convert", "-i", src, "-o", os.path.join(tmp, "a.raw"),
                        "--raw-type", "RAW8", "--width", "8", "--height", "8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("INFO: Convert:", proc.stderr)
        self.assertIn("INFO: Convert OK:", proc.stderr)
        self.assertIn("Converted:", proc.stdout)

    def test_console_handler_keeps_info_but_drops_error(self):
        """N-2: INFO progress stays on the terminal; only the ERROR duplicate goes.

        Raising the level to WARNING silenced every progress line and made
        ``RAW_VIEW_LOG_LEVEL`` a no-op for the console, which contradicts
        ``docs/cli.md``.  The filter -- not the level -- is the right tool.
        """
        import logging
        import tempfile

        import raw_view.logger as log_mod

        tmp = tempfile.mkdtemp(prefix="rawview_log_unit_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        log_mod._initialized = False
        name = "raw_view_console_filter_test"
        base = logging.getLogger(name)
        for handler in list(base.handlers):
            base.removeHandler(handler)
            handler.close()
        logger = log_mod.setup_logger(name=name, log_dir=tmp)
        console = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)
                   and not getattr(h, "baseFilename", None)]
        self.assertTrue(console, "a console handler must exist")
        handler = console[0]
        self.assertEqual(handler.level, logging.INFO)
        self.assertTrue(handler.filter(logging.LogRecord(
            name, logging.INFO, __file__, 0, "progress", (), None)))
        self.assertTrue(handler.filter(logging.LogRecord(
            name, logging.WARNING, __file__, 0, "careful", (), None)))
        self.assertFalse(handler.filter(logging.LogRecord(
            name, logging.ERROR, __file__, 0, "boom", (), None)))

    def test_log_level_env_still_controls_the_console(self):
        """N-2: RAW_VIEW_LOG_LEVEL must keep affecting console output."""
        tmp = tempfile.mkdtemp(prefix="rawview_n2_env_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        import cv2

        src = os.path.join(tmp, "a.png")
        cv2.imwrite(src, np.zeros((8, 8, 3), dtype=np.uint8))
        argv = ["convert", "-i", src, "-o", os.path.join(tmp, "a.raw"),
                "--raw-type", "RAW8", "--width", "8", "--height", "8"]
        quiet = _run_cli(*argv, env_extra={"RAW_VIEW_LOG_LEVEL": "WARNING"})
        noisy = _run_cli(*argv, env_extra={"RAW_VIEW_LOG_LEVEL": "INFO"})
        self.assertEqual(noisy.returncode, 0, noisy.stderr)
        self.assertIn("INFO:", noisy.stderr)
        self.assertNotIn("INFO:", quiet.stderr)


if __name__ == "__main__":
    unittest.main()
