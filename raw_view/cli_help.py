"""User-facing help pages for the command line.

Why a separate module
---------------------
``__main__`` owns the argument parser and the three execution paths; this module
owns *what the user reads*.  Keeping the two apart means the help text can grow
(a format table, worked examples per mode) without burying the dispatch logic,
and it gives the tests one stable place to assert against -- including the batch
JSON examples, which are exported as strings so a test can prove they are valid
JSON instead of trusting a comment.

The help system has two levels:

* one summary page (``-h`` / ``--help``) that lists the modes, the options that
  work in *every* mode, and how to reach the three mode pages;
* one page per mode (``--help-view`` / ``--help-convert`` / ``--help-batch``).

The summary is deliberately short: the CLI accepts 23 options, but most of them
only apply to one mode, so listing them all in one flat block (what this module
replaced) made it impossible to tell which flag belongs to which job.  The mode
pages are where the conditional rules are spelled out, one mode at a time.

Text is deliberately plain ASCII, so a narrow console codepage (cp1252, GBK)
renders it without a ``UnicodeEncodeError`` even before ``_make_utf8_stdio``
runs -- the box-drawing arrows the previous page used were exactly what broke on
GitHub-Actions Windows runners.
"""

from __future__ import annotations

import os

__all__ = [
    "BATCH_EXAMPLES",
    "IMAGE_OUTPUT_SUFFIXES",
    "batch_help",
    "convert_help",
    "image_read_guidance",
    "image_read_hint_short",
    "looks_like_binary",
    "main_help",
    "show_batch_help",
    "show_convert_help",
    "show_main_help",
    "show_view_help",
    "suggested_prog",
    "view_help",
]

# ── Copy-pasteable batch examples ─────────────────────────────────────
#
# These are exported so tests can ``json.loads`` them: the previous help page
# showed its "example" with ``//`` comments, which is not JSON at all, so a user
# who copied it got ``JSONDecodeError`` instead of a batch run.

BATCH_EXAMPLE_MINIMAL = """\
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "alignment": "msb",
  "endianness": "little",
  "bayer_pattern": "RGGB",
  "width": 1920,
  "height": 1080,
  "output_dir": "convert_out",
  "files": [
    {"input": "photos/*.png"}
  ]
}"""

BATCH_EXAMPLE_MIXED = """\
{
  "mode": "convert",
  "target": "RAW",
  "raw_type": "RAW12",
  "width": 640,
  "height": 480,
  "output_dir": "out",
  "base_dir": "D:/work/frames",
  "files": [
    {"input": "shot1.png", "width": 1920, "height": 1080},
    {"input": "shot2.jpg", "target": "YUV", "yuv_type": "NV12",
     "width": 1280, "height": 720},
    {"input": "shot3.bmp", "raw_type": "RAW10 Packed", "source_mode": "gray"},
    {"input": "sensor.raw", "mode": "view", "alignment": "lsb",
     "width": 1920, "height": 1080, "bayer_pattern": "BGGR"},
    {"input": "frame.nv12", "mode": "view", "target": "YUV", "yuv_type": "NV12",
     "width": 1920, "height": 1080}
  ]
}"""

BATCH_EXAMPLE_YUV = """\
{
  "mode": "convert",
  "target": "YUV",
  "input_type": "yuv",
  "input_yuv_type": "NV16",
  "yuv_type": "YUYV",
  "width": 1920,
  "height": 1080,
  "output_dir": "yuv_out",
  "files": [
    {"input": "captures/*.nv16"},
    {"input": "extra.nv16", "yuv_type": "YVYU"}
  ]
}"""

BATCH_EXAMPLES = (
    BATCH_EXAMPLE_MINIMAL,
    BATCH_EXAMPLE_MIXED,
    BATCH_EXAMPLE_YUV,
)

#: YUV / RAW style suffixes whose contents convert's default ``--input-type
#: image`` cannot decode.  Used only to decide which advice a read failure
#: deserves -- never to refuse a file (a ``.bin`` that really is a PNG still
#: decodes through the normal path).
_BINARY_SUFFIXES = frozenset({
    ".bin", ".raw", ".yuv", ".dat", ".i420", ".yv12", ".nv12", ".nv21",
    ".nv16", ".nv61", ".yuyv", ".uyvy", ".yvyu", ".vyuy", ".yonly",
})

