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
import glob
import json
import os
import re
import sys
from pathlib import Path

from raw_view.filetypes import is_dangerous_extension
from raw_view.formats import FormatError
from raw_view.logger import get_logger
from raw_view.models import BAYER_PATTERNS

# The RotatingFileHandler is installed on the ``raw_view`` logger (see
# raw_view/logger.py), so this module must log *below* it.  Using ``__name__``
# would resolve to ``"__main__"`` under ``python -m raw_view`` -- a sibling, not
# a child, of ``raw_view`` -- and every record (including failure tracebacks)
# would bypass the file handler entirely.
logger = get_logger("raw_view.__main__")


def _log_failure(message: str, *args: object, exc: BaseException) -> None:
    """Record a failed operation in the log file only (D2).

    The console handler shares this logger with the rotating file handler and
    emits at INFO, so ``logger.exception(...)`` printed the full Python stack on
    the user's terminal *in addition to* the CLI's own ``Error: ...`` line.  The
    CLI's user-facing contract is one readable line; the diagnosis stays in the
    log file (the file handler is at DEBUG, the console handler at INFO), so no
    debugging capability is lost.
    """
    logger.debug(message, *args, exc_info=exc)


# Historical CLI defaults for --width/--height (image -> RAW/YUV encode, and
# RAW/YUV -> image decode).  The parser leaves them as None so this release can
# tell "user did not pass it" from "user passed it" (a YUV transcode must reject
# a guessed 640x480); every pre-existing command backfills exactly these.
DEFAULT_WIDTH = 640
DEFAULT_HEIGHT = 480


# Windows 文件名非法字符（含不可见控制字符）。glob 展开 + 显式 output 派生输出
# 名时，输入 stem 可能含这些字符（如 Unix 侧创建的 "a:b<c>.png"）；直接拼进
# 输出名会在 Windows 上创建失败。统一替换为 "_"（0.3.0-M-1 相关路径健壮性）。
_OUTPUT_STEM_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_output_stem(stem: str) -> str:
    """把输入文件名 stem 消毒为跨平台合法的输出名字符串。"""
    return _OUTPUT_STEM_ILLEGAL.sub("_", stem)


def get_app_version() -> str:
    """返回应用版本号（单一来源，ENG-6：见 raw_view.models.APP_VERSION）。"""
    from raw_view.models import APP_VERSION

    return APP_VERSION


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
    # P2-4 / ENG-6：版本号单一来源（models.APP_VERSION），CLI 与 About/Help 一致。
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_app_version()}",
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
    # 默认值仍是 640x480（既有命令行为不变），但 parser 层留 None + 下方回填：
    # ``--input-type yuv`` 必须能区分"用户显式给了 --width/--height"与"走默认"，
    # 否则缺省会静默按 640x480 解析布局、产出错误结果。
    p.add_argument("--width", type=int, default=None,
                   help="Output width (image source) / source frame width "
                        "(YUV source). Default: 640 for image input; required "
                        "for --input-type yuv")
    p.add_argument("--height", type=int, default=None,
                   help="Output height (image source) / source frame height "
                        "(YUV source). Default: 480 for image input; required "
                        "for --input-type yuv")
    p.add_argument("--offset", type=int, default=0, help="Data offset in bytes (default: 0)")

    # ── Input source (convert mode) ──
    # 默认 image = 既有行为（解码 PNG/JPG/BMP 再编码）。yuv = YUV 家族内部
    # 无损字节重排（不 resize，width/height 是源帧尺寸）。raw 预留。
    p.add_argument("--input-type", choices=("image", "yuv", "raw"), default="image",
                   help="Convert-mode input source: image (decode PNG/JPG/BMP, "
                        "default), yuv (lossless YUV 4:2:2/4:2:0 rearrange) or "
                        "raw (reserved; not implemented yet)")
    p.add_argument("--input-yuv-type",
                   help="Input YUV sub-format for --input-type yuv "
                        "(e.g. NV16). Defaults to --yuv-type")
    p.add_argument("--input-alignment", choices=("lsb", "msb"),
                   help="Input RAW/YOnly bit alignment (reserved for future "
                        "input types). Defaults to --alignment")
    p.add_argument("--input-endianness", choices=("little", "big"),
                   help="Input RAW/YOnly byte order (reserved for future input "
                        "types). Defaults to --endianness")
    p.add_argument("--input-raw-type",
                   help="Input RAW sub-format for --input-type raw "
                        "(reserved; parsed but not converted yet)")

    # ── Encode options (convert mode) ──
    # default=None so a YUV source can default to YUV without hiding an explicit
    # "-t RAW" (which must stay an error); _apply_arg_defaults() backfills RAW.
    p.add_argument("-t", "--target", choices=("RAW", "YUV"), default=None,
                   help="Target/input type — RAW or YUV (default: RAW, or YUV "
                        "when --input-type yuv)")
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
    # D5: validate at parse time (exit 2) instead of silently degrading to gray
    # in view mode while failing with a traceback in convert mode.
    # `type=str.upper` keeps the rejection while still accepting the lowercase
    # spelling that always worked (the codec layer upper()s anyway) -- a case-
    # sensitive `choices` alone turned `--bayer-pattern rggb` into a hard error.
    p.add_argument("--bayer-pattern", type=str.upper,
                   choices=tuple(BAYER_PATTERNS), default="RGGB",
                   help="Bayer pattern, case-insensitive (default: RGGB)")

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
  "mode": "convert",              // "convert" (encode/transcode) or "view" (decode)
  "target": "RAW",                // "RAW" or "YUV" (case-insensitive)
  "input_type": "image",          // "image" (PNG/JPG/BMP), "yuv" (lossless YUV
                                  // 4:2:2/4:2:0 rearrange) or "raw" (reserved).
                                  // Per-entry: one batch may mix image and yuv.
                                  // Only meaningful for mode="convert"; see the
                                  // note below for mode="view".
  "input_yuv_type": "NV16",       // layout of the file being read.
                                  //   mode="convert" + input_type="yuv" -> the
                                  //     source layout (defaults to yuv_type)
                                  //   mode="view" -> the layout to DECODE
                                  //     (defaults to yuv_type; the same value the
                                  //     single-file `view --input-yuv-type` uses)
  "input_alignment": "msb",       // input bit alignment (raw/YOnly); also used by
                                  // mode="view" when decoding
  "input_endianness": "little",   // input byte order (raw/YOnly); also used by
                                  // mode="view" when decoding
  "input_raw_type": "RAW12",      // input RAW layout; mode="view" decodes with it
                                  // (defaults to raw_type), reserved otherwise
  "raw_type": "RAW12",
  "yuv_type": "YUYV",             // output YUV layout (transcode target)
  "width": 640,
  "height": 480,
  "alignment": "lsb",
  "endianness": "little",
  "source_mode": "bayer",
  "bayer_pattern": "RGGB",        // RGGB / GRBG / GBRG / BGGR
  "preview_mode": "Bayer Color",  // only for mode="view"
  "offset": 0,
  "output_dir": null,             // optional. 若指定（全局或单条）则输出到该目录；
                                  // 未指定且未给 "output" 时，自动输出放到输入文件同目录。
  "base_dir": null,               // optional (P2-3). 相对基准目录：files[].input
                                  // 的相对路径从该目录解析；给绝对路径则忽略。

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
    },
    {
      "input": "path/to/frame.nv16",   // YUV -> YUV lossless rearrange
      "input_type": "yuv",
      "input_yuv_type": "NV16",
      "yuv_type": "NV12",              // must stay in the same chroma family
      "width": 1920, "height": 1080    // SOURCE frame size — required, no resize
    }
  ]
}

