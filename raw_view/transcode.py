"""Lossless byte-rearrangement transcode between YUV formats.

Why a ``(Y, U, V)`` plane intermediate?
---------------------------------------
A YUV frame is stored in many interchangeable layouts.  The 4:2:2 family can be
packed per macropixel (``YUYV``/``UYVY``/``YVYU``/``VYUY``) or split into a Y
plane plus an interleaved chroma plane (``NV16``/``NV61``); the 4:2:0 family can
be fully planar (``I420``/``YV12``) or semi-planar (``NV12``/``NV21``).  All of
them describe *exactly the same* image data, so converting between them is a
pure permutation of bytes: no resampling, no rounding, bit-exact round trips.

Implemented naively that is ``N * (N - 1)`` direction-specific routines
(6 * 5 = 30 for 4:2:2, 4 * 3 = 12 for 4:2:0, 42 in total) that all repeat the
same index arithmetic and each hide their own copy of the layout.  Going through
one canonical intermediate splits the problem into ``N`` splitters and ``N``
packers: a direction is then ``split(source) -> pack(target)``, and adding a new
format costs one splitter plus one packer rather than ``2 * (N - 1)`` code
paths.  The intermediate is ``(Y, U, V)`` and nothing else, because every format
in scope is exactly a different arrangement of those three planes (4:2:2 chroma
planes are ``h x w/2``, 4:2:0 chroma planes are ``h/2 x w/2``).

Layers (the dispatch table is reserved for the later ones)
----------------------------------------------------------
1. :func:`_rearrange_yuv_planes` -- implemented here.  Byte rearrangement
   inside one chroma-sampling family; sizes are identical on both sides.
2. *Reserved*: packed <-> unpacked RAW bit-unpacking (reusing
   ``formats.decode_raw`` and the ``_pack_*`` helpers).  A route entry would
   point at a different splitter/packer pair with no change to the public API.
3. *Reserved*: bit alignment / bit-depth conversion (``YOnly10``.. and RAW N-bit
   stored in 16-bit containers).  ``TranscodeSpec.alignment`` /
   ``TranscodeSpec.endianness`` are carried for this layer; they have no effect
   on the ten 8-bit YUV formats handled today.

Deliberately out of scope (and rejected with a readable reason):
4:2:2 <-> 4:2:0 (that changes chroma sampling, i.e. it is *lossy* resampling),
any RAW <-> YUV direction, and the YOnly / 4:0:0 family (no chroma planes at
all).  :func:`reject_reason` is the single place that decides, so the CLI and
the GUI grey out exactly what :func:`can_transcode` refuses.

Frame geometry
--------------
On this path width/height are the **source frame dimensions**, used only to
parse the byte layout -- never an output size.  There is no resize (unlike
``converter.image_file_to_yuv``, which resamples a decoded image to a requested
output size).  A file may contain several back-to-back frames; the byte count
must be an exact multiple of one frame's size, otherwise the whole file is
rejected instead of silently truncating the tail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

from raw_view.formats import RAW_BITS, FormatError, expected_frame_size_yuv

__all__ = [
    "YUV420_FAMILY",
    "YUV420_FORMATS",
    "YUV422_FAMILY",
    "YUV422_FORMATS",
    "TranscodeError",
    "TranscodeSpec",
    "can_transcode",
    "family_formats",
    "frame_count",
    "frame_size",
    "reject_reason",
    "transcode_bytes",
    "transcode_file",
    "transcode_frames",
]

# ── Format families ───────────────────────────────────────────────────
# Ordered tuples (not sets) so dropdowns and error messages are deterministic.
YUV422_FORMATS = ("YUYV", "UYVY", "YVYU", "VYUY", "NV16", "NV61")
YUV420_FORMATS = ("I420", "YV12", "NV12", "NV21")
YUV422_FAMILY = frozenset(YUV422_FORMATS)
YUV420_FAMILY = frozenset(YUV420_FORMATS)
YUV_TRANSCODE_FORMATS = YUV422_FORMATS + YUV420_FORMATS

# Case-insensitive spellings -> the canonical name used in this module.  The
# YOnly spellings are included so their rejection message names the format as
# the UI does ("YOnly12", not "YONLY12"); RAW spellings likewise.
_CANONICAL_NAMES: dict[str, str] = {
    name.upper(): name for name in YUV_TRANSCODE_FORMATS
}
_CANONICAL_NAMES.update(
    {name.upper(): name for name in ("YOnly", "YOnly8", "YOnly10", "YOnly12", "YOnly14", "YOnly16")}
)
_CANONICAL_NAMES.update({name.upper(): name for name in RAW_BITS})

# Layer identifiers for the (reserved) transcoding implementations.
_LAYER_REARRANGE = 1   # implemented
_LAYER_UNPACK = 2      # reserved: packed <-> unpacked RAW
_LAYER_BIT_DEPTH = 3   # reserved: bit alignment / bit depth


class TranscodeError(ValueError):
    """Raised when a direction is unsupported or the input bytes do not fit.

    Subclasses :class:`ValueError` so callers that already treat bad parameters
    as ``ValueError`` (CLI error paths, dialog exception handlers) need no
    special case.
    """


@dataclass(frozen=True)
class TranscodeSpec:
    """Everything needed to transcode a frame buffer.

    Attributes
    ----------
    source_format, target_format
        YUV sub-format names, e.g. ``"NV16"`` -> ``"I420"``.
    width, height
        Source frame dimensions (the layout's geometry).  Not an output size.
    alignment, endianness
        Only meaningful for the 16-bit-stored YOnly formats, which this round
        rejects; carried so layers 2/3 can use the same spec object.
    offset
        Bytes to skip before the first frame (file header, padding, ...).
    """

    source_format: str
    target_format: str
    width: int
    height: int
    alignment: str = "msb"
    endianness: str = "little"
    offset: int = 0


# ── Canonical plane bundle ────────────────────────────────────────────


@dataclass(frozen=True)
class _YUVPlanes:
    """One frame split into its three planes (uint8, no padding).

    4:2:2: ``y`` is ``h x w``, ``u``/``v`` are ``h x w/2``.
    4:2:0: ``y`` is ``h x w``, ``u``/``v`` are ``h/2 x w/2``.
    """

    y: np.ndarray
    u: np.ndarray
    v: np.ndarray
    width: int
    height: int


_Splitter = Callable[[object, int, int], _YUVPlanes]
_Packer = Callable[[_YUVPlanes], bytes]


def _as_u8(raw: object) -> np.ndarray:
    """View *raw* (bytes/bytearray/memoryview) as a flat uint8 array, no copy."""
    return np.frombuffer(raw, dtype=np.uint8)


def _interleave(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Interleave two equally shaped chroma planes sample-by-sample (UVUV...)."""
    out = np.empty((u.shape[0], u.shape[1] * 2), dtype=np.uint8)
    out[:, 0::2] = u
    out[:, 1::2] = v
    return out


# ── 4:2:0 splitters (chroma is h/2 x w/2) ─────────────────────────────


def _split_i420(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    uv_size = y_size // 4
    y = arr[:y_size].reshape(h, w)
    u = arr[y_size:y_size + uv_size].reshape(h // 2, w // 2)
    v = arr[y_size + uv_size:y_size + 2 * uv_size].reshape(h // 2, w // 2)
    return _YUVPlanes(y, u, v, w, h)


def _split_yv12(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    uv_size = y_size // 4
    y = arr[:y_size].reshape(h, w)
    v = arr[y_size:y_size + uv_size].reshape(h // 2, w // 2)
    u = arr[y_size + uv_size:y_size + 2 * uv_size].reshape(h // 2, w // 2)
    return _YUVPlanes(y, u, v, w, h)


def _split_nv12(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    y = arr[:y_size].reshape(h, w)
    uv = arr[y_size:].reshape(h // 2, w)
    return _YUVPlanes(y, uv[:, 0::2], uv[:, 1::2], w, h)


def _split_nv21(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    y = arr[:y_size].reshape(h, w)
    vu = arr[y_size:].reshape(h // 2, w)
    return _YUVPlanes(y, vu[:, 1::2], vu[:, 0::2], w, h)


# ── 4:2:2 splitters (chroma is h x w/2) ───────────────────────────────


#: Byte roles of the four samples of one 4:2:2 macropixel: Y0/Y1 are the
#: even/odd luma samples, U/V the chroma shared by the pair.  One table entry
#: per packed format -- adding a fifth layout costs one line, not a direction
#: matrix.
ROLE_ORDER_422: dict[str, tuple[str, str, str, str]] = {
    "YUYV": ("Y0", "U", "Y1", "V"),
    "YVYU": ("Y0", "V", "Y1", "U"),
    "UYVY": ("U", "Y0", "V", "Y1"),
    "VYUY": ("V", "Y0", "U", "Y1"),
}


def _split_packed(raw: object, w: int, h: int, order: tuple[str, ...]) -> _YUVPlanes:
    """Split a packed 4:2:2 macropixel layout.

    ``order`` gives the role of each of the four bytes per macropixel (see
    :data:`ROLE_ORDER_422`), so one function covers all four packed variants.
    """
    p = _as_u8(raw).reshape(h, w // 2, 4)
    roles = {role: p[:, :, i] for i, role in enumerate(order)}
    y = np.empty((h, w), dtype=np.uint8)
    y[:, 0::2] = roles["Y0"]
    y[:, 1::2] = roles["Y1"]
    return _YUVPlanes(y, roles["U"], roles["V"], w, h)


def _split_yuyv(raw: object, w: int, h: int) -> _YUVPlanes:
    return _split_packed(raw, w, h, ROLE_ORDER_422["YUYV"])


def _split_uyvy(raw: object, w: int, h: int) -> _YUVPlanes:
    return _split_packed(raw, w, h, ROLE_ORDER_422["UYVY"])


def _split_yvyu(raw: object, w: int, h: int) -> _YUVPlanes:
    return _split_packed(raw, w, h, ROLE_ORDER_422["YVYU"])


def _split_vyuy(raw: object, w: int, h: int) -> _YUVPlanes:
    return _split_packed(raw, w, h, ROLE_ORDER_422["VYUY"])


def _split_nv16(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    y = arr[:y_size].reshape(h, w)
    c = arr[y_size:].reshape(h, w)
    return _YUVPlanes(y, c[:, 0::2], c[:, 1::2], w, h)


def _split_nv61(raw: object, w: int, h: int) -> _YUVPlanes:
    arr = _as_u8(raw)
    y_size = w * h
    y = arr[:y_size].reshape(h, w)
    c = arr[y_size:].reshape(h, w)
    return _YUVPlanes(y, c[:, 1::2], c[:, 0::2], w, h)


# ── 4:2:0 packers ─────────────────────────────────────────────────────


def _pack_i420(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), p.u.tobytes(), p.v.tobytes()))


def _pack_yv12(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), p.v.tobytes(), p.u.tobytes()))


def _pack_nv12(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), _interleave(p.u, p.v).tobytes()))


def _pack_nv21(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), _interleave(p.v, p.u).tobytes()))


# ── 4:2:2 packers ─────────────────────────────────────────────────────


def _pack_packed(p: _YUVPlanes, order: tuple[str, ...]) -> bytes:
    """Pack planes into a macropixel layout; see :func:`_split_packed`."""
    roles = {
        "Y0": p.y[:, 0::2],
        "Y1": p.y[:, 1::2],
        "U": p.u,
        "V": p.v,
    }
    out = np.empty((p.height, p.width // 2, 4), dtype=np.uint8)
    for i, role in enumerate(order):
        out[:, :, i] = roles[role]
    return out.tobytes()


def _pack_yuyv(p: _YUVPlanes) -> bytes:
    return _pack_packed(p, ROLE_ORDER_422["YUYV"])


def _pack_uyvy(p: _YUVPlanes) -> bytes:
    return _pack_packed(p, ROLE_ORDER_422["UYVY"])


def _pack_yvyu(p: _YUVPlanes) -> bytes:
    return _pack_packed(p, ROLE_ORDER_422["YVYU"])


def _pack_vyuy(p: _YUVPlanes) -> bytes:
    return _pack_packed(p, ROLE_ORDER_422["VYUY"])


def _pack_nv16(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), _interleave(p.u, p.v).tobytes()))


def _pack_nv61(p: _YUVPlanes) -> bytes:
    return b"".join((p.y.tobytes(), _interleave(p.v, p.u).tobytes()))


# ── Layout tables: one split + one pack per format ────────────────────
# Adding a format means adding one entry to each table; every direction
# involving it then exists automatically (no per-direction code).

_SPLITTERS: dict[str, _Splitter] = {
    "I420": _split_i420,
    "YV12": _split_yv12,
    "NV12": _split_nv12,
    "NV21": _split_nv21,
    "YUYV": _split_yuyv,
    "UYVY": _split_uyvy,
    "YVYU": _split_yvyu,
    "VYUY": _split_vyuy,
    "NV16": _split_nv16,
    "NV61": _split_nv61,
}

_PACKERS: dict[str, _Packer] = {
    "I420": _pack_i420,
    "YV12": _pack_yv12,
    "NV12": _pack_nv12,
    "NV21": _pack_nv21,
    "YUYV": _pack_yuyv,
    "UYVY": _pack_uyvy,
    "YVYU": _pack_yvyu,
    "VYUY": _pack_vyuy,
    "NV16": _pack_nv16,
    "NV61": _pack_nv61,
}


@dataclass(frozen=True)
class TranscodeRoute:
    """A registered (source -> target) implementation.

    ``layer`` documents which transcode layer backs the route.  Only
    :data:`_LAYER_REARRANGE` exists today; layers 2 and 3 are reserved slots
    that will register here with their own splitter/packer pair.
    """

    layer: int
    splitter: _Splitter
    packer: _Packer


# (source, target) -> route.  Built once at import from the two tables above.
_ROUTES: dict[tuple[str, str], TranscodeRoute] = {}


def _register_family(formats: tuple[str, ...]) -> None:
    for source in formats:
        for target in formats:
            if source == target:
                # A same-format "conversion" is a no-op; refusing it keeps the
                # GUI list honest instead of offering a meaningless target.
                continue
            _ROUTES[(source, target)] = TranscodeRoute(
                layer=_LAYER_REARRANGE,
                splitter=_SPLITTERS[source],
                packer=_PACKERS[target],
            )


_register_family(YUV422_FORMATS)
_register_family(YUV420_FORMATS)

# Layer 2 / 3 reserved slots -- kept explicit so the intent survives refactors:
#   _ROUTES[("RAW10", "RAW10 Packed")] = TranscodeRoute(_LAYER_UNPACK, ...)
#   _ROUTES[("YOnly12", "YOnly8")]      = TranscodeRoute(_LAYER_BIT_DEPTH, ...)
# Such a route must also relax reject_reason() for its (source, target) family;
# the public API (transcode_bytes/frames/file, TranscodeSpec) needs no change.


# ── Format helpers ────────────────────────────────────────────────────


def _canonical(name: object) -> str:
    """Case-insensitive format name -> canonical spelling (or stripped input)."""
    text = str(name or "").strip()
    return _CANONICAL_NAMES.get(text.upper(), text)


def _is_yonly(name: str) -> bool:
    return name.upper().startswith("YONLY")


def _is_raw(name: str) -> bool:
    return name.upper().startswith("RAW")


def _family_of(name: str) -> str | None:
    """Return ``"4:2:2"`` / ``"4:2:0"`` for a canonical format, else ``None``."""
    if name in YUV422_FAMILY:
        return "4:2:2"
    if name in YUV420_FAMILY:
        return "4:2:0"
    return None


def family_formats(format_name: str) -> tuple[str, ...]:
    """Formats that share *format_name*'s chroma sampling (empty if unknown)."""
    family = _family_of(_canonical(format_name))
    if family == "4:2:2":
        return YUV422_FORMATS
    if family == "4:2:0":
        return YUV420_FORMATS
    return ()


def reject_reason(source_format: str, target_format: str) -> str | None:
    """Why ``source -> target`` is refused, or ``None`` when it is supported.

    This is the single source of truth for both the CLI and the GUI: the GUI
    greys a target out exactly when this returns a message, and shows the
    message as its tooltip so "this cannot convert" is explained rather than
    hidden.
    """
    source = _canonical(source_format)
    target = _canonical(target_format)
    if (source, target) in _ROUTES:
        return None
    if not source or not target:
        return "both a source and a target YUV format are required"
    if source == target:
        return f"{source} -> {target} is a no-op; choose a different target format"
    if _is_yonly(source) or _is_yonly(target):
        return (
            f"{source if _is_yonly(source) else target} is YUV 4:0:0 (luma only, "
            "no chroma planes); converting between it and YUV 4:2:2/4:2:0 would "
            "have to resample or invent colour and is not lossless"
        )
    if _is_raw(source) or _is_raw(target):
        return (
            "RAW <-> YUV conversion is reserved for a future release; this build "
            "only re-arranges YUV 4:2:2 / 4:2:0 frames"
        )
    source_family = _family_of(source)
    target_family = _family_of(target)
    if source_family is None:
        return f"unsupported source format {source!r}: supported formats are {', '.join(YUV_TRANSCODE_FORMATS)}"
    if target_family is None:
        return f"unsupported target format {target!r}: supported formats are {', '.join(YUV_TRANSCODE_FORMATS)}"
    if source_family != target_family:
        return (
            f"{source} ({source_family}) -> {target} ({target_family}) changes "
            "chroma sampling; 4:2:2 <-> 4:2:0 needs lossy chroma resampling, so "
            "it is not a lossless byte rearrangement"
        )
    return f"conversion from {source} to {target} is not supported"


def can_transcode(source_format: str, target_format: str) -> bool:
    """True when the direction is implemented by this module."""
    return reject_reason(source_format, target_format) is None


# ── Spec / size validation ────────────────────────────────────────────


def _resolve_spec(spec: TranscodeSpec) -> tuple[str, str]:
    """Validate *spec* and return its canonical (source, target) names."""
    if not isinstance(spec, TranscodeSpec):
        raise TranscodeError("a TranscodeSpec is required")
    source = _canonical(spec.source_format)
    target = _canonical(spec.target_format)
    reason = reject_reason(source, target)
    if reason:
        raise TranscodeError(reason)
    if spec.width <= 0 or spec.height <= 0:
        raise TranscodeError(
            f"invalid frame dimensions: {spec.width}x{spec.height}"
        )
    if spec.offset < 0:
        raise TranscodeError(f"offset must be >= 0, got {spec.offset}")
    return source, target


def _frame_size(source: str, width: int, height: int) -> int:
    """Bytes per frame, applying the family's even-dimension rules."""
    try:
        return expected_frame_size_yuv(source, width, height)
    except FormatError as exc:  # re-raise in this module's error type
        raise TranscodeError(str(exc)) from exc


def frame_size(spec: TranscodeSpec) -> int:
    """Byte size of one source frame (identical to the target frame size)."""
    source, target = _resolve_spec(spec)
    source_size = _frame_size(source, spec.width, spec.height)
    target_size = _frame_size(target, spec.width, spec.height)
    if source_size != target_size:  # pragma: no cover - family invariant
        raise TranscodeError(
            f"internal error: {source} and {target} frames differ in size "
            f"({source_size} vs {target_size} bytes)"
        )
    return source_size


def frame_count(byte_count: int, spec: TranscodeSpec) -> int:
    """Number of whole frames in *byte_count* bytes, or :class:`TranscodeError`.

    A partial trailing frame is an error: silently dropping it (or writing it
    truncated) would corrupt the output in a way the user cannot see.
    """
    size = frame_size(spec)
    payload = int(byte_count) - spec.offset
    if payload < 0:
        raise TranscodeError(
            f"offset {spec.offset} is beyond the {int(byte_count)}-byte input"
        )
    if payload == 0:
        raise TranscodeError(
            f"no frame data: {int(byte_count)} bytes is empty after offset {spec.offset} "
            f"(one {spec.source_format} frame at {spec.width}x{spec.height} is {size} bytes)"
        )
    remainder = payload % size
    if remainder:
        raise TranscodeError(
            f"input size {int(byte_count)} bytes is not an integer multiple of the "
            f"{size}-byte {spec.source_format} frame ({spec.width}x{spec.height}): "
            f"{payload} bytes after offset {spec.offset} leave {remainder} trailing "
            f"byte(s); refusing to transcode a partial frame"
        )
    return payload // size


# ── Transcoding ───────────────────────────────────────────────────────


def _validate_planes(planes: _YUVPlanes, source: str, width: int, height: int) -> None:
    """Guard the splitter's output shape (catches layout-table mistakes)."""
    if source in YUV420_FAMILY:
        expected = ((height, width), (height // 2, width // 2))
    else:
        expected = ((height, width), (height, width // 2))
    if planes.y.shape != expected[0] or planes.u.shape != expected[1] or planes.v.shape != expected[1]:
        raise TranscodeError(
            f"internal error: {source} split produced planes "
            f"{planes.y.shape}/{planes.u.shape}/{planes.v.shape}, expected "
            f"{expected[0]}/{expected[1]}"
        )


def _transcode_one(
    payload: object,
    source: str,
    target: str,
    width: int,
    height: int,
    size: int,
) -> bytes:
    """Split one frame and re-pack it (layer 1: pure byte rearrangement)."""
    route = _ROUTES.get((source, target))
    if route is None:  # pragma: no cover - _resolve_spec already rejected
        raise TranscodeError(reject_reason(source, target) or "unsupported direction")
    if route.layer != _LAYER_REARRANGE:  # pragma: no cover - only layer 1 exists
        raise TranscodeError(f"{source} -> {target} uses unimplemented layer {route.layer}")
    planes = route.splitter(payload, width, height)
    _validate_planes(planes, source, width, height)
    out = route.packer(planes)
    if len(out) != size:  # pragma: no cover - packer/splitter invariant
        raise TranscodeError(
            f"internal error: {source} -> {target} produced {len(out)} bytes, "
            f"expected {size}"
        )
    return out


def transcode_bytes(data: bytes, spec: TranscodeSpec) -> bytes:
    """Transcode exactly one frame; multi-frame data goes to :func:`transcode_frames`."""
    source, target = _resolve_spec(spec)
    size = frame_size(spec)
    view = memoryview(data)
    if spec.offset:
        view = view[spec.offset:]
    if len(view) != size:
        raise TranscodeError(
            f"transcode_bytes expects exactly one {source} frame of {size} bytes "
            f"({spec.width}x{spec.height}), got {len(view)} bytes after offset "
            f"{spec.offset}; use transcode_frames for multi-frame data"
        )
    return _transcode_one(view, source, target, spec.width, spec.height, size)


def transcode_frames(data: bytes, spec: TranscodeSpec) -> list[bytes]:
    """Transcode every whole frame in *data*, preserving the frame count."""
    source, target = _resolve_spec(spec)
    size = frame_size(spec)
    count = frame_count(len(data), spec)
    view = memoryview(data)
    if spec.offset:
        view = view[spec.offset:]
    frames: list[bytes] = []
    for index in range(count):
        chunk = view[index * size:(index + 1) * size]
        frames.append(
            _transcode_one(chunk, source, target, spec.width, spec.height, size)
        )
    return frames


def transcode_file(
    input_path: str,
    output_path: str,
    spec: TranscodeSpec,
) -> tuple[int, int]:
    """Transcode a file frame by frame.

    The frame count is validated from the on-disk size *before* the output file
    is created, so an invalid input never leaves a half-written result behind.
    Returns ``(frame_count, bytes_written)``.
    """
    source, target = _resolve_spec(spec)
    size = frame_size(spec)
    count = frame_count(os.path.getsize(input_path), spec)
    parent = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(parent, exist_ok=True)
    written = 0
    with open(input_path, "rb") as src, open(output_path, "wb") as dst:
        if spec.offset:
            src.seek(spec.offset)
        for index in range(count):
            chunk = src.read(size)
            if len(chunk) != size:
                raise TranscodeError(
                    f"input shrank while transcoding: frame {index + 1}/{count} "
                    f"has {len(chunk)} of {size} bytes"
                )
            out = _transcode_one(chunk, source, target, spec.width, spec.height, size)
            dst.write(out)
            written += len(out)
    return count, written