#: Suffixes that mean "the output should be a viewable image".  This is the
#: single definition used by both questions that depend on it:
#:
#: * does this input name claim to be a decodable image (``looks_like_binary``),
#:   and
#: * does this output name ask for a decode rather than a byte rearrangement
#:   (``__main__._wants_image_output``).
#:
#: Two copies of this set had already drifted (6 vs 9 entries) and ``.webp``/``.jpe``
#: fell through to the transcode branch with an error unrelated to what the user
#: asked for.  One constant, one meaning.
#:
#: ``.gif``/``.webp`` are here because OpenCV decodes them as input; they are
#: *not* in :data:`IMAGE_OUTPUT_SUFFIXES` because ``cv2.imencode`` cannot write
#: them reliably (see that constant).
IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".jpe", ".bmp", ".tif", ".tiff", ".gif", ".webp",
})

#: Suffixes this tool can actually *write* as an image.  A deliberate subset of
#: :data:`IMAGE_SUFFIXES`: ``cv2.imencode`` is only driven for the formats in
#: :data:`raw_view.converter._IMWRITE_EXT_ALIASES` plus png/jpg/bmp/tiff, so
#: naming ``-o out.webp`` must not silently produce PNG bytes (which is exactly
#: what ``view`` does today -- see ``image_output_advice``).  Kept in sync with
#: :func:`raw_view.converter._imwrite_extension` by
#: ``tests/test_cli_help.py::ImageOutputSuffixTests``.
IMAGE_OUTPUT_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff",
})

#: Leading bytes of the formats ``load_bgr_image`` (OpenCV) can decode.
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8\xff",       # JPEG
    b"BM",                 # BMP
    b"GIF87a", b"GIF89a",  # GIF
    b"II*\x00", b"MM\x00*",  # TIFF
    b"RIFF",               # WEBP (RIFF....WEBP)
)

_RAW_FORMATS = (
    "RAW8, RAW10, RAW12, RAW16, RAW32, RAW10 Packed, RAW12 Packed, RAW14 Packed"
)
_YUV_YONLY = "YOnly, YOnly8, YOnly10, YOnly12, YOnly14, YOnly16"
_YUV_422 = "YUYV, UYVY, YVYU, VYUY, NV16, NV61"
_YUV_420 = "I420, YV12, NV12, NV21"


def looks_like_binary(path: str) -> bool:
    """Whether *path* is a likely RAW/YUV binary rather than a broken image.

    True when the suffix is a known RAW/YUV one, when the file is empty, or when
    it carries neither a known RAW/YUV suffix nor a known image signature.  A
    ``.png``/``.jpg`` name wins over the content sniff: that file is a damaged
    image, and telling its owner to add ``--input-type yuv`` would be wrong.
    Unreadable files answer False -- the caller already has a read error to
    report and should not speculate about a file it cannot open.
    """
    from pathlib import Path

    suffix = Path(path).suffix.lower()
    if suffix in _BINARY_SUFFIXES:
        return True
    if suffix in IMAGE_SUFFIXES:
        return False
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    if not head:
        return True
    return not any(head.startswith(magic) for magic in _IMAGE_MAGIC)


# ── Diagnostic text ───────────────────────────────────────────────────


def suggested_prog() -> str:
    """How this process was invoked, for the commands printed in an error.

    A packaged build is ``raw-view.exe`` / ``raw-view-cli.exe`` and is meant to
    be typed by that name; a source checkout has no such command on PATH, so the
    suggestion must be ``python -m raw_view``.  Printing a command the reader
    cannot run would defeat the point of printing it.
    """
    argv0 = ""
    try:
        import sys

        argv0 = sys.argv[0] or ""
    except Exception:  # pragma: no cover - defensive, embedded hosts
        pass
    if not argv0 or argv0.endswith(("__main__.py", "-m")):
        return "python -m raw_view"
    name = os.path.basename(argv0)
    # `raw-view.exe`, `raw-view`, `raw-view-cli.exe` all read correctly.
    return name or "raw-view"


