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

    Rearrange a YUV file into another YUV layout (lossless), or decode a YUV
    binary to an image when the output name ends in an image extension::

        python -m raw_view convert -i f.nv16 -o f.yuyv --input-type yuv \
            --input-yuv-type NV16 --yuv-type YUYV --width 1920 --height 1080
        python -m raw_view convert -i grey.bin -o grey.png --input-type yuv \
            --input-yuv-type YOnly10 --width 1296 --height 736

batch
    Batch convert/view multiple files from a JSON file.
    See ``--help-batch`` for the expected JSON format.

Help is two-level: ``-h``/``--help`` is a short overview that points at
``--help-view``, ``--help-convert`` and ``--help-batch``.  The page text lives
in :mod:`raw_view.cli_help`, the option table lives in :func:`_build_parser`.
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


class _OverviewHelpAction(argparse.Action):
    """``-h``/``--help``: print the level-1 page instead of argparse's table.

    The parser still knows every option (scripts depend on all 23 of them), but
    a flat table of them is exactly what made the old help unreadable: most of
    those flags only apply to one mode.  The level-1 page lists the modes, the
    options shared by all of them, and where the per-mode pages live.
    """

    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from raw_view.cli_help import show_main_help

        show_main_help(get_app_version())
        parser.exit(0)


def _build_parser() -> argparse.ArgumentParser:
    # add_help=False: -h is re-added below with a custom action so the level-1
    # page (not argparse's flat option table) is what users see.
    p = argparse.ArgumentParser(
        prog="raw-view",
        add_help=False,
        description="RAW/YUV image viewer and converter",
        usage=(
            "raw-view [-h] [--version] [--help-view | --help-convert | "
            "--help-batch]\n"
            "                [{view,convert,batch}] [-i INPUT] [-o OUTPUT] "
            "[--width N] [--height N]\n"
            "                [--offset N] [file ...]"
        ),
    )

    # ── Help & version ──
    help_group = p.add_argument_group(
        "help & version",
        "Run any of these alone to print a page and exit.",
    )
    help_group.add_argument("-h", "--help", action=_OverviewHelpAction,
                            help="Overview page (this one) and where to find "
                                 "the per-mode pages")
    # P2-4 / ENG-6：版本号单一来源（models.APP_VERSION），CLI 与 About/Help 一致。
    help_group.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {get_app_version()}",
    )
    help_group.add_argument(
        "--help-view", action="store_true", dest="help_view",
        help="view mode: file layout, every RAW/YUV format, decode examples",
    )
    help_group.add_argument(
        "--help-convert", action="store_true", dest="help_convert",
        help="convert mode: image->RAW/YUV, YUV->YUV, YUV->image, examples",
    )
    help_group.add_argument(
        "--help-batch", "--batch-help", action="store_true", dest="batch_help",
        help="batch mode: JSON schema, copy-pasteable examples "
             "(--batch-help is kept as an alias)",
    )

    # ── Modes ──
    mode_group = p.add_argument_group(
        "modes",
        "view decodes RAW/YUV to PNG/JPEG; convert encodes/transcodes; batch "
        "runs a JSON job list.\nWith no mode at all, raw-view opens the GUI.",
    )
    mode_group.add_argument(
        "mode",
        nargs="?",
        default="view",
        choices=("view", "convert", "batch"),
        help="Operation mode (default: view)",
    )
    mode_group.add_argument(
        "files",
        nargs="*",
        metavar="file",
        help="File(s) to open in GUI mode (view without -i)",
    )

    # ── Shared I/O (every mode) ──
    core = p.add_argument_group(
        "core options",
        "Accepted by every mode. Everything else is mode-specific -- see the "
        "page for\nthat mode (--help-view / --help-convert / --help-batch).",
    )
    core.add_argument("-i", "--input", help="Input file path")
    core.add_argument("-o", "--output", help="Output file path; omitted -> "
                                            "auto-named inside the mode's page")
    # 默认值仍是 640x480（既有命令行为不变），但 parser 层留 None + 下方回填：
    # ``--input-type yuv`` 必须能区分"用户显式给了 --width/--height"与"走默认"，
    # 否则缺省会静默按 640x480 解析布局、产出错误结果。
    core.add_argument("--width", type=int, default=None,
                      help="Frame width; output size for an image input, "
                           "source frame size for a binary input (default 640)")
    core.add_argument("--height", type=int, default=None,
                      help="Frame height; output size for an image input, "
                           "source frame size for a binary input (default 480)")
    core.add_argument("--offset", type=int, default=0,
                      help="Data offset in bytes (default: 0)")

    # ── Mode-specific options ──
    # Accepted at top level (backwards compatibility: scripts pass them with
    # and without a preceding mode word), but hidden from the overview so it
    # stays readable. Each one is documented on its mode's page.
    adv = p.add_argument_group("mode-specific options")
    # Input source (convert mode)
    # 默认 image = 既有行为（解码 PNG/JPG/BMP 再编码）。yuv = YUV 家族内部
    # 无损字节重排（不 resize，width/height 是源帧尺寸）。raw 预留。
    adv.add_argument("--input-type", choices=("image", "yuv", "raw"),
                     default="image", help=argparse.SUPPRESS)
    adv.add_argument("--input-yuv-type", help=argparse.SUPPRESS)
    adv.add_argument("--input-alignment", choices=("lsb", "msb"),
                     help=argparse.SUPPRESS)
    adv.add_argument("--input-endianness", choices=("little", "big"),
                     help=argparse.SUPPRESS)
    adv.add_argument("--input-raw-type", help=argparse.SUPPRESS)
    # Encode/decode options
    # default=None so a YUV source can default to YUV without hiding an explicit
    # "-t RAW" (which must stay an error); _apply_arg_defaults() backfills RAW.
    adv.add_argument("-t", "--target", choices=("RAW", "YUV"), default=None,
                     help=argparse.SUPPRESS)
    adv.add_argument("--raw-type", default="RAW12", help=argparse.SUPPRESS)
    adv.add_argument("--yuv-type", default="YUYV", help=argparse.SUPPRESS)
    adv.add_argument("--alignment", choices=("lsb", "msb"), default="msb",
                     help=argparse.SUPPRESS)
    adv.add_argument("--endianness", choices=("little", "big"),
                     default="little", help=argparse.SUPPRESS)
    adv.add_argument("--source-mode", choices=("bayer", "gray"),
                     default="bayer", help=argparse.SUPPRESS)
    # D5: validate at parse time (exit 2) instead of silently degrading to gray
    # in view mode while failing with a traceback in convert mode.
    # `type=str.upper` keeps the rejection while still accepting the lowercase
    # spelling that always worked (the codec layer upper()s anyway) -- a case-
    # sensitive `choices` alone turned `--bayer-pattern rggb` into a hard error.
    adv.add_argument("--bayer-pattern", type=str.upper,
                     choices=tuple(BAYER_PATTERNS), default="RGGB",
                     help=argparse.SUPPRESS)
    # Decode options (view mode)
    adv.add_argument("--preview-mode", choices=("Bayer Color", "Grayscale"),
                     default="Bayer Color", help=argparse.SUPPRESS)
    # Batch options
    adv.add_argument("-b", "--batch-file", help=argparse.SUPPRESS)

    return p


