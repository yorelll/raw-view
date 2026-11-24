"""YUV transcode + CLI defect-fix regression tests.

Covers two bodies of work:

* **Transcode** (``raw_view/transcode.py``, CLI ``--input-type yuv``, Convert
  dialog "Input type"): all 42 in-family directions round-trip byte-exactly,
  known byte layouts per format, multi-frame files, integer-multiple validation,
  odd-dimension rejection, cross-family rejection, and the CLI/GUI surfaces.
* **CLI defects D1-D7** found by the CLI review: batch convert limit, traceback-
  free error paths, cross-entry output collisions, target-aware error wording,
  ``--bayer-pattern`` validation, batch JSON schema validation and the
  executable-suffix refusal.

GUI tests use the real dialogs and obey the L3 widget-leak guard
(``begin/end_widget_leak_check`` + ``destroy_widget``); no ``deleteLater()``.
"""

from __future__ import annotations

import argparse
import errno
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from _settings_isolation import (
    ensure_test_settings_file,
    isolated_env,
    isolated_qt_material_resources,
)

ensure_test_settings_file()
isolated_qt_material_resources()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

from raw_view import transcode as T  # noqa: E402

#: Process-wide QApplication (see :func:`_ensure_app`).
_APP = None


# ── Layout references ────────────────────────────────────────────────
# Hand-computed expectations for a 4x2 frame.  A 4x2 4:2:0 frame has 1x2 chroma;
# a 4x2 4:2:2 frame has 2x2 chroma (U/V per pixel pair).

W, H = 4, 2

Y_PLANE = np.arange(8, dtype=np.uint8).reshape(H, W)
U_420 = np.array([[100, 101]], dtype=np.uint8)            # h/2 x w/2 = 1x2
V_420 = np.array([[200, 201]], dtype=np.uint8)
U_422 = np.array([[100, 101], [110, 111]], dtype=np.uint8)  # h x w/2 = 2x2
V_422 = np.array([[200, 201], [210, 211]], dtype=np.uint8)


def _norm(path) -> str:
    """Normalize a path for structural comparisons on every platform."""
    return os.path.normpath(os.fspath(path))


def _i420_bytes() -> bytes:
    return b"".join((Y_PLANE.tobytes(), U_420.tobytes(), V_420.tobytes()))


def _nv16_bytes() -> bytes:
    chroma = np.empty((H, W), dtype=np.uint8)
    chroma[:, 0::2], chroma[:, 1::2] = U_422, V_422
    return b"".join((Y_PLANE.tobytes(), chroma.tobytes()))


def _spec(source: str, target: str, *, width: int = W, height: int = H) -> T.TranscodeSpec:
    return T.TranscodeSpec(source, target, width, height)


def _ensure_app():
    """Return the process-wide QApplication, keeping a strong reference.

    Creating the app inside a test method lets it fall out of scope, and Qt then
    destroys every widget it owns.  The module-level ``_APP`` matches the
    convention in ``tests/test_ui_interactions.py``.
    """
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    return _run_cli_env({}, *argv)


