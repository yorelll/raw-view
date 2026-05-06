"""Image file conversion helpers (encode image→RAW/YUV and decode RAW/YUV→image)."""

from __future__ import annotations

import os

import numpy as np

from .formats import (
    gray8_to_raw_bytes,
    rgb_to_yuv_bytes,
    decode_raw,
    decode_yuv,
    ImageSpec,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    raw_to_display_gray,
)
from .logger import get_logger

logger = get_logger(__name__)

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def _require_cv2() -> None:
    if cv2 is None:
        raise RuntimeError("opencv-python is required for image file conversion")


def load_bgr_image(path: str) -> np.ndarray:
    _require_cv2()
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"failed to read image: {path}")
    return img


def bgr_to_gray8(bgr: np.ndarray, out_width: int, out_height: int) -> np.ndarray:
    _require_cv2()
    if out_width <= 0 or out_height <= 0:
        raise ValueError("output width/height must be > 0")
    src_h, src_w = bgr.shape[:2]
    if (src_w, src_h) != (out_width, out_height):
        bgr = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_LINEAR)
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


def bgr_to_bayer8(
    bgr: np.ndarray,
    out_width: int,
    out_height: int,
    pattern: str = "RGGB",
) -> np.ndarray:
    _require_cv2()
    if out_width <= 0 or out_height <= 0:
        raise ValueError("output width/height must be > 0")
    src_h, src_w = bgr.shape[:2]
    if (src_w, src_h) != (out_width, out_height):
        bgr = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_LINEAR)
    if bgr.dtype != np.uint8:
        bgr = np.clip(bgr, 0, 255).astype(np.uint8)
    b = bgr[:, :, 0]
    g = bgr[:, :, 1]
    r = bgr[:, :, 2]
    out = np.empty((out_height, out_width), dtype=np.uint8)
    p = pattern.upper()
    if p == "RGGB":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = (
            r[0::2, 0::2],
            g[0::2, 1::2],
            g[1::2, 0::2],
            b[1::2, 1::2],
        )
    elif p == "BGGR":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = (
            b[0::2, 0::2],
            g[0::2, 1::2],
            g[1::2, 0::2],
            r[1::2, 1::2],
        )
    elif p == "GRBG":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = (
            g[0::2, 0::2],
            r[0::2, 1::2],
            b[1::2, 0::2],
            g[1::2, 1::2],
        )
    elif p == "GBRG":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = (
            g[0::2, 0::2],
            b[0::2, 1::2],
            r[1::2, 0::2],
            g[1::2, 1::2],
        )
    else:
        raise ValueError(f"unsupported bayer pattern: {pattern}")
    return out


def bayer8_to_rgb(bayer8: np.ndarray, pattern: str = "RGGB") -> np.ndarray:
    try:
        _require_cv2()
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    if bayer8.ndim != 2:
        raise ValueError("bayer image must be 2D")
    p = pattern.upper()
    conversion = {
        "RGGB": cv2.COLOR_BayerRG2BGR,
        "BGGR": cv2.COLOR_BayerBG2BGR,
        "GRBG": cv2.COLOR_BayerGR2BGR,
        "GBRG": cv2.COLOR_BayerGB2BGR,
    }.get(p)
    if conversion is None:
        raise ValueError(f"unsupported bayer pattern: {pattern}")
    try:
        return cv2.cvtColor(bayer8, conversion)
    except Exception as exc:
        cv_error = getattr(cv2, "error", None)
        if cv_error is not None and isinstance(exc, cv_error):
            raise ValueError(f"failed to convert Bayer pattern {p} to RGB: {exc}") from exc
        raise


def image_file_to_raw(
    input_path: str,
    output_path: str,
    raw_type: str,
    out_width: int,
    out_height: int,
    alignment: str = "lsb",
    endianness: str = "little",
    source_mode: str = "bayer",
    bayer_pattern: str = "RGGB",
) -> int:
    bgr = load_bgr_image(input_path)
    mode = source_mode.lower()
    if mode == "gray":
        raw8 = bgr_to_gray8(bgr, out_width, out_height)
    elif mode == "bayer":
        raw8 = bgr_to_bayer8(bgr, out_width, out_height, pattern=bayer_pattern)
    else:
        raise ValueError(f"unsupported RAW source mode '{source_mode}', valid options are: 'bayer', 'gray'")
    raw_bytes = gray8_to_raw_bytes(raw8, raw_type, alignment=alignment, endianness=endianness)
    with open(output_path, "wb") as f:
        f.write(raw_bytes)
    return len(raw_bytes)


def image_file_to_yuv(
    input_path: str,
    output_path: str,
    subformat: str,
    out_width: int,
    out_height: int,
) -> int:
    bgr = load_bgr_image(input_path)
    src_h, src_w = bgr.shape[:2]
    if (src_w, src_h) != (out_width, out_height):
        bgr = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    yuv_bytes = rgb_to_yuv_bytes(rgb, subformat)
    with open(output_path, "wb") as f:
        f.write(yuv_bytes)
    return len(yuv_bytes)


# ── Decode (RAW/YUV → viewable image) ─────────────────────────────────


def raw_file_to_image(
    input_path: str,
    output_path: str,
    raw_type: str,
    width: int,
    height: int,
    alignment: str = "lsb",
    endianness: str = "little",
    preview_mode: str = "Bayer Color",
    bayer_pattern: str = "RGGB",
    offset: int = 0,
) -> int:
    """Decode a RAW file and save as PNG/JPEG."""
    logger.debug(
        "raw_file_to_image: %s -> %s (%s, %dx%d, align=%s, endian=%s, preview=%s, pattern=%s, offset=%d)",
        input_path, output_path, raw_type, width, height,
        alignment, endianness, preview_mode, bayer_pattern, offset,
    )
    try:
        with open(input_path, "rb") as f:
            data = f.read()
    except OSError as exc:
        logger.error("Failed to read input file %s: %s", input_path, exc)
        raise
    spec = ImageSpec(width, height, offset)
    raw = decode_raw(data, spec, raw_type, alignment=alignment, endianness=endianness)
    raw8 = raw_to_display_gray(raw, raw_type)

    if preview_mode.startswith("Bayer") and raw8.ndim == 2:
        try:
            rgb = bayer8_to_rgb(raw8, pattern=bayer_pattern)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except ValueError as exc:
            logger.warning("Bayer demosaic failed (%s), falling back to grayscale", exc)
            bgr = cv2.cvtColor(raw8, cv2.COLOR_GRAY2BGR)
    else:
        bgr = cv2.cvtColor(raw8, cv2.COLOR_GRAY2BGR)

    cv2.imwrite(output_path, bgr)
    size = os.path.getsize(output_path)
    logger.debug("raw_file_to_image OK: %d bytes written", size)
    return size


def yuv_file_to_image(
    input_path: str,
    output_path: str,
    subformat: str,
    width: int,
    height: int,
    offset: int = 0,
) -> int:
    """Decode a YUV file and save as PNG/JPEG."""
    logger.debug(
        "yuv_file_to_image: %s -> %s (%s, %dx%d, offset=%d)",
        input_path, output_path, subformat, width, height, offset,
    )
    with open(input_path, "rb") as f:
        data = f.read()
    spec = ImageSpec(width, height, offset)
    rgb = decode_yuv(data, spec, subformat)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, bgr)
    size = os.path.getsize(output_path)
    logger.debug("yuv_file_to_image OK: %d bytes written", size)
    return size