def _show_batch_help() -> None:
    """Print the batch page (kept as the internal name; ``--help-batch``)."""
    from raw_view.cli_help import show_batch_help

    logger.debug("Showing batch JSON format help")
    show_batch_help()


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
    """CLI decode (when -i given), or the GUI when that is allowed."""
    if not args.input and not _gui_allowed():
        # A pure CLI executable has no GUI to fall back to.  Silently starting
        # one would block the terminal the user is typing in (and, under a
        # --console image, do so with no window to close).  Report usage
        # instead, exactly like argparse does for any other bad invocation.
        print(
            "Error: view mode needs --input in the command-line executable; "
            "run raw-view.exe (the launcher) or raw-view-gui.exe for the GUI",
            file=sys.stderr,
        )
        sys.exit(2)
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


#: Set by the packaged console executable (``raw-view-cli.exe``) via
#: ``RAW_VIEW_NO_GUI=1``.  It must never accept an invocation that would need a
#: window, because it has none -- and because silently starting one would hang
#: whatever terminal launched it.
_NO_GUI_ENV = "RAW_VIEW_NO_GUI"


def _gui_allowed() -> bool:
    """Whether this process may start the interactive GUI."""
    return os.environ.get(_NO_GUI_ENV, "") not in {"1", "true", "yes"}


def _run_gui(files: list[str]) -> None:
    """Launch the interactive GUI, optionally opening the given files."""
    if not _gui_allowed():
        print(
            "Error: this executable is command-line only (no GUI). "
            "Run raw-view.exe or raw-view-gui.exe instead.",
            file=sys.stderr,
        )
        sys.exit(2)
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
        print(_image_read_error(str(exc), args, output_path), file=sys.stderr)
        sys.exit(1)