"mode" / "target" / "input_type" are validated: an unknown or misspelled value
is rejected with a one-line error instead of silently running a different
operation.

mode="view" semantics -- what plain, pre-existing batch files always relied on:

  * "target" names the kind of file being DECODED (RAW or YUV), exactly as it
    always has; width/height default to 640x480 when omitted.
  * The input_* fields are optional overrides for that decode: input_yuv_type /
    input_raw_type pick the layout (defaulting to yuv_type / raw_type) and
    input_alignment / input_endianness override the decode alignment/byte order.
    They are read-only with respect to "target": setting input_type in a view
    entry does NOT switch it to transcoding.
  * Transcoding (and its "source frame dimensions are required" contract) only
    exists for mode="convert"; a convert entry that omits width/height therefore
    falls back to 640x480 as well, while input_type="yuv" + convert requires them.

Limits: one frame may not exceed 512 MB, for view (decoded frame), convert
(encoded output frame) and YUV transcode (both source and target layouts). The
check runs before anything is written, so an over-limit entry never leaves a
partial or gigantic file behind.

Glob expansion (P2-3): any "input" containing * ? []  is expanded via glob,
e.g. "frames/*.raw" turns each match into its own entry (per-file overrides
still apply). With "base_dir" set, relative "input" paths resolve from it
(first when present).

If "output" is omitted, the path is auto-generated from the input name
+ resolution into the "output_dir" (or a default dir next to the input).
Auto-generated paths are unique across the WHOLE batch: when two inputs derive
the same name (e.g. a/same.png and b/same.png), the later one is written as
"..._2" instead of overwriting the earlier result.