def _other_yuv_target(source: str) -> str | None:
    """A YUV layout ``source`` can be losslessly re-arranged into, or ``None``.

    The guidance used to print ``--input-yuv-type X --yuv-type X`` (one variable
    filled both holes), which ``reject_reason()`` refuses as a no-op -- a
    command that could never work.

    ``None`` is the honest answer for a format with no transcode at all: the
    YOnly (4:0:0) family has no chroma planes, so *every* rearrangement involving
    it is refused, and printing a "try this" command would repeat the original
    bug in a different shape.  The caller drops the section in that case.
    """
    from raw_view.transcode import family_formats

    name = str(source or "").strip()
    for candidate in family_formats(name):
        if candidate.upper() != name.upper():
            return candidate
    return None


def image_output_advice(output_path: str | None) -> str:
    """Explain what to do when ``--input-type yuv`` names a non-image output.

    ``convert`` decides "decode vs re-arrange" from the output suffix alone.
    That is a heuristic: ``-o out.webp`` or ``-o out`` is a perfectly reasonable
    request to a user but reads as "re-arrange" here, and the resulting error
    ("no-op" / "changes chroma sampling") talks about the wrong problem.  This
    turns it into a sentence that names the fix.
    """
    from pathlib import Path

    suffix = Path(output_path or "").suffix.lower()
    return (
        f"'{output_path}' does not end in a writable image extension "
        f"({', '.join(sorted(IMAGE_OUTPUT_SUFFIXES))}), so this is treated as a "
        f"YUV -> YUV re-arrangement, not a decode. To decode to an image, name "
        f"the output .png/.jpg/.bmp/.tif."
    )


def image_read_guidance(
    input_path: str,
    output_path: str | None,
    width: int,
    height: int,
    error: BaseException | str,
    *,
    yuv_type: str = "YOnly10",
    raw_type: str = "RAW12",
    prog: str | None = None,
) -> str:
    """The full ``Error:`` block printed when an image input cannot be decoded.

    The old message was one line -- ``failed to read image: x.bin`` -- which
    states the symptom and nothing else.  The overwhelmingly common cause is a
    RAW/YUV binary handed to the default ``--input-type image`` path, so the
    block names that cause and prints runnable commands, with the user's own
    paths, dimensions and format names already filled in.

    **Every command printed here must run as-is.** The transcode example once
    used the same value for ``--input-yuv-type`` and ``--yuv-type``, which is
    rejected as a no-op; see :func:`_other_yuv_target` and
    ``test_guidance_offers_a_command_that_actually_works``, which now executes
    every suggestion instead of just the first.
    """
    prog = prog or suggested_prog()
    out = output_path or "out.png"
    lines = [f"Error: {error}", ""]
    if not looks_like_binary(input_path):
        lines.extend([
            "The file exists but it is not a decodable PNG/JPEG/BMP image.",
            "  * corrupted or renamed image -> open it in an image viewer to check",
            "  * actually a RAW/YUV dump under an image name -> rename it to .bin,",
            "    then follow the commands under --help-convert (\"YUV -> image\")",
        ])
        return "\n".join(lines)

    lines.extend([
        f"'{input_path}' is not a PNG/JPEG/BMP image, so convert's default",
        "--input-type image cannot read it. It looks like a RAW/YUV binary:",
        "tell the CLI what the bytes are.",
        "",
        "  YUV binary -> PNG (decode; `view` and `convert` both work):",
        (
            f'    {prog} view -i "{input_path}" -o "{out}" '
            f"-t YUV --yuv-type {yuv_type} --width {width} --height {height}"
        ),
        (
            f'    {prog} convert -i "{input_path}" -o "{out}" '
            f"--input-type yuv --input-yuv-type {yuv_type} "
            f"--width {width} --height {height}"
        ),
        "",
        "  RAW binary -> PNG (decode):",
        (
            f'    {prog} view -i "{input_path}" -o "{out}" '
            f"-t RAW --raw-type {raw_type} --width {width} --height {height}"
        ),
    ])
    # Only offer the re-arrangement when a legal target exists.  YOnly has none
    # (see _other_yuv_target), and suggesting one is how this block shipped a
    # command that always failed.
    transcode_target = _other_yuv_target(yuv_type)
    if transcode_target is not None:
        lines.extend([
            "",
            "  YUV binary -> another YUV layout (lossless byte rearrangement):",
            (
                f'    {prog} convert -i "{input_path}" -o out.yuv '
                f"--input-type yuv --input-yuv-type {yuv_type} "
                f"--yuv-type {transcode_target} "
                f"--width {width} --height {height}"
            ),
        ])
    lines.extend([
        "",
        "Use the file's real layout and frame size; the names above are the ones",
        "you passed. Formats: --help-view / --help-convert. Manual: docs/cli.md",
    ])
    return "\n".join(lines)


