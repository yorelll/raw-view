"""Entry point: python -m raw_view [options] [files...]

CLI modes
---------
view (default)
    Decode RAW/YUV to a viewable image file (PNG/JPEG)::

        python -m raw_view view -i input.raw -o output.jpg --width 1920 --height 1080

    Without ``-i`` the interactive GUI is launched::

        python -m raw_view view file1.raw file2.png

convert
    Encode an image to RAW/YUV::

        python -m raw_view convert -i img.png -o output.raw --target RAW --width 1920 --height 1080

batch
    Batch convert/view multiple files from a JSON file.
    See ``--batch-help`` for the expected JSON format.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from raw_view.formats import FormatError
from raw_view.logger import get_logger

logger = get_logger(__name__)


def _make_utf8_stdio() -> None:
    """Reconfigure stdout/stderr to UTF-8 so Unicode CLI messages survive.

    On Windows the default console/stdout encoding is the system ANSI codepage
    (e.g. cp1252 on a US-locale runner, cp936/GBK on Chinese systems). The CLI
    prints box-drawing arrows and CJK text (``--batch-help``, progress lines),
    which raises ``UnicodeEncodeError`` when those characters have no mapping
    in the current codepage — a hard crash on GitHub-Actions Windows runners.
    Pin the encoding to UTF-8 and make encoding errors visible instead of
    raising, so the help/completion always prints.

    - ``PYTHONIOENCODING`` already exports a UTF-8 preference => leave alone
      (nothing to fix, avoids overwriting the user's explicit choice).
    - ``PYTHONUTF8=1`` (PEP 540 UTF-8 mode) already works => leave alone.
    """
    if sys.flags.utf8_mode or os.environ.get("PYTHONUTF8") == "1":
        return
    configured = os.environ.get("PYTHONIOENCODING", "").lower()
    if configured and "utf" in configured:
        return
    # 用 getattr 防御：某些环境（如 pytest 的 DontReadFromInput、部分嵌入宿主）
    # 会把 sys.stdin/stdout/stderr 换成没有 reconfigure 方法的对象，直接调用
    # 会 AttributeError。这里静默跳过无法重配置的流。
    for stream, kwargs in (
        (sys.stdin, {"encoding": "utf-8"}),
        (sys.stdout, {"encoding": "utf-8", "errors": "replace"}),
        (sys.stderr, {"encoding": "utf-8", "errors": "replace"}),
    ):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(**kwargs)
            except (OSError, ValueError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m raw_view",
        description="RAW/YUV image viewer and converter",
    )
    p.add_argument(
        "mode",
        nargs="?",
        default="view",
        choices=("view", "convert", "batch"),
        help="Operation mode (default: view)",
    )
    p.add_argument(
        "files",
        nargs="*",
        metavar="file",
        help="File(s) to open in view/GUI mode (optional)",
    )

    # ── Shared I/O ──
    p.add_argument("-i", "--input", help="Input file path")
    p.add_argument("-o", "--output", help="Output file path")
    p.add_argument("--width", type=int, default=640, help="Output width (default: 640)")
    p.add_argument("--height", type=int, default=480, help="Output height (default: 480)")
    p.add_argument("--offset", type=int, default=0, help="Data offset in bytes (default: 0)")

    # ── Encode options (convert mode) ──
    p.add_argument("-t", "--target", choices=("RAW", "YUV"), default="RAW",
                   help="Target/input type — RAW or YUV (default: RAW)")
    p.add_argument("--raw-type", default="RAW12",
                   help="RAW sub-format (default: RAW12)")
    p.add_argument("--yuv-type", default="YUYV",
                   help="YUV sub-format (default: YUYV)")
    p.add_argument("--alignment", choices=("lsb", "msb"), default="msb",
                   help="RAW alignment (default: msb)")
    p.add_argument("--endianness", choices=("little", "big"), default="little",
                   help="RAW endianness (default: little)")
    p.add_argument("--source-mode", choices=("bayer", "gray"), default="bayer",
                   help="RAW source mode (default: bayer)")
    p.add_argument("--bayer-pattern", default="RGGB",
                   help="Bayer pattern (default: RGGB)")

    # ── Decode options (view mode) ──
    p.add_argument("--preview-mode", choices=("Bayer Color", "Grayscale"), default="Bayer Color",
                   help="RAW preview mode for view/decode (default: Bayer Color)")

    # ── Batch options ──
    p.add_argument("-b", "--batch-file", help="Path to batch JSON file")
    p.add_argument(
        "--batch-help",
        action="store_true",
        help="Show batch JSON format and exit",
    )

    return p


def _show_batch_help() -> None:
    logger.debug("Showing batch JSON format help")
    print("""Batch JSON format:
（注意：batch JSON 文件须为 UTF-8 编码，含中文/unicode 键值时可正常读取）

{
  // ── Global defaults (applied to every file) ──
  "mode": "convert",              // "convert" (image→RAW/YUV) or "view" (RAW/YUV→image)
  "target": "RAW",
  "raw_type": "RAW12",
  "yuv_type": "YUYV",
  "width": 640,
  "height": 480,
  "alignment": "lsb",
  "endianness": "little",
  "source_mode": "bayer",
  "bayer_pattern": "RGGB",
  "preview_mode": "Bayer Color",  // only for mode="view"
  "offset": 0,
  "output_dir": null,             // optional. 若指定（全局或单条）则输出到该目录；
                                  // 未指定且未给 "output" 时，自动输出放到输入文件同目录。

  "files": [
    {
      "input": "path/to/image1.png",
      "output": "path/to/out1.raw"    // optional; auto-generated if omitted

      // ── Per-file overrides (any global key) ──
      // "width": 1920,
      // "mode": "view",
      // "preview_mode": "Grayscale",
      // ...
    },
    {
      "input": "path/to/file2.raw",
      "mode": "view",
      "width": 1920, "height": 1080,
      "bayer_pattern": "BGGR"
    }
  ]
}

If "output" is omitted, the path is auto-generated from the input name
+ resolution into the "output_dir" (or a default dir next to the input).
""")
    sys.exit(0)


# ── Resolve overrides ─────────────────────────────────────────────────

_RESOLVE_KEYS = (
    "mode", "target", "raw_type", "yuv_type", "width", "height",
    "alignment", "endianness", "source_mode", "bayer_pattern",
    "preview_mode", "offset", "output_dir",
)


def _resolve_entry_params(entry: dict, defaults: dict) -> dict:
    """Merge per-file entry overrides on top of global defaults."""
    params = dict(defaults)
    for key in _RESOLVE_KEYS:
        if key in entry:
            params[key] = entry[key]
    return params


# ── Constants ─────────────────────────────────────────────────────────

CONVERT_OUT_DIR = "convert_out"
VIEW_OUT_DIR = "view_out"


def _default_out_dir(mode: str) -> str:
    return VIEW_OUT_DIR if mode == "view" else CONVERT_OUT_DIR


# ── 解码尺寸上限保护 ─────────────────────────────────────────────────

def _check_decode_args_for(
    type_name: str, width: int, height: int, alignment: str = "msb", endianness: str = "little"
) -> None:
    """根据 target/raw/yuv 类型计算单帧字节并做上限校验（CLI 入口统一走这里）。

    复用 ``formats.require_decode_size``（MAX_DECODE_BYTES = 512MB）保证
    GUI / CLI / batch 共用同一上限。
    """
    from raw_view.formats import require_decode_size
    from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv

    if type_name.startswith("RAW"):
        frame_size = expected_frame_size_raw(type_name, width, height)
    else:
        frame_size = expected_frame_size_yuv(
            type_name, width, height, alignment=alignment, endianness=endianness
        )
    require_decode_size(width, height, frame_size)


# ── View mode (CLI decode + GUI fallback) ─────────────────────────────

def _run_view_mode(args: argparse.Namespace) -> None:
    """CLI decode (when -i given) or interactive GUI."""
    if args.input:
        _run_view_decode(
            input_path=args.input,
            output_path=args.output,
            width=args.width,
            height=args.height,
            target=args.target,  # describes input type (RAW or YUV)
            raw_type=args.raw_type,
            yuv_type=args.yuv_type,
            alignment=args.alignment,
            endianness=args.endianness,
            preview_mode=args.preview_mode,
            bayer_pattern=args.bayer_pattern,
            offset=args.offset,
        )
    else:
        _run_gui(args.files)


def _run_gui(files: list[str]) -> None:
    """Launch the interactive GUI, optionally opening the given files."""
    from raw_view.gui.app import run
    run(files)


def _run_view_decode(
    input_path: str,
    output_path: str | None,
    width: int,
    height: int,
    target: str,                   # "RAW" or "YUV" — describes input type
    raw_type: str,
    yuv_type: str,
    alignment: str,
    endianness: str,
    preview_mode: str,
    bayer_pattern: str,
    offset: int,
) -> None:
    """Decode a RAW/YUV file to a PNG/JPEG image."""
    # 直接调用本函数（不走 main()）时也要有 UTF-8 stdout，避免打印中文路径崩溃
    _make_utf8_stdio()
    if not os.path.isfile(input_path):
        logger.error("Input file not found: %s", input_path)
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # 解码前上限校验：单帧字节超过 512MB 直接拒绝
    try:
        _check_decode_args_for(
            raw_type if target == "RAW" else yuv_type, width, height,
            alignment=alignment, endianness=endianness,
        )
    except FormatError as exc:
        logger.error("Decode rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    from raw_view.converter import raw_file_to_image, yuv_file_to_image
    from raw_view.models import format_output_template

    if not output_path:
        output_path = format_output_template(
            "{input_stem}_{width}x{height}_{format}{ext}",
            input_path, width, height, target,
            output_dir=VIEW_OUT_DIR,
            output_ext=".png",
            raw_type=raw_type if target == "RAW" else "",
            yuv_type=yuv_type if target == "YUV" else "",
            bayer_pattern=bayer_pattern if target == "RAW" else "",
            source_mode="bayer" if (target == "RAW" and preview_mode.startswith("Bayer")) else "gray",
            alignment=alignment,
            endianness=endianness,
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    is_raw = target == "RAW"

    logger.info(
        "View decode: %s -> %s (%s, %dx%d, %s)",
        input_path, output_path, raw_type if is_raw else yuv_type, width, height,
    )

    print(f"Input:        {input_path}")
    print(f"Output:       {output_path}")
    print(f"Mode:         view (decode)")
    print(f"Input type:   {'RAW' if is_raw else 'YUV'}")
    print(f"Dimensions:   {width}x{height}")
    if is_raw:
        print(f"RAW type:     {raw_type}")
        print(f"Alignment:    {alignment}")
        print(f"Endianness:   {endianness}")
        print(f"Preview:      {preview_mode}")
        print(f"Bayer patt.:  {bayer_pattern if preview_mode.startswith('Bayer') else '-'}")
    else:
        print(f"YUV type:     {yuv_type}")
    print()

    try:
        if is_raw:
            size = raw_file_to_image(
                input_path, output_path, raw_type, width, height,
                alignment=alignment, endianness=endianness,
                preview_mode=preview_mode, bayer_pattern=bayer_pattern,
                offset=offset,
            )
        else:
            size = yuv_file_to_image(
                input_path, output_path, yuv_type, width, height,
                offset=offset,
                alignment=alignment, endianness=endianness,
            )
        logger.info("View decode OK: %s -> %s (%d bytes)", input_path, output_path, size)
        print(f"Decoded: {input_path} -> {output_path} ({size} bytes)")
    except Exception as exc:
        logger.exception("View decode failed: %s -> %s", input_path, output_path)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Convert mode (image → RAW/YUV) ───────────────────────────────────

def _run_convert(args: argparse.Namespace) -> None:
    """Single-file encode and print all parameters."""
    _make_utf8_stdio()
    if not args.input:
        logger.error("--input is required for convert mode")
        print("Error: --input is required for convert mode", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.input):
        logger.error("Input file not found: %s", args.input)
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # 编码前上限校验：输出单帧字节超过 512MB 直接拒绝
    try:
        _check_decode_args_for(
            args.raw_type if args.target == "RAW" else args.yuv_type,
            args.width, args.height,
            alignment=args.alignment, endianness=args.endianness,
        )
    except FormatError as exc:
        logger.error("Convert rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    from raw_view.converter import image_file_to_raw, image_file_to_yuv
    from raw_view.models import format_output_template

    output_path = args.output
    if not output_path:
        output_path = format_output_template(
            "{input_stem}_{width}x{height}_{format}{ext}",
            args.input, args.width, args.height, args.target,
            output_dir=CONVERT_OUT_DIR,
            raw_type=getattr(args, "raw_type", "") if args.target == "RAW" else "",
            yuv_type=getattr(args, "yuv_type", "") if args.target == "YUV" else "",
            bayer_pattern=getattr(args, "bayer_pattern", "") if args.target == "RAW" else "",
            source_mode=getattr(args, "source_mode", ""),
            alignment=getattr(args, "alignment", ""),
            endianness=getattr(args, "endianness", ""),
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Convert: %s -> %s (%s, %dx%d)",
        args.input, output_path, args.target, args.width, args.height,
    )

    print(f"Input:        {args.input}")
    print(f"Output:       {output_path}")
    print(f"Mode:         convert (encode)")
    print(f"Target:       {args.target}")
    if args.target == "RAW":
        print(f"RAW type:     {args.raw_type}")
        print(f"Alignment:    {args.alignment}")
        print(f"Endianness:   {args.endianness}")
        print(f"Source mode:  {args.source_mode}")
        print(f"Bayer patt.:  {args.bayer_pattern}")
    else:
        print(f"YUV type:     {args.yuv_type}")
    print(f"Dimensions:   {args.width}x{args.height}")
    print()

    try:
        if args.target == "RAW":
            size = image_file_to_raw(
                args.input, output_path,
                args.raw_type, args.width, args.height,
                alignment=args.alignment, endianness=args.endianness,
                source_mode=args.source_mode, bayer_pattern=args.bayer_pattern,
            )
        else:
            size = image_file_to_yuv(
                args.input, output_path,
                args.yuv_type, args.width, args.height,
                alignment=args.alignment, endianness=args.endianness,
            )
        logger.info("Convert OK: %s -> %s (%d bytes)", args.input, output_path, size)
        print(f"Converted: {args.input} -> {output_path} ({size} bytes)")
    except Exception as exc:
        logger.exception("Convert failed: %s -> %s", args.input, output_path)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Batch mode ────────────────────────────────────────────────────────

def _run_batch(args: argparse.Namespace) -> None:
    """Batch encode/decode from a JSON file."""
    _make_utf8_stdio()
    if args.batch_help:
        _show_batch_help()

    if not args.batch_file:
        print("Error: --batch-file is required for batch mode", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.batch_file):
        print(f"Error: batch file not found: {args.batch_file}", file=sys.stderr)
        sys.exit(1)

    # 显式 UTF-8 读取，避免中文路径/注释在 GBK/cp936 等 locale 下解码失败
    with open(args.batch_file, encoding="utf-8") as f:
        spec = json.load(f)

    # ── Global defaults ──
    defaults = {
        "mode": spec.get("mode", "convert"),
        "target": spec.get("target", "RAW"),
        "raw_type": spec.get("raw_type", "RAW12"),
        "yuv_type": spec.get("yuv_type", "YUYV"),
        "width": spec.get("width", 640),
        "height": spec.get("height", 480),
        "alignment": spec.get("alignment", "msb"),
        "endianness": spec.get("endianness", "little"),
        "source_mode": spec.get("source_mode", "bayer"),
        "bayer_pattern": spec.get("bayer_pattern", "RGGB"),
        "preview_mode": spec.get("preview_mode", "Bayer Color"),
        "offset": spec.get("offset", 0),
        "output_dir": spec.get("output_dir"),
    }

    from raw_view.converter import (
        image_file_to_raw,
        image_file_to_yuv,
        raw_file_to_image,
        yuv_file_to_image,
    )
    from raw_view.models import format_output_template

    files = spec.get("files", [])
    if not files:
        print("No files to process.")
        return

    success = 0
    failed = 0

    for entry in files:
        input_path = entry.get("input", "")
        if not input_path:
            print("  Skipping entry with no 'input' path")
            failed += 1
            continue
        if not os.path.isfile(input_path):
            print(f"  Skipping (not found): {input_path}")
            failed += 1
            continue

        params = _resolve_entry_params(entry, defaults)
        mode = params["mode"]
        target = params["target"]
        width = params["width"]
        height = params["height"]
        output_dir = params.pop("output_dir") or _default_out_dir(mode)

        # ── 解码前上限校验（防超大宽高导致 OOM/崩溃）──
        if mode == "view":
            view_type = params["raw_type"] if target == "RAW" else params["yuv_type"]
            try:
                _check_decode_args_for(
                    view_type, width, height,
                    alignment=params.get("alignment", "msb"),
                    endianness=params.get("endianness", "little"),
                )
            except FormatError as exc:
                print(f"  FAIL: {input_path} -> {exc}")
                failed += 1
                continue

        output_path = entry.get("output")
        if not output_path:
            if mode == "convert":
                out_ext = None
            else:
                out_ext = ".png"
            output_path = format_output_template(
                "{input_stem}_{width}x{height}_{format}{ext}",
                input_path, width, height, target,
                output_dir=output_dir,
                output_ext=out_ext,
                raw_type=params.get("raw_type", "") if target == "RAW" else "",
                yuv_type=params.get("yuv_type", "") if target == "YUV" else "",
                bayer_pattern=params.get("bayer_pattern", "") if target == "RAW" else "",
                source_mode=params.get("source_mode", ""),
                alignment=params.get("alignment", ""),
                endianness=params.get("endianness", ""),
            )
            # 未显式指定 output_dir（JSON 全局/entry 均未给）且无显式 output 时，
            # 保持旧行为：把自动输出放到输入文件同目录。一旦用户显式给了
            # output_dir 或 output，就尊重它，绝不覆盖（0.2.1 review M-2）。
            has_explicit_dir = (
                entry.get("output_dir") is not None
                or spec.get("output_dir") is not None
                or entry.get("output")  # 已由上方分支覆盖，这里不会命中 output
            )
            if not has_explicit_dir:
                output_path = str(Path(input_path).parent / Path(output_path).name)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            if mode == "convert":
                if target == "RAW":
                    size = image_file_to_raw(
                        input_path, output_path,
                        params["raw_type"], width, height,
                        alignment=params["alignment"],
                        endianness=params["endianness"],
                        source_mode=params["source_mode"],
                        bayer_pattern=params["bayer_pattern"],
                    )
                else:
                    size = image_file_to_yuv(
                        input_path, output_path,
                        params["yuv_type"], width, height,
                        alignment=params["alignment"],
                        endianness=params["endianness"],
                    )
            else:  # view mode
                is_raw = target == "RAW"
                if is_raw:
                    size = raw_file_to_image(
                        input_path, output_path,
                        params["raw_type"], width, height,
                        alignment=params["alignment"],
                        endianness=params["endianness"],
                        preview_mode=params["preview_mode"],
                        bayer_pattern=params["bayer_pattern"],
                        offset=params["offset"],
                    )
                else:
                    size = yuv_file_to_image(
                        input_path, output_path,
                        params["yuv_type"], width, height,
                        offset=params["offset"],
                        alignment=params["alignment"],
                        endianness=params["endianness"],
                    )
            logger.info("Batch OK: %s -> %s (%d bytes)", input_path, output_path, size)
            print(f"  OK: {input_path} -> {output_path} ({size} bytes)")
            success += 1
        except Exception as exc:
            logger.exception("Batch FAIL: %s -> %s", input_path, output_path)
            print(f"  FAIL: {input_path} -> {exc}")
            failed += 1

    logger.info("Batch complete: %d succeeded, %d failed", success, failed)
    print(f"\nBatch complete: {success} succeeded, {failed} failed")
    if failed > 0:
        sys.exit(1)


# ── Main dispatch ─────────────────────────────────────────────────────

def main() -> None:
    _make_utf8_stdio()
    parser = _build_parser()
    args = parser.parse_args()

    if args.batch_help:
        _show_batch_help()

    if args.mode == "convert":
        _run_convert(args)
    elif args.mode == "batch":
        _run_batch(args)
    elif args.mode == "view":
        _run_view_mode(args)
    else:
        _run_gui(args.files)


if __name__ == "__main__":
    main()
