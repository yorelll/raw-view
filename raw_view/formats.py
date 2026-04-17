"""RAW/YUV parsing and conversion utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

Alignment = Literal["lsb", "msb"]
Endianness = Literal["little", "big"]


class FormatError(ValueError):
    """Raised when format parameters or data size are invalid."""


@dataclass(frozen=True)
class ImageSpec:
    width: int
    height: int
    offset: int = 0

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise FormatError("width and height must be > 0")
        if self.offset < 0:
            raise FormatError("offset must be >= 0")


RAW_BITS = {
    "RAW8": 8,
    "RAW10": 10,
    "RAW12": 12,
    "RAW16": 16,
    "RAW32": 32,
    "RAW10 Packed": 10,
    "RAW12 Packed": 12,
    "RAW14 Packed": 14,
}


YUV_BYTES_PER_PIXEL = {
    "I420": 1.5,
    "YV12": 1.5,
    "NV12": 1.5,
    "NV21": 1.5,
    "YUYV": 2.0,
    "UYVY": 2.0,
    "NV16": 2.0,
}


def _dtype_u16(endianness: Endianness) -> np.dtype:
    return np.dtype("<u2") if endianness == "little" else np.dtype(">u2")


def _dtype_u32(endianness: Endianness) -> np.dtype:
    return np.dtype("<u4") if endianness == "little" else np.dtype(">u4")


def expected_frame_size_raw(raw_type: str, width: int, height: int) -> int:
    pixels = width * height
    if raw_type in {"RAW8"}:
        return pixels
    if raw_type in {"RAW10", "RAW12", "RAW16"}:
        return pixels * 2
    if raw_type == "RAW32":
        return pixels * 4
    if raw_type == "RAW10 Packed":
        if width % 4 != 0:
            raise FormatError("RAW10 Packed requires width divisible by 4")
        return pixels * 5 // 4
    if raw_type == "RAW12 Packed":
        if width % 2 != 0:
            raise FormatError("RAW12 Packed requires width divisible by 2")
        return pixels * 3 // 2
    if raw_type == "RAW14 Packed":
        if width % 4 != 0:
            raise FormatError("RAW14 Packed requires width divisible by 4")
        return pixels * 7 // 4
    raise FormatError(f"unsupported RAW type: {raw_type}")


def expected_frame_size_yuv(subformat: str, width: int, height: int) -> int:
    if subformat not in YUV_BYTES_PER_PIXEL:
        raise FormatError(f"unsupported YUV subformat: {subformat}")
    if subformat in {"I420", "YV12", "NV12", "NV21"} and (width % 2 or height % 2):
        raise FormatError(f"{subformat} requires even width/height")
    if subformat in {"YUYV", "UYVY", "NV16"} and (width % 2):
        raise FormatError(f"{subformat} requires even width")
    return int(width * height * YUV_BYTES_PER_PIXEL[subformat])


def _slice_frame(data: bytes, spec: ImageSpec, frame_size: int) -> bytes:
    spec.validate()
    start = spec.offset
    end = start + frame_size
    if len(data) < end:
        raise FormatError(f"data too short: need {end} bytes, got {len(data)}")
    return data[start:end]


def decode_raw(
    data: bytes,
    spec: ImageSpec,
    raw_type: str,
    alignment: Alignment = "lsb",
    endianness: Endianness = "little",
) -> np.ndarray:
    frame_size = expected_frame_size_raw(raw_type, spec.width, spec.height)
    frame = _slice_frame(data, spec, frame_size)
    pixels = spec.width * spec.height

    if raw_type == "RAW8":
        arr = np.frombuffer(frame, dtype=np.uint8)
        return arr.reshape(spec.height, spec.width).astype(np.uint16)

    if raw_type in {"RAW10", "RAW12", "RAW16"}:
        arr16 = np.frombuffer(frame, dtype=_dtype_u16(endianness)).astype(np.uint16)
        if raw_type == "RAW10":
            arr16 = (arr16 & 0x03FF) if alignment == "lsb" else (arr16 >> 6)
        elif raw_type == "RAW12":
            arr16 = (arr16 & 0x0FFF) if alignment == "lsb" else (arr16 >> 4)
        return arr16.reshape(spec.height, spec.width)

    if raw_type == "RAW32":
        arr = np.frombuffer(frame, dtype=_dtype_u32(endianness)).astype(np.uint32)
        return arr.reshape(spec.height, spec.width)

    if raw_type == "RAW10 Packed":
        b = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 5).astype(np.uint16)
        out = np.empty(b.shape[0] * 4, dtype=np.uint16)
        out[0::4] = b[:, 0] | ((b[:, 4] & 0x03) << 8)
        out[1::4] = b[:, 1] | (((b[:, 4] >> 2) & 0x03) << 8)
        out[2::4] = b[:, 2] | (((b[:, 4] >> 4) & 0x03) << 8)
        out[3::4] = b[:, 3] | (((b[:, 4] >> 6) & 0x03) << 8)
        if alignment == "msb":
            out = out << 6
        return out[:pixels].reshape(spec.height, spec.width)

    if raw_type == "RAW12 Packed":
        b = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 3).astype(np.uint16)
        out = np.empty(b.shape[0] * 2, dtype=np.uint16)
        out[0::2] = b[:, 0] | ((b[:, 1] & 0x0F) << 8)
        out[1::2] = (b[:, 2] << 4) | ((b[:, 1] >> 4) & 0x0F)
        if alignment == "msb":
            out = out << 4
        return out[:pixels].reshape(spec.height, spec.width)

    if raw_type == "RAW14 Packed":
        b = np.frombuffer(frame, dtype=np.uint8).reshape(-1, 7).astype(np.uint16)
        out = np.empty(b.shape[0] * 4, dtype=np.uint16)
        out[0::4] = b[:, 0] | ((b[:, 4] & 0x3F) << 8)
        out[1::4] = b[:, 1] | (((b[:, 4] >> 6) & 0x03) << 8) | (((b[:, 5] >> 4) & 0x0F) << 10)
        # P2: low 8 bits from B2, then bits[9:8] from B6[7:6], then bits[13:10] from B5[3:0].
        out[2::4] = b[:, 2] | (((b[:, 6] >> 6) & 0x03) << 8) | ((b[:, 5] & 0x0F) << 10)
        # P3: low 8 bits from B3 and high 6 bits from B6[5:0] => 14-bit value.
        out[3::4] = b[:, 3] | ((b[:, 6] & 0x3F) << 8)
        if alignment == "msb":
            out = out << 2
        return out[:pixels].reshape(spec.height, spec.width)

    raise FormatError(f"unsupported RAW type: {raw_type}")


def _to_8bit(values: np.ndarray, bits: int | None = None) -> np.ndarray:
    v = values.astype(np.float32)
    if bits is not None and bits > 0:
        vmax = float((1 << bits) - 1)
        return np.clip(np.round(v / vmax * 255.0), 0, 255).astype(np.uint8)
    vmin, vmax = float(v.min()), float(v.max())
    if vmax <= vmin:
        return np.zeros(v.shape, dtype=np.uint8)
    return np.clip(np.round((v - vmin) * (255.0 / (vmax - vmin))), 0, 255).astype(np.uint8)


def raw_to_display_gray(raw_values: np.ndarray, raw_type: str) -> np.ndarray:
    bits = RAW_BITS.get(raw_type)
    return _to_8bit(raw_values, bits=bits if bits and bits <= 16 else None)


def _yuv_to_rgb(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    c = y.astype(np.float32)
    d = u.astype(np.float32) - 128.0
    e = v.astype(np.float32) - 128.0
    r = c + 1.402 * e
    g = c - 0.344136 * d - 0.714136 * e
    b = c + 1.772 * d
    rgb = np.stack([r, g, b], axis=-1)
    return np.clip(np.round(rgb), 0, 255).astype(np.uint8)


def decode_yuv(data: bytes, spec: ImageSpec, subformat: str) -> np.ndarray:
    frame_size = expected_frame_size_yuv(subformat, spec.width, spec.height)
    frame = _slice_frame(data, spec, frame_size)
    w, h = spec.width, spec.height
    arr = np.frombuffer(frame, dtype=np.uint8)

    if subformat in {"I420", "YV12", "NV12", "NV21"}:
        y_size = w * h
        uv_size = y_size // 4
        y = arr[:y_size].reshape(h, w)
        if subformat == "I420":
            u = arr[y_size:y_size + uv_size].reshape(h // 2, w // 2)
            v = arr[y_size + uv_size:].reshape(h // 2, w // 2)
        elif subformat == "YV12":
            v = arr[y_size:y_size + uv_size].reshape(h // 2, w // 2)
            u = arr[y_size + uv_size:].reshape(h // 2, w // 2)
        elif subformat == "NV12":
            uv = arr[y_size:].reshape(h // 2, w)
            u, v = uv[:, 0::2], uv[:, 1::2]
        else:  # NV21
            vu = arr[y_size:].reshape(h // 2, w)
            v, u = vu[:, 0::2], vu[:, 1::2]
        u_up = u.repeat(2, axis=0).repeat(2, axis=1)
        v_up = v.repeat(2, axis=0).repeat(2, axis=1)
        return _yuv_to_rgb(y, u_up, v_up)

    if subformat in {"YUYV", "UYVY"}:
        p = arr.reshape(h, w // 2, 4)
        if subformat == "YUYV":
            y0, u, y1, v = p[:, :, 0], p[:, :, 1], p[:, :, 2], p[:, :, 3]
        else:
            u, y0, v, y1 = p[:, :, 0], p[:, :, 1], p[:, :, 2], p[:, :, 3]
        y = np.empty((h, w), dtype=np.uint8)
        y[:, 0::2] = y0
        y[:, 1::2] = y1
        u_up = np.repeat(u, 2, axis=1)
        v_up = np.repeat(v, 2, axis=1)
        return _yuv_to_rgb(y, u_up, v_up)

    if subformat == "NV16":
        y_size = w * h
        y = arr[:y_size].reshape(h, w)
        uv = arr[y_size:].reshape(h, w)
        u = uv[:, 0::2]
        v = uv[:, 1::2]
        u_up = np.repeat(u, 2, axis=1)
        v_up = np.repeat(v, 2, axis=1)
        return _yuv_to_rgb(y, u_up, v_up)

    raise FormatError(f"unsupported YUV subformat: {subformat}")


def _pack_raw10(values_10: np.ndarray) -> bytes:
    v = values_10.astype(np.uint16).reshape(-1)
    if len(v) % 4 != 0:
        raise FormatError("RAW10 Packed requires total pixels divisible by 4")
    p = v.reshape(-1, 4)
    b0 = (p[:, 0] & 0xFF).astype(np.uint8)
    b1 = (p[:, 1] & 0xFF).astype(np.uint8)
    b2 = (p[:, 2] & 0xFF).astype(np.uint8)
    b3 = (p[:, 3] & 0xFF).astype(np.uint8)
    b4 = (
        ((p[:, 0] >> 8) & 0x03)
        | (((p[:, 1] >> 8) & 0x03) << 2)
        | (((p[:, 2] >> 8) & 0x03) << 4)
        | (((p[:, 3] >> 8) & 0x03) << 6)
    ).astype(np.uint8)
    out = np.stack([b0, b1, b2, b3, b4], axis=1)
    return out.tobytes()


def _pack_raw12(values_12: np.ndarray) -> bytes:
    v = values_12.astype(np.uint16).reshape(-1)
    if len(v) % 2 != 0:
        raise FormatError("RAW12 Packed requires total pixels divisible by 2")
    p = v.reshape(-1, 2)
    b0 = (p[:, 0] & 0xFF).astype(np.uint8)
    b1 = (((p[:, 0] >> 8) & 0x0F) | ((p[:, 1] & 0x0F) << 4)).astype(np.uint8)
    b2 = ((p[:, 1] >> 4) & 0xFF).astype(np.uint8)
    return np.stack([b0, b1, b2], axis=1).tobytes()


def _pack_raw14(values_14: np.ndarray) -> bytes:
    v = values_14.astype(np.uint16).reshape(-1)
    if len(v) % 4 != 0:
        raise FormatError("RAW14 Packed requires total pixels divisible by 4")
    p = v.reshape(-1, 4)
    b0 = (p[:, 0] & 0xFF).astype(np.uint8)
    b1 = (p[:, 1] & 0xFF).astype(np.uint8)
    b2 = (p[:, 2] & 0xFF).astype(np.uint8)
    b3 = (p[:, 3] & 0xFF).astype(np.uint8)
    b4 = (((p[:, 0] >> 8) & 0x3F) | (((p[:, 1] >> 8) & 0x03) << 6)).astype(np.uint8)
    b5 = ((((p[:, 1] >> 10) & 0x0F) << 4) | ((p[:, 2] >> 10) & 0x0F)).astype(np.uint8)
    b6 = ((((p[:, 2] >> 8) & 0x03) << 6) | ((p[:, 3] >> 8) & 0x3F)).astype(np.uint8)
    return np.stack([b0, b1, b2, b3, b4, b5, b6], axis=1).tobytes()


def gray8_to_raw_bytes(
    gray: np.ndarray,
    raw_type: str,
    alignment: Alignment = "lsb",
    endianness: Endianness = "little",
) -> bytes:
    g = np.clip(gray.astype(np.float32), 0, 255)
    if raw_type == "RAW8":
        return g.astype(np.uint8).tobytes()

    bits = RAW_BITS.get(raw_type)
    if bits is None:
        raise FormatError(f"unsupported RAW type: {raw_type}")

    v = np.clip(np.round(g / 255.0 * ((1 << bits) - 1)), 0, (1 << bits) - 1).astype(np.uint16)

    if raw_type in {"RAW10", "RAW12", "RAW16"}:
        if alignment == "msb" and bits < 16:
            v = v << (16 - bits)
        dtype = _dtype_u16(endianness)
        return v.astype(dtype).tobytes()

    if raw_type == "RAW10 Packed":
        return _pack_raw10(v)
    if raw_type == "RAW12 Packed":
        return _pack_raw12(v)
    if raw_type == "RAW14 Packed":
        return _pack_raw14(v)

    if raw_type == "RAW32":
        dtype = _dtype_u32(endianness)
        return v.astype(dtype).tobytes()

    raise FormatError(f"unsupported RAW type: {raw_type}")


def rgb_to_yuv_bytes(rgb: np.ndarray, subformat: str) -> bytes:
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    y = np.clip(np.round(0.299 * r + 0.587 * g + 0.114 * b), 0, 255).astype(np.uint8)
    u = np.clip(np.round(-0.169 * r - 0.331 * g + 0.5 * b + 128.0), 0, 255).astype(np.uint8)
    v = np.clip(np.round(0.5 * r - 0.419 * g - 0.081 * b + 128.0), 0, 255).astype(np.uint8)

    h, w = y.shape

    if subformat in {"I420", "YV12", "NV12", "NV21"}:
        if w % 2 or h % 2:
            raise FormatError(f"{subformat} requires even width/height")
        u_ds = u.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).round().astype(np.uint8)
        v_ds = v.reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3)).round().astype(np.uint8)
        if subformat == "I420":
            return b"".join([y.tobytes(), u_ds.tobytes(), v_ds.tobytes()])
        if subformat == "YV12":
            return b"".join([y.tobytes(), v_ds.tobytes(), u_ds.tobytes()])
        inter = np.empty((h // 2, w), dtype=np.uint8)
        if subformat == "NV12":
            inter[:, 0::2], inter[:, 1::2] = u_ds, v_ds
        else:
            inter[:, 0::2], inter[:, 1::2] = v_ds, u_ds
        return b"".join([y.tobytes(), inter.tobytes()])

    if subformat in {"YUYV", "UYVY", "NV16"}:
        if w % 2:
            raise FormatError(f"{subformat} requires even width")
        u_ds = u.reshape(h, w // 2, 2).mean(axis=2).round().astype(np.uint8)
        v_ds = v.reshape(h, w // 2, 2).mean(axis=2).round().astype(np.uint8)
        if subformat == "NV16":
            uv = np.empty((h, w), dtype=np.uint8)
            uv[:, 0::2], uv[:, 1::2] = u_ds, v_ds
            return b"".join([y.tobytes(), uv.tobytes()])
        out = np.empty((h, w // 2, 4), dtype=np.uint8)
        if subformat == "YUYV":
            out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = y[:, 0::2], u_ds, y[:, 1::2], v_ds
        else:
            out[:, :, 0], out[:, :, 1], out[:, :, 2], out[:, :, 3] = u_ds, y[:, 0::2], v_ds, y[:, 1::2]
        return out.tobytes()

    raise FormatError(f"unsupported YUV subformat: {subformat}")
