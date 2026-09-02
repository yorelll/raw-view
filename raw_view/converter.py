"""Image file conversion helpers (encode image→RAW/YUV and decode RAW/YUV→image)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from .formats import (
    FormatError,
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


def resolve_output_path_collision(
    output_path: str,
    on_existing: str = "overwrite",
    prefer_keep: bool = False,
) -> str:
    """处理目标输出路径已存在时的命名（P1-6「转换加覆盖确认」）。

    只做**纯路径解析**（不写盘），供 GUI 单文件/多变体/Batch 转换统一使用：

    - ``on_existing == "overwrite"`` → 原路径不动（覆盖；默认，保持旧行为）；
    - ``on_existing == "rename"`` → 已存在时自动改为 ``<stem>_N.ext``
      （跳过被占用的序号），可重复生成不丢旧文件；
    - ``on_existing == "skip"`` → 已存在返回 ``None``，由调用方跳过。

    ``prefer_keep`` 供 Batch 使用：默认 ``rename`` 更安全（不因提示缺失丢文件），
    但如果调用方在上层已经做过一次确认（如"全部覆盖"），则按该选择覆盖。
    """
    if on_existing == "overwrite":
        return output_path
    if on_existing == "skip":
        return None if os.path.exists(output_path) else output_path
    # rename
    if not os.path.exists(output_path):
        return output_path
    p = Path(output_path)
    stem, ext = p.stem, p.suffix
    for i in range(1, 10_000):
        candidate = p.with_name(f"{stem}_{i}{ext}")
        if not os.path.exists(candidate):
            return str(candidate)
    return output_path  # 前 9999 个序号都被占用 → 原路径（由底层写入决定）


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
    alignment: str = "msb",
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
    alignment: str = "msb",
    endianness: str = "little",
) -> int:
    bgr = load_bgr_image(input_path)
    src_h, src_w = bgr.shape[:2]
    if (src_w, src_h) != (out_width, out_height):
        bgr = cv2.resize(bgr, (out_width, out_height), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    # alignment/endianness 对 YOnly 多 bit（16-bit 存储）有效，其余 YUV 忽略。
    yuv_bytes = rgb_to_yuv_bytes(
        rgb, subformat, alignment=alignment, endianness=endianness
    )
    with open(output_path, "wb") as f:
        f.write(yuv_bytes)
    return len(yuv_bytes)


# ── Multi-variant generation (one image → many outputs) ───────────────


def plan_image_variants(
    input_path: str,
    formats: list[str],
    sizes: list[tuple[int, int]],
    bayer_patterns: list[str],
    *,
    source_mode: str = "bayer",
    alignment: str = "msb",
    endianness: str = "little",
    output_dir: str | None = None,
    template: str | None = None,
) -> list[dict]:
    """Expand the selected formats/sizes/bayer patterns into a flat list of
    variant descriptors without writing any files.

    Each descriptor is a dict with keys: ``target_type`` ("RAW"/"YUV"),
    ``format``, ``width``, ``height``, ``bayer`` (may be ""), and
    ``output_path``. Bayer patterns are only fanned out for RAW formats with
    ``source_mode == "bayer"``; YUV and gray-source RAW produce a single
    entry per (format, size).
    """
    from raw_view.formats import RAW_BITS
    from raw_view.models import DEFAULT_OUTPUT_TEMPLATE, format_output_template

    raw_set = set(RAW_BITS.keys())
    tmpl = template or DEFAULT_OUTPUT_TEMPLATE
    plans: list[dict] = []
    for fmt in formats:
        is_raw = fmt in raw_set
        target_type = "RAW" if is_raw else "YUV"
        for (w, h) in sizes:
            if is_raw and source_mode.lower() == "bayer":
                patterns = bayer_patterns or ["RGGB"]
            else:
                patterns = [""]
            for pat in patterns:
                out = format_output_template(
                    tmpl,
                    input_path,
                    w,
                    h,
                    target_type,
                    output_dir=output_dir,
                    raw_type=fmt if is_raw else "",
                    yuv_type="" if is_raw else fmt,
                    bayer_pattern=pat,
                    source_mode=source_mode,
                    alignment=alignment,
                    endianness=endianness,
                )
                plans.append({
                    "target_type": target_type,
                    "format": fmt,
                    "width": w,
                    "height": h,
                    "bayer": pat,
                    "output_path": out,
                })
    return plans


def generate_image_variants(
    input_path: str,
    formats: list[str],
    sizes: list[tuple[int, int]],
    bayer_patterns: list[str],
    *,
    source_mode: str = "bayer",
    alignment: str = "msb",
    endianness: str = "little",
    output_dir: str | None = None,
    template: str | None = None,
    on_output=None,
    output_paths=None,
) -> list[str]:
    """Generate every variant produced by :func:`plan_image_variants`.

    Returns the list of written output paths. ``on_output`` — if given — is
    called with each output path as it is written (useful for progress UIs).
    ``output_paths`` — if given — is a callable ``out -> out|str`` consulted
    on each plan to (re)resolve the target path (e.g. GUI P1-6 rename-on-
    collision). Returned paths are recorded in ``written``; the written path
    may differ from the planned one.
    """
    plans = plan_image_variants(
        input_path,
        formats,
        sizes,
        bayer_patterns,
        source_mode=source_mode,
        alignment=alignment,
        endianness=endianness,
        output_dir=output_dir,
        template=template,
    )
    written: list[str] = []
    for plan in plans:
        out = plan["output_path"]
        if output_paths is not None:
            out = output_paths(out)
        if out is None:
            # 覆盖策略 "skip"：该目标已存在 → 跳过本变体（不写不报错）。
            # 调用方（batch/convert）在 plan 级解析路径时可能按单文件策略跳过。
            continue
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        if plan["target_type"] == "RAW":
            image_file_to_raw(
                input_path,
                out,
                plan["format"],
                plan["width"],
                plan["height"],
                alignment=alignment,
                endianness=endianness,
                source_mode=source_mode,
                bayer_pattern=plan["bayer"] or "RGGB",
            )
        else:
            # 0.2.1-M-3：YUV 分支必须把 endianness（对 YOnly 多 bit 有效）传下去，
            # 否则调用者请求 big endian 会按默认 little 写盘。
            image_file_to_yuv(
                input_path, out, plan["format"], plan["width"], plan["height"],
                alignment=alignment, endianness=endianness,
            )
        written.append(out)
        if on_output is not None:
            on_output(out)
    return written


# ── Decode (RAW/YUV → viewable image) ─────────────────────────────────


def _read_frame(input_path: str, width: int, height: int, frame_size: int, offset: int) -> bytes:
    """只读取 [offset, offset+frame_size) 的一帧数据，避免整读大文件（H-2/H-3）。

    - 文件不足一帧时抛与旧整读路径一致的 ``FormatError``（``data too short``）。
    - 读取失败（不存在/无权限等）直接抛 OSError，语义与原先的 open/read 一致。
    """
    try:
        file_size = os.path.getsize(input_path)
    except OSError:
        raise
    spec = ImageSpec(width, height, offset)
    spec.validate()
    need = offset + frame_size
    if file_size < need:
        raise FormatError(f"data too short: need {need} bytes, got {file_size}")
    with open(input_path, "rb") as f:
        f.seek(offset)
        data = f.read(frame_size)
    return data


def raw_file_to_image(
    input_path: str,
    output_path: str,
    raw_type: str,
    width: int,
    height: int,
    alignment: str = "msb",
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
    frame_size = expected_frame_size_raw(raw_type, width, height)
    # offset 已在 _read_frame 里通过 seek 消费；这里给 decode 用 0 偏移，
    # 避免 _slice_frame 对已切好的窗口二次偏移。
    data = _read_frame(input_path, width, height, frame_size, offset)
    spec = ImageSpec(width, height, 0)
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
    alignment: str = "msb",
    endianness: str = "little",
) -> int:
    """Decode a YUV file and save as PNG/JPEG."""
    logger.debug(
        "yuv_file_to_image: %s -> %s (%s, %dx%d, offset=%d)",
        input_path, output_path, subformat, width, height, offset,
    )
    frame_size = expected_frame_size_yuv(
        subformat, width, height, alignment=alignment, endianness=endianness
    )
    data = _read_frame(input_path, width, height, frame_size, offset)
    spec = ImageSpec(width, height, 0)  # offset 已在 _read_frame 里 seek 消费
    rgb = decode_yuv(data, spec, subformat, alignment=alignment, endianness=endianness)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    cv2.imwrite(output_path, bgr)
    size = os.path.getsize(output_path)
    logger.debug("yuv_file_to_image OK: %d bytes written", size)
    return size