Explicit "output" + glob magic in the same entry (0.3.0-M-1): each expansion
gets a UNIQUE output name derived as "<stem>_<given output name>" in the same
directory (same-stem collisions are auto-suffixed "_2", "_3", ...; Windows-
illegal filename characters in the stem are replaced with "_"), so a batch
never silently overwrites one file with the previous match's result.
""")
    sys.exit(0)


# ── Resolve overrides ─────────────────────────────────────────────────

_RESOLVE_KEYS = (
    "mode", "target", "raw_type", "yuv_type", "width", "height",
    "alignment", "endianness", "source_mode", "bayer_pattern",
    "preview_mode", "offset", "output_dir",
    # Input-source keys: each entry interprets its own input_type, so a batch
    # can mix image and yuv inputs.
    "input_type", "input_yuv_type", "input_alignment", "input_endianness",
    "input_raw_type",
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


def _dedupe_output_path(
    output_path: str, used: dict[str, str], *, owner: str = "auto output"
) -> str:
    """Make an auto-generated *batch* output path unique across the batch (D3).

    ``_unique_output`` inside the glob expander already de-duplicated matches of
    one entry, but nothing coordinated *different* entries: ``a/same.png`` and
    ``b/same.png`` both auto-derive ``same_..._RAW.raw`` in the same output
    directory, the second overwrote the first, and the report claimed two
    successes.  Auto paths are only generated when the author did not name an
    output, so rewriting one cannot destroy a user-chosen filename - unlike an
    explicit ``output``, which is left exactly as written.

    *used* maps the already-taken (normalised) path to the input that took it, so
    the message can name both inputs.
    """
    key = os.path.normcase(os.path.abspath(output_path))
    if key not in used:
        used[key] = owner
        return output_path
    stem, ext = os.path.splitext(output_path)
    suffix = 2
    while True:
        candidate = f"{stem}_{suffix}{ext}"
        candidate_key = os.path.normcase(os.path.abspath(candidate))
        if candidate_key not in used:
            used[candidate_key] = owner
            print(
                f"  Note: auto output {os.path.basename(output_path)} is already "
                f"taken by {used[key]}; writing this entry to "
                f"{os.path.basename(candidate)} instead"
            )
            return candidate
        suffix += 1


# ── 解码尺寸上限保护 ─────────────────────────────────────────────────

def _check_decode_args_for(
    type_name: str,
    width: int,
    height: int,
    alignment: str = "msb",
    endianness: str = "little",
    *,
    target: str | None = None,
) -> None:
    """根据 target/raw/yuv 类型计算单帧字节并做上限校验（CLI 入口统一走这里）。

    复用 ``formats.require_decode_size``（MAX_DECODE_BYTES = 512MB）保证
    GUI / CLI / batch 共用同一上限。

    ``target``（``"RAW"`` / ``"YUV"``）决定用哪个 registry 解释 *type_name*。
    不给时按前缀猜（``RAW*`` → RAW，其余 → YUV），仅用于历史上只有一种类型
    的调用点；前缀猜测会把 ``--target RAW --raw-type BOGUS`` 报成"unsupported
    YUV subformat"（D4），因此新调用点一律显式传 ``target``。
    """
    from raw_view.formats import require_decode_size
    from raw_view.formats import expected_frame_size_raw, expected_frame_size_yuv

    is_raw = (
        target == "RAW" if target in ("RAW", "YUV") else type_name.startswith("RAW")
    )

    if is_raw:
        frame_size = expected_frame_size_raw(type_name, width, height)
    else:
        frame_size = expected_frame_size_yuv(
            type_name, width, height, alignment=alignment, endianness=endianness
        )
    require_decode_size(width, height, frame_size)


def _check_convert_args_for(
    target: str,
    raw_type: str,
    yuv_type: str,
    width: int,
    height: int,
    alignment: str = "msb",
    endianness: str = "little",
) -> None:
    """512MB frame-size guard for the *encode* direction (D1).

    ``convert`` used to skip the limit entirely: ``batch`` happily wrote a
    513MB ``RAW32`` frame (and could be asked for ~17GB at 65535x65535) while the
    single-file ``convert`` rejected the same parameters.  The frame size here is
    the **output** format's, because that is what gets written - the input is a
    decoded image and has no byte size of its own.
    """
    _check_decode_args_for(
        raw_type if target == "RAW" else yuv_type,
        width, height,
        alignment=alignment, endianness=endianness,
        target=target,
    )


def _check_transcode_args_for(spec, target: str = "YUV") -> None:
    """512MB guard for a YUV transcode, applied to *both* sides.

    Source and target frames are byte-identical in size inside one chroma family
    (that is what makes the rearrangement lossless), so one check would suffice
    today.  Checking both is deliberate: it keeps the guard correct if a future
    layer (unpacking, bit-depth conversion) changes the sizes, and it costs one
    integer comparison.  Sizes are computed through
    :func:`_check_decode_args_for`, i.e. the very same registry the rest of the
    CLI uses, so the limit cannot drift.
    """
    _check_decode_args_for(
        spec.source_format, spec.width, spec.height, target=target,
        alignment=spec.alignment, endianness=spec.endianness,
    )
    _check_decode_args_for(
        spec.target_format, spec.width, spec.height, target=target,
        alignment=spec.alignment, endianness=spec.endianness,
    )


# ── View mode (CLI decode + GUI fallback) ─────────────────────────────

def _apply_arg_defaults(args: argparse.Namespace) -> None:
    """Backfill the historical defaults of ``--width``/``--height``/``--target``.

    Those three flags default to ``None`` in the parser so this release can tell
    "user did not pass it" from "user passed it".  A YUV transcode needs that
    distinction (a guessed 640x480 would parse the layout wrong, and an explicit
    ``-t RAW`` must stay an error); every pre-existing command funnels through
    here and keeps its documented defaults.
    """
    if getattr(args, "width", None) is None:
        args.width = DEFAULT_WIDTH
    if getattr(args, "height", None) is None:
        args.height = DEFAULT_HEIGHT
    if getattr(args, "target", None) is None:
        args.target = "RAW"


def _run_view_mode(args: argparse.Namespace) -> None:
    """CLI decode (when -i given) or interactive GUI."""
    _apply_arg_defaults(args)
    if args.input:
        raw_type = args.input_raw_type or args.raw_type
        yuv_type = args.input_yuv_type or args.yuv_type
        _run_view_decode(
            input_path=args.input,
            output_path=args.output,
            width=args.width,
            height=args.height,
            target=args.target,  # describes input type (RAW or YUV)
            raw_type=raw_type,
            yuv_type=yuv_type,
            alignment=args.input_alignment or args.alignment,
            endianness=args.input_endianness or args.endianness,
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
    # D7: an executable/script must never be decoded as image data.
    if is_dangerous_extension(input_path):
        msg = (
            f"refusing to decode executable/script file as image data: "
            f"{os.path.basename(input_path)}"
        )
        logger.error("Decode rejected: %s", msg)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    # 解码前上限校验：单帧字节超过 512MB 直接拒绝
    try:
        _check_decode_args_for(
            raw_type if target == "RAW" else yuv_type, width, height,
            alignment=alignment, endianness=endianness,
            target="RAW" if target == "RAW" else "YUV",
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
        preview_mode if is_raw else "-",
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
        _log_failure("View decode failed: %s -> %s", input_path, output_path, exc=exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Convert mode (image → RAW/YUV) ───────────────────────────────────

def _reject_input_file(input_path: str) -> None:
    """Refuse an executable/script input for every convert/batch entry (D7).

    The GUI already refused these; the CLI decoded ``notepad.exe`` into a PNG and
    exited 0.  A suffix-less or unknown-suffix file (``.dat``/``.bin``/none) is
    *not* affected: those are legitimate RAW inputs.
    """
    if not is_dangerous_extension(input_path):
        return
    msg = (
        f"refusing to convert executable/script file as image data: "
        f"{os.path.basename(input_path)}"
    )
    logger.error("Convert rejected: %s", msg)
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


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

    input_type = getattr(args, "input_type", "image") or "image"
    if input_type == "yuv":
        _run_convert_yuv(args)
        return
    if input_type == "raw":
        # 预留入口：参数可解析，但本轮不实现 RAW 家族转换（packed/unpacked、
        # 位对齐、位深）——明确报错，绝不静默按 image 处理。
        _print_input_type_unavailable("raw")
        sys.exit(1)

    _reject_input_file(args.input)
    _apply_arg_defaults(args)

    # 编码前上限校验：输出单帧字节超过 512MB 直接拒绝
    try:
        _check_convert_args_for(
            args.target, args.raw_type, args.yuv_type, args.width, args.height,
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
        _log_failure("Convert failed: %s -> %s", args.input, output_path, exc=exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Convert mode: YUV family lossless transcode ──────────────────────
#
# Semantics differ deliberately from the image path above: --width/--height are
# the *source frame* dimensions used to parse the byte layout, there is no
# resize, and multi-frame files are transcoded frame by frame.  Everything that
# decides which directions exist lives in raw_view.transcode (the GUI greys out
# targets using the same reject_reason()).

def _print_input_type_unavailable(input_type: str) -> None:
    """Explain a reserved --input-type instead of guessing at it."""
    if input_type == "raw":
        msg = (
            "--input-type raw is reserved for a future release: RAW <-> YUV / "
            "packed RAW conversion (bit unpacking, bit alignment, bit depth) is "
            "not implemented yet. This build only re-arranges YUV 4:2:2 / 4:2:0 "
            "frames losslessly (--input-type yuv)."
        )
    else:  # pragma: no cover - argparse restricts the choices
        msg = f"--input-type {input_type} is not supported"
    logger.error("Convert rejected: %s", msg)
    print(f"Error: {msg}", file=sys.stderr)


def _require_source_dimensions(args: argparse.Namespace) -> tuple[int, int]:
    """Return (width, height) for a YUV input, failing if they were not given.

    The image path treats width/height as an *output* size and has a 640x480
    default.  For a YUV transcode they are the source frame geometry: assuming a
    default would parse the bytes at the wrong stride and emit silently corrupt
    output, so the CLI demands them explicitly.
    """
    missing = [
        flag for flag, value in (("--width", args.width), ("--height", args.height))
        if value is None
    ]
    if missing:
        msg = (
            f"{' and '.join(missing)} required with --input-type yuv: "
            "width/height describe the SOURCE frame layout (no resize is "
            "performed), so they cannot be defaulted"
        )
        logger.error("Convert rejected: %s", msg)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)
    if args.width <= 0 or args.height <= 0:
        msg = f"invalid source dimensions: {args.width}x{args.height} (must be > 0)"
        logger.error("Convert rejected: %s", msg)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)
    return args.width, args.height


def _run_convert_yuv(args: argparse.Namespace) -> None:
    """Losslessly transcode a YUV 4:2:2 / 4:2:0 file into another layout."""
    from raw_view.models import format_output_template
    from raw_view.transcode import (
        TranscodeError,
        TranscodeSpec,
        frame_count,
        reject_reason,
        transcode_file,
    )

    width, height = _require_source_dimensions(args)

    source_format = args.input_yuv_type or args.yuv_type
    target_format = args.yuv_type

    # -t defaults to None meaning "unspecified"; for a YUV source the only
    # meaningful target is YUV, so backfill it there and reject an explicit
    # non-YUV target (which the user really did ask for).
    if args.target is None:
        args.target = "YUV"
    if args.target != "YUV":
        msg = (
            f"--input-type yuv cannot target {args.target}: a YUV source is "
            "transcoded into another YUV layout; use --target YUV (and "
            "--yuv-type for the output format)"
        )
        logger.error("Convert rejected: %s", msg)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    reason = reject_reason(source_format, target_format)
    if reason:
        logger.error("Convert rejected: %s", reason)
        print(f"Error: {reason}", file=sys.stderr)
        sys.exit(1)

    spec = TranscodeSpec(
        source_format=source_format,
        target_format=target_format,
        width=width,
        height=height,
        alignment=args.input_alignment or args.alignment,
        endianness=args.input_endianness or args.endianness,
        offset=args.offset,
    )

    # 上限校验（源帧尺寸 / 目标格式两侧都查）——在产出任何输出之前。
    try:
        _check_transcode_args_for(spec)
    except FormatError as exc:
        logger.error("Convert rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 先按文件大小验证帧数/整帧倍数，再产出输出路径与文件——非整帧输入
    # 绝不留下半成品（也没有"只转第一帧"的静默截断）。
    try:
        count = frame_count(os.path.getsize(args.input), spec)
    except TranscodeError as exc:
        logger.error("Convert rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    if not output_path:
        # {format} 渲染为目标布局；尺寸仍是源帧尺寸（本轮不改变几何）。
        output_path = format_output_template(
            "{input_stem}_{width}x{height}_{format}{ext}",
            args.input, width, height, "YUV",
            output_dir=CONVERT_OUT_DIR,
            yuv_type=target_format,
        )

    logger.info(
        "Convert (YUV rearrange): %s -> %s (%s -> %s, %dx%d, %d frame(s))",
        args.input, output_path, source_format, target_format, width, height, count,
    )

    print(f"Input:        {args.input}")
    print(f"Output:       {output_path}")
    print(f"Mode:         convert (YUV lossless rearrange)")
    print(f"Input type:   YUV")
    print(f"Source fmt:   {source_format}")
    print(f"YUV type:     {target_format}")
    print(f"Dimensions:   {width}x{height} (source frame, no resize)")
    print(f"Frames:       {count}")
    if args.offset:
        print(f"Offset:       {args.offset}")
    print()

    try:
        count, size = transcode_file(args.input, output_path, spec)
        logger.info(
            "Convert OK: %s -> %s (%d bytes, %d frame(s))",
            args.input, output_path, size, count,
        )
        print(
            f"Converted: {args.input} -> {output_path} "
            f"({size} bytes, {count} frame(s))"
        )
    except Exception as exc:
        _log_failure("Convert failed: %s -> %s", args.input, output_path, exc=exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ── Batch mode ────────────────────────────────────────────────────────

class _BatchSpecError(ValueError):
    """The batch JSON as a whole is unusable (bad shape / unknown enum values)."""


_BATCH_MODES = ("convert", "view")
_BATCH_TARGETS = ("RAW", "YUV")
_BATCH_INPUT_TYPES = ("image", "yuv", "raw")
# ints and numeric strings are both accepted (JSON authors write either); bool
# is rejected explicitly because ``True`` is an int subclass but never a size.
_BATCH_NUMERIC_KEYS = ("width", "height", "offset")


def _reject_json_constant(token: str):
    """Reject the non-standard ``NaN``/``Infinity`` JSON scalars explicitly.

    ``json.load`` accepts them by default and would then fail deep inside the
    converter with a bare ``TypeError``/``OverflowError`` traceback (D6).
    """
    raise _BatchSpecError(
        f"invalid JSON value {token!r}: use plain numbers (NaN/Infinity are not "
        "valid JSON)"
    )


def _normalize_choice(value: object, allowed: tuple[str, ...], what: str) -> str:
    """Validate an enum-like batch field, case-insensitively (D6).

    ``"target": "raw"`` used to fall through to the YUV branch and silently
    convert to the wrong format; an unknown ``"mode"`` silently ran as ``view``.
    Both are hard errors now.  Matching is case-insensitive; the canonical
    spelling from *allowed* is returned.
    """
    text = str(value).strip()
    for candidate in allowed:
        if text.upper() == candidate.upper():
            return candidate
    raise _BatchSpecError(
        f"invalid {what} {text!r}: expected one of {', '.join(allowed)}"
    )


def _coerce_numeric(container: dict, key: str, label: str) -> None:
    """Turn ``container[key]`` into an int in place, or raise ``_BatchSpecError``.

    JSON authors write ``"width": 1920`` or ``"width": "1920"``; both are fine.
    ``"abc"``, ``True`` and containers are not, and used to survive far enough to
    raise ``TypeError``/``AttributeError`` mid-conversion (D6).
    """
    if key not in container or container[key] is None:
        return
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _BatchSpecError(
            f"{label} must be a number, got {type(value).__name__}"
        )
    try:
        container[key] = int(value)
    except ValueError as exc:
        raise _BatchSpecError(
            f"{label} must be an integer, got {value!r}"
        ) from exc


def _coerce_bayer_pattern(container: dict, label: str) -> None:
    """Normalise/validate ``bayer_pattern`` in a batch document (L3).

    The codec layer upper-cases its input, so ``"rggb"`` has always worked; an
    outright invalid value (``"XYZW"``) silently fell back to RGGB instead of
    saying so.  Folding case here keeps lowercase working while making a typo a
    readable batch error -- the same contract ``--bayer-pattern`` now has.
    """
    if "bayer_pattern" not in container or container["bayer_pattern"] is None:
        return
    value = container["bayer_pattern"]
    if not isinstance(value, str):
        raise _BatchSpecError(
            f"{label}.bayer_pattern must be a string, got {type(value).__name__}"
        )
    folded = value.strip().upper()
    if folded not in BAYER_PATTERNS:
        raise _BatchSpecError(
            f"{label}.bayer_pattern {value!r} is invalid: expected one of "
            f"{', '.join(BAYER_PATTERNS)} (case-insensitive)"
        )
    container["bayer_pattern"] = folded


def _sheet_check_entry(entry: object, position: int) -> dict:
    """Validate one ``files[]`` item's *shape* (D6).

    Reported per entry so the author sees which item is broken, instead of an
    ``AttributeError`` on ``{"files": "abc"}`` or a ``TypeError`` on
    ``{"width": "abc"}``.
    """
    if not isinstance(entry, dict):
        raise _BatchSpecError(
            f"files[{position}] must be a JSON object, got {type(entry).__name__}"
        )
    for key in _BATCH_NUMERIC_KEYS:
        _coerce_numeric(entry, key, f"files[{position}].{key}")
    _coerce_bayer_pattern(entry, f"files[{position}]")
    return entry


def _validate_batch_spec(spec: object) -> dict:
    """Validate the top-level batch document, returning it for use (D6)."""
    if not isinstance(spec, dict):
        raise _BatchSpecError(
            f"batch JSON must be an object, got {type(spec).__name__}"
        )
    for key in ("mode", "target", "input_type"):
        if spec.get(key) is not None:
            allowed = {
                "mode": _BATCH_MODES,
                "target": _BATCH_TARGETS,
                "input_type": _BATCH_INPUT_TYPES,
            }[key]
            _normalize_choice(spec[key], allowed, key)
    for key in _BATCH_NUMERIC_KEYS:
        _coerce_numeric(spec, key, key)
    _coerce_bayer_pattern(spec, "batch")
    files = spec.get("files", [])
    if files is None:
        files = []
    if not isinstance(files, list):
        raise _BatchSpecError(
            f'"files" must be a JSON array of objects, got {type(files).__name__}'
        )
    spec["files"] = [
        _sheet_check_entry(entry, index) for index, entry in enumerate(files)
    ]
    return spec


def _resolve_entry_input_type(params: dict, mode: str) -> str:
    """Which kind of input this entry names: ``"image"``, ``"yuv"`` or ``"raw"``.

    In **view** mode ``target`` has always described the *input* file, so an
    entry's own ``input_type`` is not consulted there: view decodes whatever
    ``target`` says.  That keeps every pre-existing batch view job working
    exactly as before.  The strict "source frame dimensions are required"
    contract is *not* keyed on this function -- it is keyed on
    ``mode == "convert"`` in :func:`_resolve_entry_dimensions`, because
    transcoding (unlike decoding) never gets to guess a layout.  See the note in
    that function.
    """
    if mode == "view":
        return "yuv" if params.get("target") == "YUV" else "raw"
    return _normalize_choice(
        params.get("input_type") or "image", _BATCH_INPUT_TYPES, "input_type"
    )


def _resolve_entry_dimensions(
    kind: str, width, height, mode: str
) -> tuple[int, int]:
    """Validate/backfill width & height for the entry's input kind.

    Source frame dimensions are mandatory only for a YUV transcode: that is, a
    YUV input in **convert** mode, where width/height describe the byte layout
    and nothing is resized.  A wrong guess there would emit silently corrupt
    output, so it is refused instead of defaulted.

    Everything else -- including decoding a YUV file in view mode, which is the
    long-standing behaviour -- keeps the historical 640x480 default.  The
    ``mode == "convert"`` half of the condition is the actual defence for M1;
    ``kind`` alone would break every legacy batch view job.
    """
    requires_dimensions = kind == "yuv" and mode == "convert"
    if not requires_dimensions:
        return (
            DEFAULT_WIDTH if width is None else int(width),
            DEFAULT_HEIGHT if height is None else int(height),
        )
    if width is None or height is None:
        missing = " and ".join(
            name for name, value in (("width", width), ("height", height))
            if value is None
        )
        raise _BatchSpecError(
            f"{missing} is required for input_type yuv: it describes the SOURCE "
            "frame layout (no resize is performed) and cannot be defaulted"
        )
    return int(width), int(height)


def _entry_transcode_spec(params: dict, width: int, height: int):
    """Build and pre-validate the TranscodeSpec for one YUV batch entry."""
    from raw_view.transcode import TranscodeError, TranscodeSpec, reject_reason

    if params.get("mode") != "convert":
        raise _BatchSpecError(
            "input_type yuv is only supported with mode=convert (a YUV source "
            "is transcoded into another YUV layout, not decoded to an image)"
        )
    if params.get("target") != "YUV":
        raise _BatchSpecError(
            f"input_type yuv cannot target {params.get('target')}: use "
            'target "YUV" and yuv_type for the output layout'
        )
    source_format = params.get("input_yuv_type") or params.get("yuv_type") or "YUYV"
    target_format = params.get("yuv_type") or "YUYV"
    reason = reject_reason(source_format, target_format)
    if reason:
        raise _BatchSpecError(reason)
    try:
        return TranscodeSpec(
            source_format=source_format,
            target_format=target_format,
            width=width,
            height=height,
            alignment=params.get("input_alignment") or params.get("alignment", "msb"),
            endianness=params.get("input_endianness") or params.get("endianness", "little"),
            offset=int(params.get("offset") or 0),
        )
    except TranscodeError as exc:  # pragma: no cover - defensive
        raise _BatchSpecError(str(exc)) from exc


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

    # 显式 UTF-8 读取，避免中文路径/注释在 GBK/cp936 等 locale 下解码失败。
    # JSON/schema 错误一律给一行可读提示（D6），不再抛 AttributeError/TypeError
    # 的完整栈；未知 mode/target 明确拒绝，绝不静默换语义。
    try:
        with open(args.batch_file, encoding="utf-8") as f:
            spec = json.load(f, parse_constant=_reject_json_constant)
        spec = _validate_batch_spec(spec)
    except _BatchSpecError as exc:
        print(f"Error: invalid batch file: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid batch JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    except UnicodeDecodeError as exc:
        print(
            f"Error: batch file must be UTF-8 encoded: {exc}", file=sys.stderr
        )
        sys.exit(1)

    # ── Global defaults ──
    # Unknown enum values were rejected above, so the fallbacks here are only
    # reached when a key is absent.
    defaults = {
        "mode": _normalize_choice(spec.get("mode", "convert"), _BATCH_MODES, "mode"),
        "target": _normalize_choice(spec.get("target", "RAW"), _BATCH_TARGETS, "target"),
        "raw_type": spec.get("raw_type", "RAW12"),
        "yuv_type": spec.get("yuv_type", "YUYV"),
        # width/height stay None unless given: the image path backfills its
        # historical 640x480, but a YUV transcode must reject a missing value
        # instead of parsing the layout at a guessed size.
        "width": spec.get("width"),
        "height": spec.get("height"),
        "alignment": spec.get("alignment", "msb"),
        "endianness": spec.get("endianness", "little"),
        "source_mode": spec.get("source_mode", "bayer"),
        "bayer_pattern": spec.get("bayer_pattern", "RGGB"),
        "preview_mode": spec.get("preview_mode", "Bayer Color"),
        "offset": spec.get("offset", 0),
        "output_dir": spec.get("output_dir"),
        # New in this release: per-entry input source. Entries may mix image and
        # yuv inputs; each is interpreted by its own input_type.
        # None (not "image") when the document never mentions it: "declared
        # image" and "did not say" are different things in view mode, where a
        # legacy entry must keep the historical target-based inference.
        "input_type": spec.get("input_type"),
        "input_yuv_type": spec.get("input_yuv_type", ""),
        "input_alignment": spec.get("input_alignment", ""),
        "input_endianness": spec.get("input_endianness", ""),
        "input_raw_type": spec.get("input_raw_type", ""),
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

    # ── P2-3：相对路径基准 + glob 展开 ─────────────────────────────────
    # "base_dir": 可选顶层字段，作为 files[].input 的基准目录（相对路径解析
    # 基准）。glob：input 含 "*"/"?"/"[]" 时按 glob 展开成一批文件。
    base_dir = spec.get("base_dir", "") or ""
    if base_dir and not os.path.isabs(base_dir):
        base_dir = os.path.abspath(base_dir)

    expanded_files: list[dict] = []
    for entry in files:
        raw_input = entry.get("input", "")
        if not raw_input:
            expanded_files.append(entry)
            continue
        if base_dir:
            raw_input = (
                os.path.join(base_dir, raw_input)
                if not os.path.isabs(raw_input)
                else raw_input
            )
        if glob.has_magic(raw_input):
            matches = sorted(glob.glob(raw_input))
            if not matches:
                expanded_files.append(entry)
            else:
                # 0.3.0-M-1：若 entry 显式给了 "output"，展开的每个匹配都必须派生
                # 唯一输出名——旧的 ``{**entry, "input": m}`` 会保留同一个 output，
                # N 个匹配写同一文件、后写覆盖先写（静默丢数据）。派生规则：把
                # 用户给的 output 文件名前插入输入 stem，目录保持不变。
                explicit_output = entry.get("output")
                if explicit_output:
                    _out_dir = os.path.dirname(explicit_output) or "."
                    _out_path = Path(explicit_output)
                    _out_suffix = _out_path.suffix  # "" 表示无扩展名
                    # 同一入口内已派生过的输出名：不同输入可能映射到同一派生名
                    # （同 stem 不同扩展名，如 a.png/a.jpg → 都派生 a_sink.raw）
                    # ——若不做去重，后一个会静默覆盖前一个（M-1 同类问题）。
                    # 碰撞时对后来者追加序号，保证本入口内输出路径两两不同。
                    _used_outputs: dict[str, str] = {}

                    # 输入 stem 可能含 Windows 非法字符（'a:b.png' 等）；直接拼进
                    # 输出名会创建失败或写错位置，这里把它消毒成合法文件名字符，
                    # 保证派生名对 Windows / ext4 都可用（0.2.2-L-3 相关路径健壮性）。
                    def _unique_output(match: str) -> str:
                        stem = _sanitize_output_stem(Path(match).stem)
                        base = os.path.join(
                            _out_dir,
                            f"{stem}_{_out_path.stem}{_out_suffix}",
                        )
                        return _dedupe_output_path(base, _used_outputs, owner=match)

                    for m in matches:
                        e = {**entry, "input": m}
                        e["output"] = _unique_output(m)
                        expanded_files.append(e)
                else:
                    expanded_files.extend(
                        {**entry, "input": m} for m in matches
                    )
        elif os.path.isabs(raw_input):
            expanded_files.append({**entry, "input": raw_input})
        else:
            # 不带魔法符号且为相对路径：归一为基准下（或本 CWD 下）的绝对路径，
            # 保证 os.path.isfile 判定一致。
            expanded_files.append({**entry, "input": os.path.abspath(raw_input)})
    files = expanded_files

    success = 0
    failed = 0
    # D3: auto-generated output paths must be unique across the WHOLE batch, not
    # only inside one entry's glob expansion.  Two inputs with the same basename
    # (a/same.png + b/same.png) otherwise derive the same auto output name; the
    # second silently overwrote the first while both were reported OK.
    auto_outputs: dict[str, str] = {}

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
        if is_dangerous_extension(input_path):
            print(
                f"  FAIL: {input_path} -> refusing to convert executable/script "
                "file as image data"
            )
            failed += 1
            continue

        params = _resolve_entry_params(entry, defaults)

        # ── Input source for this entry (each entry carries its own) ──
        # Everything that can reject the entry's own settings happens here,
        # before any output directory is created or any file is written.  The
        # enum checks live inside the try too: a per-entry "mode": "FROBNICATE"
        # must produce one readable FAIL line, not a traceback (D6).
        try:
            mode = _normalize_choice(params["mode"], _BATCH_MODES, "mode")
            target = _normalize_choice(params["target"], _BATCH_TARGETS, "target")
            kind = _resolve_entry_input_type(params, mode)
            width, height = _resolve_entry_dimensions(
                kind, params["width"], params["height"], mode
            )
            transcode_spec = (
                _entry_transcode_spec(params, width, height)
                if kind == "yuv" and mode == "convert" else None
            )
            # N4: a batch view entry honours input_raw_type / input_yuv_type the
            # same way single-file `view` does; otherwise the declared input
            # format was silently ignored and the file was decoded as the
            # *output* format instead.
            view_raw_type = params.get("input_raw_type") or params["raw_type"]
            view_yuv_type = params.get("input_yuv_type") or params["yuv_type"]
        except _BatchSpecError as exc:
            print(f"  FAIL: {input_path} -> {exc}")
            failed += 1
            continue
        output_dir = params.pop("output_dir") or _default_out_dir(mode)

        # ── 单帧字节上限校验（防超大宽高导致 OOM/崩溃/写出巨型文件）──
        # D1: convert 分支此前完全没有校验（batch 能真的写出 513MB 的 RAW32、
        # 理论上 17GB）。view 用「解码目标格式」，convert 用「编码输出格式」；
        # YUV 转码两侧都查（见 _check_transcode_args_for）。全部在写盘之前。
        try:
            if transcode_spec is not None:
                _check_transcode_args_for(transcode_spec)
            elif mode == "view":
                _check_decode_args_for(
                    view_raw_type if target == "RAW" else view_yuv_type,
                    width, height,
                    alignment=params.get("input_alignment") or params.get("alignment", "msb"),
                    endianness=params.get("input_endianness") or params.get("endianness", "little"),
                    target=target,
                )
            else:
                _check_convert_args_for(
                    target, params["raw_type"], params["yuv_type"], width, height,
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
            # YUV transcode: {format} is the TARGET layout; width/height remain
            # the source frame dimensions because nothing is resized.
            template_target = "YUV" if transcode_spec is not None else target
            if transcode_spec is not None:
                template_yuv = transcode_spec.target_format
            elif target != "YUV":
                template_yuv = ""
            elif mode == "view":
                # View decodes the *input* layout, which may come from
                # input_yuv_type (N4); naming the file after the ignored
                # yuv_type would mislabel the result.
                template_yuv = view_yuv_type
            else:
                template_yuv = params.get("yuv_type", "")
            output_path = format_output_template(
                "{input_stem}_{width}x{height}_{format}{ext}",
                input_path, width, height, template_target,
                output_dir=output_dir,
                output_ext=out_ext,
                raw_type=(
                    (view_raw_type if mode == "view" else params.get("raw_type", ""))
                    if target == "RAW" else ""
                ),
                yuv_type=template_yuv,
                bayer_pattern=params.get("bayer_pattern", "") if target == "RAW" else "",
                source_mode=params.get("source_mode", ""),
                alignment=params.get("alignment", ""),
                endianness=params.get("endianness", ""),
            )
            # 未显式指定 output_dir（JSON 全局/entry 均未给）时，保持旧行为：把
            # 自动输出放到输入文件同目录。显式 output 不可能走到这里（上方分支已
            # 消费），因此 has_explicit_dir 只由 output_dir 判定——output 与输出
            # 目录两条语义严格分离（0.2.2-L-3）：只要给了目录就尊重，绝不覆盖。
            has_explicit_dir = (
                entry.get("output_dir") is not None
                or spec.get("output_dir") is not None
            )
            if not has_explicit_dir:
                output_path = str(Path(input_path).parent / Path(output_path).name)
            # D3：跨 entry 去重（见 auto_outputs 的注释）。已占用的自动输出名
            # 追加 _2/_3…，绝不报告成功却让后一个条目覆盖前一个。
            output_path = _dedupe_output_path(output_path, auto_outputs, owner=input_path)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        try:
            if transcode_spec is not None:
                # YUV family lossless rearrange (no resize; every frame kept).
                from raw_view.transcode import transcode_file

                frame_total, size = transcode_file(input_path, output_path, transcode_spec)
                logger.info(
                    "Batch OK: %s -> %s (%d bytes, %d frame(s), %s -> %s)",
                    input_path, output_path, size, frame_total,
                    transcode_spec.source_format, transcode_spec.target_format,
                )
                print(
                    f"  OK: {input_path} -> {output_path} "
                    f"({size} bytes, {frame_total} frame(s))"
                )
                success += 1
                continue
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
                        view_raw_type, width, height,
                        alignment=params["input_alignment"]
                        or params["alignment"],
                        endianness=params["input_endianness"]
                        or params["endianness"],
                        preview_mode=params["preview_mode"],
                        bayer_pattern=params["bayer_pattern"],
                        offset=params["offset"],
                    )
                else:
                    size = yuv_file_to_image(
                        input_path, output_path,
                        view_yuv_type, width, height,
                        offset=params["offset"],
                        alignment=params["input_alignment"]
                        or params["alignment"],
                        endianness=params["input_endianness"]
                        or params["endianness"],
                    )
            logger.info("Batch OK: %s -> %s (%d bytes)", input_path, output_path, size)
            print(f"  OK: {input_path} -> {output_path} ({size} bytes)")
            success += 1
        except Exception as exc:
            _log_failure("Batch FAIL: %s -> %s", input_path, output_path, exc=exc)
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