def _run_cli_env(extra_env: dict[str, str], *argv: str) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess with file logging pointed at a temp dir.

    Every CLI subprocess test goes through here.  Without the redirect each one
    appends to the developer's real log; several running at once then make
    ``RotatingFileHandler``'s rename fail, and the resulting "--- Logging error
    ---" tracebacks were what leaked onto the CLI's stderr.  ``isolated_env``
    sets ``RAW_VIEW_LOG_DIR`` first, so a test that deliberately passes its own
    value (``LogDirOverrideSafetyTests``) still overrides it.
    """
    env = isolated_env(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "raw_view", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(_PROJECT_ROOT),
    )


# ── 1. All 42 directions round-trip ──────────────────────────────────

class RoundTripTests(unittest.TestCase):
    """A -> B -> A must return the exact original bytes (losslessness proof)."""

    def test_family_sizes_cover_all_42_directions(self):
        self.assertEqual(len(T.YUV422_FORMATS), 6)
        self.assertEqual(len(T.YUV420_FORMATS), 4)
        self.assertEqual(len(list(itertools.permutations(T.YUV422_FORMATS, 2))), 30)
        self.assertEqual(len(list(itertools.permutations(T.YUV420_FORMATS, 2))), 12)

    def test_all_42_directions_round_trip_byte_exact(self):
        rng = np.random.default_rng(20240917)
        checked = 0
        for family in (T.YUV422_FORMATS, T.YUV420_FORMATS):
            for source, target in itertools.permutations(family, 2):
                with self.subTest(source=source, target=target):
                    size = T.frame_size(_spec(source, target))
                    original = bytes(rng.integers(0, 256, size, dtype=np.uint8))
                    there = T.transcode_bytes(original, _spec(source, target))
                    self.assertEqual(len(there), size, "同一家族内帧大小必须一致")
                    back = T.transcode_bytes(there, _spec(target, source))
                    self.assertEqual(
                        back, original,
                        f"{source}->{target}->{source} 必须逐字节还原",
                    )
                    checked += 1
        self.assertEqual(checked, 42, "42 个方向都要覆盖")

    def test_transcode_is_not_a_no_op(self):
        """Conversion must actually change the bytes, or the round-trip is vacuous."""
        gapped = 0
        for family in (T.YUV422_FORMATS, T.YUV420_FORMATS):
            for source, target in itertools.permutations(family, 2):
                size = T.frame_size(_spec(source, target))
                data = bytes(range(size % 256)) if size <= 256 else bytes(
                    (i * 7) % 256 for i in range(size)
                )
                if T.transcode_bytes(data, _spec(source, target)) != data:
                    gapped += 1
        self.assertEqual(gapped, 42, "每个方向都应真正重排字节（非恒等）")

    def test_u_v_swap_directions_differ(self):
        """I420 -> YV12 swaps chroma; the reverse must not return the same frame."""
        data = _i420_bytes()
        yv12 = T.transcode_bytes(data, _spec("I420", "YV12"))
        self.assertNotEqual(yv12, data)
        self.assertEqual(T.transcode_bytes(yv12, _spec("YV12", "I420")), data)

    def test_multi_frame_round_trip_preserves_every_frame(self):
        rng = np.random.default_rng(7)
        size = T.frame_size(_spec("I420", "NV12"))
        frames = [
            bytes(rng.integers(0, 256, size, dtype=np.uint8)) for _ in range(3)
        ]
        out = T.transcode_frames(b"".join(frames), _spec("I420", "NV12"))
        self.assertEqual(len(out), 3)
        back = T.transcode_frames(b"".join(out), _spec("NV12", "I420"))
        self.assertEqual(back, frames)


# ── 2. Known byte layouts (hand-computed) ────────────────────────────

class KnownLayoutTests(unittest.TestCase):
    """Spot-check each format against a hand-built frame, not against itself."""

    def test_i420_plane_order(self):
        self.assertEqual(_i420_bytes(), bytes(range(8)) + bytes([100, 101, 200, 201]))

    def test_i420_to_yv12_swaps_chroma_planes(self):
        out = T.transcode_bytes(_i420_bytes(), _spec("I420", "YV12"))
        self.assertEqual(out, bytes(range(8)) + bytes([200, 201, 100, 101]))

    def test_i420_to_nv12_interleaves_uv_rows(self):
        out = T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12"))
        self.assertEqual(out, bytes(range(8)) + bytes([100, 200, 101, 201]))

    def test_i420_to_nv21_interleaves_vu_rows(self):
        out = T.transcode_bytes(_i420_bytes(), _spec("I420", "NV21"))
        self.assertEqual(out, bytes(range(8)) + bytes([200, 100, 201, 101]))

    def test_nv16_to_yuyv_macropixel_order(self):
        # YUYV macropixel = Y0 U Y1 V, row by row.
        out = T.transcode_bytes(_nv16_bytes(), _spec("NV16", "YUYV"))
        self.assertEqual(out, bytes([
            0, 100, 1, 200,  2, 101, 3, 201,
            4, 110, 5, 210,  6, 111, 7, 211,
        ]))

    def test_nv16_to_uyvy_macropixel_order(self):
        out = T.transcode_bytes(_nv16_bytes(), _spec("NV16", "UYVY"))
        self.assertEqual(out, bytes([
            100, 0, 200, 1,  101, 2, 201, 3,
            110, 4, 210, 5,  111, 6, 211, 7,
        ]))

    def test_nv16_to_yvyu_macropixel_order(self):
        out = T.transcode_bytes(_nv16_bytes(), _spec("NV16", "YVYU"))
        self.assertEqual(out, bytes([
            0, 200, 1, 100,  2, 201, 3, 101,
            4, 210, 5, 110,  6, 211, 7, 111,
        ]))

    def test_nv16_to_vyuy_macropixel_order(self):
        out = T.transcode_bytes(_nv16_bytes(), _spec("NV16", "VYUY"))
        self.assertEqual(out, bytes([
            200, 0, 100, 1,  201, 2, 101, 3,
            210, 4, 110, 5,  211, 6, 111, 7,
        ]))

    def test_nv16_to_nv61_swaps_chroma_within_pairs(self):
        out = T.transcode_bytes(_nv16_bytes(), _spec("NV16", "NV61"))
        self.assertEqual(out, bytes(range(8)) + bytes([
            200, 100, 201, 101, 210, 110, 211, 111,
        ]))

    def test_yuyv_back_to_planes(self):
        """The inverse direction reads macropixels correctly too."""
        yuyv = bytes([
            0, 100, 1, 200,  2, 101, 3, 201,
            4, 110, 5, 210,  6, 111, 7, 211,
        ])
        # YUYV is 4:2:2 and cannot reach 4:2:0, so the reference is NV16.
        self.assertEqual(
            T.transcode_bytes(yuyv, _spec("YUYV", "NV16")), _nv16_bytes()
        )
        # And the packed -> planar direction rebuilds both chroma planes.
        self.assertEqual(
            T.transcode_bytes(yuyv, _spec("YUYV", "NV16")),
            _nv16_bytes(),
        )


# ── 3. Validation / rejection ────────────────────────────────────────

class ValidationTests(unittest.TestCase):

    def test_partial_trailing_frame_is_rejected(self):
        data = _i420_bytes() + b"x"
        with self.assertRaises(T.TranscodeError) as caught:
            T.transcode_frames(data, _spec("I420", "NV12"))
        message = str(caught.exception)
        self.assertIn("not an integer multiple", message)
        self.assertIn("12-byte", message)
        self.assertIn("1 trailing byte", message)

    def test_empty_after_offset_is_rejected(self):
        with self.assertRaises(T.TranscodeError) as caught:
            T.transcode_frames(b"", _spec("I420", "NV12"))
        self.assertIn("no frame data", str(caught.exception))

    def test_frame_count_reports_whole_frames(self):
        self.assertEqual(T.frame_count(len(_i420_bytes()) * 3, _spec("I420", "NV12")), 3)
        with self.assertRaises(T.TranscodeError):
            T.frame_count(len(_i420_bytes()) * 3 + 1, _spec("I420", "NV12"))

    def test_transcode_bytes_rejects_wrong_length(self):
        with self.assertRaises(T.TranscodeError) as caught:
            T.transcode_bytes(_i420_bytes() * 2, _spec("I420", "NV12"))
        self.assertIn("exactly one", str(caught.exception))

    def test_odd_width_rejected_for_4_2_2(self):
        for fmt in T.YUV422_FORMATS:
            with self.subTest(fmt=fmt):
                with self.assertRaises(T.TranscodeError) as caught:
                    T.frame_size(T.TranscodeSpec(fmt, "NV16" if fmt != "NV16" else "YUYV", 5, 2))
                self.assertIn("even width", str(caught.exception))

    def test_odd_width_or_height_rejected_for_4_2_0(self):
        for fmt in T.YUV420_FORMATS:
            with self.subTest(fmt=fmt):
                peer = "NV12" if fmt != "NV12" else "I420"
                for dims in ((5, 2), (4, 3)):
                    with self.assertRaises(T.TranscodeError) as caught:
                        T.frame_size(T.TranscodeSpec(fmt, peer, *dims))
                    self.assertIn("even width/height", str(caught.exception))

    def test_odd_height_allowed_for_4_2_2(self):
        # 4:2:2 only requires an even width; a 4x3 frame is legal.
        self.assertEqual(
            T.frame_size(T.TranscodeSpec("NV16", "YUYV", 4, 3)), 4 * 3 * 2
        )

    def test_cross_family_rejected_with_reason(self):
        for source in T.YUV422_FORMATS:
            for target in T.YUV420_FORMATS:
                with self.subTest(source=source, target=target):
                    reason = T.reject_reason(source, target)
                    self.assertTrue(reason, "跨家族必须给出拒绝原因")
                    self.assertIn("lossy", reason)
                    self.assertFalse(T.can_transcode(source, target))
                    with self.assertRaises(T.TranscodeError):
                        T.transcode_bytes(b"\x00" * 64, _spec(source, target))

    def test_same_format_is_not_a_conversion(self):
        self.assertFalse(T.can_transcode("NV16", "NV16"))
        self.assertIn("no-op", T.reject_reason("NV16", "NV16"))

    def test_yonly_rejected_with_explanation(self):
        reason = T.reject_reason("YOnly12", "NV12")
        self.assertTrue(reason)
        self.assertIn("4:0:0", reason)

    def test_raw_rejected_as_reserved(self):
        reason = T.reject_reason("RAW12", "NV12")
        self.assertTrue(reason)
        self.assertIn("reserved", reason)

    def test_reject_reason_is_none_for_every_legal_direction(self):
        """L1: a supported direction must return ``None``, not a message."""
        for family in (T.YUV422_FORMATS, T.YUV420_FORMATS):
            for source, target in itertools.permutations(family, 2):
                with self.subTest(source=source, target=target):
                    self.assertIsNone(
                        T.reject_reason(source, target),
                        f"{source}->{target} 是合法方向，reject_reason 必须返回 None",
                    )

    def test_unknown_format_rejected(self):
        self.assertTrue(T.reject_reason("YUY2", "NV16"))
        self.assertTrue(T.reject_reason("NV16", "BOGUS"))

    def test_reject_reason_none_iff_can_transcode(self):
        formats = T.YUV422_FORMATS + T.YUV420_FORMATS + ("YOnly12", "RAW12", "NOPE")
        for source in formats:
            for target in formats:
                with self.subTest(source=source, target=target):
                    self.assertEqual(
                        T.can_transcode(source, target),
                        T.reject_reason(source, target) is None,
                    )

    def test_format_names_are_case_insensitive(self):
        self.assertEqual(
            T.transcode_bytes(_i420_bytes(), _spec("i420", "nv12")),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
        )

    def test_family_formats(self):
        self.assertEqual(T.family_formats("NV16"), T.YUV422_FORMATS)
        self.assertEqual(T.family_formats("NV12"), T.YUV420_FORMATS)
        self.assertEqual(T.family_formats("YOnly12"), ())

    def test_offset_is_honoured(self):
        with_header = b"HEADER" + _i420_bytes()
        self.assertEqual(
            T.transcode_bytes(
                with_header, T.TranscodeSpec("I420", "NV12", W, H, offset=6)
            ),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
        )


# ── 4. File-level transcode ──────────────────────────────────────────

class TranscodeFileTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-transcode-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, name: str, data: bytes) -> str:
        path = os.path.join(self._tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_multi_frame_file_is_fully_converted(self):
        src = self._write("in.i420", _i420_bytes() * 2)
        dst = os.path.join(self._tmp, "out.nv12")
        count, size = T.transcode_file(src, dst, _spec("I420", "NV12"))
        self.assertEqual(count, 2)
        self.assertEqual(size, len(_i420_bytes()) * 2)
        self.assertEqual(Path(dst).read_bytes(), _i420_bytes().replace(
            bytes([100, 101, 200, 201]), bytes([100, 200, 101, 201])
        ) * 2)

    def test_invalid_size_writes_nothing(self):
        src = self._write("bad.i420", _i420_bytes() + b"x")
        dst = os.path.join(self._tmp, "never.nv12")
        with self.assertRaises(T.TranscodeError):
            T.transcode_file(src, dst, _spec("I420", "NV12"))
        self.assertFalse(os.path.exists(dst), "校验失败绝不能留下半成品输出")

    def test_cross_family_writes_nothing(self):
        src = self._write("in.i420", _i420_bytes())
        dst = os.path.join(self._tmp, "never.yuyv")
        with self.assertRaises(T.TranscodeError):
            T.transcode_file(src, dst, _spec("I420", "YUYV"))
        self.assertFalse(os.path.exists(dst))


# ── 5. CLI end-to-end ────────────────────────────────────────────────

class CliTranscodeTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-cli-transcode-")
        self._src = os.path.join(self._tmp, "in.i420")
        Path(self._src).write_bytes(_i420_bytes() * 2)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_convert_yuv_end_to_end(self):
        out = os.path.join(self._tmp, "out.yuv")
        proc = _run_cli(
            "convert", "-i", self._src, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "I420",
            "--yuv-type", "NV12", "--width", "4", "--height", "2",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("lossless rearrange", proc.stdout)
        self.assertIn("2 frame(s)", proc.stdout)
        expected = T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")) * 2
        self.assertEqual(Path(out).read_bytes(), expected)

    def test_auto_output_name_names_source_and_target(self):
        """B1-1: CLI YUV -> YUV uses the same {source}_to_{target} tag."""
        from raw_view.__main__ import _run_convert_yuv

        args = argparse.Namespace(
            input=self._src,
            output=None,
            target=None,
            width=4,
            height=2,
            input_yuv_type="I420",
            yuv_type="NV12",
            input_alignment="",
            input_endianness="",
            alignment="msb",
            endianness="little",
            offset=0,
        )
        with mock.patch.object(
            T, "transcode_file", return_value=(1, 6)
        ) as transcode, mock.patch("builtins.print"):
            _run_convert_yuv(args)
        transcode.assert_called_once()
        output_path = transcode.call_args[0][1]
        self.assertEqual(
            os.path.basename(output_path), "in_4x2_I420_to_NV12.yuv"
        )

    def test_image_to_yonly_auto_name_uses_the_canonical_spelling(self):
        """L-2: ``YONLY`` -> ``YOnly`` is intentional and must stay documented.

        Only the auto-derived name changes; an explicit ``-o`` is untouched.
        """
        import raw_view.__main__ as main_mod

        png = os.path.join(self._tmp, "a.png")
        Path(png).write_bytes(b"not-a-real-png")
        args = argparse.Namespace(
            input=png,
            output=None,
            target="YUV",
            raw_type="",
            yuv_type="YOnly",
            width=4,
            height=2,
            bayer_pattern="RGGB",
            source_mode="bayer",
            alignment="msb",
            endianness="little",
            input_type="image",
        )
        out_dir = os.path.join(self._tmp, "convert_out")
        with mock.patch.object(main_mod, "CONVERT_OUT_DIR", out_dir), \
             mock.patch(
                 "raw_view.converter.image_file_to_yuv", return_value=8
             ) as convert, mock.patch("builtins.print"):
            main_mod._run_convert(args)
        convert.assert_called_once()
        output = convert.call_args[0][1]
        self.assertEqual(os.path.basename(output), "a_4x2_YOnly.yuv")

    def test_image_to_yuv_passes_an_empty_raw_type_to_the_template(self):
        """L-3 (CLI): a YUV target must not borrow the RAW type either."""
        import raw_view.__main__ as main_mod
        import raw_view.models as models_mod

        png = os.path.join(self._tmp, "a.png")
        Path(png).write_bytes(b"not-a-real-png")
        args = argparse.Namespace(
            input=png,
            output=None,
            target="YUV",
            raw_type="RAW10",
            yuv_type="NV12",
            width=4,
            height=2,
            bayer_pattern="RGGB",
            source_mode="bayer",
            alignment="msb",
            endianness="little",
            input_type="image",
        )
        captured: dict[str, object] = {}

        def _fake_template(template, *a, **kwargs):
            captured.update(kwargs)
            return os.path.join(self._tmp, "out.yuv")

        with mock.patch.object(
            models_mod, "format_output_template", side_effect=_fake_template
        ), mock.patch(
            "raw_view.converter.image_file_to_yuv", return_value=8
        ), mock.patch("builtins.print"):
            main_mod._run_convert(args)
        self.assertEqual(captured.get("raw_type"), "")

    def test_batch_auto_output_name_names_source_and_target(self):
        """B1-1: the CLI batch writer uses the same template tag."""
        import raw_view.__main__ as main_mod

        out_dir = os.path.join(self._tmp, "out")
        spec = {
            "mode": "convert",
            "target": "YUV",
            "width": 4,
            "height": 2,
            "output_dir": out_dir,
            "files": [{
                "input": self._src,
                "input_type": "yuv",
                "input_yuv_type": "I420",
                "yuv_type": "NV12",
            }],
        }
        batch_path = os.path.join(self._tmp, "auto.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch("raw_view.__main__.sys.exit"), \
             mock.patch("builtins.print"):
            main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )
        expected = os.path.join(out_dir, "in_4x2_I420_to_NV12.yuv")
        self.assertTrue(os.path.isfile(expected), expected)

    def test_missing_width_height_errors(self):
        proc = _run_cli(
            "convert", "-i", self._src, "-o", os.path.join(self._tmp, "x.yuv"),
            "--input-type", "yuv", "--input-yuv-type", "I420", "--yuv-type", "NV12",
        )
        self.assertEqual(proc.returncode, 1)
        combined = proc.stdout + proc.stderr
        self.assertIn("--width and --height required", combined)
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "x.yuv")))

    def test_only_one_missing_dimension_is_named(self):
        proc = _run_cli(
            "convert", "-i", self._src, "-o", os.path.join(self._tmp, "x.yuv"),
            "--input-type", "yuv", "--input-yuv-type", "I420", "--yuv-type", "NV12",
            "--width", "4",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--height required", proc.stdout + proc.stderr)

    def test_cross_family_exit_code_and_message(self):
        proc = _run_cli(
            "convert", "-i", self._src, "-o", os.path.join(self._tmp, "x.yuv"),
            "--input-type", "yuv", "--input-yuv-type", "I420", "--yuv-type", "YUYV",
            "--width", "4", "--height", "2",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("lossy chroma resampling", proc.stderr)

    def test_partial_frame_is_rejected_without_output(self):
        bad = os.path.join(self._tmp, "bad.i420")
        Path(bad).write_bytes(_i420_bytes() + b"x")
        out = os.path.join(self._tmp, "x.yuv")
        proc = _run_cli(
            "convert", "-i", bad, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "I420", "--yuv-type", "NV12",
            "--width", "4", "--height", "2",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not an integer multiple", proc.stderr)
        self.assertFalse(os.path.exists(out))

    def test_explicit_raw_target_is_rejected(self):
        proc = _run_cli(
            "convert", "-i", self._src, "-o", os.path.join(self._tmp, "x.raw"),
            "--input-type", "yuv", "--input-yuv-type", "I420", "--yuv-type", "NV12",
            "--width", "4", "--height", "2", "--target", "RAW",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot target RAW", proc.stderr)

    def test_input_type_raw_is_reserved(self):
        proc = _run_cli(
            "convert", "-i", self._src, "-o", os.path.join(self._tmp, "x.yuv"),
            "--input-type", "raw", "--width", "4", "--height", "2",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reserved", proc.stderr)

    def test_yuv_transcode_over_limit_is_rejected_before_writing(self):
        """D1 for the transcode path: the 512MB guard also covers YUV input."""
        from raw_view.formats import MAX_DECODE_BYTES

        # 65535x65535 NV16 would be ~8.6GB; the file need not exist that large
        # because the size limit is evaluated from the requested geometry.
        out = os.path.join(self._tmp, "big.yuyv")
        proc = _run_cli(
            "convert", "-i", self._src, "-o", out,
            "--input-type", "yuv", "--input-yuv-type", "NV16", "--yuv-type", "YUYV",
            "--width", "65534", "--height", "65532",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("too large", proc.stderr)
        self.assertGreater(65534 * 65532 * 2, MAX_DECODE_BYTES)
        self.assertFalse(os.path.exists(out), "超限拒绝不得留下输出文件")


# ── 6. CLI backward compatibility ────────────────────────────────────

class CliBackwardCompatibilityTests(unittest.TestCase):
    """Pre-existing commands must behave exactly as before this release."""

    def setUp(self):
        import cv2

        self._tmp = tempfile.mkdtemp(prefix="rv-cli-compat-")
        self._png = os.path.join(self._tmp, "in.png")
        cv2.imwrite(self._png, np.zeros((8, 8, 3), dtype=np.uint8))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_convert_image_to_raw_uses_640x480_default(self):
        out = os.path.join(self._tmp, "a.raw")
        proc = _run_cli("convert", "-i", self._png, "-o", out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Dimensions:   640x480", proc.stdout)
        self.assertIn("Target:       RAW", proc.stdout)
        self.assertEqual(os.path.getsize(out), 640 * 480 * 2)  # RAW12

    def test_convert_image_to_yuv_explicit_size(self):
        out = os.path.join(self._tmp, "a.yuv")
        proc = _run_cli(
            "convert", "-i", self._png, "-o", out,
            "--target", "YUV", "--width", "8", "--height", "8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(os.path.getsize(out), 8 * 8 * 2)

    def test_view_raw_round_trip(self):
        raw = os.path.join(self._tmp, "a.raw")
        _run_cli("convert", "-i", self._png, "-o", raw, "--raw-type", "RAW8",
                 "--width", "8", "--height", "8")
        png = os.path.join(self._tmp, "v.png")
        proc = _run_cli(
            "view", "-i", raw, "-o", png,
            "--width", "8", "--height", "8", "--raw-type", "RAW8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.isfile(png))

    def test_view_yuv_round_trip(self):
        yuv = os.path.join(self._tmp, "a.yuv")
        _run_cli("convert", "-i", self._png, "-o", yuv, "-t", "YUV",
                 "--width", "8", "--height", "8")
        png = os.path.join(self._tmp, "v.png")
        proc = _run_cli(
            "view", "-i", yuv, "-o", png, "-t", "YUV",
            "--width", "8", "--height", "8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_batch_image_convert_still_works(self):
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 8,
            "height": 8,
            "output_dir": self._tmp,
            "files": [{"input": self._png, "output": os.path.join(self._tmp, "b.raw")}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 succeeded", proc.stdout)
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "b.raw")))

    def test_batch_help_still_exits_zero(self):
        proc = _run_cli("--batch-help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("UTF-8", proc.stdout)


# ── 7. GUI: Convert dialog input-type behaviour ──────────────────────

def _form_field_sequence(dialog, names: dict) -> list:
    """Named widgets in the order they currently occupy ``dialog._form``.

    Rows are compared by widget identity, not by label text: some rows carry an
    empty label (QFormLayout then has no LabelRole item) and ``insertRow`` may
    re-create label wrappers.  Composite fixed rows (Input/Output file) are not
    in *names* and are skipped; the conditional fields plus the fixed fields
    they anchor against are enough to lock the user-visible order.
    """
    from PyQt5.QtWidgets import QFormLayout

    by_widget_id = {id(widget): name for name, widget in names.items()}
    sequence = []
    for row in range(dialog._form.rowCount()):
        item = dialog._form.itemAt(row, QFormLayout.FieldRole)
        widget = item.widget() if item is not None else None
        name = by_widget_id.get(id(widget))
        if name is not None:
            sequence.append(name)
    return sequence


class _DialogTestBase(unittest.TestCase):
    """Real dialogs, L3 leak guard armed, teardown through destroy_widget."""

    def setUp(self):
        from _qt_cleanup import begin_widget_leak_check

        self._dialogs: list = []
        begin_widget_leak_check()

    def tearDown(self):
        from _qt_cleanup import destroy_widget, end_widget_leak_check

        for widget in getattr(self, "_dialogs", []):
            destroy_widget(widget)
        self._dialogs = []
        end_widget_leak_check(f"{type(self).__name__}.{self._testMethodName}")

    def make_convert_dialog(self):
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        dialog = ConvertDialog(AppSettings())
        self._dialogs.append(dialog)
        app.processEvents()
        return dialog


class ConvertDialogInputTypeTests(_DialogTestBase):

    def test_input_type_choices(self):
        dialog = self.make_convert_dialog()
        labels = [dialog.input_type.itemText(i) for i in range(dialog.input_type.count())]
        self.assertEqual(labels[:2], ["Image", "YUV"])
        self.assertIn("RAW", labels[2], "RAW 预留项应可见（置灰）而非消失")

    def test_raw_input_choice_is_really_disabled(self):
        """D3: "RAW (coming soon)" must be disabled, not merely tooltipped.

        The comment used to claim the entry was greyed while nothing called
        setEnabled(False); selecting it then fell through to the image branch.
        """
        from PyQt5.QtCore import Qt

        dialog = self.make_convert_dialog()
        item = dialog.input_type.model().item(2)
        self.assertIsNotNone(item)
        self.assertFalse(item.isEnabled())
        self.assertEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)

    def test_raw_input_choice_is_greyed_in_both_themes(self):
        """R6: disabled RAW entry must also *look* grey, not just be unclickable.

        qt-material does not recolour a disabled combo item, so the reserved
        entry looked selectable.  The item keeps ``ItemIsEnabled`` cleared and
        is painted with the theme's disabled text token in both themes.
        """
        from PyQt5.QtCore import Qt

        from raw_view.models import THEME_PALETTES

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)
        dialog = self.make_convert_dialog()
        old_theme = dialog._settings.ui_theme
        self.addCleanup(setattr, dialog._settings, "ui_theme", old_theme)
        dialog.show()
        app.processEvents()
        for theme in ("light", "dark"):
            dialog._settings.ui_theme = theme
            _apply_product_theme(app, theme, 13)
            app.processEvents()
            item = dialog.input_type.model().item(2)
            self.assertIsNotNone(item)
            self.assertFalse(item.isEnabled())
            self.assertEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)
            rendered = item.foreground().color().name().lower()
            disabled = THEME_PALETTES[theme]["text_secondary"].lower()
            normal = THEME_PALETTES[theme]["text_color"].lower()
            self.assertEqual(
                rendered, disabled,
                f"{theme}: 预留项必须用禁用文字色绘制，实际 {rendered}",
            )
            self.assertNotEqual(
                rendered, normal,
                f"{theme}: 预留项不能与正常文字同色（看起来仍可选）",
            )

    def test_yuv_sources_exclude_yonly_and_every_one_has_a_target(self):
        """D4: a source format that converts to nothing is a dead end."""
        dialog = self.make_convert_dialog()
        sources = [
            dialog.input_yuv_type.itemText(i)
            for i in range(dialog.input_yuv_type.count())
        ]
        targets = [
            dialog.yuv_type.itemText(i)
            for i in range(dialog.yuv_type.count())
        ]
        self.assertNotIn("YOnly", sources, "YOnly 源会让所有目标置灰（死路）")
        # Membership matters, not list order: reordering the family list is a
        # valid refactor.  Every source still has to reach a target (below).
        self.assertEqual(set(sources), set(T.YUV_TRANSCODE_FORMATS))
        for source in sources:
            self.assertTrue(
                any(T.reject_reason(source, target) is None for target in targets),
                f"{source} 必须至少有一个可转目标",
            )

    def test_input_type_hint_explains_both_modes(self):
        """P1: explain Image vs YUV before the user has to choose."""
        dialog = self.make_convert_dialog()
        image_hint = dialog._input_hint.text().lower()
        self.assertIn("image", image_hint)
        self.assertIn("output size", image_hint)
        dialog.input_type.setCurrentText("YUV")
        yuv_hint = dialog._input_hint.text().lower()
        self.assertIn("lossless", yuv_hint)
        self.assertIn("source frame size", yuv_hint)
        self.assertNotIn("output size", yuv_hint)
        dialog.input_type.setCurrentText("Image")
        self.assertIn("output size", dialog._input_hint.text().lower())

    def test_naming_does_not_claim_image_only(self):
        """D2: title/placeholder/accessible name admit YUV input too."""
        dialog = self.make_convert_dialog()
        self.assertNotIn("image", dialog.windowTitle().lower())
        self.assertEqual(dialog.input_edit.accessibleName(), "Input file")
        text = (
            dialog.input_edit.placeholderText()
            + " "
            + dialog.input_edit.accessibleDescription()
        ).lower()
        self.assertIn("yuv", text)

    def test_browse_input_filter_offers_all_files(self):
        """D1: a YUV source may have any suffix, so All Files must be listed."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        with mock.patch.object(
            convert_mod.QFileDialog, "getOpenFileName", return_value=("", "")
        ) as picker:
            dialog._browse_input()
        args = picker.call_args[0]
        self.assertIn("All Files", args[3])
        self.assertNotIn("image", args[1].lower())

    def test_variant_page_offers_yuv_targets_instead_of_disabling_them(self):
        """V4: the Variant page has a real YUV mode, not a greyed-out panel.

        The old behaviour greyed the whole variant generator for a YUV input;
        the product now asks for lossless same-family fan-out (one YUYV file ->
        UYVY/YVYU/VYUY/NV16/NV61 at once), so the YUV card replaces the image
        selector instead of disabling everything.
        """
        import raw_view.gui.dialogs.convert as convert_mod
        from raw_view.models import AppSettings

        _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = convert_mod.ConvertDialog(settings)
        self._dialogs.append(dialog)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        self.assertFalse(page.image_card.isVisibleTo(page))
        self.assertTrue(page.yuv_card.isVisibleTo(page))
        self.assertEqual(
            set(page.yuv_selector.selected_formats()),
            set(T.YUV422_FORMATS) - {"YUYV"},
        )

    def test_image_is_default_and_keeps_output_semantics(self):
        dialog = self.make_convert_dialog()
        self.assertEqual(dialog.input_type.currentText(), "Image")
        self.assertEqual(dialog.width.value(), 2560)
        self.assertEqual(dialog.height.value(), 1440)
        self.assertTrue(dialog.target_type.isEnabled())
        self.assertFalse(dialog.input_yuv_type.isVisibleTo(dialog))

    def test_switching_to_yuv_uses_default_dimensions(self):
        """Requirement change: YUV input starts at 2560x1440, not 1x1.

        Width/Height are the SOURCE frame size for a YUV input; the dialog now
        offers the same documented 2560x1440 initial guess as the image path.
        A file whose size does not match is rejected when Convert starts.
        """
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.width.value(), 2560)
        self.assertEqual(dialog.height.value(), 1440)
        dialog.input_type.setCurrentText("Image")
        self.assertEqual(dialog.width.value(), 2560)
        self.assertEqual(dialog.height.value(), 1440)

    def test_yuv_input_pins_target_to_yuv_and_enables_format_dropdown(self):
        dialog = self.make_convert_dialog()
        dialog.target_type.setCurrentText("RAW")
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.target_type.currentText(), "YUV")
        self.assertFalse(dialog.target_type.isEnabled())
        self.assertTrue(dialog.yuv_type.isEnabled())
        self.assertTrue(dialog.input_yuv_type.isVisibleTo(dialog))

    def test_yuv_input_hides_raw_only_controls(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        for widget in (
            dialog.raw_type, dialog.raw_source_mode, dialog.bayer_pattern,
            dialog.bit_depth_combo,
        ):
            self.assertFalse(widget.isVisibleTo(dialog))

    def test_non_convertible_targets_are_greyed_not_hidden(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        model = dialog.yuv_type.model()
        enabled = {}
        for index in range(dialog.yuv_type.count()):
            enabled[dialog.yuv_type.itemText(index)] = model.item(index).isEnabled()
        # Same family stays convertible...
        for name in T.YUV422_FORMATS:
            if name != "NV16":
                self.assertTrue(enabled[name], f"{name} 应可转")
        # ...other families / YOnly / same-format stay listed but disabled.
        for name in T.YUV420_FORMATS + ("YOnly", "NV16"):
            self.assertFalse(enabled[name], f"{name} 应置灰")
        self.assertTrue(
            any(not flag for flag in enabled.values()),
            "不可转目标必须仍然出现在列表里（置灰），不是被移除",
        )
        self.assertTrue(
            dialog.yuv_type.itemData(dialog.yuv_type.findText("NV12"), 3),
            "置灰项应带 reject_reason 作为 tooltip",
        )

    def test_switch_to_420_source_regreys_targets(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV12")
        model = dialog.yuv_type.model()
        for name in T.YUV420_FORMATS:
            if name != "NV12":
                self.assertTrue(model.item(dialog.yuv_type.findText(name)).isEnabled())
        for name in T.YUV422_FORMATS:
            self.assertFalse(model.item(dialog.yuv_type.findText(name)).isEnabled())

    def test_selected_target_falls_back_to_a_convertible_one(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.yuv_type.setCurrentText("NV12")  # not convertible from NV16
        dialog.input_yuv_type.setCurrentText("NV61")
        self.assertTrue(
            T.can_transcode("NV61", dialog.yuv_type.currentText()),
            f"选中的目标必须可转，实际 {dialog.yuv_type.currentText()}",
        )

    def test_back_to_image_reenables_all_targets(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.input_type.setCurrentText("Image")
        model = dialog.yuv_type.model()
        self.assertTrue(all(model.item(i).isEnabled() for i in range(dialog.yuv_type.count())))
        self.assertTrue(dialog.target_type.isEnabled())

    def test_switching_to_yuv_hides_and_disables_align_endian(self):
        """M-1: Alignment/Endianness must not survive the switch to YUV input.

        ``is_raw`` used to be read before the target was pinned to YUV; the
        re-entrant sync triggered by the pin was swallowed by the guard, so the
        stale ``is_raw=True`` kept both dropdowns visible and enabled even
        though transcoding ignores them.
        """
        dialog = self.make_convert_dialog()
        self.assertEqual(dialog.target_type.currentText(), "RAW")
        self.assertTrue(dialog.align.isVisibleTo(dialog))
        self.assertTrue(dialog.align.isEnabled())
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.target_type.currentText(), "YUV")
        for widget in (dialog.align, dialog.endian):
            self.assertFalse(widget.isVisibleTo(dialog), "YUV 输入下不得残留控件")
            self.assertFalse(widget.isEnabled(), "不可见的控件不得可操作")

    def test_conditional_row_labels_hide_with_their_fields(self):
        """H-1: a hidden conditional field must not leave a dangling label.

        QFormLayout only hides the field.  Every conditional row is checked as
        a (field, label) pair across the meaningful states, so a regression in
        any single row fails here.
        """
        dialog = self.make_convert_dialog()

        def expected(*, input_yuv=False, raw=False, yonly=False, bayer=True):
            show_raw = raw and not input_yuv
            show_bit = (not raw) and yonly and not input_yuv
            show_align = raw or show_bit
            return {
                dialog.input_yuv_type: input_yuv,
                dialog.offset: input_yuv,
                dialog.raw_type: show_raw,
                dialog.bit_depth_combo: show_bit,
                dialog.align: show_align,
                dialog.endian: show_align,
                dialog.raw_source_mode: show_raw,
                dialog.bayer_pattern: show_raw and bayer,
            }

        names = {
            "input_type": dialog.input_type,
            "input_hint": dialog._input_hint,
            "input_yuv_type": dialog.input_yuv_type,
            "offset": dialog.offset,
            "target": dialog.target_type,
            "raw_type": dialog.raw_type,
            "yuv_type": dialog.yuv_type,
            "bit_depth": dialog.bit_depth_combo,
            "align": dialog.align,
            "endian": dialog.endian,
            "raw_source": dialog.raw_source_mode,
            "bayer": dialog.bayer_pattern,
            "width": dialog.width,
            "height": dialog.height,
        }

        def expected_order(*, input_yuv=False, raw=False, yonly=False, bayer=True):
            """Canonical order declared by ``_cond_row_groups`` in __init__."""
            show_raw = raw and not input_yuv
            show_bit = (not raw) and yonly and not input_yuv
            show_align = raw or show_bit
            order = ["input_type", "input_hint"]
            if input_yuv:
                order += ["input_yuv_type", "offset"]
            order += ["target"]
            if show_raw:
                order += ["raw_type"]
            order += ["yuv_type"]
            if show_bit:
                order += ["bit_depth"]
            if show_align:
                order += ["align", "endian"]
            if show_raw:
                order += ["raw_source"]
                if bayer:
                    order += ["bayer"]
            order += ["width", "height"]
            return order

        def check(state, **kwargs):
            wanted = expected(**kwargs)
            for field, want in wanted.items():
                label = dialog._cond_row_labels[field]
                self.assertEqual(
                    field.isVisibleTo(dialog), want,
                    f"{state}: field {label.text()!r} visibility",
                )
                self.assertEqual(
                    label.isVisibleTo(dialog), want,
                    f"{state}: label {label.text()!r} left dangling",
                )
            # L-1: hidden rows must be *removed*, not merely hidden.  With
            # setVisible only, QFormLayout keeps one 6px gap per hidden row.
            # Fixed rows: Input file, Input type, hint, Output file,
            # Target, YUV format, Width, Height (8).  The Convert page no
            # longer has an "Output folder" row (U-2) and the former YUV-note
            # row was removed; its explanation lives in Format Help only.
            visible_cond = sum(1 for want in wanted.values() if want)
            self.assertEqual(
                dialog._form.rowCount(), 8 + visible_cond,
                f"{state}: hidden rows must not reserve layout height",
            )
            # L-1 (0.5.10.2): lock the actual row *order*, not just the count.
            # Deleting the ``anchor_row += 1`` makes rows inserted in the same
            # group land in reverse order (e.g. Source offset before Input YUV
            # format) while every visibility/rowCount assertion above still
            # passes.
            self.assertEqual(
                _form_field_sequence(dialog, names),
                expected_order(**kwargs),
                f"{state}: conditional rows must keep their declared order",
            )

        # Default: Image input, Target=RAW, bayer source.
        self.assertEqual(dialog.target_type.currentText(), "RAW")
        check("default Image/RAW/bayer", raw=True, bayer=True)

        dialog.target_type.setCurrentText("YUV")  # yuv_type default is YOnly
        check("Image/YUV/YOnly", raw=False, yonly=True)

        dialog.yuv_type.setCurrentText("NV12")
        check("Image/YUV/NV12", raw=False, yonly=False)

        dialog.input_type.setCurrentText("YUV")
        check("YUV input", input_yuv=True, raw=False, yonly=False)

        dialog.input_type.setCurrentText("Image")
        dialog.target_type.setCurrentText("RAW")
        dialog.raw_source_mode.setCurrentText("gray")
        check("Image/RAW/gray", raw=True, bayer=False)

    def test_sync_keeps_keyboard_focus_on_a_still_visible_field(self):
        """M-1 (0.5.10.2): hiding every conditional row steals focus.

        ``_apply_cond_rows`` used to detach *all* conditional rows and then
        re-insert the visible ones.  ``setVisible(False)`` on the focused
        widget moves focus to the next widget in the chain and re-showing does
        not restore it; for Convert the focus jumped from ``RAW source`` to the
        Width spin box, so a following Up/Down silently edited Width.
        """
        app = _ensure_app()
        dialog = self.make_convert_dialog()
        dialog.show()
        app.processEvents()
        # ``raw_source_mode`` stays visible in the post-sync state (Image/RAW),
        # so a correct sync must leave it untouched.
        dialog.raw_source_mode.setFocus()
        app.processEvents()
        self.assertIs(
            app.focusWidget(), dialog.raw_source_mode,
            "precondition: the field must own focus before the sync",
        )
        dialog._sync_controls_locked()
        app.processEvents()
        self.assertIs(
            app.focusWidget(), dialog.raw_source_mode,
            "a still-visible field must keep keyboard focus across a sync",
        )

    def test_sync_keeps_an_open_yuv_source_popup_open(self):
        """M-1 (0.5.10.2): hiding the focused combo closes its popup.

        ``QComboBox`` emits ``currentTextChanged`` while the user arrows
        through the popup; the re-entrant sync used to hide the combo, which
        closes the popup after the very first Down key press.  Keeping the row
        where it is is the only fix -- a focus restore cannot reopen the popup.
        """
        app = _ensure_app()
        dialog = self.make_convert_dialog()
        dialog.show()
        dialog.input_type.setCurrentText("YUV")
        app.processEvents()
        combo = dialog.input_yuv_type
        self.assertTrue(combo.isVisible())
        combo.showPopup()
        app.processEvents()
        self.assertTrue(
            combo.view().isVisible(), "precondition: the popup must be open",
        )
        try:
            dialog._sync_controls_locked()
            app.processEvents()
            self.assertTrue(
                combo.view().isVisible(),
                "a sync must not close the YUV source format popup",
            )
        finally:
            combo.hidePopup()
            app.processEvents()

    def test_switching_input_type_resets_source_offset(self):
        """L-2: an offset belongs to the previous input kind; switching clears it."""
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.offset.setValue(6)
        self.assertEqual(dialog.offset.value(), 6)
        dialog.input_type.setCurrentText("Image")
        self.assertEqual(dialog.offset.value(), 0, "切换输入类型必须清空 offset")
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.offset.value(), 0)
        # Changing the source layout keeps the offset: still a YUV source.
        dialog.offset.setValue(6)
        dialog.input_yuv_type.setCurrentText("NV16")
        self.assertEqual(dialog.offset.value(), 6, "改源格式不应清空 offset")


def _contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x relative-luminance contrast ratio for two ``#RRGGBB`` colours."""

    def _channel(value: int) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def _luminance(color: str) -> float:
        color = color.lstrip("#")
        r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)

    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _apply_product_theme(app, theme: str, font_size: int) -> None:
    """Mirror ``MainWindow._apply_theme_impl`` exactly.

    The hint background is a *layered* result: the qt-material base theme
    paints the QDialog window (#E6E8EE / #171A24), then our overlay only
    recolours the label.  Applying ``build_ui_stylesheet`` alone (as the old
    test did) therefore used the wrong background and hid a sub-4.5:1 hint
    (0.5.10.1 review M-2).
    """
    from qt_material import apply_stylesheet

    from raw_view.gui.app import resource_path
    from raw_view.models import MATERIAL_EXTRA, THEME_XML, build_ui_stylesheet

    theme_file = resource_path(
        os.path.join("assets", THEME_XML.get(theme, THEME_XML["dark"]))
    )
    apply_stylesheet(
        app, theme=theme_file, invert_secondary=False, extra=MATERIAL_EXTRA
    )
    app.setStyleSheet(
        f"{app.styleSheet()}\n{build_ui_stylesheet(theme, font_size)}"
    )


def _rendered_background(widget, ancestor) -> str:
    """Most common pixel colour behind *widget* in the real product render.

    Sampling the widget's own rect (rather than reading a palette token or
    guessing ``panel_bg``) follows the actual paint stack: the hint label is
    transparent, so what the user sees is whatever its card/dialog painted.
    """
    from collections import Counter

    from PyQt5.QtCore import QPoint

    image = ancestor.grab().toImage()
    rect = widget.rect()
    step_x = max(1, rect.width() // 24)
    step_y = max(1, rect.height() // 12)
    samples = Counter()
    for x in range(1, rect.width(), step_x):
        for y in range(1, rect.height(), step_y):
            point = widget.mapTo(ancestor, QPoint(x, y))
            samples[image.pixelColor(point).name()] += 1
    return samples.most_common(1)[0][0].lower()


class DialogHintThemeTests(_DialogTestBase):
    """M-4 / 0.5.10.1 M-2: hint text follows theme, font size and real contrast."""

    def test_input_hint_uses_theme_token_and_follows_font_size(self):
        from PyQt5.QtGui import QPalette

        from raw_view.models import THEME_PALETTES

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)

        dialog = self.make_convert_dialog()
        dialog.show()
        app.processEvents()
        self.assertEqual(
            dialog._input_hint.styleSheet(), "",
            "提示文字不得用 widget 级 QSS 硬编码颜色/字号",
        )
        for theme in ("light", "dark"):
            token = THEME_PALETTES[theme]["hint_text"]
            for font_size in (10, 13, 20, 24):
                _apply_product_theme(app, theme, font_size)
                app.processEvents()
                hint = dialog._input_hint
                hint.ensurePolished()
                self.assertEqual(
                    hint.palette().color(QPalette.WindowText).name(),
                    token.lower(),
                    f"{theme}/{font_size}: 提示文字必须使用主题 hint_text",
                )
                self.assertEqual(
                    hint.font().pixelSize(),
                    max(font_size - 2, 9),
                    f"{theme}/{font_size}: 提示字号必须跟随 ui/font_size "
                    "且比正文小 2 号（下限 9）",
                )
                # The hint must really be smaller than body text, not merely
                # "1 px different" (user: at least one, ideally two steps).
                body_size = (
                    dialog._form.labelForField(dialog.input_type)
                    .font().pixelSize()
                )
                self.assertEqual(
                    body_size, font_size,
                    f"{theme}/{font_size}: 正文基准字号应等于 ui/font_size",
                )
                self.assertLess(
                    hint.font().pixelSize(), body_size,
                    f"{theme}/{font_size}: 提示必须小于正文字号",
                )
                background = _rendered_background(hint, dialog)
                # If this ever equals panel_bg the test has lost its real
                # background basis (the old 3.95:1 false-green, M-2).
                self.assertNotEqual(
                    background, THEME_PALETTES[theme]["panel_bg"].lower(),
                    f"{theme}/{font_size}: 必须按提示的实际绘制背景断言，"
                    "不能用 panel_bg",
                )
                ratio = _contrast_ratio(token, background)
                self.assertGreaterEqual(
                    ratio, 4.5,
                    f"{theme}/{font_size}: {token} on real background "
                    f"{background} = {ratio:.2f}:1 低于 WCAG AA",
                )

    def test_batch_empty_hint_uses_theme_token(self):
        from PyQt5.QtGui import QPalette

        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings, THEME_PALETTES

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)

        dialog = BatchConvertDialog(AppSettings())
        self._dialogs.append(dialog)
        dialog.show()
        app.processEvents()
        self.assertEqual(dialog._empty_hint.styleSheet(), "")
        for theme in ("light", "dark"):
            token = THEME_PALETTES[theme]["hint_text"]
            for font_size in (10, 13, 20, 24):
                _apply_product_theme(app, theme, font_size)
                app.processEvents()
                for hint in (dialog._empty_hint, dialog._input_hint):
                    hint.ensurePolished()
                    self.assertEqual(
                        hint.palette().color(QPalette.WindowText).name(),
                        token.lower(),
                        f"{theme}/{font_size}: {hint.objectName()} 必须使用 hint_text",
                    )
                    self.assertEqual(
                        hint.font().pixelSize(),
                        max(font_size - 2, 9),
                        f"{theme}/{font_size}: {hint.objectName()} 字号必须"
                        "比正文小 2 号（下限 9），两个提示保持一致",
                    )
                    background = _rendered_background(hint, dialog)
                    ratio = _contrast_ratio(token, background)
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"{theme}/{font_size}: {hint.objectName()} on real "
                        f"background {background} = {ratio:.2f}:1 低于 WCAG AA",
                    )
                # The empty-table hint sits directly on the dialog window, not
                # on a card; assert its real background is not panel_bg.
                empty_bg = _rendered_background(dialog._empty_hint, dialog)
                self.assertNotEqual(
                    empty_bg, THEME_PALETTES[theme]["panel_bg"].lower(),
                    f"{theme}/{font_size}: 空表提示背景不是 panel_bg",
                )


class ConvertDialogYuvConvertTests(_DialogTestBase):
    """Drive the actual conversion path (message boxes suppressed)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="rv-gui-transcode-")
        self._src = os.path.join(self._tmp, "in.i420")
        Path(self._src).write_bytes(_i420_bytes())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_offset_control_only_for_yuv_input(self):
        """D5: the offset field is meaningful for YUV input only."""
        dialog = self.make_convert_dialog()
        self.assertFalse(dialog.offset.isVisibleTo(dialog))
        self.assertFalse(dialog.offset.isEnabled())
        # L-3: the row label must disappear together with the field.
        self.assertFalse(dialog._offset_label.isVisibleTo(dialog))
        dialog.input_type.setCurrentText("YUV")
        self.assertTrue(dialog.offset.isVisibleTo(dialog))
        self.assertTrue(dialog.offset.isEnabled())
        self.assertTrue(dialog._offset_label.isVisibleTo(dialog))
        self.assertEqual(dialog.offset.value(), 0)
        dialog.input_type.setCurrentText("Image")
        self.assertFalse(dialog.offset.isVisibleTo(dialog))
        self.assertFalse(dialog.offset.isEnabled())
        self.assertFalse(dialog._offset_label.isVisibleTo(dialog))

    def test_changing_offset_refreshes_preview(self):
        """L-1: editing offset must re-run the preview's frame count.

        The file is 6 header bytes + one 12-byte I420 frame.  Without an offset
        the preview must report the trailing bytes; with offset=6 it must report
        exactly one frame.
        """
        header = b"HEADER"
        Path(self._src).write_bytes(header + _i420_bytes())
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._src)

        self.assertIn("trailing", dialog._preview_info.text(),
                      "offset=0 时应提示非整帧")
        dialog.offset.setValue(len(header))
        self.assertIn("1 frame(s)", dialog._preview_info.text(),
                      "改 offset 后预览必须刷新帧数")
        self.assertIn(f"{len(_i420_bytes()):,} B", dialog._preview_info.text())
        dialog.offset.setValue(0)
        self.assertIn("trailing", dialog._preview_info.text(),
                      "offset 清零后预览必须回到非整帧提示")

    def test_offset_skips_leading_bytes_and_affects_output(self):
        """D5: a header before the frame must be skippable (CLI parity)."""
        import raw_view.gui.dialogs.convert as convert_mod

        header = b"HEADER"  # 6 bytes; without offset the size is not a frame multiple
        Path(self._src).write_bytes(header + _i420_bytes())
        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "out.nv12")
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.offset.setValue(len(header))
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(out)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        critical.assert_not_called()
        self.assertEqual(
            Path(out).read_bytes(),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
            "offset 应跳过文件头，输出等价于无头帧的转换",
        )
        self.assertEqual(Path(out).stat().st_size, len(_i420_bytes()))

    def test_convert_writes_transcoded_file(self):
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "out.nv12")
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(out)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        critical.assert_not_called()
        self.assertEqual(
            Path(out).read_bytes(),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
        )

    def test_over_limit_frame_is_rejected_before_writing(self):
        """L2: the GUI must enforce the same 512MB limit the CLI does."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "huge.yuyv")
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.width.setValue(16384)
        dialog.height.setValue(16385)
        dialog.yuv_type.setCurrentText("YUYV")
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(out)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        self.assertTrue(critical.called, "GUI 也必须拒绝超限帧")
        self.assertIn("too large", critical.call_args[0][2])
        self.assertFalse(os.path.exists(out), "超限拒绝不得留下输出文件")

    def test_limit_is_checked_before_overwrite_prompt_and_mkdir(self):
        """N2: an over-limit frame must be refused *first*.

        The sequence used to be "ask to overwrite, then report too large" and it
        left empty parent directories behind.  Both are observable side effects
        of an operation that can never succeed.
        """
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        nested_out = os.path.join(self._tmp, "a", "b", "c", "huge.yuyv")
        Path(nested_out).parent.mkdir(parents=True, exist_ok=True)
        Path(nested_out).write_bytes(b"existing")  # triggers the overwrite prompt
        order: list[str] = []

        def _spy_collision(path):
            order.append("collision_prompt")
            return path

        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.width.setValue(16384)
        dialog.height.setValue(16385)
        dialog.yuv_type.setCurrentText("YUYV")
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(nested_out)
        with mock.patch.object(dialog, "_confirm_output_collision", _spy_collision), \
             mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        self.assertTrue(critical.called)
        self.assertEqual(order, [], "超限必须早于覆盖询问")
        self.assertEqual(Path(nested_out).read_bytes(), b"existing", "不得覆盖已有文件")

    def test_limit_rejection_creates_no_directories(self):
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        deep = os.path.join(self._tmp, "x", "y", "z", "huge.yuyv")
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.width.setValue(16384)
        dialog.height.setValue(16385)
        dialog.yuv_type.setCurrentText("YUYV")
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(deep)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical"):
            dialog._do_convert()
        self.assertFalse(
            os.path.exists(os.path.join(self._tmp, "x")),
            "超限拒绝不得留下空目录",
        )

    def test_bad_size_reports_clear_error_and_writes_nothing(self):
        import raw_view.gui.dialogs.convert as convert_mod

        Path(self._src).write_bytes(_i420_bytes() + b"x")
        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "out.nv12")
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._src)
        dialog.output_edit.setText(out)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        self.assertTrue(critical.called, "非整帧输入必须报错")
        message = critical.call_args[0][2]
        self.assertIn("not an integer multiple", message)
        self.assertFalse(os.path.exists(out))


    def test_variant_button_stays_disabled_if_input_switches_to_yuv_mid_run(self):
        """V4: switching the Variant page to YUV mid-run must not wedge it.

        YUV is now a supported Variant mode, so the old "button stays greyed"
        rule no longer applies; what must hold is that the final button state is
        derived from the *current* input type and that the run finishes.
        """
        import raw_view.gui.dialogs.variant_convert as variant_mod
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = ConvertDialog(settings)
        self._dialogs.append(dialog)
        page = dialog._variant_page
        page.input_edit.setText(self._src)

        def _switch_to_yuv_and_finish(*_args, **_kwargs):
            page.input_type.setCurrentText("YUV")
            return []

        with mock.patch.object(variant_mod, "plan_image_variants", return_value=["p1"]), \
             mock.patch.object(
                 variant_mod, "generate_image_variants",
                 side_effect=_switch_to_yuv_and_finish,
             ), \
             mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()

        self.assertEqual(page.input_type.currentText(), "YUV")
        self.assertFalse(page._generating)
        self.assertTrue(
            page.generate_button.isEnabled(),
            "YUV 也是受支持的 Variant 模式，结束后按钮应可用",
        )
        self.assertEqual(page.generate_button.text(), "Generate Variants")

    def test_variant_button_stays_disabled_and_cannot_reenter_mid_run(self):
        """0.5.10.1 M-1: editing a control mid-run must not re-enable the button.

        ``generate()`` pumps events while writing, so any control edit reaches
        the page's sync.  Without the ``_generating`` flag the sync re-enabled
        the button (text still "Generating 0/1…"), and a second click started
        an overlapping generation writing the same folder.
        """
        import raw_view.gui.dialogs.variant_convert as variant_mod
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = ConvertDialog(settings)
        self._dialogs.append(dialog)
        page = dialog._variant_page
        page.input_edit.setText(self._src)

        generate_calls: list[int] = []
        observed: dict[str, object] = {}

        def _mid_run(*_args, **_kwargs):
            generate_calls.append(1)
            # A user-editable control that triggers _sync_controls.
            page.raw_source_mode.setCurrentText("gray")
            observed["enabled"] = page.generate_button.isEnabled()
            observed["text"] = page.generate_button.text()
            # A second click must be a no-op (re-entry guard).
            page.generate()
            return []

        with mock.patch.object(
            variant_mod, "plan_image_variants", return_value=["p1"]
        ) as plan, mock.patch.object(
            variant_mod, "generate_image_variants", side_effect=_mid_run
        ), mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()

        self.assertFalse(
            observed["enabled"],
            "生成期间改控件后按钮必须仍为 disabled",
        )
        self.assertEqual(observed["text"], "Generating 0/1…")
        self.assertEqual(len(generate_calls), 1, "不得重入第二轮生成")
        self.assertEqual(plan.call_count, 1, "重入时不得重新规划")
        # Normal completion restores the image-input state.
        self.assertTrue(page.generate_button.isEnabled())
        self.assertEqual(page.generate_button.text(), "Generate Variants")