def image_read_hint_short(input_path: str) -> str:
    """One-line version of :func:`image_read_guidance` for batch FAIL lines."""
    if not looks_like_binary(input_path):
        return ""
    return (
        ' (looks like a RAW/YUV binary: add "input_type": "yuv" + '
        '"input_yuv_type" + width/height, or use "mode": "view" to decode it)'
    )


# ── Level 1: the summary page ─────────────────────────────────────────


def main_help(version: str) -> str:
    return f"""\
raw-view {version} -- RAW/YUV image viewer and converter

USAGE
  raw-view.exe {{view|convert|batch}} [options] [file ...]   launcher
  raw-view-cli.exe {{view|convert|batch}} [options]          console-only build
  python -m raw_view  {{view|convert|batch}} [options]       from a source tree
  raw-view {{view|convert|batch}} --help                     that mode's own page

  A mode word is REQUIRED for every command-line operation: raw-view --width 800
  is rejected with exit 2 instead of opening a window. A completely bare
  `raw-view` (no options at all) still opens the GUI.

MODES
  view       Decode a RAW/YUV binary into PNG/JPEG. With no -i, opens the GUI.
  convert    Encode an image into RAW/YUV, re-arrange YUV into another YUV
             layout, or decode a YUV binary into an image.
  batch      Run many view/convert jobs described by a JSON file.

QUICK EXAMPLES
  raw-view view -i frame.bin -o frame.png -t YUV --yuv-type YOnly10 \\
           --width 1296 --height 736
  raw-view convert -i photo.png -o photo.raw -t RAW --raw-type RAW12 \\
           --width 1920 --height 1080
  raw-view convert -i photo.png -o photo.yuv -t YUV --yuv-type NV12 \\
           --width 1280 --height 720
  raw-view batch -b jobs.json
  raw-view view file1.raw file2.png          (opens the GUI on these files)

CORE OPTIONS
  -i, --input PATH     Input file, exactly one              (view, convert)
                       (a directory is rejected -- use `batch` for a folder)
  -o, --output PATH    Output file (auto-named if omitted)  (view, convert)
  --width N            Frame width                          (view, convert)
  --height N           Frame height                         (view, convert)
  --offset N           Bytes skipped before the frame       (view, convert)

  batch ignores these five; it takes every parameter from the JSON file.
  See --help-batch for how to set width/height/offset there.

MODE PAGES -- the remaining options belong to one mode each:
  --help-view       view:    all RAW/YUV formats, alignment/endianness, Bayer,
                             preview modes, worked examples
  --help-convert    convert: image->RAW/YUV, YUV->YUV lossless rearrangement,
                             YUV->image, which options apply in which direction
  --help-batch      batch:   -b/--batch-file, the JSON schema, three
                             copy-pasteable examples
                             (--batch-help is kept as an alias)

  -h, --help        This page
  --version         Print the version

Which option do I need?
  "the input is a .raw/.bin/.yuv file"                 -> view  (--help-view)
  "turn this PNG into RAW/YUV"                         -> convert (--help-convert)
  "this .bin decode fails with 'failed to read image'" -> convert (--help-convert,
                                                          "YUV -> image")
  "convert a whole folder"                             -> batch (--help-batch)

Full manual (Chinese, with every example): docs/cli.md
"""


# ── Level 2: view ─────────────────────────────────────────────────────


