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


def _run_cli(*argv: str, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess and decode its output as UTF-8.

    Bytes (not ``text=True``): the help pages are UTF-8 by contract, but the
    surrounding Windows console may be cp936/cp1252, and ``text=True`` decodes
    with the locale codec -- which is exactly the failure mode those pages guard
    against.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "raw_view", *argv],
        capture_output=True, env=isolated_env(), cwd=str(_MAIN_DIR),
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

    def test_help_beats_a_mode(self):
        """`view --help` prints the overview instead of starting a decode."""
        for mode in ("view", "convert", "batch"):
            with self.subTest(mode=mode):
                proc = _run_cli(mode, "--help")
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(proc.stdout, self.text)

    def test_usage_mentions_the_help_entries(self):
        self.assertIn("--help-view", self.text.split("MODES")[0])


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
        for token in ("--target RAW", "--target YUV", "--raw-type", "--yuv-type",
                      "--alignment", "--endianness", "--bayer-pattern",
                      "--preview-mode", "--offset"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_view_page_has_generalise_notes(self):
        """YVYU from YUYV, YOnly8 from YOnly10, packed RAW variants, like the ask."""
        text = self.view.stdout
        self.assertIn("YVYU", text)
        self.assertIn("YOnly8", text)
        self.assertIn("RAW10 Packed", text)
        self.assertIn("Generalise", text)

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
        text = self.convert.stdout
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
        self.assertIn("--input-type yuv --input-yuv-type YOnly10", self.convert.stdout)

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

    def test_guidance_offers_a_command_that_actually_works(self):
        """Extract the suggested `view` command and run it."""
        source = self._write_yonly10("grey2.bin", 64, 48)
        proc = self._convert_binary_as_image(
            source, "grey2.png", "--target", "YUV", "--yuv-type", "YOnly10",
            "--width", "64", "--height", "48",
        )
        self.assertEqual(proc.returncode, 1)
        suggestion = next(
            line.strip() for line in proc.stderr.splitlines()
            if " view -i " in line and line.strip().endswith("YOnly10 --width 64 --height 48")
        )
        # shlex, not str.split: the quoted paths contain spaces on Windows.
        argv = shlex.split(suggestion)
        self.assertIn(argv[0], ("raw-view", "python", "raw-view.exe", "raw-view-cli.exe"))
        if argv[0] == "raw-view":
            argv = argv[1:]            # packaged build spelling
        elif argv[:3] == ["python", "-m", "raw_view"]:
            argv = argv[3:]            # source-checkout spelling
        else:                          # pragma: no cover - other packaged names
            self.fail(f"unexpected executable in suggestion: {argv[0]}")
        proc2 = _run_cli(*argv)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertTrue(os.path.isfile(self._path("grey2.png")))

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


if __name__ == "__main__":
    unittest.main()
