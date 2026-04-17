"""Image file conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .formats import gray8_to_raw_bytes, rgb_to_yuv_bytes

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None


@dataclass(frozen=True)
class ConvertOptions:
    out_width: int
    out_height: int


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


def image_file_to_raw(
    input_path: str,
    output_path: str,
    raw_type: str,
    out_width: int,
    out_height: int,
    alignment: str = "lsb",
    endianness: str = "little",
) -> int:
    bgr = load_bgr_image(input_path)
    gray = bgr_to_gray8(bgr, out_width, out_height)
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
    _require_cv2()
    src_h, src_w = bgr.shape[:2]
    if (src_w, src_h) != (out_width, out_height):
        bgr = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    yuv_bytes = rgb_to_yuv_bytes(rgb, subformat)
    with open(output_path, "wb") as f:
        f.write(yuv_bytes)
    return len(yuv_bytes)