def view_help() -> str:
    return f"""\
raw-view view -- decode a RAW/YUV binary into a PNG/JPEG image

USAGE
  raw-view view -i INPUT -o OUTPUT.png -t {{RAW|YUV}} [format options]
  raw-view view [file ...]        (no -i: opens the GUI and loads these files)

CORE OPTIONS
  -i, --input PATH    the binary to decode; exactly one file
                      (a directory is rejected -- use `batch` for a folder)
  -o, --output PATH   image to write (.png/.jpg/.bmp/.tif)
                      omitted -> <input dir>/view_out/<stem>_<W>x<H>_<fmt>.png
  --width / --height  SOURCE frame geometry (default 640x480) -- must match the
                      file, or the decode reads the wrong stride
  --offset N          bytes to skip before the frame (file header), default 0

TWO INPUT FAMILIES -- pick one with -t/--target (default: RAW)
  In view mode -t names what the INPUT is (in convert it names the OUTPUT;
  same flag, opposite direction -- see --help-convert).

  -t RAW -> --raw-type, --alignment, --endianness, --bayer-pattern,
            --preview-mode
  -t YUV -> --yuv-type  (--input-yuv-type works too, same meaning)

RAW OPTIONS
  --raw-type       {_RAW_FORMATS}
                   (default RAW12)
  --alignment      msb | lsb (default msb). Only RAW10/12/16 stored in 16-bit
                   words: msb = value sits in the high N bits, lsb = low N bits.
  --endianness     little | big (default little). RAW10/12/16/32 byte order.
  --bayer-pattern  RGGB | GRBG | GBRG | BGGR (default RGGB, case-insensitive)
  --preview-mode   "Bayer Color" (demosaic) | "Grayscale" (default Bayer Color)

YUV OPTIONS
  --yuv-type       {_YUV_YONLY}
                   {_YUV_422}
                   {_YUV_420}
                   (default YUYV)
  --alignment / --endianness only affect YOnly10/12/14/16 (stored in 16-bit
  words); YOnly8 and every 4:2:x format ignore them.
  Geometry rules: 4:2:2 needs an even width; 4:2:0 needs even width AND height;
  YOnly accepts any positive size.

EXAMPLES -- each is a complete command; change only what you need

  Example 1: RAW12, MSB-aligned, little endian, RGGB
    raw-view view -i sensor.raw -o sensor.png -t RAW \\
             --raw-type RAW12 --width 1920 --height 1080 \\
             --alignment msb --endianness little --bayer-pattern RGGB
    Try next: LSB-aligned big endian -> change those two flags only
    (--alignment lsb --endianness big); everything else is identical.

  Example 2: RAW10 Packed (MIPI: 4 pixels in 5 bytes, width % 4 == 0)
    raw-view view -i packed.raw -o packed.png -t RAW \\
             --raw-type "RAW10 Packed" --width 1920 --height 1080
    Try next: "RAW12 Packed" (2 px / 3 bytes, width % 2) and
    "RAW14 Packed" (4 px / 7 bytes, width % 4) are the same command shape --
    only --raw-type changes. Unpacked RAW10/RAW12/RAW16 are also identical,
    except that alignment and endianness become meaningful.

  Example 3: YUYV (4:2:2, packed 2 pixels per 4 bytes)
    raw-view view -i frame.yuv -o frame.png -t YUV \\
             --yuv-type YUYV --width 1920 --height 1080
    Try next: YVYU / UYVY / VYUY are the same four bytes per macropixel in a
    different order -- swap --yuv-type and nothing else. NV16 / NV61 store a Y
    plane plus one interleaved chroma plane, same total size, so the same
    command works with --yuv-type NV16.

  Example 4: YOnly10 (4:0:0, 10 bits left-aligned in 16-bit words, 2 B/pixel)
    raw-view view -i grey.bin -o grey.png -t YUV \\
             --yuv-type YOnly10 --width 1296 --height 736
    Try next: YOnly12 / YOnly14 / YOnly16 use the same command (bit depth
    only, still 2 bytes/pixel). YOnly8 is 1 byte/pixel and ignores
    alignment/endianness, so drop them:
    raw-view view -i grey8.bin -o grey8.png -t YUV \\
             --yuv-type YOnly8 --width 1296 --height 736
    If the values look wrong (too bright / banded), the file is probably
    LSB-aligned: add --alignment lsb (or --endianness big).

  Example 5: NV12 (4:2:0, semi-planar; width and height must both be even)
    raw-view view -i frame.nv12 -o frame.png -t YUV \\
             --yuv-type NV12 --width 1280 --height 720
    Try next: I420, YV12, NV21 -- the four 4:2:0 layouts differ only in plane
    order, so only --yuv-type changes.

  Example 6: skip a 128-byte header and preview grayscale
    raw-view view -i a.raw -o a.png -t RAW --raw-type RAW12 \\
             --width 640 --height 480 --offset 128 --preview-mode Grayscale

AUTO OUTPUT NAME
  Without -o the file is written next to the input under view_out/ using the
  template <stem>_<width>x<height>_<format>.png (configurable in the GUI).

LIMIT
  One frame may not exceed 512 MB; the check runs before anything is written.

SEE ALSO
  --help-convert (encode / transcode), --help-batch (JSON batches), docs/cli.md
"""