class ConvertOutputPathAndCollisionTests(_DialogTestBase):
    """R3: unified output naming, same-directory option, collision checkbox,
    and the Windows "file is in use" guidance."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="rv-gui-output-")
        self._png = os.path.join(self._tmp, "a.png")
        Path(self._png).write_bytes(b"not-a-real-png")
        self._yuv = os.path.join(self._tmp, "a.yuv")
        Path(self._yuv).write_bytes(_i420_bytes())

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _name(self, path):
        return os.path.basename(path)

    def test_default_output_is_next_to_the_input_and_naming_is_unified(self):
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        self.assertEqual(
            _norm(dialog.output_edit.text()),
            _norm(os.path.join(self._tmp, "a_2560x1440_RGGB10.raw")),
        )
        # Image -> YOnly: YOnly<bit> token and .yuv extension.
        dialog.target_type.setCurrentText("YUV")
        dialog.yuv_type.setCurrentText("YOnly")
        dialog.bit_depth_combo.setCurrentText("12")
        self.assertEqual(
            self._name(dialog.output_edit.text()),
            "a_2560x1440_YOnly12.yuv",
        )

    def test_yuv_source_uses_its_layout_in_the_name(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("YUYV")
        dialog.yuv_type.setCurrentText("NV16")
        dialog.input_edit.setText(self._yuv)
        self.assertEqual(
            _norm(dialog.output_edit.text()),
            _norm(os.path.join(self._tmp, "a_2560x1440_YUYV_to_NV16.yuv")),
        )

    def test_same_input_different_bayer_and_size_never_collide(self):
        """0.5.11 L-3: the default template must keep variants apart."""
        dialog = self.make_convert_dialog()
        dialog.input_edit.setText(self._png)
        dialog.raw_type.setCurrentText("RAW10")
        dialog.raw_source_mode.setCurrentText("bayer")
        dialog.bayer_pattern.setCurrentText("RGGB")
        rggb = dialog.output_edit.text()
        dialog.bayer_pattern.setCurrentText("BGGR")
        bggr = dialog.output_edit.text()
        dialog.raw_source_mode.setCurrentText("gray")
        gray = dialog.output_edit.text()
        dialog.raw_source_mode.setCurrentText("bayer")
        dialog.bayer_pattern.setCurrentText("RGGB")
        dialog.width.setValue(1920)
        small = dialog.output_edit.text()

        names = [self._name(p) for p in (rggb, bggr, gray, small)]
        self.assertEqual(len(set(names)), 4, names)
        self.assertEqual(names[0], "a_2560x1440_RGGB10.raw")
        self.assertEqual(names[1], "a_2560x1440_BGGR10.raw")
        self.assertEqual(names[2], "a_2560x1440_RAW10.raw")
        self.assertEqual(names[3], "a_1920x1440_RGGB10.raw")

    def test_auto_path_follows_input_but_never_clobbers_a_manual_edit(self):
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        other = os.path.join(self._tmp, "b.png")
        Path(other).write_bytes(b"x")
        dialog.input_edit.setText(other)
        self.assertEqual(
            self._name(dialog.output_edit.text()), "b_2560x1440_RGGB10.raw",
        )
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()  # what textEdited emits for a real edit
        dialog.raw_type.setCurrentText("RAW12")
        self.assertEqual(
            dialog.output_edit.text(), custom,
            "用户手动改过的输出路径不得被自动命名覆盖",
        )

    def test_editing_the_output_back_to_the_auto_value_resumes_following(self):
        """N-1: the follow/keep decision is a live value comparison, not a flag.

        The old implementation cleared ``_auto_output_path`` on the first edit,
        so a user who typed a custom path and then deleted it back to the
        automatic value could never get automatic updates again.
        """
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        auto = dialog.output_edit.text()
        self.assertEqual(self._name(auto), "a_2560x1440_RGGB10.raw")

        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()
        dialog.raw_type.setCurrentText("RAW12")
        self.assertEqual(dialog.output_edit.text(), custom, "前置条件：手动值被保留")

        # Delete the edit back to the automatic value, then change a parameter.
        dialog.output_edit.setText(auto)
        dialog._on_output_edited()
        dialog.raw_type.setCurrentText("RAW8")
        self.assertEqual(
            self._name(dialog.output_edit.text()), "a_2560x1440_RGGB8.raw",
            "改回自动路径后，后续参数变化必须重新跟随（N-1）",
        )

    def test_switching_input_file_resets_a_manual_output_name(self):
        """N-1: an old custom name must never survive an input-file switch."""
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)  # a.png
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()

        other = os.path.join(self._tmp, "b.png")
        Path(other).write_bytes(b"x")
        dialog.input_edit.setText(other)
        self.assertEqual(
            _norm(dialog.output_edit.text()),
            _norm(os.path.join(self._tmp, "b_2560x1440_RGGB10.raw")),
            "换输入文件后必须无条件重置为新的自动路径",
        )
        self.assertNotIn("my_name", dialog.output_edit.text())
        # ...and the new input keeps following parameter changes.
        dialog.raw_type.setCurrentText("RAW12")
        self.assertEqual(
            self._name(dialog.output_edit.text()), "b_2560x1440_RGGB12.raw",
        )

    def test_different_input_files_reset_in_both_directions(self):
        """L-1 guard: normalising spellings must not blur real file switches."""
        other = os.path.join(self._tmp, "b.png")
        Path(other).write_bytes(b"x")
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()

        dialog.input_edit.setText(other)
        self.assertEqual(
            self._name(dialog.output_edit.text()), "b_2560x1440_RGGB10.raw",
            "a.png → b.png 是换文件，必须重置",
        )
        self.assertNotIn("my_name", dialog.output_edit.text())

        dialog.output_edit.setText(custom)
        dialog._on_output_edited()
        dialog.input_edit.setText(self._png)
        self.assertEqual(
            self._name(dialog.output_edit.text()), "a_2560x1440_RGGB10.raw",
            "b.png → a.png 是换文件，必须重置",
        )
        self.assertNotIn("my_name", dialog.output_edit.text())

    @unittest.skipUnless(
        os.name == "nt", "case-only spelling differences are Windows-specific"
    )
    def test_case_only_input_change_is_the_same_file(self):
        """L-1: ``a.png`` → ``A.png`` is the same NTFS file, not a switch."""
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()

        upper = os.path.join(self._tmp, "A.png")
        self.assertTrue(os.path.isfile(upper), "NTFS 上 a.png/A.png 是同一文件")
        dialog.input_edit.setText(upper)
        self.assertEqual(
            dialog.output_edit.text(), custom,
            "仅大小写不同的同一文件不得重置自定义输出名",
        )

    def test_relative_and_absolute_spellings_of_the_same_file_keep_the_name(self):
        """L-1: absolute ↔ relative spellings are the same input file."""
        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()

        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self._tmp)

        dialog.input_edit.setText("a.png")  # absolute → relative
        self.assertEqual(
            dialog.output_edit.text(), custom,
            "绝对路径 → 同一文件的相对拼写不得重置",
        )
        dialog.input_edit.setText(self._png)  # relative → absolute
        self.assertEqual(
            dialog.output_edit.text(), custom,
            "相对拼写 → 同一文件的绝对路径不得重置",
        )

    def test_separator_and_dot_spellings_of_the_same_file_keep_the_name(self):
        """L-1: ``sub\\a.png`` and ``./sub/a.png`` are the same input file."""
        subdir = os.path.join(self._tmp, "sub")
        os.makedirs(subdir)
        target = os.path.join(subdir, "a.png")
        Path(target).write_bytes(b"x")

        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(target)
        custom = os.path.join(self._tmp, "my_name.raw")
        dialog.output_edit.setText(custom)
        dialog._on_output_edited()

        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self._tmp)

        first = os.path.join("sub", "a.png")  # Windows: sub\a.png
        dialog.input_edit.setText(first)
        self.assertEqual(
            dialog.output_edit.text(), custom,
            f"{first!r} 与绝对路径是同一文件，不得重置",
        )
        dialog.input_edit.setText("./sub/a.png")
        self.assertEqual(
            dialog.output_edit.text(), custom,
            "'./sub/a.png' 与 'sub/a.png' 是同一文件，不得重置",
        )

    def test_paste_and_character_typing_of_the_same_input_are_consistent(self):
        """L-2: clearing and re-entering the same path keeps the manual name."""
        from PyQt5.QtTest import QTest

        results = {}
        for mode in ("paste", "typing"):
            with self.subTest(mode=mode):
                dialog = self.make_convert_dialog()
                dialog.raw_type.setCurrentText("RAW10")
                dialog.input_edit.setText(self._png)
                custom = os.path.join(self._tmp, "my_name.raw")
                dialog.output_edit.setText(custom)
                dialog._on_output_edited()

                dialog.input_edit.clear()
                self.assertEqual(dialog.input_edit.text(), "")
                if mode == "paste":
                    dialog.input_edit.setText(self._png)
                else:
                    dialog.input_edit.setFocus()
                    QTest.keyClicks(dialog.input_edit, self._png)
                    self.assertEqual(
                        dialog.input_edit.text(), self._png,
                        "逐字符输入必须真实重建完整路径",
                    )
                results[mode] = dialog.output_edit.text()
                self.assertEqual(
                    results[mode], custom,
                    f"{mode}: 清空后重新输入同一文件不得重置自定义输出名",
                )
        self.assertEqual(
            results["paste"], results["typing"],
            "粘贴与逐字符重打必须得到一致结果",
        )

    def test_cancelled_output_browse_is_not_a_modification(self):
        """N-1: cancelling Browse leaves the field (and auto-follow) untouched."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        auto = dialog.output_edit.text()

        with mock.patch.object(
            convert_mod.QFileDialog,
            "getSaveFileName",
            return_value=("", ""),  # user pressed Cancel
        ):
            dialog._browse_output()
        self.assertEqual(dialog.output_edit.text(), auto)
        self.assertEqual(dialog._auto_output_path, auto)

        dialog.raw_type.setCurrentText("RAW12")
        self.assertEqual(
            self._name(dialog.output_edit.text()), "a_2560x1440_RGGB12.raw",
            "Browse 取消后自动更新必须继续",
        )

    def test_browse_selection_survives_parameter_changes(self):
        """A real Browse choice is a manual value and must be kept."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)
        chosen = os.path.join(self._tmp, "browse_out.raw")
        with mock.patch.object(
            convert_mod.QFileDialog,
            "getSaveFileName",
            return_value=(chosen, ""),
        ):
            dialog._browse_output()
        self.assertEqual(dialog.output_edit.text(), chosen)
        dialog.raw_type.setCurrentText("RAW12")
        self.assertEqual(dialog.output_edit.text(), chosen)

    def test_convert_has_a_single_editable_output_file_row(self):
        """U-2: one destination control, auto-filled next to the input."""
        from PyQt5.QtWidgets import QLabel

        dialog = self.make_convert_dialog()
        self.assertFalse(
            hasattr(dialog, "_same_dir_cb"),
            "Convert 页不应再有 Same directory 复选框",
        )
        convert_labels = [
            label.text() for label in dialog._convert_page.findChildren(QLabel)
        ]
        self.assertIn("Output file", convert_labels)
        self.assertNotIn("Output folder", convert_labels)

        old_dir = dialog._settings.default_output_dirname
        self.addCleanup(
            setattr, dialog._settings, "default_output_dirname", old_dir,
        )
        # The Settings folder must not affect Convert's default any more.
        dialog._settings.default_output_dirname = "convert_out"
        dialog.raw_type.setCurrentText("RAW10")
        dialog.input_edit.setText(self._png)

        self.assertTrue(dialog.output_edit.isEnabled())
        self.assertFalse(dialog.output_edit.isReadOnly(), "Output file 必须可编辑")
        self.assertEqual(
            _norm(os.path.dirname(dialog.output_edit.text())), _norm(self._tmp),
        )
        self.assertEqual(
            self._name(dialog.output_edit.text()), "a_2560x1440_RGGB10.raw",
        )
        self.assertNotIn(
            _norm("convert_out"), _norm(dialog.output_edit.text()),
            "Convert 的默认不再使用 Settings 的 Default output folder",
        )

    def test_mode_selector_aligns_with_each_pages_form_columns(self):
        """U-1 + L-1/L-3: real product styles, font matrix, both pages.

        The old test asserted only under the default (unstyled) font metrics:
        the YUV label column there is 374 px, while the product renders 197 px
        at font 13 and 354 px at font 24 (0.5.11.3 review L-3).  It also never
        checked the Variant page, whose form is much narrower than Convert's
        (0.5.11.3 review L-1).
        """
        from PyQt5.QtCore import QPoint

        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)

        def column_x(widget, dialog) -> int:
            return widget.mapTo(dialog, QPoint(0, 0)).x()

        def label_of(form, text):
            for row in range(form.rowCount()):
                item = form.itemAt(row, form.LabelRole)
                widget = item.widget() if item is not None else None
                if widget is not None and widget.text() == text:
                    return widget
            self.fail(f"form has no {text!r} label")

        def widest_label(form) -> int:
            widths = []
            for row in range(form.rowCount()):
                item = form.itemAt(row, form.LabelRole)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widths.append(widget.sizeHint().width())
            return max(widths)

        def assert_aligned(dialog, label, field, state: str) -> None:
            label_dx = column_x(dialog._mode_label, dialog) - column_x(label, dialog)
            field_dx = column_x(dialog.mode_selector, dialog) - column_x(field, dialog)
            self.assertLessEqual(
                abs(label_dx), 2,
                f"{state}: Mode 标签与 Input file 标签左边缘未对齐 ({label_dx:+d} px)",
            )
            self.assertLessEqual(
                abs(field_dx), 2,
                f"{state}: Mode 下拉框与 Input file 输入框左边缘未对齐 "
                f"({field_dx:+d} px)",
            )

        # L-3 (0.5.11.4): prove the product stylesheet/font size really took
        # effect.  Without this, deleting ``_apply_product_theme`` above leaves
        # the whole matrix silently running on default metrics and every
        # alignment assertion still passes (mutation #9 in 0.5.11.4 review).
        mode_label_widths: dict[str, dict[int, int]] = {}

        for theme in ("dark", "light"):
            mode_label_widths[theme] = {}
            for font_size in (10, 13, 20, 24):
                with self.subTest(theme=theme, font_size=font_size):
                    _apply_product_theme(app, theme, font_size)
                    app.processEvents()
                    settings = AppSettings()
                    settings.multi_variant_enabled = True
                    self.addCleanup(
                        setattr, settings, "multi_variant_enabled", False
                    )
                    dialog = ConvertDialog(settings)
                    self._dialogs.append(dialog)
                    mode_label_widths[theme][font_size] = (
                        dialog._mode_label.width()
                    )

                    # Before show(): the fixed Mode label must already equal
                    # the Convert form's widest label (the old code only
                    # synced after show, so a bare minimum-size dialog was
                    # misaligned until the first layout pass).
                    self.assertEqual(
                        dialog._mode_label.width(),
                        widest_label(dialog._form),
                        "show() 之前 _sync_mode_row_alignment 必须已生效",
                    )

                    dialog.show()
                    app.processEvents()
                    convert_label = label_of(dialog._form, "Input file")

                    assert_aligned(
                        dialog, convert_label, dialog.input_edit,
                        f"{theme}/{font_size}: Convert/Image",
                    )
                    # The label column grows when YUV-only rows (e.g.
                    # "Source offset (bytes)") appear; the Mode row must follow.
                    dialog.input_type.setCurrentText("YUV")
                    dialog.yuv_type.setCurrentText("NV16")
                    app.processEvents()
                    assert_aligned(
                        dialog, label_of(dialog._form, "Input file"),
                        dialog.input_edit, f"{theme}/{font_size}: Convert/YUV",
                    )

                    # Variant page: its own form has only Input file / Input
                    # type, so the Mode row must be narrower here than on the
                    # Convert page (0.5.11.3 L-1).
                    page = dialog._variant_page
                    dialog.mode_selector.setCurrentIndex(1)
                    app.processEvents()
                    variant_label = label_of(page._top_form, "Input file")
                    assert_aligned(
                        dialog, variant_label, page.input_edit,
                        f"{theme}/{font_size}: Variant",
                    )
                    self.assertEqual(
                        dialog._mode_label.width(),
                        widest_label(page._top_form),
                        "Variant 页 Mode 标签宽度必须取该页标签列",
                    )

                    # Resizing (wider, then narrower) must not break either
                    # page's column alignment.  NB: dialog.width / dialog.height
                    # are QSpinBox attributes (they shadow QWidget.width()), so
                    # use geometry().
                    geometry = dialog.geometry()
                    dialog.resize(geometry.width() + 120, geometry.height())
                    app.processEvents()
                    assert_aligned(
                        dialog, variant_label, page.input_edit,
                        f"{theme}/{font_size}: Variant after widen",
                    )
                    dialog.resize(
                        max(geometry.width() - 300, dialog.minimumWidth()),
                        geometry.height(),
                    )
                    app.processEvents()
                    assert_aligned(
                        dialog, variant_label, page.input_edit,
                        f"{theme}/{font_size}: Variant after narrow",
                    )

                    # Back to Convert: the anchor form switches again.
                    dialog.mode_selector.setCurrentIndex(0)
                    app.processEvents()
                    assert_aligned(
                        dialog, label_of(dialog._form, "Input file"),
                        dialog.input_edit,
                        f"{theme}/{font_size}: back to Convert",
                    )

        # L-3: font size must really drive the metrics; otherwise the matrix
        # above is eight copies of the same default-font run.
        for theme, widths in mode_label_widths.items():
            ordered = [widths[size] for size in (10, 13, 20, 24)]
            for smaller, larger, size_small, size_large in zip(
                ordered, ordered[1:], (10, 13, 20), (13, 20, 24)
            ):
                self.assertLess(
                    smaller, larger,
                    f"{theme}: 字号未生效，_mode_label.width() 未随字号递增 "
                    f"(font{size_small}={smaller}px, font{size_large}={larger}px)",
                )

    def test_disabled_mode_entry_is_fully_visible_in_the_popup(self):
        """L-2: the popup must fit the disabled entry at every font size.

        Qt sizes the popup like the combo, and the combo is pinned to the form
        field column, so ``Variant Convert (enable in Settings)`` was elided
        (font 13 + YUV: 347 px text in a 283 px viewport).  Widening only the
        popup's item view fixes it without touching the combo alignment.
        """
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)

        for font_size in (10, 13, 20, 24):
            with self.subTest(font_size=font_size):
                _apply_product_theme(app, "dark", font_size)
                app.processEvents()
                settings = AppSettings()
                settings.multi_variant_enabled = False
                dialog = ConvertDialog(settings)
                self._dialogs.append(dialog)
                dialog.show()
                app.processEvents()
                combo = dialog.mode_selector
                item = combo.model().item(1)
                self.assertIsNotNone(item)
                self.assertIn("enable in Settings", item.text())

                for input_type in ("Image", "YUV"):
                    with self.subTest(font_size=font_size, input_type=input_type):
                        dialog.input_type.setCurrentText(input_type)
                        app.processEvents()
                        combo.showPopup()
                        app.processEvents()
                        view = combo.view()
                        metrics = view.fontMetrics()
                        viewport = view.viewport().width()
                        for text in (item.text(), combo.currentText()):
                            advance = metrics.horizontalAdvance(text)
                            self.assertGreaterEqual(
                                viewport, advance,
                                f"font {font_size}/{input_type}: {text!r} "
                                f"({advance} px) 宽于弹层视口 ({viewport} px)",
                            )
                        self.assertGreaterEqual(
                            viewport, metrics.horizontalAdvance(item.text()) + 1,
                            "禁用项必须完整显示，不能被 ElideRight 截断",
                        )
                        combo.hidePopup()
                        app.processEvents()

    def test_yuv_note_under_height_is_gone(self):
        """R2: the YUV layout explanation lives in Format Help only."""
        dialog = self.make_convert_dialog()
        self.assertFalse(hasattr(dialog, "_yuv_note"))

    def test_settings_template_help_names_all_consumers(self):
        """M-3: the template field must say who it applies to."""
        from raw_view.gui.dialogs.settings import SettingsDialog
        from raw_view.models import AppSettings

        _ensure_app()
        dialog = SettingsDialog(AppSettings())
        self._dialogs.append(dialog)
        # The ⓘ icon carries the full scope help; the line edit itself only
        # shows the (possibly long) current template.
        tip = dialog.template_help_icon.toolTip()
        for consumer in (
            "Convert", "Batch Convert", "Generate Variants", "CLI",
            "Default output folder",
        ):
            self.assertIn(consumer, tip)
        self.assertIn(
            "Applies to Convert", dialog.template_edit.accessibleDescription()
        )

    def test_settings_output_folder_help_names_variant_and_batch(self):
        """0.5.11.3 L-5: Convert no longer has "Same directory as input"."""
        from raw_view.gui.dialogs.settings import SettingsDialog
        from raw_view.models import AppSettings

        dialog = SettingsDialog(AppSettings())
        self._dialogs.append(dialog)
        for text in (
            dialog.template_edit.accessibleDescription(),
            dialog.template_help_icon.toolTip(),
        ):
            self.assertIn("Variant Convert / Batch", text)
            self.assertNotIn("(Convert / Batch)", text)

    def test_format_help_documents_all_three_sampling_notes(self):
        """L-1: everything R2 deleted must remain findable in Format Help.

        The deleted ``_yuv_note`` said three things: YOnly is full-resolution
        grayscale (4:0:0), 4:2:0 downsamples U/V by 2x2, and 4:2:2 downsamples
        chroma horizontally 2:1.  Format Help covered the first two only.
        """
        from raw_view import help_content as hc

        html = hc.HELP_HTML
        self.assertIn("4:0:0", html)
        self.assertIn("full-resolution grayscale", html)
        self.assertIn("2x2", html, "4:2:0 的 2x2 下采样说明")
        self.assertIn("horizontally 2:1", html, "4:2:2 的水平 2:1 下采样说明")
        self.assertIn("422", html)

    def _fake_exec(self, box, button_text, sticky=False, expect_checkbox=False):
        checkbox = box.checkBox()
        if expect_checkbox:
            self.assertIsNotNone(checkbox, "批量覆盖弹窗必须带复选框")
            self.assertIn("remaining", checkbox.text().lower())
        else:
            self.assertIsNone(
                checkbox,
                "单文件 Convert 的覆盖弹窗不应出现 'all remaining files' 复选框",
            )
        texts = [b.text() for b in box.buttons()]
        self.assertNotIn("Overwrite All", texts, "按钮式 Overwrite All 应改为复选框")
        if checkbox is not None:
            checkbox.setChecked(sticky)
        for button in box.buttons():
            if button.text() == button_text:
                button.click()
                return 0
        self.fail(f"no button {button_text!r} in {texts}")

    def test_single_file_convert_has_no_sticky_checkbox(self):
        """0.5.11 L-2: one file means there is no "remaining file" to apply to."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "existing.raw")
        Path(out).write_bytes(b"old")
        calls = []

        def _exec(box):
            calls.append(1)
            return self._fake_exec(box, "Skip")

        with mock.patch.object(convert_mod.QMessageBox, "exec_", new=_exec):
            self.assertIsNone(dialog._confirm_output_collision(out))
            self.assertIsNone(dialog._confirm_output_collision(out))
        # No checkbox => every single-file conflict asks again.
        self.assertEqual(calls, [1, 1])
        self.assertIsNone(dialog._operation_collision_choice)

    def test_sticky_checkbox_is_not_accepted_for_single_file_convert(self):
        """The dialog calls the prompt without ``allow_sticky``; even a
        programmatic sticky choice must not be set by it."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "existing.raw")
        Path(out).write_bytes(b"old")
        with mock.patch.object(
            convert_mod.QMessageBox, "exec_",
            new=lambda box: self._fake_exec(box, "Overwrite"),
        ):
            self.assertEqual(dialog._confirm_output_collision(out), out)
        self.assertIsNone(dialog._operation_collision_choice)

    def test_multi_output_prompt_can_offer_the_sticky_checkbox(self):
        """The plumbing stays available for Batch / future multi-output paths."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "existing.raw")
        Path(out).write_bytes(b"old")
        with mock.patch.object(
            convert_mod.QMessageBox, "exec_",
            new=lambda box: self._fake_exec(
                box, "Overwrite", sticky=True, expect_checkbox=True
            ),
        ):
            self.assertEqual(
                dialog._confirm_output_collision(out, allow_sticky=True), out
            )
        self.assertEqual(dialog._operation_collision_choice, "overwrite")

    def test_convert_resets_the_sticky_choice_for_the_next_operation(self):
        """L-5: a leftover "do this for all" choice must not survive _convert().

        The single-file dialog never sets the sticky field through the UI today
        (the checkbox is only offered on multi-output prompts), but the reset is
        the contract that makes any future reuse safe.  Without it a stale
        "overwrite" silently bypasses the overwrite prompt.
        """
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        out = os.path.join(self._tmp, "existing.raw")
        Path(out).write_bytes(b"old")
        dialog.input_edit.setText(self._png)
        dialog.output_edit.setText(out)
        dialog._on_output_edited()
        dialog._operation_collision_choice = "overwrite"  # stale from a prior op
        prompts = []

        def _exec(box):
            prompts.append(1)
            return self._fake_exec(box, "Skip")

        with mock.patch.object(
            convert_mod.QMessageBox, "exec_", new=_exec
        ), mock.patch.object(convert_mod.QMessageBox, "critical"):
            dialog._convert()
        self.assertEqual(
            prompts, [1], "重新开始时必须重新询问，不得沿用上一次的 sticky 选择"
        )

    def test_all_three_collision_prompts_default_to_rename(self):
        """M-3: Enter must never silently overwrite in any entry point.

        The old mutation ``setDefaultButton(ovw_b)`` survived the whole suite;
        this asserts the real ``defaultButton()`` object of the real prompt.
        """
        import raw_view.gui.dialogs.batch_convert as bc_mod
        import raw_view.gui.dialogs.convert as convert_mod
        import raw_view.gui.dialogs.variant_convert as variant_mod
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        out = os.path.join(self._tmp, "exists.raw")
        Path(out).write_bytes(b"old")
        seen: list[str] = []

        def _exec(box):
            default = box.defaultButton()
            self.assertIsNotNone(default, "覆盖弹窗必须有默认按钮")
            seen.append(default.text())
            for button in box.buttons():
                if button.text() == "Skip":
                    button.click()
                    return 0
            self.fail(f"no Skip button in {[b.text() for b in box.buttons()]}")

        dialog = self.make_convert_dialog()
        with mock.patch.object(convert_mod.QMessageBox, "exec_", new=_exec):
            self.assertIsNone(dialog._confirm_output_collision(out))

        page = dialog._variant_page
        with mock.patch.object(variant_mod.QMessageBox, "exec_", new=_exec):
            self.assertIsNone(page._confirm_output_collision(out))

        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        batch = BatchConvertDialog(settings)
        self._dialogs.append(batch)
        with mock.patch.object(bc_mod.QMessageBox, "exec_", new=_exec):
            self.assertIsNone(batch._resolve_collision(out))

        self.assertEqual(seen, ["Rename (_1)"] * 3)

    def test_file_in_use_shows_actionable_guidance(self):
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._yuv)
        out = os.path.join(self._tmp, "locked.nv12")
        dialog.output_edit.setText(out)
        error = PermissionError(
            errno.EACCES, "The process cannot access the file"
        )
        # A real Windows sharing violation carries WinError 32.  A bare
        # EACCES/PermissionError is a *permission* problem and must not be
        # reported as an open file (0.5.11 review M-2).
        error.winerror = 32
        with mock.patch.object(
            T, "transcode_file", side_effect=error
        ) as transcode, \
             mock.patch.object(convert_mod.QMessageBox, "warning") as warning, \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        transcode.assert_called_once()
        critical.assert_not_called()
        warning.assert_called_once()
        message = warning.call_args[0][2]
        self.assertIn(out, message)
        self.assertIn("open in another program", message)
        self.assertIn("Close", message)
        self.assertFalse(
            os.path.exists(out), "被占用时不得留下半截输出文件",
        )

    def test_skip_does_not_report_the_file_in_use_error(self):
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._yuv)
        dialog.output_edit.setText(os.path.join(self._tmp, "skip.nv12"))
        with mock.patch.object(dialog, "_confirm_output_collision", return_value=None), \
             mock.patch.object(T, "transcode_file", side_effect=PermissionError(13, "in use")) as transcode, \
             mock.patch.object(convert_mod.QMessageBox, "warning") as warning, \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        transcode.assert_not_called()
        warning.assert_not_called()
        critical.assert_not_called()

    def _yuv_dialog(self, output_name="out.nv12"):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._yuv)
        dialog.output_edit.setText(os.path.join(self._tmp, output_name))
        return dialog

    def test_permission_denied_is_not_reported_as_file_in_use(self):
        """M-2: EACCES means "not allowed", not "another program has it open"."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self._yuv_dialog("readonly.nv12")
        error = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(T, "transcode_file", side_effect=error), \
             mock.patch.object(convert_mod.QMessageBox, "warning") as warning, \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        warning.assert_not_called()
        critical.assert_called_once()
        message = critical.call_args[0][2]
        self.assertIn("readonly.nv12", message)
        self.assertIn("Permission denied", message)
        self.assertNotIn("open in another program", message)
        self.assertNotIn("Close the program", message)

    def test_busy_device_is_reported_as_file_in_use(self):
        """M-2: POSIX EBUSY is the one genuinely "in use" errno."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self._yuv_dialog("busy.nv12")
        error = OSError(errno.EBUSY, "Device or resource busy")
        with mock.patch.object(T, "transcode_file", side_effect=error), \
             mock.patch.object(convert_mod.QMessageBox, "warning") as warning, \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        critical.assert_not_called()
        warning.assert_called_once()
        self.assertIn("open in another program", warning.call_args[0][2])

    def test_mkdir_permission_failure_is_not_reported_as_file_in_use(self):
        """M-2: failing to create the output folder is a permission problem."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self._yuv_dialog("nested/out.nv12")
        error = PermissionError(errno.EACCES, "Permission denied")
        with mock.patch.object(
            T, "transcode_file"
        ) as transcode, mock.patch(
            "pathlib.Path.mkdir", side_effect=error
        ), mock.patch.object(
            convert_mod.QMessageBox, "warning"
        ) as warning, mock.patch.object(
            convert_mod.QMessageBox, "critical"
        ) as critical:
            dialog._do_convert()
        transcode.assert_not_called()
        warning.assert_not_called()
        critical.assert_called_once()
        message = critical.call_args[0][2]
        self.assertIn("Permission denied", message)
        self.assertNotIn("open in another program", message)

    def test_mkdir_busy_failure_is_never_reported_as_in_use(self):
        """M-2: even a busy errno on mkdir is not "the output file is open".

        The mkdir handler opts out of the in-use classification entirely; an
        unrelated EBUSY must not tell the user to close a program.
        """
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self._yuv_dialog("nested2/out.nv12")
        error = OSError(errno.EBUSY, "Device or resource busy")
        with mock.patch(
            "pathlib.Path.mkdir", side_effect=error
        ), mock.patch.object(
            convert_mod.QMessageBox, "warning"
        ) as warning, mock.patch.object(
            convert_mod.QMessageBox, "critical"
        ) as critical:
            dialog._do_convert()
        warning.assert_not_called()
        critical.assert_called_once()
        self.assertNotIn("open in another program", critical.call_args[0][2])

    def test_unreadable_input_has_its_own_message(self):
        """M-2: a missing/unreadable input is not a locked output file."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self._yuv_dialog("unreadable.nv12")
        missing = os.path.join(self._tmp, "does-not-exist.i420")
        dialog.input_edit.setText(missing)
        with mock.patch.object(T, "transcode_file") as transcode, \
             mock.patch.object(convert_mod.QMessageBox, "warning") as warning, \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        transcode.assert_not_called()
        warning.assert_not_called()
        critical.assert_called_once()
        title, message = critical.call_args[0][1], critical.call_args[0][2]
        self.assertIn("read", title.lower())
        self.assertIn(missing, message)
        self.assertNotIn("open in another program", message)
        self.assertNotIn("Close the program", message)

    def test_yuv_default_size_mismatch_is_reported_not_written(self):
        """R7: 2560x1440 is only a guess; a mismatched file fails loudly."""
        import raw_view.gui.dialogs.convert as convert_mod

        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.yuv_type.setCurrentText("NV12")
        # Keep the new 2560x1440 default: the 4x2 file cannot match it.
        out = os.path.join(self._tmp, "mismatch.nv12")
        dialog.input_edit.setText(self._yuv)
        dialog.output_edit.setText(out)
        with mock.patch.object(convert_mod.QMessageBox, "information"), \
             mock.patch.object(convert_mod.QMessageBox, "critical") as critical:
            dialog._do_convert()
        self.assertTrue(critical.called, "尺寸不匹配必须明确报错")
        self.assertFalse(os.path.exists(out), "不得静默产出错误结果")
        # M-1: the raw size error must say what to change.  All three actions
        # plus the real byte counts so the right dimensions can be worked out.
        message = critical.call_args[0][2]
        self.assertIn("not an integer multiple", message, "原始字节信息必须保留")
        for phrase in (
            "SOURCE frame size",
            "not an output size",
            "real frame dimensions",
            "Source offset",
            f"{len(_i420_bytes())} bytes",
            "5529600 bytes",
        ):
            self.assertIn(phrase, message, f"尺寸错误缺少可操作指引: {phrase!r}")

    def test_yuv_preview_wraps_the_long_size_mismatch_guidance(self):
        """M-1: the preview label must wrap instead of growing to ~3145 px."""
        import raw_view.gui.dialogs.convert as convert_mod

        app = _ensure_app()
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.yuv_type.setCurrentText("NV12")
        dialog.input_edit.setText(self._yuv)  # default 2560x1440 -> mismatch
        dialog.show()
        app.processEvents()
        info = dialog._preview_info
        self.assertTrue(info.wordWrap(), "预览标签必须换行")
        self.assertIn("SOURCE frame size", info.text())
        self.assertGreater(len(info.text()), 300, "前提：这确实是一条长文案")
        self.assertLessEqual(
            info.width(), dialog.geometry().width(),
            "长文案不得把预览标签撑得比对话框还宽",
        )
        self.assertLess(
            dialog.geometry().width(), 2000,
            "换行后对话框不应被长文案撑到超宽",
        )


