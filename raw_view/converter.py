"""Image file conversion helpers."""

from __future__ import annotations

import numpy as np

from .formats import gray8_to_raw_bytes, rgb_to_yuv_bytes

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
    b = bgr[:, :, 0].astype(np.uint8)
    g = bgr[:, :, 1].astype(np.uint8)
    r = bgr[:, :, 2].astype(np.uint8)
    out = np.empty((out_height, out_width), dtype=np.uint8)
    p = pattern.upper()
    if p == "RGGB":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = r[0::2, 0::2], g[0::2, 1::2], g[
            1::2, 0::2
        ], b[1::2, 1::2]
    elif p == "BGGR":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = b[0::2, 0::2], g[0::2, 1::2], g[
            1::2, 0::2
        ], r[1::2, 1::2]
    elif p == "GRBG":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = g[0::2, 0::2], r[0::2, 1::2], b[
            1::2, 0::2
        ], g[1::2, 1::2]
    elif p == "GBRG":
        out[0::2, 0::2], out[0::2, 1::2], out[1::2, 0::2], out[1::2, 1::2] = g[0::2, 0::2], b[0::2, 1::2], r[
            1::2, 0::2
        ], g[1::2, 1::2]
    else:
        raise ValueError(f"unsupported bayer pattern: {pattern}")
    return out


def bayer8_to_rgb(bayer8: np.ndarray, pattern: str = "RGGB") -> np.ndarray:
    _require_cv2()
    p = pattern.upper()
    conversion = {
        "RGGB": cv2.COLOR_BayerRG2RGB,
        "BGGR": cv2.COLOR_BayerBG2RGB,
        "GRBG": cv2.COLOR_BayerGR2RGB,
        "GBRG": cv2.COLOR_BayerGB2RGB,
    }.get(p)
    if conversion is None:
        raise ValueError(f"unsupported bayer pattern: {pattern}")
    return cv2.cvtColor(bayer8, conversion)


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
        gray = bgr_to_gray8(bgr, out_width, out_height)
    elif mode == "bayer":
        gray = bgr_to_bayer8(bgr, out_width, out_height, pattern=bayer_pattern)
    else:
        raise ValueError(f"unsupported RAW source mode: {source_mode}")
    raw_bytes = gray8_to_raw_bytes(gray, raw_type, alignment=alignment, endianness=endianness)
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