# ── Level 2: convert ──────────────────────────────────────────────────


def convert_help() -> str:
    return f"""\
raw-view convert -- encode, transcode, or decode -- one command, three jobs

USAGE
  raw-view convert -i INPUT -o OUTPUT [-t {{RAW|YUV}}] [options]
  In convert, -t/--target names what you WRITE (in view it names what you READ).

INPUT
  -i, --input PATH    exactly one file to read
                      (a directory is rejected -- use `batch` for a folder)

WHICH JOB RUNS (decided by --input-type and by the output file extension)
  1. image -> RAW/YUV   --input-type image (default)
       Decodes PNG/JPG/BMP, resizes it to --width x --height, writes RAW or YUV.
       --width/--height are the OUTPUT size (default 640x480).
  2. YUV -> YUV         --input-type yuv, output is not an image extension
       Lossless byte rearrangement inside one chroma family. No resize.
       --width/--height are the SOURCE frame size and are REQUIRED.
  3. YUV -> image       --input-type yuv, output ends in
                       .png/.jpg/.jpeg/.bmp/.tif/.tiff
       Decodes ONE frame of the YUV file to that image. Same parameters as
       job 2 (source frame size), plus optional --offset.
       Unlike job 2 this tolerates trailing bytes: it reports how many whole
       frames the file holds, decodes the first, and warns if the tail is not
       a whole frame (job 2 rejects the whole file instead).

  --input-type raw is reserved (RAW <-> YUV bit unpacking is not implemented).

WHICH OPTIONS APPLY TO WHICH JOB
  job 1 -> RAW : -i -o -t RAW --raw-type --alignment --endianness
                 --source-mode --bayer-pattern --width --height
  job 1 -> YUV : -i -o -t YUV --yuv-type --alignment --endianness --width --height
  job 2       : -i -o --input-type yuv --input-yuv-type --yuv-type --width --height
                 [--offset] [--input-alignment] [--input-endianness]
  job 3       : -i -o --input-type yuv --input-yuv-type --width --height
                 [--offset] [--input-alignment] [--input-endianness]
                 (--yuv-type is not consulted: job 3 has no target layout)

DEFAULTS AND FALLBACKS -- there is NO format auto-detection
  --input-type         image (jobs 1) | yuv (jobs 2 and 3)
  --target             RAW, except with --input-type yuv where it defaults to
                       YUV; an explicit "-t RAW" with a YUV source is an error
  --input-yuv-type     falls back to --yuv-type (so an omitted input layout is
                       decoded as YUYV, not guessed from the file)
  --input-alignment    falls back to --alignment
  --input-endianness   falls back to --endianness
  --input-raw-type     reserved; parsed but not used yet (--input-type raw is
                       not implemented)

JOB 1 DETAILS
  --raw-type       {_RAW_FORMATS}
                   (default RAW12)
  --alignment      msb | lsb (default msb) -- RAW10/12/16 in 16-bit words
  --endianness     little | big (default little) -- RAW10/12/16/32
  --source-mode    bayer (default) | gray -- gray writes one luma sample/pixel
  --bayer-pattern  RGGB | GRBG | GBRG | BGGR (default RGGB, used only for bayer)
  --yuv-type       {_YUV_YONLY}
                   {_YUV_422}
                   {_YUV_420}
                   (default YUYV; --alignment/--endianness matter only for
                   YOnly10/12/14/16)
  RAW10/12/14 Packed require width % 4 / % 2 / % 4 == 0; 4:2:2 needs an even
  width and 4:2:0 even width and height. --width/--height RESIZE the image.

JOB 2 DETAILS -- the YUV families
  Conversions are pure byte permutations inside one family:
    {_YUV_422}   (6 formats, 30 directions)
    {_YUV_420}{" " * (len(_YUV_422) - len(_YUV_420))}   (4 formats, 12 directions)
  Every pair inside a family converts; the following are refused with a reason:
    4:2:2 <-> 4:2:0   changes chroma sampling, i.e. lossy chroma resampling
    YOnly <-> 4:2:x   YOnly has no chroma planes; colour would have to be invented
    RAW <-> YUV       reserved for a later release
    X -> X            no-op
  The file size must be an exact multiple of one frame; every frame is converted
  and kept.

EXAMPLES -- each is a complete command

  Example 1: PNG -> RAW12, MSB-aligned, little endian, RGGB Bayer mosaic
    raw-view convert -i photo.png -o photo.raw -t RAW \\
             --raw-type RAW12 --width 1920 --height 1080 \\
             --alignment msb --endianness little \\
             --source-mode bayer --bayer-pattern RGGB
    Try next: LSB-aligned big endian is the same command with
    (--alignment lsb --endianness big); a grayscale RAW is the same command with
    (--source-mode gray), which also makes --bayer-pattern irrelevant.

  Example 2: PNG -> RAW10 Packed (4 pixels in 5 bytes, width % 4 == 0)
    raw-view convert -i photo.png -o photo.raw -t RAW \\
             --raw-type "RAW10 Packed" --width 1920 --height 1080
    Try next: "RAW12 Packed" (width % 2) and "RAW14 Packed" (width % 4) are
    the same command with a different --raw-type.

  Example 3: PNG -> YUYV, resized to 1280x720
    raw-view convert -i photo.png -o photo.yuv -t YUV \\
             --yuv-type YUYV --width 1280 --height 720
    Try next: every other 4:2:2 / 4:2:0 layout is the same command with
    --yuv-type swapped (NV12, I420, NV16, ...). For YOnly10 add the storage
    flags: --yuv-type YOnly10 --alignment msb --endianness little

  Example 4: NV16 -> YUYV (4:2:2 family, lossless, no resize; 1920x1080 source)
    raw-view convert -i frame.nv16 -o frame.yuyv --input-type yuv \\
             --input-yuv-type NV16 --yuv-type YUYV \\
             --width 1920 --height 1080
    Try next: YVYU / UYVY / VYUY / NV61 are the same command with --yuv-type
    changed; any 4:2:2 source works by changing --input-yuv-type. Both knobs
    are independent, so all 30 directions are reachable this way.

  Example 5: I420 -> NV12 (4:2:0 family, lossless; width and height must be even)
    raw-view convert -i frame.i420 -o frame.nv12 --input-type yuv \\
             --input-yuv-type I420 --yuv-type NV12 \\
             --width 1920 --height 1080
    Try next: YV12 and NV21 are the same command with --yuv-type YV12/NV21.

  Example 6: YUV binary -> PNG (decode; this is the fix for
             "Error: failed to read image: x.bin" from a plain convert)
    raw-view convert -i grey.bin -o grey.png --input-type yuv \\
             --input-yuv-type YOnly10 --width 1296 --height 736
    The same decode is available as
    raw-view view -i grey.bin -o grey.png -t YUV --yuv-type YOnly10 \\
             --width 1296 --height 736 ; both are equivalent.
    NOTE: the output extension must be one of
    .png/.jpg/.jpeg/.bmp/.tif/.tiff -- that is how the decode is recognised.

  Example 7: skip a 128-byte file header while transcoding
    raw-view convert -i frame.nv16 -o frame.yuyv --input-type yuv \\
             --input-yuv-type NV16 --yuv-type YUYV \\
             --width 1920 --height 1080 --offset 128

REJECTED ON PURPOSE
  -t RAW with --input-type yuv, --input-type raw, a source and target in
  different chroma families, a partial trailing frame, and any frame above
  512 MB (checked before writing, so no half-written output is left behind).

SEE ALSO
  --help-view (decode parameters), --help-batch (JSON batches), docs/cli.md
"""