class VariantConvertPageTests(_DialogTestBase):
    """V1–V5: the Convert dialog's two modes and the Variant page itself.

    Everything here drives the *real* widgets (no mocked page internals), so a
    regression that re-mixes the two workflows, loses the disabled paint, or
    writes the wrong variant bytes fails loudly.
    """

    def setUp(self):
        super().setUp()
        import cv2

        self._tmp = tempfile.mkdtemp(prefix="rv-variant-page-")
        self._png = os.path.join(self._tmp, "a.png")
        cv2.imwrite(self._png, np.zeros((8, 8, 3), dtype=np.uint8))
        self._yuv = os.path.join(self._tmp, "clip.yuv")
        self._yuyv_bytes = T.transcode_bytes(
            _nv16_bytes(), _spec("NV16", "YUYV")
        )
        Path(self._yuv).write_bytes(self._yuyv_bytes)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _dialog(self, *, variants: bool):
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.models import AppSettings

        _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = variants
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = ConvertDialog(settings)
        self._dialogs.append(dialog)
        return dialog

    # ── V1 / V2: mode selector ───────────────────────────────────────

    def test_default_mode_is_convert(self):
        dialog = self._dialog(variants=False)
        self.assertEqual(dialog.mode_selector.currentText(), "Convert")
        self.assertEqual(dialog.mode_selector.currentIndex(), 0)
        self.assertEqual(dialog._stack.currentIndex(), 0)
        self.assertIs(dialog._stack.currentWidget(), dialog._convert_page)

    def test_switching_to_variant_shows_only_the_variant_page(self):
        dialog = self._dialog(variants=True)
        dialog.mode_selector.setCurrentIndex(1)
        self.assertEqual(dialog._stack.currentIndex(), 1)
        self.assertIs(dialog._stack.currentWidget(), dialog._variant_page)
        dialog.show()
        _ensure_app().processEvents()
        self.assertTrue(dialog._variant_page.generate_button.isVisibleTo(dialog))
        self.assertFalse(dialog._btn_run.isVisibleTo(dialog))

    def test_disabled_variant_mode_is_greyed_and_cannot_be_selected(self):
        """V2: the entry says why it is off, and is disabled *and* painted grey."""
        from PyQt5.QtCore import Qt

        from raw_view.models import THEME_PALETTES

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)
        dialog = self._dialog(variants=False)
        old_theme = dialog._settings.ui_theme
        self.addCleanup(setattr, dialog._settings, "ui_theme", old_theme)
        dialog.show()
        app.processEvents()
        for theme in ("light", "dark"):
            dialog._settings.ui_theme = theme
            _apply_product_theme(app, theme, 13)
            app.processEvents()
            item = dialog.mode_selector.model().item(1)
            self.assertIsNotNone(item)
            self.assertIn("enable in Settings", item.text())
            self.assertFalse(item.isEnabled())
            self.assertEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)
            rendered = item.foreground().color().name().lower()
            disabled = THEME_PALETTES[theme]["text_secondary"].lower()
            normal = THEME_PALETTES[theme]["text_color"].lower()
            self.assertEqual(
                rendered, disabled,
                f"{theme}: Variant 禁用项必须用禁用文字色绘制，实际 {rendered}",
            )
            self.assertNotEqual(
                rendered, normal,
                f"{theme}: 禁用项不能与正常项同色（看起来仍可选）",
            )
        # Programmatic selection must not bypass the disabled state.
        dialog.mode_selector.setCurrentIndex(1)
        self.assertEqual(dialog._stack.currentIndex(), 0)
        self.assertEqual(dialog.mode_selector.currentIndex(), 0)

    def test_enabled_variant_mode_is_selectable(self):
        from PyQt5.QtCore import Qt

        dialog = self._dialog(variants=True)
        item = dialog.mode_selector.model().item(1)
        self.assertEqual(item.text(), "Variant Convert")
        self.assertTrue(item.isEnabled())
        self.assertNotEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)
        dialog.mode_selector.setCurrentIndex(1)
        self.assertEqual(dialog.mode_selector.currentIndex(), 1)
        self.assertEqual(dialog._stack.currentIndex(), 1)

    def test_switching_mode_carries_the_selected_image_input(self):
        """M-1: the input file is a dialog-level concept, not page-local."""
        dialog = self._dialog(variants=True)
        dialog.input_edit.setText(self._png)
        dialog.mode_selector.setCurrentIndex(1)
        page = dialog._variant_page
        self.assertEqual(page.input_edit.text(), self._png)
        self.assertEqual(page.input_type.currentText(), "Image")
        # Reverse: the Convert page already holds a file, so it is untouched;
        # the point is that switching never blanks the target page.
        dialog.mode_selector.setCurrentIndex(0)
        self.assertEqual(dialog.input_edit.text(), self._png)

    def test_switching_mode_carries_yuv_input_and_its_parameters(self):
        """M-1: YUV needs the format, source size and offset carried too."""
        dialog = self._dialog(variants=True)
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.offset.setValue(6)
        dialog.input_edit.setText(self._yuv)
        dialog.mode_selector.setCurrentIndex(1)

        page = dialog._variant_page
        self.assertEqual(page.input_edit.text(), self._yuv)
        self.assertEqual(page.input_type.currentText(), "YUV")
        self.assertEqual(page.input_yuv_type.currentText(), "NV16")
        self.assertEqual((page.width.value(), page.height.value()), (W, H))
        self.assertEqual(page.offset.value(), 6)

    def test_switching_back_carries_a_variant_configured_yuv_input(self):
        """M-1 reverse direction: Variant -> Convert must fill an empty page."""
        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        page.input_yuv_type.setCurrentText("NV16")
        page.width.setValue(W)
        page.height.setValue(H)
        page.offset.setValue(6)
        page.input_edit.setText(self._yuv)
        self.assertEqual(dialog.input_edit.text(), "", "precondition: Convert empty")

        dialog.mode_selector.setCurrentIndex(1)  # Variant now visible
        dialog.mode_selector.setCurrentIndex(0)  # back to the empty Convert page

        self.assertEqual(dialog.input_edit.text(), self._yuv)
        self.assertEqual(dialog.input_type.currentText(), "YUV")
        self.assertEqual(dialog.input_yuv_type.currentText(), "NV16")
        self.assertEqual((dialog.width.value(), dialog.height.value()), (W, H))
        self.assertEqual(dialog.offset.value(), 6)

    def test_convert_page_has_no_variant_controls(self):
        """V1: the two workflows must not be mixed on one page again."""
        from PyQt5.QtWidgets import QWidget

        from raw_view.gui.widgets import VariantSelector

        dialog = self._dialog(variants=True)
        descendants = dialog._convert_page.findChildren(QWidget)
        selector = dialog._variant_page.image_selector
        self.assertNotIn(selector, descendants)
        self.assertNotIn(dialog._variant_page.generate_button, descendants)
        self.assertNotIn(dialog._variant_page.yuv_selector, descendants)
        # The selector really belongs to the Variant page.
        self.assertIs(dialog._variant_page.findChild(VariantSelector), selector)
        # The old compatibility aliases are gone (0.5.11.1 review L-4).
        self.assertFalse(hasattr(dialog, "_variant_selector"))
        self.assertFalse(hasattr(dialog, "_btn_variants"))
        self.assertFalse(hasattr(dialog, "_generate_variants"))
        dialog.show()
        _ensure_app().processEvents()
        self.assertFalse(selector.isVisibleTo(dialog))

    # ── V3: image variants ───────────────────────────────────────────

    def _select_raw8_raw10_at_8x8(self, page):
        selector = page.image_selector
        for name, box in selector._format_boxes.items():
            box.setChecked(name in {"RAW8", "RAW10"})
        for box, _w, _h, _custom in selector._size_boxes:
            box.setChecked(False)
        selector._add_size_checkbox(8, 8, checked=True, is_custom=True)

    def test_image_variants_write_distinct_files_to_the_input_directory(self):
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("Image")
        page.input_edit.setText(self._png)
        self._select_raw8_raw10_at_8x8(page)
        self.assertTrue(page._same_dir_cb.isChecked())

        with mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()

        produced = sorted(
            name for name in os.listdir(self._tmp) if name.endswith(".raw")
        )
        self.assertEqual(
            produced, ["a_8x8_RGGB10.raw", "a_8x8_RGGB8.raw"]
        )
        for name in produced:
            path = os.path.join(self._tmp, name)
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 0)
        self.assertIsNone(page._operation_collision_choice)

    def test_image_mode_hides_width_height_but_yuv_mode_shows_them(self):
        """U-3: Width/Height are YUV source dimensions, not Image controls."""
        app = _ensure_app()
        dialog = self._dialog(variants=True)
        dialog.mode_selector.setCurrentIndex(1)
        dialog.show()
        app.processEvents()
        page = dialog._variant_page

        def visible() -> tuple:
            return (
                page.width.isVisibleTo(page),
                page._width_label.isVisibleTo(page),
                page.height.isVisibleTo(page),
                page._height_label.isVisibleTo(page),
            )

        page.input_type.setCurrentText("Image")
        app.processEvents()
        self.assertEqual(visible(), (False, False, False, False))
        self.assertEqual(
            page._dims_form.rowCount(), 0,
            "Image 模式下 Width/Height 行必须移出布局，不能只隐藏字段",
        )

        # Three round trips: the rows are taken out and re-inserted each time.
        # The *order* matters too — the old test only checked rowCount(), so a
        # mutation inserting ``rows[::-1]`` (Height above Width) survived
        # (0.5.11.3 review L-4).
        for round_trip in range(1, 4):
            page.input_type.setCurrentText("YUV")
            app.processEvents()
            self.assertEqual(visible(), (True, True, True, True))
            self.assertEqual(page._dims_form.rowCount(), 2)
            self.assertEqual(page._width_label.text(), "Source width")
            self.assertEqual(page._height_label.text(), "Source height")
            self.assertEqual(
                page._dims_form.getWidgetPosition(page.width)[0], 0,
                f"第 {round_trip} 次进入 YUV：Width 必须是第一行",
            )
            self.assertEqual(
                page._dims_form.getWidgetPosition(page.height)[0], 1,
                f"第 {round_trip} 次进入 YUV：Height 必须是第二行（Width 在上）",
            )

            page.input_type.setCurrentText("Image")
            app.processEvents()
            self.assertEqual(visible(), (False, False, False, False))
            self.assertEqual(page._dims_form.rowCount(), 0)

    def test_unchecked_output_folder_controls_where_variants_land(self):
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        out_dir = os.path.join(self._tmp, "chosen")
        page._same_dir_cb.setChecked(False)
        page.output_dir_edit.setText(out_dir)
        page.input_type.setCurrentText("Image")
        page.input_edit.setText(self._png)
        self._select_raw8_raw10_at_8x8(page)
        with mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()
        for name in ("a_8x8_RGGB8.raw", "a_8x8_RGGB10.raw"):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)

    def test_empty_format_selection_warns_once_without_a_contradictory_info(self):
        """L-3: a validation warning must not be followed by "nothing written"."""
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("Image")
        page.input_edit.setText(self._png)
        for box in page.image_selector._format_boxes.values():
            box.setChecked(False)
        with mock.patch.object(
            variant_mod.QMessageBox, "warning"
        ) as warning, mock.patch.object(
            variant_mod.QMessageBox, "information"
        ) as information:
            page.generate()
        self.assertEqual(warning.call_count, 1, "只应给一条“select a format”提示")
        information.assert_not_called()

    def test_empty_yuv_target_selection_warns_once_without_a_contradictory_info(self):
        """L-3 for the YUV path: the same single-message rule applies."""
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        page.input_edit.setText(self._yuv)
        for box in page.yuv_selector._boxes.values():
            box.setChecked(False)
        with mock.patch.object(
            variant_mod.QMessageBox, "warning"
        ) as warning, mock.patch.object(
            variant_mod.QMessageBox, "information"
        ) as information:
            page.generate()
        self.assertEqual(warning.call_count, 1)
        information.assert_not_called()

    def test_yuv_size_validation_failure_warns_once_without_a_contradictory_info(self):
        """M-1: a frame-size error must not be followed by "nothing written".

        The default 2560x1440 source size makes this the *normal* path for any
        ordinary YUV file, so the user must see exactly one critical box.
        """
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        bad = os.path.join(self._tmp, "small.i420")
        Path(bad).write_bytes(b"\x00" * 3072)  # 64x32 I420, not 2560x1440
        page.input_type.setCurrentText("YUV")
        page.input_yuv_type.setCurrentText("I420")
        page.width.setValue(2560)
        page.height.setValue(1440)
        page.input_edit.setText(bad)
        self.assertTrue(
            page.yuv_selector.selected_formats(), "前置条件：至少选中一个目标"
        )
        with mock.patch.object(
            variant_mod.QMessageBox, "critical"
        ) as critical, mock.patch.object(
            variant_mod.QMessageBox, "information"
        ) as information:
            page.generate()
        self.assertEqual(critical.call_count, 1, "尺寸错误只应提示一次")
        information.assert_not_called()

    def test_variant_output_dir_reuses_the_shared_resolver(self):
        """L-2: Variant must use ``models.resolve_output_dir`` like Convert/Batch.

        Spy on the resolver so the test fails if the page goes back to
        ``Path.resolve()`` (which would change 8.3/symlink spelling).
        """
        import raw_view.gui.dialogs.variant_convert as variant_mod

        page = self._dialog(variants=True)._variant_page
        page.output_dir_edit.setText("chosen")
        page._same_dir_cb.setChecked(False)
        with mock.patch.object(
            variant_mod, "resolve_output_dir", return_value="sentinel"
        ) as resolver:
            self.assertEqual(page._output_dir_for(self._png), "sentinel")
        resolver.assert_called_once_with(False, self._png, "chosen")

        page._same_dir_cb.setChecked(True)
        with mock.patch.object(
            variant_mod, "resolve_output_dir", return_value="sentinel"
        ) as resolver:
            self.assertEqual(page._output_dir_for(self._png), "sentinel")
        resolver.assert_called_once_with(True, self._png, "chosen")

    # ── V4: YUV variants ─────────────────────────────────────────────

    def test_yuv_variants_write_every_same_family_target_losslessly(self):
        import raw_view.gui.dialogs.variant_convert as variant_mod

        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        page.input_yuv_type.setCurrentText("YUYV")
        page.width.setValue(W)
        page.height.setValue(H)
        page.input_edit.setText(self._yuv)
        self.assertEqual(
            set(page.yuv_selector.selected_formats()),
            set(T.YUV422_FORMATS) - {"YUYV"},
        )
        # No Bayer / Sizes / Bit depth controls participate in YUV mode.
        self.assertFalse(page.image_card.isVisibleTo(page))
        self.assertTrue(page.yuv_card.isVisibleTo(page))

        with mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()

        produced = sorted(
            name
            for name in os.listdir(self._tmp)
            if name.endswith(".yuv") and name != Path(self._yuv).name
        )
        expected_targets = [
            name for name in T.YUV422_FORMATS if name != "YUYV"
        ]
        self.assertEqual(
            produced,
            sorted(
                f"clip_{W}x{H}_YUYV_to_{target}.yuv"
                for target in expected_targets
            ),
        )
        for target in expected_targets:
            path = os.path.join(
                self._tmp, f"clip_{W}x{H}_YUYV_to_{target}.yuv"
            )
            expected = T.transcode_bytes(
                self._yuyv_bytes, T.TranscodeSpec("YUYV", target, W, H)
            )
            self.assertEqual(Path(path).read_bytes(), expected, target)

    def test_yuv_cross_family_targets_are_greyed_with_a_reason(self):
        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        page.input_yuv_type.setCurrentText("YUYV")
        boxes = page.yuv_selector._boxes
        for name in T.YUV420_FORMATS:
            self.assertFalse(boxes[name].isEnabled(), name)
            self.assertIn("changes chroma sampling", boxes[name].toolTip())
        self.assertFalse(
            boxes["YUYV"].isEnabled(), "same-format is a no-op, not a target"
        )
        # And the selection list can never contain an impossible target.
        self.assertNotIn("NV12", page.yuv_selector.selected_formats())

    def test_yuv_variant_switch_between_420_and_422_refreshes_targets(self):
        dialog = self._dialog(variants=True)
        page = dialog._variant_page
        page.input_type.setCurrentText("YUV")
        page.input_yuv_type.setCurrentText("I420")
        self.assertEqual(
            set(page.yuv_selector.selected_formats()),
            set(T.YUV420_FORMATS) - {"I420"},
        )
        page.input_yuv_type.setCurrentText("NV16")
        self.assertEqual(
            set(page.yuv_selector.selected_formats()),
            set(T.YUV422_FORMATS) - {"NV16"},
        )

    # ── V3/V4: output destination ───────────────────────────────────

    def test_variant_output_folder_is_always_judgeable(self):
        dialog = self._dialog(variants=True)
        dialog.mode_selector.setCurrentIndex(1)
        dialog.show()
        app = _ensure_app()
        app.processEvents()
        page = dialog._variant_page

        self.assertTrue(page._same_dir_cb.isChecked())
        self.assertFalse(page._output_dir_widget.isVisible())
        hint = page._output_dir_hint.text()
        self.assertTrue(hint.strip())
        self.assertIn("next to the input file", hint)

        page._same_dir_cb.setChecked(False)
        app.processEvents()
        self.assertTrue(page._output_dir_widget.isVisible())
        self.assertTrue(page.output_dir_edit.isVisible())
        self.assertTrue(page._output_dir_browse_btn.isVisible())
        self.assertTrue(page.output_dir_edit.text().strip())
        self.assertIn(
            page.output_dir_edit.text().strip(),
            page._output_dir_hint.text(),
        )

        page._same_dir_cb.setChecked(True)
        app.processEvents()
        self.assertFalse(page._output_dir_widget.isVisible())
        self.assertTrue(page._output_dir_hint.text().strip())

    # ── V3: collision prompt ────────────────────────────────────────

    def _variant_collision_exec(self, prompts, *, sticky: bool):
        def _exec(box):
            checkbox = box.checkBox()
            self.assertIsNotNone(
                checkbox, "Variant 多输出覆盖弹窗必须带 'remaining files' 复选框"
            )
            self.assertIn("remaining", checkbox.text().lower())
            self.assertNotIn(
                "Overwrite All", [b.text() for b in box.buttons()]
            )
            prompts.append(1)
            checkbox.setChecked(sticky)
            for button in box.buttons():
                if button.text() == "Overwrite":
                    button.click()
                    return 0
            self.fail(f"no Overwrite button in {[b.text() for b in box.buttons()]}")

        return _exec

    def _prepare_two_existing_variant_outputs(self):
        page = self._dialog(variants=True)._variant_page
        page.input_type.setCurrentText("Image")
        page.input_edit.setText(self._png)
        self._select_raw8_raw10_at_8x8(page)
        expected = [
            os.path.join(self._tmp, "a_8x8_RGGB8.raw"),
            os.path.join(self._tmp, "a_8x8_RGGB10.raw"),
        ]
        for path in expected:
            Path(path).write_bytes(b"old")
        return page, expected

    def test_variant_collision_checkbox_is_sticky_for_the_operation_only(self):
        import raw_view.gui.dialogs.variant_convert as variant_mod

        page, expected = self._prepare_two_existing_variant_outputs()
        prompts: list[int] = []
        with mock.patch.object(
            variant_mod.QMessageBox, "exec_",
            new=self._variant_collision_exec(prompts, sticky=True),
        ), mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()
        self.assertEqual(prompts, [1], "勾选后本操作剩余冲突不应再次询问")
        self.assertIsNone(
            page._operation_collision_choice,
            "操作结束后必须重置 sticky 选择",
        )
        for path in expected:
            self.assertNotEqual(Path(path).read_bytes(), b"old", path)

        # A new operation asks again for every conflict (the reset is real).
        prompts = []
        with mock.patch.object(
            variant_mod.QMessageBox, "exec_",
            new=self._variant_collision_exec(prompts, sticky=False),
        ), mock.patch.object(variant_mod.QMessageBox, "information"):
            page.generate()
        self.assertEqual(prompts, [1, 1], "下一次操作每个冲突都应重新询问")


