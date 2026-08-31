"""CLI/格式层修复的回归测试。

覆盖（与并行修复任务对应的文件范围）：
- M-5 ①：batch JSON 显式 UTF-8 读取（中文/unicode 键值）
- M-6   ：CLI view/convert 解码尺寸上限校验
- M-8   ：RAW32 平坦帧不再全黑（中性灰 128）+ 端序读写正确 + encode/decode 对称
- L-12  ：RAW_VIEW_LOG_LEVEL 环境变量控制 logger 级别
- H-2/H-3：raw_file_to_image / yuv_file_to_image 只读取所需帧字节区间
- L-1   ：_resolve_ext 死代码已删除

运行：.venv/Scripts/python -m unittest tests.test_cli_fixes -q
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

_MAIN_DIR = Path(__file__).resolve().parents[1]


def _run_cli(*argv: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "raw_view", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(_MAIN_DIR),
    )


class BatchJsonEncodingTests(unittest.TestCase):
    """M-5 ①：batch JSON 以 UTF-8 显式读取（中文/unicode 键值）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_m5_")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _call_batch(self, spec: dict):
        """构造 Namespace 调 _run_batch（不经过 argparse）。

        files 里的输入路径刻意不存在 —— 每个 entry 会被"not found"跳过，
        因此在写盘前完成 JSON 加载，正好验证 UTF-8 读取这一环；
        全程不触碰 converter/gui。
        """
        import raw_view.__main__ as main_mod

        batch_file = os.path.join(self._tmp, "spec.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        args = argparse.Namespace(batch_file=batch_file, batch_help=False)
        # _run_batch 末尾在 failed>0 时会 sys.exit(1)；测试里静默掉
        with mock.patch("raw_view.__main__.sys.exit"):
            return main_mod._run_batch(args)

    def _write_spec(self, spec: dict) -> str:
        batch_file = os.path.join(self._tmp, "spec.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(spec, f, ensure_ascii=False)
        return batch_file

    def test_unicode_key_and_value_loaded(self):
        """含中文 input 路径的值能正常读取，不抛 UnicodeDecodeError。"""
        spec = {
            "mode": "view",
            "target": "RAW",
            "raw_type": "RAW12",
            "width": 4,
            "height": 4,
            "files": [{"input": os.path.join(self._tmp, "测试 文件.raw"), "mode": "view"}],
        }
        self._call_batch(spec)  # 不抛错即通过

    def test_batch_help_mentions_utf8(self):
        """--batch-help 文本里注明 batch JSON 需 UTF-8。"""
        proc = _run_cli("--batch-help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("UTF-8", (proc.stdout or "") + (proc.stderr or ""))

    def test_batch_view_oversize_entry_fails(self):
        """batch view 超大尺寸 entry 应在写盘前被拦截为 FAIL（不 OOM 崩溃）。"""
        import raw_view.__main__ as main_mod

        big_in = os.path.join(self._tmp, "big.raw")
        with open(big_in, "wb") as f:
            f.write(b"\x00" * 64)
        spec = {
            "mode": "view",
            "target": "RAW",
            "raw_type": "RAW16",
            "width": 2**16 + 4,
            "height": 2**15,
            "files": [{"input": big_in}],
        }
        captured = []
        with mock.patch.object(main_mod.logger, "info") as mi, mock.patch.object(
            main_mod.logger, "exception"
        ) as _me, mock.patch.object(main_mod.logger, "debug") as _md, mock.patch(
            "raw_view.__main__.sys.exit"
        ) as mexit, mock.patch("raw_view.converter.raw_file_to_image") as m_conv:
            messages = []
            mexit.side_effect = lambda code: messages.append(code)
            main_mod._run_batch(
                argparse.Namespace(batch_file=self._write_spec(spec), batch_help=False)
            )

        # 拦截发生在 converter 调用之前：raw_file_to_image 不应被调用
        m_conv.assert_not_called()
        self.assertTrue(messages, "failed>0 应走到 sys.exit(1)")


class DecodeLimitTests(unittest.TestCase):
    """M-6：CLI view/convert 解码上限校验与拒绝退出行为。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_m6_")
        # view/convert 的输入文件需存在（缺失会在尺寸校验前先报错）
        self._in_raw = os.path.join(self._tmp, "in.raw")
        with open(self._in_raw, "wb") as f:
            f.write(b"\x00" * 64)
        self._in_png = os.path.join(self._tmp, "in.png")
        np.zeros((4, 4, 3), dtype=np.uint8).tofile(self._in_png)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # —— 纯函数级（快且稳）——

    def test_require_decode_size_rejects_oversize(self):
        from raw_view.__main__ import MAX_DECODE_BYTES, _require_decode_size
        from raw_view.formats import FormatError

        with self.assertRaises(FormatError) as ctx:
            _require_decode_size(32768, 8192, 1024 * 1024 * 1024)
        self.assertIn("too large", str(ctx.exception))
        with self.assertRaises(FormatError):
            _require_decode_size(32768, 8192, MAX_DECODE_BYTES + 1)
        # 正常尺寸通过
        _require_decode_size(4096, 2160, MAX_DECODE_BYTES)

    def test_check_decode_args_raw_and_yuv(self):
        from raw_view.__main__ import _check_decode_args_for
        from raw_view.formats import FormatError

        _check_decode_args_for("RAW12", 4096, 2160)     # 4K RAW12 ≈ 17.7MB
        _check_decode_args_for("YUYV", 4096, 2160)      # ≈ 17.7MB
        with self.assertRaises(FormatError):
            _check_decode_args_for("RAW32", 65535, 65535)  # ~17GB

    # —— 子进程级（验证 CLI 真实退出码 + stderr 文案）——

    def test_view_rejects_oversize_exit(self):
        proc = _run_cli(
            "view", "-i", self._in_raw, "-o", os.path.join(self._tmp, "o.png"),
            "--width", str(2**16 + 4), "--height", str(2**15),
            "--raw-type", "RAW16",
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = (proc.stderr or "") + (proc.stdout or "")
        self.assertTrue(
            ("上限" in combined) or ("过大" in combined) or ("too large" in combined),
            f"stderr 应含上限/过大字样, got: {combined}",
        )

    def test_convert_rejects_oversize_exit(self):
        proc = _run_cli(
            "convert", "-i", self._in_png, "-o", os.path.join(self._tmp, "o.raw"),
            "--width", str(2**16 + 4), "--height", str(2**15),
            "--raw-type", "RAW16",
        )
        self.assertNotEqual(proc.returncode, 0)
        combined = (proc.stderr or "") + (proc.stdout or "")
        self.assertTrue(
            ("上限" in combined) or ("过大" in combined) or ("too large" in combined),
            f"stderr 应含上限/过大字样, got: {combined}",
        )


class Raw32DisplayTests(unittest.TestCase):
    """M-8：RAW32 平坦帧显示 + 端序读写正确性。"""

    def test_raw32_all_zero_frame_neutral_gray(self):
        from raw_view.formats import ImageSpec, decode_raw, raw_to_display_gray

        w = h = 8
        data = bytes(w * h * 4)  # 全 0
        raw = decode_raw(data, ImageSpec(w, h), "RAW32", endianness="little")
        gray = raw_to_display_gray(raw, "RAW32")
        self.assertEqual(gray.shape, (w, h))
        self.assertTrue(np.all(gray == 128), f"全 0 帧应得中性灰 128, got {np.unique(gray)}")

    def test_raw32_constant_frame_neutral_gray(self):
        from raw_view.formats import ImageSpec, decode_raw, raw_to_display_gray

        w = h = 8
        arr = np.full((h, w), 0xDEADBEEF, dtype=np.uint32)
        for endianness in ("little", "big"):
            data = arr.astype("<u4" if endianness == "little" else ">u4").tobytes()
            raw = decode_raw(data, ImageSpec(w, h), "RAW32", endianness=endianness)
            gray = raw_to_display_gray(raw, "RAW32")
            self.assertTrue(
                np.all(gray == 128),
                f"{endianness} 恒定帧应得 128, got {np.unique(gray)}",
            )

    def test_raw32_roundtrip_both_endianness(self):
        """gray8 → RAW32 → decode 与 encode 的 float32 公式逐位一致（端序读写对称）。"""
        from raw_view.formats import (
            ImageSpec,
            decode_raw,
            gray8_to_raw_bytes,
            raw_to_display_gray,
        )

        raw8 = np.arange(256, dtype=np.uint8).reshape(16, 16)
        g = np.clip(raw8.astype(np.float32), 0, 255)
        expected = np.clip(
            np.round(g / 255.0 * ((1 << 32) - 1)), 0, (1 << 32) - 1
        ).astype(np.uint32)
        for endianness in ("little", "big"):
            encoded = gray8_to_raw_bytes(raw8, "RAW32", endianness=endianness)
            decoded = decode_raw(encoded, ImageSpec(16, 16), "RAW32", endianness=endianness)
            self.assertEqual(decoded.dtype, np.uint32)
            np.testing.assert_array_equal(
                decoded, expected, err_msg=f"endianness={endianness} 读写不一致"
            )
            display = raw_to_display_gray(decoded, "RAW32")
            self.assertEqual(display.dtype, np.uint8)

    def test_wrong_endianness_differs(self):
        """同一份数据用错误端序读取，内容应明显不同（端序确实生效）。"""
        from raw_view.formats import ImageSpec, decode_raw, gray8_to_raw_bytes

        raw8 = np.arange(256, dtype=np.uint8).reshape(16, 16)
        encoded_big = gray8_to_raw_bytes(raw8, "RAW32", endianness="big")
        decoded_little = decode_raw(encoded_big, ImageSpec(16, 16), "RAW32", endianness="little")
        g = np.clip(raw8.astype(np.float32), 0, 255)
        expected = np.clip(
            np.round(g / 255.0 * ((1 << 32) - 1)), 0, (1 << 32) - 1
        ).astype(np.uint32)
        self.assertFalse(bool((decoded_little == expected).all()))

    def test_raw32_ignores_alignment(self):
        """RAW32 无 16-bit 对齐语义：lsb/msb 读取结果一致（文档化行为）。"""
        from raw_view.formats import ImageSpec, decode_raw

        data = np.arange(16, dtype=np.uint32).astype("<u4").tobytes()
        lsb = decode_raw(data, ImageSpec(4, 4), "RAW32", alignment="lsb", endianness="little")
        msb = decode_raw(data, ImageSpec(4, 4), "RAW32", alignment="msb", endianness="little")
        np.testing.assert_array_equal(lsb, msb)


class LoggerLevelEnvTests(unittest.TestCase):
    """L-12：RAW_VIEW_LOG_LEVEL 环境变量控制 logger 级别（向后兼容）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_l12_")
        # 重置模块级初始化标志，保证每次 setup_logger 都重建
        import raw_view.logger as log_mod

        log_mod._initialized = False

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _fresh_setup(self, env_val):
        import raw_view.logger as log_mod

        env = dict(os.environ)
        if env_val is None:
            env.pop("RAW_VIEW_LOG_LEVEL", None)
        else:
            env["RAW_VIEW_LOG_LEVEL"] = env_val
        # 先清掉上次 setup 残留的 handler，避免同名字 logger 跨用例累计
        log_mod._initialized = False
        for h in list(logging.getLogger("raw_view_test_l12").handlers):
            logging.getLogger("raw_view_test_l12").removeHandler(h)
            h.close()
        with mock.patch.dict(os.environ, env, clear=True):
            log_mod._initialized = False
            return log_mod.setup_logger(
                name="raw_view_test_l12", level=logging.DEBUG, log_dir=self._tmp
            )

    def test_env_controls_logger_level(self):
        self.assertEqual(self._fresh_setup("INFO").level, logging.INFO)
        self.assertEqual(self._fresh_setup("DEBUG").level, logging.DEBUG)
        self.assertEqual(self._fresh_setup("WARNING").level, logging.WARNING)
        self.assertEqual(self._fresh_setup("20").level, logging.INFO)   # 数字形式
        self.assertEqual(self._fresh_setup(None).level, logging.DEBUG)  # 未设置 → 默认

    def test_file_handler_level_follows_env(self):
        lg = self._fresh_setup("INFO")
        file_handlers = [h for h in lg.handlers if getattr(h, "baseFilename", None)]
        self.assertTrue(file_handlers, "应有文件 handler")
        for h in file_handlers:
            self.assertGreaterEqual(h.level, logging.INFO)


# ── H-2/H-3：文件级解码只读取所需帧字节区间 ────────────────────────────


class _TrackingFile:
    """记录每次 read 的起始位置与长度，包装真实文件句柄。"""

    def __init__(self, underlying, log):
        self._f = underlying
        self._log = log
        self._pos = 0

    def seek(self, pos):
        self._pos = pos
        self._f.seek(pos)

    def read(self, n=-1):
        chunk = self._f.read(n)
        self._log.append((int(self._pos), len(chunk)))
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()


class ReadOneFrameRawTests(unittest.TestCase):
    """raw_file_to_image 只读 offset..offset+frame_size 区间（多帧文件）。"""

    _FRAME_BYTES = 4 * 4 * 2  # RAW12 4x4, 16-bit 存储

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_h2_")
        self._path = os.path.join(self._tmp, "multi.raw12")
        with open(self._path, "wb") as f:
            for i in range(3):
                (np.arange(self._FRAME_BYTES // 2, dtype=np.uint16) + i * 1000).astype(
                    "<u2"
                ).tofile(f)
        self.assertEqual(os.path.getsize(self._path), self._FRAME_BYTES * 3)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_offset_frame_decode(self):
        """offset=一帧大小 应解码第 2 帧并能出图。"""
        from raw_view.converter import raw_file_to_image

        out = os.path.join(self._tmp, "out.png")
        size = raw_file_to_image(
            self._path, out, "RAW12", 4, 4,
            alignment="msb", offset=self._FRAME_BYTES, preview_mode="Grayscale",
        )
        self.assertGreater(size, 0)

    def test_reads_only_needed_bytes(self):
        """只发生一次 read，起始位置=offset，长度=frame_size。"""
        from raw_view.converter import _read_frame

        reads = []
        real_open = open

        def _patched_open(name, mode="rb", **kw):
            return _TrackingFile(real_open(name, mode, **kw), reads)

        with mock.patch("raw_view.converter.open", side_effect=_patched_open):
            data = _read_frame(self._path, 4, 4, self._FRAME_BYTES, self._FRAME_BYTES)

        self.assertEqual(len(reads), 1, f"应只 read 一次, got {reads}")
        self.assertEqual(reads[0], (self._FRAME_BYTES, self._FRAME_BYTES))
        self.assertEqual(len(data), self._FRAME_BYTES)

    def test_offset_past_end_raises(self):
        """offset+frame_size 超过文件大小 → FormatError（语义与整读路径一致）。"""
        from raw_view.converter import raw_file_to_image
        from raw_view.formats import FormatError

        out = os.path.join(self._tmp, "nope.png")
        with self.assertRaises(FormatError):
            raw_file_to_image(
                self._path, out, "RAW12", 4, 4,
                offset=self._FRAME_BYTES * 2 + 1, preview_mode="Grayscale",
            )

    def test_short_file_raises(self):
        from raw_view.converter import raw_file_to_image
        from raw_view.formats import FormatError

        short = os.path.join(self._tmp, "short.raw12")
        with open(short, "wb") as f:
            f.write(b"\x00" * 8)  # 4x4 RAW12 需 32 字节
        out = os.path.join(self._tmp, "short.png")
        with self.assertRaises(FormatError):
            raw_file_to_image(short, out, "RAW12", 4, 4, preview_mode="Grayscale")


class ReadOneFrameYuvTests(unittest.TestCase):
    """yuv_file_to_image 只读所需帧字节区间（多帧 YUV 文件）。"""

    _FRAME_BYTES = 4 * 2 * 2  # YUYV 4x2 = 16 字节

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="rawview_h3_")
        self._path = os.path.join(self._tmp, "multi.yuyv")
        with open(self._path, "wb") as f:
            for i in range(3):
                np.full((self._FRAME_BYTES,), i + 1, dtype=np.uint8).tofile(f)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_offset_frame_decode(self):
        from raw_view.converter import yuv_file_to_image

        out = os.path.join(self._tmp, "out.png")
        size = yuv_file_to_image(self._path, out, "YUYV", 4, 2, offset=self._FRAME_BYTES)
        self.assertGreater(size, 0)

    def test_reads_only_needed_bytes(self):
        from raw_view.converter import yuv_file_to_image

        reads = []
        real_open = open

        def _patched_open(name, mode="rb", **kw):
            return _TrackingFile(real_open(name, mode, **kw), reads)

        with mock.patch("raw_view.converter.open", side_effect=_patched_open):
            out = os.path.join(self._tmp, "out2.png")
            yuv_file_to_image(self._path, out, "YUYV", 4, 2, offset=self._FRAME_BYTES)

        self.assertTrue(reads, "应发生过读取")
        self.assertEqual(reads[0], (self._FRAME_BYTES, self._FRAME_BYTES))

    def test_offset_past_end_raises(self):
        from raw_view.converter import yuv_file_to_image
        from raw_view.formats import FormatError

        out = os.path.join(self._tmp, "nope.png")
        with self.assertRaises(FormatError):
            yuv_file_to_image(self._path, out, "YUYV", 4, 2, offset=999)


class ResolveExtRemovedTests(unittest.TestCase):
    """L-1：_resolve_ext 已从 __main__ 删除。"""

    def test_resolve_ext_not_defined(self):
        import raw_view.__main__ as main_mod

        self.assertFalse(hasattr(main_mod, "_resolve_ext"))

    def test_source_does_not_contain(self):
        src = Path(_MAIN_DIR, "raw_view", "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn("def _resolve_ext", src)


if __name__ == "__main__":
    unittest.main()
