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
    p.add_argument("--alignment", choices=("lsb", "msb"), default="lsb",
                   help="RAW alignment (default: lsb)")
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
    print("""Batch JSON format:
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
  "output_dir": null,             // optional; defaults to convert_out/ or view_out/

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


def _resolve_ext(raw_type: str, yuv_type: str, mode: str) -> str:
    """Default output extension based on mode."""
    if mode == "convert":
        return ".raw" if raw_type != "N/A" else ".yuv"
    return ".png"  # view/decode mode


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
    if not os.path.isfile(input_path):
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    from raw_view.converter import raw_file_to_image, yuv_file_to_image
    from raw_view.models import format_output_template

    if not output_path:
        output_path = format_output_template(
            "{input_stem}_{width}x{height}{ext}",
            input_path, width, height, target,
            output_dir=VIEW_OUT_DIR,
            output_ext=".png",
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    is_raw = target == "RAW"

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
            )
        print(f"Decoded: {input_path} -> {output_path} ({size} bytes)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Convert mode (image → RAW/YUV) ───────────────────────────────────

def _run_convert(args: argparse.Namespace) -> None:
    """Single-file encode and print all parameters."""
    if not args.input:
        print("Error: --input is required for convert mode", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    from raw_view.converter import image_file_to_raw, image_file_to_yuv
    from raw_view.models import format_output_template

    output_path = args.output
    if not output_path:
        output_path = format_output_template(
            "{input_stem}_{width}x{height}{ext}",
            args.input, args.width, args.height, args.target,
            output_dir=CONVERT_OUT_DIR,
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

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
            )
        print(f"Converted: {args.input} -> {output_path} ({size} bytes)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Batch mode ────────────────────────────────────────────────────────

def _run_batch(args: argparse.Namespace) -> None:
    """Batch encode/decode from a JSON file."""
    if args.batch_help:
        _show_batch_help()

    if not args.batch_file:
        print("Error: --batch-file is required for batch mode", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.batch_file):
        print(f"Error: batch file not found: {args.batch_file}", file=sys.stderr)
        sys.exit(1)

    with open(args.batch_file) as f:
        spec = json.load(f)

    # ── Global defaults ──
    defaults = {
        "mode": spec.get("mode", "convert"),
        "target": spec.get("target", "RAW"),
        "raw_type": spec.get("raw_type", "RAW12"),
        "yuv_type": spec.get("yuv_type", "YUYV"),
        "width": spec.get("width", 640),
        "height": spec.get("height", 480),
        "alignment": spec.get("alignment", "lsb"),
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

        output_path = entry.get("output")
        if not output_path:
            if mode == "convert":
                out_ext = None
            else:
                out_ext = ".png"
            output_path = format_output_template(
                "{input_stem}_{width}x{height}{ext}",
                input_path, width, height, target,
                output_dir=output_dir,
                output_ext=out_ext,
            )
            # Place next to input when no explicit output dir was set
            if not entry.get("output"):
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
                    )
            print(f"  OK: {input_path} -> {output_path} ({size} bytes)")
            success += 1
        except Exception as exc:
            print(f"  FAIL: {input_path} -> {exc}")
            failed += 1

    print(f"\nBatch complete: {success} succeeded, {failed} failed")
    if failed > 0:
        sys.exit(1)


# ── Main dispatch ─────────────────────────────────────────────────────

def main() -> None:
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