class BatchConvertDialogInputTypeTests(_DialogTestBase):
    """The batch dialog must offer the same input-type semantics as Convert."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="rv-gui-batch-transcode-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _make(self):
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        dialog = BatchConvertDialog(AppSettings())
        self._dialogs.append(dialog)
        app.processEvents()
        return dialog

    def test_input_type_choices_match_convert_dialog(self):
        dialog = self._make()
        labels = [dialog.input_type.itemText(i) for i in range(dialog.input_type.count())]
        self.assertEqual(labels[:2], ["Image", "YUV"])

    def test_raw_input_choice_is_really_disabled(self):
        """D3: same disabled reserved entry as ConvertDialog."""
        from PyQt5.QtCore import Qt

        dialog = self._make()
        item = dialog.input_type.model().item(2)
        self.assertIsNotNone(item)
        self.assertFalse(item.isEnabled())
        self.assertEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)

    def test_raw_input_choice_is_greyed_in_both_themes(self):
        """R6 (Batch): the reserved RAW entry is painted disabled-grey too."""
        from PyQt5.QtCore import Qt

        from raw_view.models import THEME_PALETTES

        app = _ensure_app()
        old_qss = app.styleSheet()
        self.addCleanup(app.setStyleSheet, old_qss)
        dialog = self._make()
        old_theme = dialog._settings.ui_theme
        self.addCleanup(setattr, dialog._settings, "ui_theme", old_theme)
        dialog.show()
        app.processEvents()
        for theme in ("light", "dark"):
            dialog._settings.ui_theme = theme
            _apply_product_theme(app, theme, 13)
            app.processEvents()
            item = dialog.input_type.model().item(2)
            self.assertIsNotNone(item)
            self.assertFalse(item.isEnabled())
            self.assertEqual(int(item.flags()) & int(Qt.ItemIsEnabled), 0)
            rendered = item.foreground().color().name().lower()
            disabled = THEME_PALETTES[theme]["text_secondary"].lower()
            self.assertEqual(
                rendered, disabled,
                f"{theme}: 预留项必须用禁用文字色绘制，实际 {rendered}",
            )
            self.assertNotEqual(
                rendered, THEME_PALETTES[theme]["text_color"].lower(),
                f"{theme}: 预留项不能与正常文字同色",
            )

    def test_yuv_sources_exclude_yonly_and_every_one_has_a_target(self):
        """D4: no source format may leave every target greyed out."""
        dialog = self._make()
        sources = [
            dialog.input_yuv_type.itemText(i)
            for i in range(dialog.input_yuv_type.count())
        ]
        targets = [
            dialog.yuv_type.itemText(i)
            for i in range(dialog.yuv_type.count())
        ]
        self.assertNotIn("YOnly", sources)
        # Membership matters, not list order: reordering the family list is a
        # valid refactor.  Every source still has to reach a target (below).
        self.assertEqual(set(sources), set(T.YUV_TRANSCODE_FORMATS))
        for source in sources:
            self.assertTrue(
                any(T.reject_reason(source, target) is None for target in targets),
                f"{source} 必须至少有一个可转目标",
            )

    def test_input_type_hint_explains_both_modes(self):
        dialog = self._make()
        self.assertIn("output size", dialog._input_hint.text().lower())
        dialog.input_type.setCurrentText("YUV")
        yuv_hint = dialog._input_hint.text().lower()
        self.assertIn("lossless", yuv_hint)
        self.assertIn("source frame size", yuv_hint)

    def test_naming_does_not_claim_image_only(self):
        """D2: the batch dialog also accepts YUV files."""
        dialog = self._make()
        self.assertNotIn("image", dialog.windowTitle().lower())
        self.assertEqual(dialog.input_edit.accessibleName(), "Files to convert")
        text = (
            dialog.input_edit.placeholderText()
            + " "
            + dialog.input_edit.accessibleDescription()
        ).lower()
        self.assertIn("yuv", text)

    def test_browse_files_filter_offers_all_files(self):
        """D1 parity: YUV files with arbitrary suffixes stay pickable."""
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog = self._make()
        with mock.patch.object(
            bc_mod.QFileDialog, "getOpenFileNames", return_value=([], "")
        ) as picker:
            dialog._browse_files()
        args = picker.call_args[0]
        self.assertIn("All Files", args[3])

    def test_multi_variant_setting_has_no_effect_on_the_batch_ui(self):
        """0.5.11.1 removal: Batch never gains a variant panel.

        The setting must not add, remove or relabel a single control, so the
        two dialogs (on/off) must expose exactly the same widget inventory.
        """
        from PyQt5.QtWidgets import QWidget

        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.gui.widgets import VariantSelector
        from raw_view.models import AppSettings

        app = _ensure_app()
        # The setting is a live property over one shared QSettings store, so
        # each dialog must be *constructed* while the store is in the state it
        # is supposed to test.  Building both after writing True made the old
        # version of this test compare two "on" dialogs (0.5.11.2 review M-2).
        settings_off = AppSettings()
        settings_off.multi_variant_enabled = False
        self.addCleanup(setattr, settings_off, "multi_variant_enabled", False)
        self.assertFalse(
            settings_off.multi_variant_enabled,
            "前置条件：构造 off 对话框时设置必须为 False",
        )
        dialog_off = BatchConvertDialog(settings_off)
        self._dialogs.append(dialog_off)
        # Still False *after* construction: the dialog did not write it back.
        self.assertFalse(settings_off.multi_variant_enabled)

        settings_on = AppSettings()
        settings_on.multi_variant_enabled = True
        self.assertTrue(
            settings_on.multi_variant_enabled,
            "前置条件：构造 on 对话框时设置必须为 True",
        )
        dialog_on = BatchConvertDialog(settings_on)
        self._dialogs.append(dialog_on)
        app.processEvents()

        def inventory(dialog):
            return [
                (
                    type(widget).__name__,
                    widget.objectName(),
                    widget.accessibleName(),
                    widget.isEnabled(),
                )
                for widget in dialog.findChildren(QWidget)
            ]

        self.assertEqual(
            inventory(dialog_off),
            inventory(dialog_on),
            "multi_variant_enabled 不得改变 Batch 界面的任何控件",
        )
        for dialog in (dialog_off, dialog_on):
            self.assertIsNone(
                dialog.findChild(VariantSelector),
                "Batch 不应再出现 VariantSelector 面板",
            )
            self.assertFalse(hasattr(dialog, "_variant_selector"))
            self.assertFalse(hasattr(dialog, "_btn_variants"))
        # The variant generation path is not even imported by the module.
        import raw_view.gui.dialogs.batch_convert as bc_mod

        self.assertNotIn("generate_image_variants", vars(bc_mod))

    def test_yuv_target_renders_raw_type_and_bits_empty_like_convert(self):
        """L-3: a YUV target must not borrow the RAW type in custom templates.

        Convert, Batch and the CLI all document ``{raw_type}``/``{bits}`` as
        empty for YUV output; Batch used to pass the RAW combo value anyway.
        """
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from raw_view.gui.dialogs.convert import ConvertDialog
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import (
            AppSettings,
            format_output_template,
            resolve_output_dir,
        )

        app = _ensure_app()
        settings = AppSettings()
        old_template = settings.output_template
        self.addCleanup(setattr, settings, "output_template", old_template)
        settings.output_template = "{input_stem}_{raw_type}_{bits}_{yuv_type}"

        png = self._write_png("target.png")

        dialog = BatchConvertDialog(settings)
        self._dialogs.append(dialog)
        app.processEvents()
        dialog.input_type.setCurrentText("Image")
        dialog.target_type.setCurrentText("YUV")
        dialog.yuv_type.setCurrentText("NV12")
        dialog.raw_type.setCurrentText("RAW10")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        dialog._add_files([png])
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        batch_path = dialog._file_table.item(0, 3).text()

        convert = ConvertDialog(settings)
        self._dialogs.append(convert)
        app.processEvents()
        convert.input_edit.setText(png)
        convert.target_type.setCurrentText("YUV")
        convert.yuv_type.setCurrentText("NV12")
        convert.raw_type.setCurrentText("RAW10")
        convert.width.setValue(4)
        convert.height.setValue(4)
        convert_path = convert.output_edit.text()

        expected = format_output_template(
            settings.output_template,
            png,
            4,
            4,
            "YUV",
            output_dir=resolve_output_dir(
                True, png, settings.default_output_dirname
            ),
            raw_type="",
            yuv_type="NV12",
            source_yuv_type="",
        )
        self.assertEqual(_norm(batch_path), _norm(expected))
        self.assertEqual(_norm(convert_path), _norm(expected))
        self.assertNotIn("RAW10", batch_path)
        self.assertNotIn("RAW10", convert_path)

    def test_batch_with_multi_variant_enabled_still_writes_one_target(self):
        """The setting must not fan one input out into many outputs."""
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = BatchConvertDialog(settings)
        self._dialogs.append(dialog)
        app.processEvents()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        src = self._write_png("single.png")
        dialog._add_files([src])
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(dialog._file_table.item(0, 2).text(), "OK")
        produced = sorted(
            name for name in os.listdir(self._tmp) if name.endswith(".raw")
        )
        self.assertEqual(
            produced, ["single_4x4_RGGB8.raw"], "Batch 必须只产出一个目标"
        )

    def test_switching_to_yuv_uses_default_dimensions_and_pins_target(self):
        """Requirement change: YUV input starts at 2560x1440, not 1x1."""
        dialog = self._make()
        self.assertEqual(dialog.width.value(), 2560)
        dialog.target_type.setCurrentText("RAW")
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual((dialog.width.value(), dialog.height.value()), (2560, 1440))
        self.assertEqual(dialog.target_type.currentText(), "YUV")
        self.assertFalse(dialog.target_type.isEnabled())
        self.assertTrue(dialog.input_yuv_type.isVisibleTo(dialog))

    def test_switching_to_yuv_hides_and_disables_align_endian(self):
        """M-1 (Batch): no stale is_raw may survive the pin to YUV."""
        dialog = self._make()
        self.assertEqual(dialog.target_type.currentText(), "RAW")
        self.assertTrue(dialog.align.isVisibleTo(dialog))
        self.assertTrue(dialog.align.isEnabled())
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.target_type.currentText(), "YUV")
        for widget in (dialog.align, dialog.endian):
            self.assertFalse(widget.isVisibleTo(dialog), "YUV 输入下不得残留控件")
            self.assertFalse(widget.isEnabled(), "不可见的控件不得可操作")

    def test_non_convertible_targets_greyed(self):
        from raw_view import transcode as T

        dialog = self._make()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        model = dialog.yuv_type.model()
        for name in T.YUV422_FORMATS:
            if name != "NV16":
                self.assertTrue(model.item(dialog.yuv_type.findText(name)).isEnabled())
        for name in T.YUV420_FORMATS:
            self.assertFalse(model.item(dialog.yuv_type.findText(name)).isEnabled())

    def test_switch_back_to_image_restores_defaults(self):
        dialog = self._make()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_type.setCurrentText("Image")
        self.assertEqual(dialog.width.value(), 2560)
        self.assertEqual(dialog.height.value(), 1440)
        self.assertTrue(dialog.target_type.isEnabled())

    def test_yuv_batch_writes_transcoded_output(self):
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog = self._make()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        src = os.path.join(self._tmp, "in.i420")
        Path(src).write_bytes(_i420_bytes())
        dialog._add_files([src])
        # "Same directory as input" is on by default, so the auto-derived output
        # lands next to the source; read the path back from the table.
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(dialog._file_table.item(0, 2).text(), "OK")
        out = dialog._file_table.item(0, 3).text()
        self.assertTrue(os.path.isfile(out), out)
        self.assertEqual(
            Path(out).read_bytes(),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
        )
        self.assertIn("NV12", Path(out).name, "自动输出名应体现目标布局")
        self.assertIn(
            "I420_to_NV12", Path(out).name,
            "YUV 输入 -> YUV 输出应同时体现源布局与目标布局",
        )

    def _write_png(self, name, size=(4, 4)):
        import cv2

        path = os.path.join(self._tmp, name)
        cv2.imwrite(path, np.zeros((size[1], size[0], 3), dtype=np.uint8))
        return path

    def test_output_folder_is_always_judgeable(self):
        """0.5.11 user report: unchecking the box used to hide the destination."""
        dialog = self._make()
        dialog.show()
        _ensure_app().processEvents()

        # Checked (default): the edit is hidden, but the hint says where files go.
        self.assertTrue(dialog._same_dir_cb.isChecked())
        self.assertFalse(dialog._output_dir_widget.isVisible())
        hint = dialog._output_dir_hint.text()
        self.assertTrue(hint.strip(), "勾选时也必须说明输出位置")
        self.assertIn("next to each input file", hint)

        # Unchecked: a visible, non-empty folder field + Browse button.
        dialog._same_dir_cb.setChecked(False)
        _ensure_app().processEvents()
        self.assertTrue(dialog._output_dir_widget.isVisible())
        self.assertTrue(dialog.output_dir_edit.isVisible())
        self.assertTrue(dialog._output_dir_browse_btn.isVisible())
        self.assertTrue(
            dialog.output_dir_edit.text().strip(),
            "未勾选时输出目录控件必须有可见的当前值",
        )
        self.assertIn(
            dialog.output_dir_edit.text().strip(),
            dialog._output_dir_hint.text(),
        )

        # Toggling back and forth never leaves the destination unknowable.
        dialog._same_dir_cb.setChecked(True)
        _ensure_app().processEvents()
        self.assertFalse(dialog._output_dir_widget.isVisible())
        self.assertTrue(dialog._output_dir_hint.text().strip())

    def test_unchecked_output_folder_controls_where_files_land(self):
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog = self._make()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        out_dir = os.path.join(self._tmp, "chosen_out")
        dialog._same_dir_cb.setChecked(False)
        dialog.output_dir_edit.setText(out_dir)
        src = self._write_png("one.png")
        dialog._add_files([src])
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(dialog._file_table.item(0, 2).text(), "OK")
        landed = dialog._file_table.item(0, 3).text()
        self.assertEqual(_norm(os.path.dirname(landed)), _norm(out_dir))
        self.assertTrue(os.path.isfile(landed), landed)

    def test_checked_same_directory_writes_beside_the_input(self):
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog = self._make()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        src = self._write_png("beside.png")
        dialog._add_files([src])
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        landed = dialog._file_table.item(0, 3).text()
        self.assertEqual(_norm(os.path.dirname(landed)), _norm(self._tmp))
        self.assertTrue(os.path.isfile(landed), landed)

    def _batch_collision_exec(self, prompts, *, sticky):
        """exec_ replacement for the batch collision box."""

        def _exec(box):
            checkbox = box.checkBox()
            self.assertIsNotNone(checkbox, "Batch 覆盖弹窗必须带复选框")
            self.assertIn("remaining", checkbox.text().lower())
            texts = [b.text() for b in box.buttons()]
            self.assertNotIn("Overwrite All", texts)
            prompts.append(1)
            checkbox.setChecked(sticky)
            for button in box.buttons():
                if button.text() == "Overwrite":
                    button.click()
                    return 0
            self.fail(f"no Overwrite button in {texts}")

        return _exec

    def _prepare_two_existing_outputs(self):
        dialog = self._make()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        first = self._write_png("one.png")
        second = self._write_png("two.png")
        dialog._add_files([first, second])
        names = [
            os.path.join(
                self._tmp, f"{stem}_4x4_RGGB8.raw"
            )
            for stem in ("one", "two")
        ]
        for name in names:
            Path(name).write_bytes(b"old")
        return dialog, names

    def test_batch_collision_checkbox_is_sticky_for_the_batch_only(self):
        """0.5.11 L-2: Batch uses the same checkbox as Convert, scoped to one run."""
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog, names = self._prepare_two_existing_outputs()
        prompts = []
        with mock.patch.object(
            bc_mod.QMessageBox, "exec_",
            new=self._batch_collision_exec(prompts, sticky=True),
        ), mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(prompts, [1], "勾选后本批剩余冲突不应再次询问")
        self.assertIsNone(
            dialog._operation_collision_choice,
            "批次结束后必须重置 sticky 选择",
        )
        for name in names:
            self.assertNotEqual(Path(name).read_bytes(), b"old", name)

        # A new batch asks again (the reset is real).
        prompts = []
        with mock.patch.object(
            bc_mod.QMessageBox, "exec_",
            new=self._batch_collision_exec(prompts, sticky=False),
        ), mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(prompts, [1, 1], "下一次批次每个冲突都应重新询问")

    def test_batch_without_sticky_choice_asks_per_conflict(self):
        import raw_view.gui.dialogs.batch_convert as bc_mod

        dialog, _names = self._prepare_two_existing_outputs()
        prompts = []
        with mock.patch.object(
            bc_mod.QMessageBox, "exec_",
            new=self._batch_collision_exec(prompts, sticky=False),
        ), mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        self.assertEqual(prompts, [1, 1])

    def test_conditional_row_labels_hide_with_their_fields(self):
        """H-1: Batch has the same dangling-label defect class as Convert."""
        dialog = self._make()

        def expected(*, input_yuv=False, raw=False, yonly=False, bayer=True):
            show_raw = raw and not input_yuv
            show_bit = (not raw) and yonly and not input_yuv
            show_align = raw or show_bit
            return {
                dialog.input_yuv_type: input_yuv,
                dialog.raw_type: show_raw,
                dialog.bit_depth_combo: show_bit,
                dialog.align: show_align,
                dialog.endian: show_align,
                dialog.raw_source_mode: show_raw,
                dialog.bayer_pattern: show_raw and bayer,
            }

        names = {
            "input_type": dialog.input_type,
            "input_hint": dialog._input_hint,
            "input_yuv_type": dialog.input_yuv_type,
            "target": dialog.target_type,
            "raw_type": dialog.raw_type,
            "yuv_type": dialog.yuv_type,
            "bit_depth": dialog.bit_depth_combo,
            "align": dialog.align,
            "endian": dialog.endian,
            "raw_source": dialog.raw_source_mode,
            "bayer": dialog.bayer_pattern,
            "width": dialog.width,
            "height": dialog.height,
        }

        def expected_order(*, input_yuv=False, raw=False, yonly=False, bayer=True):
            """Canonical order declared by ``_cond_row_groups`` in __init__."""
            show_raw = raw and not input_yuv
            show_bit = (not raw) and yonly and not input_yuv
            show_align = raw or show_bit
            order = ["input_type", "input_hint"]
            if input_yuv:
                order += ["input_yuv_type"]
            order += ["target"]
            if show_raw:
                order += ["raw_type"]
            order += ["yuv_type"]
            if show_bit:
                order += ["bit_depth"]
            if show_align:
                order += ["align", "endian"]
            if show_raw:
                order += ["raw_source"]
                if bayer:
                    order += ["bayer"]
            order += ["width", "height"]
            return order

        def check(state, **kwargs):
            wanted = expected(**kwargs)
            for field, want in wanted.items():
                label = dialog._cond_row_labels[field]
                self.assertEqual(
                    field.isVisibleTo(dialog), want,
                    f"{state}: field {label.text()!r} visibility",
                )
                self.assertEqual(
                    label.isVisibleTo(dialog), want,
                    f"{state}: label {label.text()!r} left dangling",
                )
                # L-1 (0.5.10.3): hiding a row disables its field; showing it
                # again must re-enable it.  Without the explicit
                # ``setEnabled(True)`` in ``_apply_cond_rows`` the field stays
                # visible but disabled after its first hide, while every
                # visibility assertion above still passes (mutation survived).
                self.assertEqual(
                    field.isEnabled(), want,
                    f"{state}: field {label.text()!r} enabled",
                )
            # L-1: hidden rows must be removed from the layout.  With
            # setVisible only, QFormLayout keeps a 6px gap per hidden row and
            # rowCount stays at 6 fixed + 7 conditional.
            visible_cond = sum(1 for want in wanted.values() if want)
            self.assertEqual(
                dialog._form.rowCount(), 6 + visible_cond,
                f"{state}: hidden rows must not reserve layout height",
            )
            # L-1 (0.5.10.2): lock the actual row order, not just the count.
            # Without the ``anchor_row += 1`` in ``_apply_cond_rows`` the rows
            # inserted into one group come back reversed (e.g. Source offset
            # before Input YUV format) while the checks above stay green.
            self.assertEqual(
                _form_field_sequence(dialog, names),
                expected_order(**kwargs),
                f"{state}: conditional rows must keep their declared order",
            )

        self.assertEqual(dialog.target_type.currentText(), "RAW")
        check("default Image/RAW/bayer", raw=True, bayer=True)

        dialog.target_type.setCurrentText("YUV")
        check("Image/YUV/YOnly", raw=False, yonly=True)

        dialog.yuv_type.setCurrentText("NV12")
        check("Image/YUV/NV12", raw=False, yonly=False)

        dialog.input_type.setCurrentText("YUV")
        check("YUV input", input_yuv=True, raw=False, yonly=False)

        dialog.input_type.setCurrentText("Image")
        dialog.target_type.setCurrentText("RAW")
        dialog.raw_source_mode.setCurrentText("gray")
        check("Image/RAW/gray", raw=True, bayer=False)

        # L-1 (0.5.10.3): walk a field across the hide boundary and back.
        # NV12 keeps Bit depth/Alignment/Endianness hidden; returning to
        # Image/RAW/bayer must bring them back *enabled*, not merely visible.
        # The ``isEnabled`` assertion above is what makes deleting the
        # ``setEnabled(True)`` in ``_apply_cond_rows`` go red.
        dialog.target_type.setCurrentText("YUV")
        check("Image/YUV/NV12 (round trip back)", raw=False, yonly=False)

        dialog.target_type.setCurrentText("RAW")
        dialog.raw_source_mode.setCurrentText("bayer")
        check("Image/RAW/bayer (round trip back)", raw=True, bayer=True)

    def test_sync_keeps_keyboard_focus_on_a_still_visible_field(self):
        """M-1 (0.5.10.2): Batch loses focus exactly like Convert does."""
        app = _ensure_app()
        dialog = self._make()
        dialog.show()
        app.processEvents()
        dialog.raw_source_mode.setFocus()
        app.processEvents()
        self.assertIs(
            app.focusWidget(), dialog.raw_source_mode,
            "precondition: the field must own focus before the sync",
        )
        dialog._sync_controls_locked()
        app.processEvents()
        self.assertIs(
            app.focusWidget(), dialog.raw_source_mode,
            "a still-visible field must keep keyboard focus across a sync",
        )

    def test_sync_keeps_an_open_yuv_source_popup_open(self):
        """M-1 (0.5.10.2): the YUV source popup must survive a sync."""
        app = _ensure_app()
        dialog = self._make()
        dialog.show()
        dialog.input_type.setCurrentText("YUV")
        app.processEvents()
        combo = dialog.input_yuv_type
        self.assertTrue(combo.isVisible())
        combo.showPopup()
        app.processEvents()
        self.assertTrue(
            combo.view().isVisible(), "precondition: the popup must be open",
        )
        try:
            dialog._sync_controls_locked()
            app.processEvents()
            self.assertTrue(
                combo.view().isVisible(),
                "a sync must not close the YUV source format popup",
            )
        finally:
            combo.hidePopup()
            app.processEvents()

    def test_yuv_batch_still_converts_with_multi_variant_enabled(self):
        """YUV lossless single-target conversion survives the variant removal.

        The setting must not block or reroute a YUV batch; the output must be
        the one byte-exact transcoded target.
        """
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        settings = AppSettings()
        settings.multi_variant_enabled = True
        self.addCleanup(setattr, settings, "multi_variant_enabled", False)
        dialog = BatchConvertDialog(settings)
        self._dialogs.append(dialog)
        app.processEvents()

        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.width.setValue(W)
        dialog.height.setValue(H)
        dialog.yuv_type.setCurrentText("NV12")
        src = os.path.join(self._tmp, "in.i420")
        Path(src).write_bytes(_i420_bytes())
        dialog._add_files([src])

        with mock.patch.object(bc_mod.QMessageBox, "information") as info:
            dialog._run_batch()

        self.assertEqual(dialog._file_table.item(0, 2).text(), "OK")
        out = dialog._file_table.item(0, 3).text()
        self.assertTrue(os.path.isfile(out), out)
        self.assertEqual(
            Path(out).read_bytes(),
            T.transcode_bytes(_i420_bytes(), _spec("I420", "NV12")),
        )
        produced = sorted(
            name for name in os.listdir(self._tmp) if name != "in.i420"
        )
        self.assertEqual(produced, [Path(out).name], "YUV 批量也必须单目标")
        messages = [call.args[2] for call in info.call_args_list if len(call.args) >= 3]
        self.assertTrue(
            any("Batch complete" in message for message in messages),
            f"应完成正常批量汇总，实际弹窗: {messages}",
        )


    def test_progress_is_hidden_during_the_per_file_collision_prompt(self):
        """L-1: the progress box must not sit behind the "Output exists" prompt."""
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from PyQt5.QtTest import QTest

        app = _ensure_app()
        dialog = self._make()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        first = self._write_png("one.png")
        second = self._write_png("two.png")
        dialog._add_files([first, second])
        for stem in ("one", "two"):
            Path(
                os.path.join(self._tmp, f"{stem}_4x4_RGGB8.raw")
            ).write_bytes(b"old")

        real_convert = bc_mod.image_file_to_raw
        state = {"slowed": False}
        observed: dict[str, bool] = {}

        def _slow_convert(*args, **kwargs):
            if not state["slowed"]:
                state["slowed"] = True
                # > minimumDuration (300 ms): let the deferred progress appear.
                QTest.qWait(350)
                observed["visible_after_first_file"] = (
                    dialog._progress is not None and dialog._progress.isVisible()
                )
            else:
                # Runs after the collision prompt returned: a progress box that
                # was visible before the question must be visible again.
                observed["visible_after_prompt"] = (
                    dialog._progress is not None and dialog._progress.isVisible()
                )
            return real_convert(*args, **kwargs)

        def _exec(box):
            if state["slowed"]:
                observed["visible_during_prompt"] = (
                    dialog._progress is not None and dialog._progress.isVisible()
                )
            for button in box.buttons():
                if button.text() == "Overwrite":
                    button.click()
                    return 0
            return 0

        with mock.patch.object(
            bc_mod, "image_file_to_raw", side_effect=_slow_convert
        ), mock.patch.object(
            bc_mod.QMessageBox, "exec_", new=_exec
        ), mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()

        self.assertTrue(
            observed.get("visible_after_first_file"),
            "前置条件：第一个文件转换后进度框应已可见",
        )
        self.assertFalse(
            observed.get("visible_during_prompt", True),
            "冲突询问期间进度框必须隐藏（否则用户以为转换仍在继续）",
        )
        self.assertTrue(
            observed.get("visible_after_prompt"),
            "询问结束后进度框必须恢复可见（否则剩余文件转换期间进度永久消失）",
        )
        self.assertIsNone(dialog._progress)

    def test_progress_never_shown_is_not_forced_visible_after_the_prompt(self):
        """L-1: the restore half must only re-show a box that was visible.

        A short batch never reaches the 300 ms minimumDuration; after the
        per-file prompt the progress box must stay hidden instead of popping
        up because of the restore logic.
        """
        import raw_view.gui.dialogs.batch_convert as bc_mod

        app = _ensure_app()
        dialog = self._make()
        dialog.raw_type.setCurrentText("RAW8")
        dialog.width.setValue(4)
        dialog.height.setValue(4)
        first = self._write_png("one.png")
        second = self._write_png("two.png")
        dialog._add_files([first, second])
        for stem in ("one", "two"):
            Path(
                os.path.join(self._tmp, f"{stem}_4x4_RGGB8.raw")
            ).write_bytes(b"old")

        real_convert = bc_mod.image_file_to_raw
        calls = {"n": 0}
        observed: dict[str, bool] = {}

        def _quick_convert(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:  # after the prompt for the second file
                observed["visible_after_prompt"] = (
                    dialog._progress is not None and dialog._progress.isVisible()
                )
            return real_convert(*args, **kwargs)

        def _exec(box):
            observed["visible_during_prompt"] = (
                dialog._progress is not None and dialog._progress.isVisible()
            )
            for button in box.buttons():
                if button.text() == "Overwrite":
                    button.click()
                    return 0
            return 0

        with mock.patch.object(
            bc_mod, "image_file_to_raw", side_effect=_quick_convert
        ), mock.patch.object(
            bc_mod.QMessageBox, "exec_", new=_exec
        ), mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()

        self.assertFalse(
            observed.get("visible_during_prompt", True),
            "前置条件：短批量下询问期间进度框本就不可见",
        )
        self.assertFalse(
            observed.get("visible_after_prompt", True),
            "从未显示过的进度框在询问结束后不得被强行显示",
        )

    def test_yuv_size_mismatch_batch_status_includes_guidance(self):
        """M-1 (Batch): the per-file failure must also say what to change."""
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        dialog = BatchConvertDialog(AppSettings())
        self._dialogs.append(dialog)
        app.processEvents()
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("I420")
        dialog.yuv_type.setCurrentText("NV12")
        # Keep the 2560x1440 default: the 4x2 file cannot match it.
        src = os.path.join(self._tmp, "in.i420")
        Path(src).write_bytes(_i420_bytes())
        dialog._add_files([src])

        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()

        status = dialog._file_table.item(0, 2).text()
        self.assertIn("Failed", status)
        self.assertIn("not an integer multiple", status)
        for phrase in (
            "SOURCE frame size",
            "not an output size",
            "real frame dimensions",
            "Source offset",
            f"{len(_i420_bytes())} bytes",
            "5529600 bytes",
        ):
            self.assertIn(phrase, status, f"Batch 尺寸错误缺少指引: {phrase!r}")
        written = [p for p in os.listdir(self._tmp) if p != "in.i420"]
        self.assertEqual(written, [], f"尺寸不匹配不得产出文件: {written}")


# ── 8. CLI defects D1-D7 ─────────────────────────────────────────────

class BatchDialogLimitTests(_DialogTestBase):
    """L2: an unattended GUI batch must also refuse an over-limit YUV frame."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.mkdtemp(prefix="rv-gui-batch-limit-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def test_over_limit_entry_fails_without_writing(self):
        import raw_view.gui.dialogs.batch_convert as bc_mod
        from raw_view.gui.dialogs.batch_convert import BatchConvertDialog
        from raw_view.models import AppSettings

        app = _ensure_app()
        dialog = BatchConvertDialog(AppSettings())
        self._dialogs.append(dialog)
        src = os.path.join(self._tmp, "in.nv16")
        Path(src).write_bytes(b"\x00" * 64)
        dialog.input_type.setCurrentText("YUV")
        dialog.input_yuv_type.setCurrentText("NV16")
        dialog.width.setValue(16384)
        dialog.height.setValue(16385)
        dialog.yuv_type.setCurrentText("YUYV")
        dialog._add_files([src])
        with mock.patch.object(bc_mod.QMessageBox, "information"):
            dialog._run_batch()
        status = dialog._file_table.item(0, 2).text()
        self.assertIn("Failed", status)
        self.assertIn("too large", status)
        written = [p for p in os.listdir(self._tmp) if p != "in.nv16"]
        self.assertEqual(written, [], f"超限条目不得产出文件: {written}")