def _image_read_error(
    message: str, args: argparse.Namespace, output_path: str
) -> str:
    """The ``Error:`` block for a failed image-input conversion (see cli_help).

    Only ``load_bgr_image``'s "failed to read image" failure gets the RAW/YUV
    hint.  Every other error (permissions, encode failure, an invalid size)
    already names its own cause and would only be diluted by a lecture about
    ``--input-type``.
    """
    from raw_view.cli_help import image_read_guidance

    if "failed to read image" not in message:
        return f"Error: {message}"
    return image_read_guidance(
        args.input, output_path, args.width, args.height, message,
        # Echo the formats the user actually passed, so the suggested commands
        # are copy-paste correct rather than showing an unrelated default.
        yuv_type=getattr(args, "input_yuv_type", None) or args.yuv_type,
        raw_type=getattr(args, "input_raw_type", None) or args.raw_type,
    )


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


#: Output suffixes that mean "write a viewable image", not a raw byte dump.
#: A YUV input plus one of these is a decode, not a rearrangement -- see
#: :func:`_wants_image_output`.
_IMAGE_OUTPUT_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
})


def _wants_image_output(output_path: str | None) -> bool:
    """Whether *output_path* asks for a PNG/JPEG rather than a raw byte dump.

    Only the file name decides: ``convert --input-type yuv -o out.png`` reads as
    "decode this YUV to an image", while ``-o out.yuv`` (or no ``-o``) reads as
    "re-arrange the bytes".  Without this distinction the two jobs shared one
    code path and the decode one did not exist.
    """
    if not output_path:
        return False
    return Path(output_path).suffix.lower() in _IMAGE_OUTPUT_SUFFIXES