# ── Level 2: batch ───────────────────────────────────────────────────


def batch_help() -> str:
    return f"""\
raw-view batch -- run many view/convert jobs described by a JSON file

USAGE
  raw-view batch -b, --batch-file PATH
  raw-view --help-batch        (--batch-help is kept as an alias)

REQUIRED OPTION
  -b, --batch-file PATH   the JSON job file. Nothing else is needed on the
                          command line: every per-file parameter lives in it.
  Options --width/--height/--offset/-i/-o are NOT read in batch mode; set
  width/height/offset inside the JSON instead.

Batch JSON format
-----------------
The file must be strict JSON encoded as UTF-8. Comments (// and /* */) and
trailing commas are NOT allowed -- they make json.load fail. Each example below
is valid JSON exactly as printed: copy one, save it, and run it.

Use batch when the jobs differ, share a shape, or are many; a shell loop calling
`raw-view convert` once per file is simpler when there is exactly one fixed
command and no per-file variation.

WANT TO PROCESS A WHOLE FOLDER? This is the way. view and convert take exactly
one file (a directory is rejected -- use `batch` for a folder); batch is what
replaces that -- point one entry's "input" at a glob:
{{"files": [{{"input": "captures/*.raw"}}]}}. Ready-to-run examples live in
sample/batch/.

GLOBAL KEYS -- defaults for every entry; any entry may override any of them
  mode             "convert" (default) | "view"
  target           "RAW" (default) | "YUV"
                     convert: the format to WRITE
                     view:    the format to READ
  input_type       "image" (default) | "yuv"      (convert only)
                     "yuv" = lossless YUV -> YUV rearrangement; width/height
                     then describe the SOURCE frame and become REQUIRED
  input_yuv_type   YUV layout of the input (default: yuv_type)
  input_raw_type   RAW layout of the input (default: raw_type)
  input_alignment  input bit alignment (default: alignment)
  input_endianness input byte order   (default: endianness)
  raw_type         RAW12 (default)
  yuv_type         YUYV (default)
  width, height    default 640x480 (source frame size when input_type is yuv)
  alignment        lsb | msb (default msb)
  endianness       little | big (default little)
  source_mode      bayer | gray (default bayer)
  bayer_pattern    RGGB | GRBG | GBRG | BGGR (default RGGB, case-insensitive)
  preview_mode     "Bayer Color" | "Grayscale" (mode=view only)
  offset           bytes to skip before the frame data (default 0)
  output_dir       optional; where auto-named outputs go
  base_dir         optional; relative files[].input paths resolve from here
  files            REQUIRED: array of entries

  RAW formats:  {_RAW_FORMATS}
  YUV formats:  {_YUV_YONLY}
                {_YUV_422}
                {_YUV_420}

PER-ENTRY KEYS (inside files[])
  input            required; * ? [ ] trigger glob expansion (one entry per match)
  output           optional; omitted -> auto name
                     convert: <stem>_<W>x<H>_<format>.raw|.yuv
                     view:    <stem>_<W>x<H>_<format>.png
                   with no output_dir the file is written next to its input
  <any global key> overrides that global value for this entry only

BEHAVIOUR
  - Entries run in order. A failing entry prints FAIL and the batch continues;
    the exit code is 1 if anything failed, 0 otherwise.
  - mode "view" decodes a RAW/YUV file to PNG. mode "convert" + input_type "yuv"
    re-arranges YUV into another YUV layout, unless the entry names an image
    output ("output": "x.png"), which decodes it -- the same rule as the
    single-file command (see --help-convert).
  - Auto-generated output names are unique across the whole batch: a collision
    becomes ..._2, ..._3 instead of silently overwriting an earlier result.
  - mode / target / input_type values are validated (case-insensitively, but a
    misspelling is an error) instead of silently running another operation.
  - One frame may not exceed 512 MB. The check runs before anything is written,
    so an over-limit entry never leaves a partial or gigantic file behind.

EXAMPLE 1 -- smallest useful batch: every PNG in photos/ -> RAW12 at 1920x1080
------------------------------------------------------------------------------
{BATCH_EXAMPLE_MINIMAL}
------------------------------------------------------------------------------

EXAMPLE 2 -- mixed batch: image->RAW, image->YUV, image->packed RAW,
             RAW->PNG and YUV->PNG in one file
------------------------------------------------------------------------------
{BATCH_EXAMPLE_MIXED}
------------------------------------------------------------------------------

EXAMPLE 3 -- YUV family rearrangement: every .nv16 -> YUYV (lossless), with one
             entry redirected to YVYU
------------------------------------------------------------------------------
{BATCH_EXAMPLE_YUV}
------------------------------------------------------------------------------

SEE ALSO
  --help-view, --help-convert, docs/cli.md
"""


# ── Printers ──────────────────────────────────────────────────────────


def show_main_help(version: str) -> None:
    print(main_help(version))


def show_view_help() -> None:
    print(view_help())


def show_convert_help() -> None:
    print(convert_help())


def show_batch_help() -> None:
    print(batch_help())