class BatchLegacyViewSemanticsTests(unittest.TestCase):
    """M1: a legacy batch view entry must keep its 640x480 default.

    ``input_type`` is new; entries written before it existed have no such key.
    Mapping those to the new "yuv transcode input" kind made every old
    batch-view-YUV job fail on the missing source dimensions, which is why the
    resolver returns ``(kind, explicit)`` and only an explicit declaration gets
    the stricter contract.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-m1-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dimension_requirement_is_keyed_on_mode_not_input_type(self):
        """N3: the M1 defence is ``mode == "convert"``, and a test must say so.

        The earlier version of this fix carried an ``explicit`` flag that no
        longer drove the decision; the reviewer proved it was dead by mutating
        it with no behaviour change.  The real mechanism is the mode check in
        ``_resolve_entry_dimensions``, so that is what is pinned here.
        """
        import raw_view.__main__ as main_mod

        # A YUV input being *decoded* never requires source dimensions...
        self.assertEqual(
            main_mod._resolve_entry_dimensions("yuv", None, None, "view"),
            (main_mod.DEFAULT_WIDTH, main_mod.DEFAULT_HEIGHT),
        )
        # ...while a YUV input being *transcoded* always does.
        with self.assertRaises(main_mod._BatchSpecError):
            main_mod._resolve_entry_dimensions("yuv", None, None, "convert")

    def test_view_mode_takes_its_input_kind_from_target(self):
        """View historically encodes the file kind in ``target``."""
        import raw_view.__main__ as main_mod

        self.assertEqual(
            main_mod._resolve_entry_input_type({"target": "YUV"}, "view"), "yuv"
        )
        self.assertEqual(
            main_mod._resolve_entry_input_type({"target": "RAW"}, "view"), "raw"
        )
        # A stray input_type must not change what view decodes.
        self.assertEqual(
            main_mod._resolve_entry_input_type(
                {"target": "YUV", "input_type": "raw"}, "view"
            ),
            "yuv",
        )

    def test_view_yuv_without_dimensions_uses_640x480(self):
        yuv = os.path.join(self._tmp, "y.yuv")
        Path(yuv).write_bytes(b"\x00" * (640 * 480))
        spec = {
            "mode": "view",
            "target": "YUV",
            "yuv_type": "YOnly",
            "files": [{"input": yuv}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 succeeded", proc.stdout)
        produced = [n for n in os.listdir(self._tmp) if n.endswith(".png")]
        self.assertEqual(len(produced), 1, produced)
        self.assertIn("640x480", produced[0], "必须沿用 640x480 默认")

    def test_view_raw_without_dimensions_uses_640x480(self):
        raw = os.path.join(self._tmp, "r.raw")
        Path(raw).write_bytes(b"\x00" * (640 * 480 * 2))
        spec = {
            "mode": "view",
            "target": "RAW",
            "raw_type": "RAW12",
            "files": [{"input": raw}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 succeeded", proc.stdout)

    def test_batch_view_honours_input_yuv_type(self):
        """N4: a declared input layout must be the one that is decoded."""
        import cv2  # noqa: F401  (converter availability)

        yuv = os.path.join(self._tmp, "f.nv16")
        # 4x4 NV16 = 16 Y bytes + 16 interleaved chroma bytes.
        Path(yuv).write_bytes(bytes([10] * 16) + bytes([20, 30] * 8))
        spec = {
            "mode": "view",
            "target": "YUV",
            "yuv_type": "YUYV",
            "input_type": "yuv",
            "input_yuv_type": "NV16",
            "width": 4,
            "height": 4,
            "files": [{"input": yuv}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("1 succeeded", proc.stdout)
        produced = [n for n in os.listdir(self._tmp) if n.endswith(".png")]
        self.assertEqual(len(produced), 1, produced)
        self.assertIn(
            "NV16", produced[0],
            "自动输出名应体现实际解码的输入布局",
        )

    def test_batch_view_input_yuv_type_drives_size_validation(self):
        """Negative control: the declared layout is what gets validated."""
        yuv = os.path.join(self._tmp, "g.nv16")
        Path(yuv).write_bytes(bytes(32))
        spec = {
            "mode": "view",
            "target": "YUV",
            "yuv_type": "YUYV",          # 4:2:2 forgiving of odd height
            "input_type": "yuv",
            "input_yuv_type": "I420",    # 4:2:0 forbids odd height
            "width": 4,
            "height": 5,
            "files": [{"input": yuv}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("I420", proc.stdout + proc.stderr)
        self.assertIn("even width/height", proc.stdout + proc.stderr)

    def test_explicit_yuv_transcode_still_requires_dimensions(self):
        """The strict contract must survive the M1 fix for the new use case."""
        yuv = os.path.join(self._tmp, "t.nv16")
        Path(yuv).write_bytes(b"\x00" * 32)
        spec = {
            "mode": "convert",
            "target": "YUV",
            "input_type": "yuv",
            "input_yuv_type": "NV16",
            "yuv_type": "YUYV",
            "files": [{"input": yuv}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("width and height is required", proc.stdout + proc.stderr)


class LogDirOverrideSafetyTests(unittest.TestCase):
    """N1: ``RAW_VIEW_LOG_DIR`` must never be able to break the CLI.

    ``--version`` has nothing to do with logging, yet an unusable override used
    to raise ``PermissionError`` from the package import -- a fake crash on the
    exact command a user runs to diagnose a real one.
    """

    def test_unusable_absolute_override_does_not_break_version(self):
        proc = _run_cli_env(
            {"RAW_VIEW_LOG_DIR": os.path.join(os.sep, "nope-does-not-exist", "xyz")},
            "--version",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # Compare against the single source of truth, not a literal: a version
        # bump must not turn this into a false failure.
        from raw_view.models import APP_VERSION

        self.assertIn(APP_VERSION, proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unusable_override_falls_back_to_platform_dir(self):
        from raw_view import logger as logger_mod

        with mock.patch.dict(
            os.environ,
            {"RAW_VIEW_LOG_DIR": os.path.join(os.sep, "nope-does-not-exist", "xyz")},
        ):
            self.assertEqual(
                logger_mod._default_log_dir(), logger_mod._platform_log_dir()
            )

    def test_relative_override_is_ignored(self):
        """A relative value would move the log into whatever the CWD is."""
        from raw_view import logger as logger_mod

        with mock.patch.dict(os.environ, {"RAW_VIEW_LOG_DIR": "logs_here"}):
            self.assertEqual(
                logger_mod._default_log_dir(), logger_mod._platform_log_dir()
            )

    def test_valid_absolute_override_is_used(self):
        from raw_view import logger as logger_mod

        target = tempfile.mkdtemp(prefix="rv-log-override-")
        try:
            with mock.patch.dict(os.environ, {"RAW_VIEW_LOG_DIR": target}):
                self.assertEqual(logger_mod._default_log_dir(), Path(target))
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def test_unwritable_log_path_does_not_stop_the_cli(self):
        """Even the platform directory failing must only degrade file logging."""
        from raw_view import logger as logger_mod

        self.assertTrue(callable(logger_mod.setup_logger))
        # setup_logger must swallow an OSError from mkdir (see its try/except);
        # simulate it rather than depending on a real unwritable path.
        with mock.patch("raw_view.logger.Path.mkdir", side_effect=OSError("nope")):
            logger_mod._initialized = False
            try:
                logger_mod.setup_logger("raw_view.n1_probe", log_dir="/nonexistent/x")
            finally:
                logger_mod._initialized = True


class LogRotationFailureTests(unittest.TestCase):
    """A locked log file must never put a traceback on the user's terminal.

    ``RotatingFileHandler.doRollover()`` renames ``raw-view.log`` to
    ``raw-view.log.1``.  On Windows that fails with ``WinError 32`` whenever
    another process holds the file open -- a second instance, a log viewer, a
    backup agent.  The exception escaped ``emit()``, Python's
    ``Handler.handleError`` printed a full ``--- Logging error ---`` traceback to
    **stderr**, and the CLI's promise of one readable ``Error: ...`` line was
    broken (several such blocks per command, one per log record).

    These tests drive the real handler and, for the end-to-end case, a real
    subprocess, so the guarantee is checked at the same level it broke.
    """

    def _locked_handler(self, log_dir: str):
        """A handler whose next write must fail to roll over.

        The failure is forced by patching ``doRollover`` rather than by holding
        an open handle: on POSIX a rename over an open file succeeds, so a
        handle-based "lock" only reproduces the bug on Windows.  The exception
        type is the one Windows raises (``WinError 32`` is an ``OSError``), so
        the production path exercised here is identical on every platform.
        """
        from raw_view.logger import SafeRotatingFileHandler

        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "raw-view.log")
        # Bigger than maxBytes so the very next record tries to roll over.
        with open(path, "wb") as f:
            f.write(b"x" * (8 * 1024))
        handler = SafeRotatingFileHandler(
            path, maxBytes=1024, backupCount=1, encoding="utf-8"
        )
        handler.doRollover = mock.Mock(
            side_effect=OSError(32, "The process cannot access the file")
        )
        self.addCleanup(handler.close)
        return handler

    def test_rollover_failure_does_not_escape_emit(self):
        import logging

        log_dir = tempfile.mkdtemp(prefix="rv-rotlock-")
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        handler = self._locked_handler(log_dir)
        record = logging.LogRecord(
            "raw_view.test", logging.INFO, __file__, 0, "hello", None, None
        )
        # Must not raise: the base class lets PermissionError out of emit().
        handler.emit(record)
        # The notice is written (and cleared) by the successful append that
        # follows the failed rollover, so the evidence lives in the file itself:
        # the record was still written *and* the degradation was recorded.
        handler.flush()
        logged = open(handler.baseFilename, "rb").read().decode("utf-8", "replace")
        self.assertIn("hello", logged, "轮转失败后记录仍应写入当前文件")
        self.assertIn("log rotation failed", logged, "降级必须可感知，不能静默隐藏")
        self.assertIsNone(
            handler._pending_notice, "提示写出后应清空，避免每条记录重复"
        )

    def test_handler_error_never_writes_to_stderr(self):
        """``handleError`` is overridden per handler, not globally."""
        import io as io_mod
        import logging

        log_dir = tempfile.mkdtemp(prefix="rv-rotlock2-")
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        handler = self._locked_handler(log_dir)
        captured = io_mod.StringIO()
        original = sys.stderr
        sys.stderr = captured
        try:
            handler.handleError(logging.LogRecord(
                "raw_view.test", logging.INFO, __file__, 0, "boom", None, None
            ))
        finally:
            sys.stderr = original
        self.assertEqual(captured.getvalue(), "", "logging 内部错误不得进 stderr")

    def test_global_raise_exceptions_is_not_changed(self):
        """The fix must be scoped to this handler, not the whole logging module."""
        import logging

        before = logging.raiseExceptions
        log_dir = tempfile.mkdtemp(prefix="rv-rotlock3-")
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        handler = self._locked_handler(log_dir)
        handler.emit(logging.LogRecord(
            "raw_view.test", logging.INFO, __file__, 0, "hi", None, None
        ))
        self.assertEqual(logging.raiseExceptions, before)

    def test_cli_stderr_stays_one_line_when_rotation_fails(self):
        """End-to-end: a failing rollover must not print stack traces.

        A lock-based reproduction (holding the file open) only works on Windows:
        POSIX happily renames an open file, so the rollover succeeds and there is
        nothing to degrade.  The child process therefore arms the failure through
        ``RAW_VIEW_TEST_FAIL_ROLLOVER``, which makes the handler raise the same
        ``OSError`` Windows raises.  That keeps the exercised path identical on
        every platform instead of skipping the test on CI.
        """
        log_dir = tempfile.mkdtemp(prefix="rv-rotcli-")
        work = tempfile.mkdtemp(prefix="rv-rotcli-w-")
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)

        path = os.path.join(log_dir, "raw-view.log")
        with open(path, "wb") as f:
            f.write(b"x" * (5 * 1024 * 1024 + 1024))  # force a rollover
        empty = os.path.join(work, "empty.raw")
        open(empty, "wb").close()

        proc = _run_cli_env(
            {"RAW_VIEW_LOG_DIR": log_dir, "RAW_VIEW_TEST_FAIL_ROLLOVER": "1"},
            "view", "-i", empty, "-o", os.path.join(work, "o.png"),
            "--width", "64", "--height", "64",
        )

        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Logging error", proc.stderr)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        self.assertIn("Error:", proc.stderr, "程序自己的错误行必须仍在")
        # ...and the degradation is still observable in the log itself.
        logged = open(path, "rb").read().decode("utf-8", "replace")
        self.assertIn("log rotation failed", logged)


class BatchConvertLimitTests(unittest.TestCase):
    """D1: batch convert must enforce the same 512MB frame limit as `convert`."""

    def setUp(self):
        import cv2

        self._tmp = tempfile.mkdtemp(prefix="rv-d1-")
        self._png = os.path.join(self._tmp, "s.png")
        cv2.imwrite(self._png, np.zeros((4, 4, 3), dtype=np.uint8))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, spec: dict, batch_name: str = "spec.json"):
        import raw_view.__main__ as main_mod

        batch_path = os.path.join(self._tmp, batch_name)
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch("raw_view.__main__.sys.exit"):
            return main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )

    def test_batch_convert_oversize_is_rejected_and_writes_nothing(self):
        """The exact reproduction: 16384x8193 RAW32 used to write a 513MB file."""
        out_dir = os.path.join(self._tmp, "out")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW32",
            "width": 16384,
            "height": 8193,
            "output_dir": out_dir,
            "files": [{"input": self._png}],
        }
        with mock.patch("raw_view.converter.image_file_to_raw") as conv:
            self._run(spec)
        conv.assert_not_called()
        self.assertFalse(
            os.path.isdir(out_dir) and os.listdir(out_dir),
            "超限条目不得产生任何输出文件",
        )

    def test_batch_convert_oversize_is_reported_as_failed(self):
        import raw_view.__main__ as main_mod

        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW32",
            "width": 16384,
            "height": 8193,
            "output_dir": self._tmp,
            "files": [{"input": self._png}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        exit_codes: list[int] = []
        with mock.patch.object(
            main_mod.sys, "exit", side_effect=lambda code=0: exit_codes.append(code)
        ), mock.patch("builtins.print") as fake_print:
            main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )
        printed = " ".join(str(c.args[0]) for c in fake_print.call_args_list if c.args)
        self.assertIn("FAIL", printed)
        self.assertIn("too large", printed)
        self.assertIn("1 failed", printed)
        self.assertEqual(exit_codes, [1], "有失败时必须 exit 1")

    def test_batch_convert_within_limit_still_converts(self):
        out = os.path.join(self._tmp, "ok.raw")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "files": [{"input": self._png, "output": out}],
        }
        self._run(spec)
        self.assertEqual(os.path.getsize(out), 16)

    def test_batch_yuv_transcode_is_size_checked_too(self):
        import raw_view.__main__ as main_mod

        yuv = os.path.join(self._tmp, "in.nv16")
        Path(yuv).write_bytes(b"\x00" * 32)
        spec = {
            "mode": "convert",
            "target": "YUV",
            "input_type": "yuv",
            "input_yuv_type": "NV16",
            "yuv_type": "YUYV",
            "width": 65534,
            "height": 65532,
            "files": [{"input": yuv}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch("raw_view.__main__.sys.exit"), \
             mock.patch("builtins.print") as fake_print:
            main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )
        printed = " ".join(str(c.args[0]) for c in fake_print.call_args_list if c.args)
        self.assertIn("too large", printed)


class BatchCrossEntryCollisionTests(unittest.TestCase):
    """D3: two entries deriving the same auto output name must not collide."""

    def setUp(self):
        import cv2

        self._tmp = tempfile.mkdtemp(prefix="rv-d3-")
        for sub in ("a", "b"):
            os.makedirs(os.path.join(self._tmp, sub))
            cv2.imwrite(
                os.path.join(self._tmp, sub, "same.png"),
                np.full((4, 4, 3), 10 if sub == "a" else 200, dtype=np.uint8),
            )

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_same_basename_across_entries_gets_unique_output(self):
        import raw_view.__main__ as main_mod

        out_dir = os.path.join(self._tmp, "out")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "output_dir": out_dir,
            "files": [
                {"input": os.path.join(self._tmp, "a", "same.png")},
                {"input": os.path.join(self._tmp, "b", "same.png")},
            ],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch("raw_view.__main__.sys.exit"):
            main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )
        produced = sorted(os.listdir(out_dir))
        self.assertEqual(
            len(produced), 2,
            f"两个 entry 必须各写一个文件，实际: {produced}",
        )
        self.assertTrue(any(name.endswith("_2.raw") for name in produced), produced)

    def test_two_successes_means_two_files(self):
        """The failure mode was "2 succeeded" with 1 file on disk."""
        out_dir = os.path.join(self._tmp, "out2")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "output_dir": out_dir,
            "files": [
                {"input": os.path.join(self._tmp, "a", "same.png")},
                {"input": os.path.join(self._tmp, "b", "same.png")},
            ],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 succeeded", proc.stdout)
        self.assertEqual(len(os.listdir(out_dir)), 2, proc.stdout)

    def test_explicit_output_is_still_honoured_verbatim(self):
        """De-duplication must never rewrite a user-chosen output name."""
        import raw_view.__main__ as main_mod

        explicit = os.path.join(self._tmp, "chosen.raw")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "files": [
                {"input": os.path.join(self._tmp, "a", "same.png"), "output": explicit},
            ],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        with mock.patch("raw_view.__main__.sys.exit"):
            main_mod._run_batch(
                argparse.Namespace(batch_file=batch_path, batch_help=False)
            )
        self.assertTrue(os.path.isfile(explicit))


class CliErrorOutputTests(unittest.TestCase):
    """D2: a failed decode/convert must print one line, not a full traceback."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-d2-")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_view_failure_has_no_traceback_on_stderr(self):
        short = os.path.join(self._tmp, "short.raw")
        Path(short).write_bytes(b"\x00" * 8)
        proc = _run_cli(
            "view", "-i", short, "-o", os.path.join(self._tmp, "o.png"),
            "--width", "64", "--height", "64", "--raw-type", "RAW12",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        self.assertIn("Error:", proc.stderr)

    def test_convert_failure_has_no_traceback_on_stderr(self):
        bad_png = os.path.join(self._tmp, "bad.png")
        Path(bad_png).write_bytes(b"not a png")
        proc = _run_cli(
            "convert", "-i", bad_png, "-o", os.path.join(self._tmp, "o.raw"),
            "--width", "8", "--height", "8",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        self.assertIn("Error:", proc.stderr)

    def test_batch_failure_has_no_traceback_on_stderr(self):
        bad_png = os.path.join(self._tmp, "bad.png")
        Path(bad_png).write_bytes(b"not a png")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 8,
            "height": 8,
            "files": [{"input": bad_png}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)

    def test_main_logger_is_a_child_of_raw_view(self):
        """D2 root cause: ``__name__`` is ``"__main__"`` under ``python -m``.

        ``"__main__"`` is a sibling of ``raw_view`` and inherits no handlers, so
        every failure record bypassed the log file.  Asserting on
        ``get_logger(__name__)`` from inside the test would be vacuous -- the
        test imports the module, where ``__name__`` is already
        ``raw_view.__main__``.  The name must therefore be observed in a real
        ``python -m raw_view`` process, which is the only place the wrong value
        ever appeared.
        """
        # A ``-c`` / import helper cannot produce the bug: importing the module
        # gives it the name ``raw_view.__main__`` either way.  The only place the
        # wrong name exists is a real ``python -m raw_view`` process, so run one
        # and inspect the name it actually logged under.
        log_dir = tempfile.mkdtemp(prefix="rv-loggername-")
        work = tempfile.mkdtemp(prefix="rv-loggername-work-")
        try:
            empty = os.path.join(work, "empty.raw")
            Path(empty).write_bytes(b"")
            env = dict(os.environ, PYTHONIOENCODING="utf-8",
                       RAW_VIEW_LOG_LEVEL="DEBUG", RAW_VIEW_LOG_DIR=log_dir)
            subprocess.run(
                [sys.executable, "-m", "raw_view", "view", "-i", empty,
                 "-o", os.path.join(work, "o.png"),
                 "--width", "64", "--height", "64"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, cwd=str(_PROJECT_ROOT),
            )
            text = (Path(log_dir) / "raw-view.log").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)
        # Records are formatted as "<date> <level> <logger name> <message>".
        self.assertIn(" raw_view.__main__ ", text,
                      "CLI 记录应挂在 raw_view.__main__ 下")
        self.assertNotIn(" __main__  ", text,
                         "__main__ 是 raw_view 的兄弟节点，拿不到文件 handler")

    def test_failure_really_lands_in_the_log_file(self):
        """End-to-end proof that the log file receives the failure + traceback."""
        import textwrap

        log_dir = tempfile.mkdtemp(prefix="rv-logfile-")
        work = tempfile.mkdtemp(prefix="rv-logfile-work-")
        try:
            empty = os.path.join(work, "empty.raw")
            Path(empty).write_bytes(b"")
            # A genuine ``python -m raw_view`` process: that is the invocation
            # where ``__name__`` used to be ``"__main__"`` and where the failure
            # record silently bypassed the log file (a ``-c`` helper would import
            # the module under its real name and reproduce nothing).
            # ``RAW_VIEW_LOG_DIR`` redirects the handler to a temp directory so
            # the developer's own log is untouched.
            env = dict(os.environ, PYTHONIOENCODING="utf-8",
                       RAW_VIEW_LOG_LEVEL="DEBUG", RAW_VIEW_LOG_DIR=log_dir)
            proc = subprocess.run(
                [sys.executable, "-m", "raw_view", "view", "-i", empty,
                 "-o", os.path.join(work, "o.png"),
                 "--width", "64", "--height", "64"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, cwd=str(_PROJECT_ROOT),
            )
            log_file = Path(log_dir) / "raw-view.log"
            self.assertTrue(log_file.is_file(), proc.stderr)
            text = log_file.read_text(encoding="utf-8")
            self.assertIn("View decode failed", text, "日志文件必须有失败记录")
            self.assertIn("Traceback (most recent call last)", text, "日志文件要有栈")
            self.assertIn("raw_view.__main__", text, "记录应来自 CLI logger")
            # ...while the user-facing stream stays a single line.
            self.assertNotIn("Traceback (most recent call last)", proc.stderr)
        finally:
            shutil.rmtree(log_dir, ignore_errors=True)
            shutil.rmtree(work, ignore_errors=True)


class BatchTargetValidationTests(unittest.TestCase):
    """D4 (message wording) and D6 (JSON/schema validation)."""

    def setUp(self):
        import cv2

        self._tmp = tempfile.mkdtemp(prefix="rv-d4-")
        self._png = os.path.join(self._tmp, "in.png")
        cv2.imwrite(self._png, np.zeros((4, 4, 3), dtype=np.uint8))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run_spec(self, payload) -> subprocess.CompletedProcess:
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        return _run_cli("batch", "-b", batch_path)

    def test_invalid_raw_type_is_reported_as_raw(self):
        """D4: `--target RAW --raw-type BOGUS` must not say "YUV subformat"."""
        proc = _run_cli(
            "convert", "-i", self._png, "-o", os.path.join(self._tmp, "o.raw"),
            "--target", "RAW", "--raw-type", "BOGUS", "--width", "4", "--height", "4",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("RAW", proc.stderr)
        self.assertNotIn("YUV subformat", proc.stderr)

    def test_invalid_yuv_type_is_reported_as_yuv(self):
        proc = _run_cli(
            "convert", "-i", self._png, "-o", os.path.join(self._tmp, "o.yuv"),
            "--target", "YUV", "--yuv-type", "BOGUS", "--width", "4", "--height", "4",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("YUV subformat", proc.stderr)

    def test_files_as_string_is_a_readable_error(self):
        proc = self._run_spec({"mode": "convert", "files": "abc"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid batch file", proc.stderr)
        self.assertIn("files", proc.stderr)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)

    def test_non_numeric_width_is_a_readable_error(self):
        proc = self._run_spec({"mode": "convert", "width": "abc", "files": []})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("width", proc.stderr)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)

    def test_unknown_mode_is_rejected(self):
        proc = self._run_spec({"mode": "FROBNICATE", "files": []})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid mode", proc.stderr)

    def test_lowercase_target_is_normalised_not_misrouted(self):
        """`"target": "raw"` used to silently take the YUV branch."""
        out = os.path.join(self._tmp, "o.raw")
        proc = self._run_spec({
            "mode": "convert",
            "target": "raw",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "files": [{"input": self._png, "output": out}],
        })
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(os.path.getsize(out), 16, "小写 target 必须仍按 RAW 编码")

    def test_invalid_target_is_rejected(self):
        proc = self._run_spec({"mode": "convert", "target": "PNG", "files": []})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid target", proc.stderr)

    def test_non_object_entry_is_rejected(self):
        proc = self._run_spec({"mode": "convert", "files": ["a.png"]})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("must be a JSON object", proc.stderr)

    def test_nan_is_rejected(self):
        proc = self._run_spec('{"mode": "convert", "width": NaN, "files": []}')
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NaN", proc.stderr)

    def test_per_entry_enum_errors_are_readable_not_tracebacks(self):
        """Per-entry mode/target/input_type validation happens inside the FAIL path."""
        import cv2

        png = os.path.join(self._tmp, "i.png")
        cv2.imwrite(png, np.zeros((4, 4, 3), dtype=np.uint8))
        for entry, needle in (
            ({"input": png, "mode": "FROBNICATE"}, "invalid mode"),
            ({"input": png, "target": "PNG"}, "invalid target"),
            ({"input": png, "input_type": "nope"}, "invalid input_type"),
        ):
            with self.subTest(entry=entry):
                proc = self._run_spec({
                    "mode": "convert", "target": "RAW", "raw_type": "RAW8",
                    "width": 4, "height": 4, "files": [entry],
                })
                self.assertEqual(proc.returncode, 1)
                combined = proc.stdout + proc.stderr
                self.assertIn(needle, combined)
                self.assertNotIn("Traceback (most recent call last)", combined)
                self.assertIn("FAIL", combined)

    def test_invalid_bayer_pattern_in_batch_is_rejected(self):
        """L3: ``"bayer_pattern": "XYZW"`` used to fall back to RGGB silently."""
        proc = self._run_spec({
            "mode": "convert", "target": "RAW", "raw_type": "RAW8",
            "width": 4, "height": 4, "bayer_pattern": "XYZW",
            "files": [{"input": self._png}],
        })
        self.assertEqual(proc.returncode, 1)
        self.assertIn("bayer_pattern", proc.stderr)
        self.assertIn("invalid", proc.stderr)

    def test_lowercase_bayer_pattern_in_batch_works(self):
        out = os.path.join(self._tmp, "lc.raw")
        proc = self._run_spec({
            "mode": "convert", "target": "RAW", "raw_type": "RAW8",
            "width": 4, "height": 4, "bayer_pattern": "rggb",
            "files": [{"input": self._png, "output": out}],
        })
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(os.path.getsize(out), 16)

    def test_malformed_json_is_a_readable_error(self):
        proc = self._run_spec("{not json")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("invalid batch JSON", proc.stderr)
        self.assertNotIn("Traceback (most recent call last)", proc.stderr)


class BayerPatternValidationTests(unittest.TestCase):
    """D5: `--bayer-pattern` is validated by argparse (exit 2), not degraded."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-d5-")
        import cv2

        self._png = os.path.join(self._tmp, "in.png")
        cv2.imwrite(self._png, np.zeros((4, 4, 3), dtype=np.uint8))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_invalid_bayer_pattern_exits_2(self):
        proc = _run_cli(
            "convert", "-i", self._png, "-o", os.path.join(self._tmp, "o.raw"),
            "--width", "4", "--height", "4", "--bayer-pattern", "WRONG",
        )
        self.assertEqual(proc.returncode, 2, "argparse 应在解析期拒绝")
        self.assertIn("invalid choice", proc.stderr)

    def test_invalid_bayer_pattern_in_view_is_rejected_the_same_way(self):
        raw = os.path.join(self._tmp, "a.raw")
        Path(raw).write_bytes(b"\x00" * 16)
        proc = _run_cli(
            "view", "-i", raw, "-o", os.path.join(self._tmp, "o.png"),
            "--width", "4", "--height", "4", "--raw-type", "RAW8",
            "--bayer-pattern", "WRONG",
        )
        self.assertEqual(proc.returncode, 2)

    def test_valid_patterns_are_accepted(self):
        from raw_view.models import BAYER_PATTERNS

        for pattern in BAYER_PATTERNS:
            with self.subTest(pattern=pattern):
                proc = _run_cli(
                    "convert", "-i", self._png, "-o",
                    os.path.join(self._tmp, f"{pattern}.raw"),
                    "--width", "4", "--height", "4", "--bayer-pattern", pattern,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_lowercase_and_mixed_case_are_accepted(self):
        """M2: the codec upper()s its input, so ``rggb`` always worked."""
        for pattern in ("rggb", "Rggb", "rGgB"):
            with self.subTest(pattern=pattern):
                proc = _run_cli(
                    "convert", "-i", self._png, "-o",
                    os.path.join(self._tmp, f"{pattern}.raw"),
                    "--width", "4", "--height", "4", "--bayer-pattern", pattern,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_lowercase_accepted_in_view_mode_too(self):
        raw = os.path.join(self._tmp, "a.raw")
        Path(raw).write_bytes(b"\x00" * (4 * 4 * 2))
        proc = _run_cli(
            "view", "-i", raw, "-o", os.path.join(self._tmp, "o.png"),
            "--width", "4", "--height", "4", "--raw-type", "RAW12",
            "--bayer-pattern", "rggb",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


class DangerousSuffixTests(unittest.TestCase):
    """D7: the CLI refuses executable/script inputs, like the GUI always did."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rv-d7-")
        self._exe = os.path.join(self._tmp, "notepad.exe")
        Path(self._exe).write_bytes(b"MZ\x90\x00" + b"\x00" * 60)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_shared_module_is_the_single_source(self):
        from raw_view import filetypes
        from raw_view.gui import app as app_mod

        self.assertIs(app_mod.is_dangerous_extension, filetypes.is_dangerous_extension)
        self.assertEqual(app_mod._DANGEROUS_EXTENSIONS, filetypes.DANGEROUS_EXTENSIONS)

    def test_view_refuses_executable(self):
        proc = _run_cli(
            "view", "-i", self._exe, "-o", os.path.join(self._tmp, "o.png"),
            "--width", "4", "--height", "4",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("executable", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "o.png")))

    def test_convert_refuses_executable(self):
        proc = _run_cli(
            "convert", "-i", self._exe, "-o", os.path.join(self._tmp, "o.raw"),
            "--width", "4", "--height", "4",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("executable", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "o.raw")))

    def test_batch_refuses_executable_entry(self):
        out = os.path.join(self._tmp, "o.raw")
        spec = {
            "mode": "convert",
            "target": "RAW",
            "raw_type": "RAW8",
            "width": 4,
            "height": 4,
            "files": [{"input": self._exe, "output": out}],
        }
        batch_path = os.path.join(self._tmp, "spec.json")
        Path(batch_path).write_text(json.dumps(spec), encoding="utf-8")
        proc = _run_cli("batch", "-b", batch_path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("executable", proc.stdout)
        self.assertFalse(os.path.exists(out))

    def test_suffixless_and_unknown_suffixes_are_not_affected(self):
        from raw_view.filetypes import is_dangerous_extension

        for name in ("frame", "frame.dat", "frame.bin", "frame.raw", "a.raw"):
            self.assertFalse(is_dangerous_extension(name), name)

    def test_case_insensitive(self):
        from raw_view.filetypes import is_dangerous_extension

        self.assertTrue(is_dangerous_extension("a.EXE"))
        self.assertTrue(is_dangerous_extension("a.Ps1"))

    def test_suffixless_raw_file_still_converts(self):
        src = os.path.join(self._tmp, "rawframe")
        Path(src).write_bytes(b"\x00" * 16)
        proc = _run_cli(
            "view", "-i", src, "-o", os.path.join(self._tmp, "o.png"),
            "--width", "4", "--height", "4", "--raw-type", "RAW8",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