def _decode_yuv_to_image(
    args: argparse.Namespace, source_format: str, width: int, height: int
) -> None:
    """Decode the first frame of a YUV file into a PNG/JPEG (convert-mode job 3).

    ``view -i x.yuv -t YUV --yuv-type ...`` already did this; the difference is
    only the spelling, which is why this path reuses the same converter and the
    same ``--offset``/alignment/endianness semantics.  It exists because a user
    who starts from ``convert`` (the mode whose help documents ``--input-type``)
    otherwise had no working command: ``--input-type yuv`` was documented as
    ``--yuv-type``-only reassembly, so an identical source/target pair was
    rejected as a no-op and there was no way to name a PNG output at all.
    """
    from raw_view.converter import yuv_file_to_image
    from raw_view.formats import expected_frame_size_yuv

    alignment = args.input_alignment or args.alignment
    endianness = args.input_endianness or args.endianness
    output_path = args.output

    # 同 view 路径：先按格式算单帧字节并做 512MB 上限校验，再读盘。
    try:
        frame_bytes = expected_frame_size_yuv(
            source_format, width, height,
            alignment=alignment, endianness=endianness,
        )
        _check_decode_args_for(
            source_format, width, height,
            alignment=alignment, endianness=endianness, target="YUV",
        )
    except FormatError as exc:
        logger.error("Convert rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # 明确告知"只解第一帧"：多帧文件其余帧被忽略是有意的（与 view 一致）。
    try:
        file_size = os.path.getsize(args.input)
    except OSError as exc:
        logger.error("Convert rejected: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    available = file_size - args.offset
    frames = available // frame_bytes if frame_bytes else 0
    if available < frame_bytes:
        msg = (
            f"input too short for one {source_format} frame at {width}x{height}: "
            f"need {args.offset + frame_bytes} bytes, got {file_size}"
        )
        logger.error("Convert rejected: %s", msg)
        print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Convert (YUV decode): %s -> %s (%s, %dx%d, %d frame(s) available)",
        args.input, output_path, source_format, width, height, frames,
    )

    print(f"Input:        {args.input}")
    print(f"Output:       {output_path}")
    print(f"Mode:         convert (YUV decode to image)")
    print(f"Input type:   YUV")
    print(f"Source fmt:   {source_format}")
    print(f"Dimensions:   {width}x{height} (source frame, no resize)")
    print(f"Frames:       {frames} (first frame decoded)")
    if args.offset:
        print(f"Offset:       {args.offset}")
    print()

    try:
        size = yuv_file_to_image(
            args.input, output_path, source_format, width, height,
            offset=args.offset, alignment=alignment, endianness=endianness,
        )
        logger.info("Convert OK: %s -> %s (%d bytes)", args.input, output_path, size)
        print(f"Converted: {args.input} -> {output_path} ({size} bytes)")
    except Exception as exc:
        _log_failure("Convert failed: %s -> %s", args.input, output_path, exc=exc)
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _run_convert_yuv(args: argparse.Namespace) -> None:
    """Convert a YUV input: transcode to another layout, or decode to an image."""
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

    # An image output name means "decode this YUV into a PNG/JPEG".  That is a
    # different job from the lossless rearrangement below: the target layout is
    # not consulted, so a source==target pair is not a no-op there.
    #
    # This is the capability behind a plain `convert -i x.bin -o x.png
    # --input-type yuv --input-yuv-type YOnly10`.  Before it existed the CLI
    # answered that command with "--target must be YUV / YOnly10 -> YOnly10 is a
    # no-op", even though `view` decoded the same file fine.
    if _wants_image_output(args.output):
        _decode_yuv_to_image(args, source_format, width, height)
        return

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


def _entry_transcode_spec(
    params: dict, width: int, height: int, output_path: str | None = None
):
    """Build and pre-validate the TranscodeSpec for one YUV batch entry.

    *output_path* matters because an image name (``out.png``) turns the entry
    into a decode -- the same rule the single-file command uses
    (:func:`_wants_image_output`) -- where the target layout is irrelevant and a
    source==target pair is legitimate.
    """
    from raw_view.transcode import TranscodeError, TranscodeSpec, reject_reason

    if params.get("mode") != "convert":
        raise _BatchSpecError(
            "input_type yuv is only supported with mode=convert (a YUV source "
            "is transcoded into another YUV layout, or decoded to an image)"
        )
    if params.get("target") != "YUV":
        raise _BatchSpecError(
            f"input_type yuv cannot target {params.get('target')}: use "
            'target "YUV" and yuv_type for the output layout'
        )
    source_format = params.get("input_yuv_type") or params.get("yuv_type") or "YUYV"
    target_format = params.get("yuv_type") or "YUYV"
    if not _wants_image_output(output_path):
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
                _entry_transcode_spec(params, width, height, entry.get("output"))
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
        # Batch parity with the single-file command: a YUV entry whose explicit
        # output is a PNG/JPEG decodes to an image instead of re-arranging bytes
        # (see _decode_yuv_to_image for why the CLI needs both spellings).
        decode_yuv = (
            transcode_spec is not None and _wants_image_output(output_path)
        )
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
            if decode_yuv:
                from raw_view.converter import yuv_file_to_image

                size = yuv_file_to_image(
                    input_path, output_path,
                    transcode_spec.source_format, width, height,
                    offset=params.get("offset") or 0,
                    alignment=transcode_spec.alignment,
                    endianness=transcode_spec.endianness,
                )
                logger.info(
                    "Batch OK: %s -> %s (%d bytes, %s decoded)",
                    input_path, output_path, size, transcode_spec.source_format,
                )
                print(f"  OK: {input_path} -> {output_path} ({size} bytes)")
                success += 1
                continue
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
            # One extra clause, not the full guidance block: a batch reports one
            # line per entry, and the same advice on every line would bury the
            # entries that failed for a different reason.
            from raw_view.cli_help import image_read_hint_short

            hint = (
                image_read_hint_short(input_path)
                if "failed to read image" in str(exc) else ""
            )
            print(f"  FAIL: {input_path} -> {exc}{hint}")
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

    # Mode pages. Checked before the mode dispatch so `view --help-convert`
    # prints the convert page instead of starting a decode; like argparse's own
    # -h they win over everything else on the command line.
    if getattr(args, "help_view", False):
        from raw_view.cli_help import show_view_help

        show_view_help()
        return
    if getattr(args, "help_convert", False):
        from raw_view.cli_help import show_convert_help

        show_convert_help()
        return
    if args.batch_help:
        _show_batch_help()
        return

    if args.mode == "convert":
        _run_convert(args)
    elif args.mode == "batch":
        _run_batch(args)
    elif args.mode == "view":
        _run_view_mode(args)
    else:
        _run_gui(args.files)


def _cli_main() -> None:
    """Entry point for ``raw-view-cli.exe``: never starts a GUI.

    ``__main__.main`` treats "no usable mode" as "open the GUI", which is right
    for a double-clicked launcher and fatal for a console executable (there is
    no window to close, so the caller just hangs).  Marking the process up front
    makes both fallbacks report usage instead.
    """
    os.environ[_NO_GUI_ENV] = "1"
    main()


if __name__ == "__main__":
    main()
