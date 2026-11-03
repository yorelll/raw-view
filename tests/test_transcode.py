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

    def test_image_is_default_and_keeps_output_semantics(self):
        dialog = self.make_convert_dialog()
        self.assertEqual(dialog.input_type.currentText(), "Image")
        self.assertEqual(dialog.width.value(), 2560)
        self.assertEqual(dialog.height.value(), 1440)
        self.assertTrue(dialog.target_type.isEnabled())
        self.assertFalse(dialog.input_yuv_type.isVisibleTo(dialog))

    def test_switching_to_yuv_clears_dimensions(self):
        dialog = self.make_convert_dialog()
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual(dialog.width.value(), 1)
        self.assertEqual(dialog.height.value(), 1)
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

    def test_switching_to_yuv_clears_dimensions_and_pins_target(self):
        dialog = self._make()
        self.assertEqual(dialog.width.value(), 2560)
        dialog.target_type.setCurrentText("RAW")
        dialog.input_type.setCurrentText("YUV")
        self.assertEqual((dialog.width.value(), dialog.height.value()), (1, 1))
        self.assertEqual(dialog.target_type.currentText(), "YUV")
        self.assertFalse(dialog.target_type.isEnabled())
        self.assertTrue(dialog.input_yuv_type.isVisibleTo(dialog))

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
        dialog._variant_selector = None
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
        dialog._variant_selector = None
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
        self.assertIn("0.5.5", proc.stdout)
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
        """A handler whose log file is held open by *another* handle."""
        from raw_view.logger import SafeRotatingFileHandler

        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "raw-view.log")
        # Bigger than maxBytes so the very next record tries to roll over.
        with open(path, "wb") as f:
            f.write(b"x" * (8 * 1024))
        holder = open(path, "rb+")  # no delete sharing on Windows -> rename fails
        handler = SafeRotatingFileHandler(
            path, maxBytes=1024, backupCount=1, encoding="utf-8"
        )
        self.addCleanup(holder.close)
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

    def test_cli_stderr_stays_one_line_with_a_locked_log(self):
        """End-to-end: the exact combination that used to print stack traces."""
        log_dir = tempfile.mkdtemp(prefix="rv-rotcli-")
        work = tempfile.mkdtemp(prefix="rv-rotcli-w-")
        self.addCleanup(shutil.rmtree, log_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, work, ignore_errors=True)

        path = os.path.join(log_dir, "raw-view.log")
        with open(path, "wb") as f:
            f.write(b"x" * (5 * 1024 * 1024 + 1024))  # force a rollover
        empty = os.path.join(work, "empty.raw")
        open(empty, "wb").close()

        holder = open(path, "rb+")
        try:
            proc = _run_cli_env(
                {"RAW_VIEW_LOG_DIR": log_dir},
                "view", "-i", empty, "-o", os.path.join(work, "o.png"),
                "--width", "64", "--height", "64",
            )
        finally:
            holder.close()

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
