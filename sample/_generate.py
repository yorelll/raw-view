"""构造示例输入文件（用户不需要修改这个文件）。

每个示例脚本在运行时调用这里的函数，就地生成一个合法的 RAW/YUV/图片文件，
所以用户 ``python xxx.py`` 就能跑，不必自己准备素材。

为什么内容不是纯色
------------------
示例产出的图片要能被"看一眼就判断对不对"。纯色或全零解码出来是均匀一片，
**stride、位对齐、大小端、Bayer 排列**写错也看不出来。因此这里的生成器都带
可辨认的图案：

* 灰度的用"左暗右亮 + 上暗下亮"的双向渐变；
* 彩色 Bayer 的从一张彩条图打散成马赛克（解码后应还原出彩条）；
* YUV 的让 Y 渐变、U/V 分区（解码后应有颜色分区）。

各生成器返回写出的字节数，便于脚本核对。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = [
    "gray_ramp",
    "bayer_mosaic_from_color",
    "write_raw8",
    "write_raw10",
    "write_raw12",
    "write_raw16",
    "write_raw32",
    "write_raw10_packed",
    "write_raw12_packed",
    "write_raw14_packed",
    "write_yonly8",
    "write_yonly_multi_bit",
    # 4:2:2 全家族（四种打包 + 两种半平面），共用一个 write_yuv422
    "write_yuv422",
    "write_yuyv",
    "write_uyvy",
    "write_yvyu",
    "write_vyuy",
    "write_nv16",
    "write_nv61",
    # 4:2:0 全家族（两种三平面 + 两种半平面），共用一个 write_yuv420
    "write_yuv420",
    "write_i420",
    "write_yv12",
    "write_nv12",
    "write_nv21",
    "write_png",
    "write_multiframe_with_header",
]


# ── 图案 ──────────────────────────────────────────────────────────────


def gray_ramp(width: int, height: int) -> np.ndarray:
    """双向渐变灰度图（uint8，0..255）。

    横向贡献 70%、纵向 30%，这样既能看到左右渐变，也能看到上下变化——
    若图片被按错误 stride 解读，会出现明显的斜纹/错行。
    """
    x = np.linspace(0.0, 1.0, width, dtype=np.float64)[None, :]
    y = np.linspace(0.0, 1.0, height, dtype=np.float64)[:, None]
    return np.clip((x * 0.7 + y * 0.3) * 255.0, 0, 255).astype(np.uint8)


def bayer_mosaic_from_color(width: int, height: int, pattern: str = "RGGB") -> np.ndarray:
    """把一张彩条图按 *pattern* 打散成 Bayer 马赛克（uint8 单通道）。

    解码时用同一个 pattern 去马赛克，应还原出彩条；pattern 写错会看到
    红/蓝互换或整体偏色，正好演示 ``--bayer-pattern`` 的作用。
    """
    # 彩条：红 / 绿 / 蓝 / 白 / 渐变，覆盖去马赛克最容易出错的区域。
    image = np.zeros((height, width, 3), dtype=np.uint8)
    bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
    bar_width = max(1, width // len(bars))
    for index, (r, g, b) in enumerate(bars):
        left = index * bar_width
        right = width if index == len(bars) - 1 else left + bar_width
        image[:, left:right] = (r, g, b)

    red, green, blue = image[:, :, 0], image[:, :, 1], image[:, :, 2]
    mosaic = np.zeros((height, width), dtype=np.uint8)
    p = pattern.upper()
    even_row, odd_row = slice(0, None, 2), slice(1, None, 2)
    even_col, odd_col = slice(0, None, 2), slice(1, None, 2)
    if p == "RGGB":
        mosaic[even_row, even_col] = red[even_row, even_col]
        mosaic[even_row, odd_col] = green[even_row, odd_col]
        mosaic[odd_row, even_col] = green[odd_row, even_col]
        mosaic[odd_row, odd_col] = blue[odd_row, odd_col]
    elif p == "BGGR":
        mosaic[even_row, even_col] = blue[even_row, even_col]
        mosaic[even_row, odd_col] = green[even_row, odd_col]
        mosaic[odd_row, even_col] = green[odd_row, even_col]
        mosaic[odd_row, odd_col] = red[odd_row, odd_col]
    elif p == "GRBG":
        mosaic[even_row, even_col] = green[even_row, even_col]
        mosaic[even_row, odd_col] = red[even_row, odd_col]
        mosaic[odd_row, even_col] = blue[odd_row, even_col]
        mosaic[odd_row, odd_col] = green[odd_row, odd_col]
    elif p == "GBRG":
        mosaic[even_row, even_col] = green[even_row, even_col]
        mosaic[even_row, odd_col] = blue[even_row, odd_col]
        mosaic[odd_row, even_col] = red[odd_row, even_col]
        mosaic[odd_row, odd_col] = green[odd_row, odd_col]
    else:
        raise ValueError(f"unsupported bayer pattern: {pattern}")
    return mosaic


# ── RAW（未打包，存在 16/32 位容器里）────────────────────────────────


def write_raw8(path, width: int, height: int) -> int:
    """RAW8：1 字节/像素，原生 8 bit。"""
    data = gray_ramp(width, height).tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw10(path, width: int, height: int, *, alignment: str = "msb",
                endianness: str = "little") -> int:
    """RAW10：10 bit 有效位存在 16-bit 字里。

    ``alignment="msb"`` 把值左移到高 10 位（value << 6）；``"lsb"`` 直接放低
    10 位。示例里两种都演示，因为相机输出两种都常见。
    """
    ramp = gray_ramp(width, height).astype(np.uint16)
    values = (ramp.astype(np.uint32) * 1023 // 255).astype(np.uint16)
    if alignment == "msb":
        values = (values << 6).astype(np.uint16)
    dtype = "<u2" if endianness == "little" else ">u2"
    data = values.astype(dtype).tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw12(path, width: int, height: int, *, alignment: str = "msb",
                endianness: str = "little") -> int:
    """RAW12：12 bit 有效位存在 16-bit 字里（msb 时 value << 4）。"""
    ramp = gray_ramp(width, height).astype(np.uint16)
    values = (ramp.astype(np.uint32) * 4095 // 255).astype(np.uint16)
    if alignment == "msb":
        values = (values << 4).astype(np.uint16)
    dtype = "<u2" if endianness == "little" else ">u2"
    data = values.astype(dtype).tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw16(path, width: int, height: int, *, endianness: str = "little") -> int:
    """RAW16：16 bit 满量程，1 像素 2 字节。"""
    ramp = gray_ramp(width, height).astype(np.uint16)
    values = (ramp.astype(np.uint32) * 65535 // 255).astype(np.uint16)
    dtype = "<u2" if endianness == "little" else ">u2"
    data = values.astype(dtype).tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw32(path, width: int, height: int, *, endianness: str = "little") -> int:
    """RAW32：32 bit 字，1 像素 4 字节（部分 ToF/HDR 传感器用）。"""
    ramp = gray_ramp(width, height).astype(np.uint32)
    values = (ramp.astype(np.uint64) * 0xFFFFFFFF // 255).astype(np.uint32)
    dtype = "<u4" if endianness == "little" else ">u4"
    data = values.astype(dtype).tobytes()
    Path(path).write_bytes(data)
    return len(data)


# ── RAW（MIPI Packed）────────────────────────────────────────────────


def write_raw10_packed(path, width: int, height: int) -> int:
    """RAW10 Packed：MIPI CSI-2，4 像素压进 5 字节。要求宽 % 4 == 0。"""
    if width % 4:
        raise ValueError("RAW10 Packed 要求宽度是 4 的倍数")
    ramp = gray_ramp(width, height)
    values = (ramp.astype(np.uint32) * 1023 // 255).astype(np.uint16).reshape(-1)
    p = values.reshape(-1, 4)
    out = np.empty((p.shape[0], 5), dtype=np.uint8)
    out[:, 0] = (p[:, 0] >> 2) & 0xFF
    out[:, 1] = (p[:, 1] >> 2) & 0xFF
    out[:, 2] = (p[:, 2] >> 2) & 0xFF
    out[:, 3] = (p[:, 3] >> 2) & 0xFF
    out[:, 4] = (
        ((p[:, 0] & 0x03) << 6) | ((p[:, 1] & 0x03) << 4)
        | ((p[:, 2] & 0x03) << 2) | (p[:, 3] & 0x03)
    ).astype(np.uint8)
    data = out.tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw12_packed(path, width: int, height: int) -> int:
    """RAW12 Packed：MIPI CSI-2，2 像素压进 3 字节。要求宽 % 2 == 0。"""
    if width % 2:
        raise ValueError("RAW12 Packed 要求宽度是 2 的倍数")
    ramp = gray_ramp(width, height)
    values = (ramp.astype(np.uint32) * 4095 // 255).astype(np.uint16).reshape(-1)
    p = values.reshape(-1, 2)
    out = np.empty((p.shape[0], 3), dtype=np.uint8)
    out[:, 0] = (p[:, 0] >> 4) & 0xFF
    out[:, 1] = (p[:, 1] >> 4) & 0xFF
    out[:, 2] = (((p[:, 0] & 0x0F) << 4) | (p[:, 1] & 0x0F)).astype(np.uint8)
    data = out.tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_raw14_packed(path, width: int, height: int) -> int:
    """RAW14 Packed：MIPI CSI-2，4 像素压进 7 字节。要求宽 % 4 == 0。"""
    if width % 4:
        raise ValueError("RAW14 Packed 要求宽度是 4 的倍数")
    ramp = gray_ramp(width, height)
    values = (ramp.astype(np.uint32) * 16383 // 255).astype(np.uint16).reshape(-1)
    p = values.reshape(-1, 4)
    out = np.empty((p.shape[0], 7), dtype=np.uint8)
    out[:, 0] = (p[:, 0] >> 6) & 0xFF
    out[:, 1] = (p[:, 1] >> 6) & 0xFF
    out[:, 2] = (p[:, 2] >> 6) & 0xFF
    out[:, 3] = (p[:, 3] >> 6) & 0xFF
    out[:, 4] = (((p[:, 0] & 0x3F) << 2) | ((p[:, 1] >> 4) & 0x03)).astype(np.uint8)
    out[:, 5] = (((p[:, 1] & 0x0F) << 4) | ((p[:, 2] >> 2) & 0x0F)).astype(np.uint8)
    out[:, 6] = (((p[:, 2] & 0x03) << 6) | (p[:, 3] & 0x3F)).astype(np.uint8)
    data = out.tobytes()
    Path(path).write_bytes(data)
    return len(data)


# ── YUV 4:0:0（YOnly）────────────────────────────────────────────────


def write_yonly8(path, width: int, height: int) -> int:
    """YOnly8：1 字节/像素，无对齐/端序概念。"""
    data = gray_ramp(width, height).tobytes()
    Path(path).write_bytes(data)
    return len(data)


def write_yonly_multi_bit(path, width: int, height: int, bits: int, *,
                          alignment: str = "msb", endianness: str = "little") -> int:
    """YOnly10/12/14/16：N bit 有效位存在 16-bit 字里，2 字节/像素。"""
    if bits <= 8:
        raise ValueError("多 bit YOnly 的 bits 必须 > 8")
    ramp = gray_ramp(width, height).astype(np.uint32)
    values = (ramp * ((1 << bits) - 1) // 255).astype(np.uint16)
    if alignment == "msb":
        values = (values << (16 - bits)).astype(np.uint16)
    dtype = "<u2" if endianness == "little" else ">u2"
    data = values.astype(dtype).tobytes()
    Path(path).write_bytes(data)
    return len(data)


# ── YUV 4:2:2 / 4:2:0 ────────────────────────────────────────────────


def _yuv_planes(width: int, height: int):
    """构造带渐变 Y 和分区 U/V 的采样平面。

    U/V 做成左右两半不同值：解码后画面左右应有可辨的颜色差异，能直观看出
    色度平面是否被正确解释（色度平面错位会出现"颜色错行"）。
    """
    y = gray_ramp(width, height)
    u = np.full((height, width // 2), 90, dtype=np.uint8)
    v = np.full((height, width // 2), 160, dtype=np.uint8)
    u[:, width // 4:] = 160
    v[:, width // 4:] = 90
    return y, u, v


#: 4:2:2 打包格式的每宏像素字节角色（与 raw_view.transcode.ROLE_ORDER_422 同义）。
#: 有了这张表，四种打包顺序共用一个写入函数，而不是四份近乎相同的代码。
_ROLE_ORDER_422 = {
    "YUYV": ("Y0", "U", "Y1", "V"),
    "UYVY": ("U", "Y0", "V", "Y1"),
    "YVYU": ("Y0", "V", "Y1", "U"),
    "VYUY": ("V", "Y0", "U", "Y1"),
}

#: 4:2:2 半平面格式：Y 平面 + 交错色度平面，两种只是 UV/VU 顺序不同。
_SEMIPLANAR_422 = ("NV16", "NV61")


def write_yuv422(path, width: int, height: int, yuv_type: str = "YUYV") -> int:
    """写任意一种 4:2:2 布局（四种打包 + 两种半平面）。要求宽为偶数。

    ``yuv_type`` 取 YUYV / UYVY / YVYU / VYUY / NV16 / NV61。
    """
    if width % 2:
        raise ValueError(f"{yuv_type} 要求宽度为偶数")
    y, u, v = _yuv_planes(width, height)

    if yuv_type in _SEMIPLANAR_422:
        chroma = np.empty((height, width), dtype=np.uint8)
        first, second = (u, v) if yuv_type == "NV16" else (v, u)
        chroma[:, 0::2] = first
        chroma[:, 1::2] = second
        data = y.tobytes() + chroma.tobytes()
    else:
        order = _ROLE_ORDER_422.get(yuv_type)
        if order is None:
            raise ValueError(f"不支持的 4:2:2 格式：{yuv_type}")
        roles = {"Y0": y[:, 0::2], "Y1": y[:, 1::2], "U": u, "V": v}
        out = np.empty((height, width // 2, 4), dtype=np.uint8)
        for index, role in enumerate(order):
            out[:, :, index] = roles[role]
        data = out.tobytes()

    Path(path).write_bytes(data)
    return len(data)


def write_yuyv(path, width: int, height: int) -> int:
    """YUYV（4:2:2 打包）：每 4 字节 Y0 U Y1 V。要求宽为偶数。"""
    return write_yuv422(path, width, height, "YUYV")


def write_uyvy(path, width: int, height: int) -> int:
    """UYVY（4:2:2 打包）：每 4 字节 U Y0 V Y1。要求宽为偶数。"""
    return write_yuv422(path, width, height, "UYVY")


def write_yvyu(path, width: int, height: int) -> int:
    """YVYU（4:2:2 打包）：每 4 字节 Y0 V Y1 U。要求宽为偶数。"""
    return write_yuv422(path, width, height, "YVYU")


def write_vyuy(path, width: int, height: int) -> int:
    """VYUY（4:2:2 打包）：每 4 字节 V Y0 U Y1。要求宽为偶数。"""
    return write_yuv422(path, width, height, "VYUY")


def write_nv16(path, width: int, height: int) -> int:
    """NV16（4:2:2 半平面）：Y 平面 + 交错 UV 平面。要求宽为偶数。"""
    return write_yuv422(path, width, height, "NV16")


def write_nv61(path, width: int, height: int) -> int:
    """NV61（4:2:2 半平面）：Y 平面 + 交错 VU 平面。要求宽为偶数。"""
    return write_yuv422(path, width, height, "NV61")


#: 4:2:0 三平面格式：色度平面的先后顺序（I420 是 U 先、YV12 是 V 先）。
_PLANAR_420_ORDER = {"I420": ("u", "v"), "YV12": ("v", "u")}

#: 4:2:0 半平面格式：交错平面里先出现的那个分量。
_SEMIPLANAR_420_FIRST = {"NV12": "u", "NV21": "v"}


def _chroma_420(width: int, height: int):
    """4:2:0 的两个色度平面（h/2 x w/2），左右两半不同值便于肉眼验证。"""
    u = np.full((height // 2, width // 2), 90, dtype=np.uint8)
    v = np.full((height // 2, width // 2), 160, dtype=np.uint8)
    u[:, width // 4:] = 160
    v[:, width // 4:] = 90
    return u, v


def write_yuv420(path, width: int, height: int, yuv_type: str = "I420") -> int:
    """写任意一种 4:2:0 布局（三平面 I420/YV12 + 半平面 NV12/NV21）。

    要求宽高均为偶数。
    """
    if width % 2 or height % 2:
        raise ValueError(f"{yuv_type} 要求宽高均为偶数")
    y = gray_ramp(width, height)
    u, v = _chroma_420(width, height)
    planes = {"u": u, "v": v}

    if yuv_type in _PLANAR_420_ORDER:
        first, second = _PLANAR_420_ORDER[yuv_type]
        data = y.tobytes() + planes[first].tobytes() + planes[second].tobytes()
    elif yuv_type in _SEMIPLANAR_420_FIRST:
        first = _SEMIPLANAR_420_FIRST[yuv_type]
        second = "v" if first == "u" else "u"
        chroma = np.empty((height // 2, width), dtype=np.uint8)
        chroma[:, 0::2] = planes[first]
        chroma[:, 1::2] = planes[second]
        data = y.tobytes() + chroma.tobytes()
    else:
        raise ValueError(f"不支持的 4:2:0 格式：{yuv_type}")

    Path(path).write_bytes(data)
    return len(data)


def write_i420(path, width: int, height: int) -> int:
    """I420（4:2:0 三平面）：Y + U + V，色度为 h/2 x w/2。要求宽高均为偶数。"""
    return write_yuv420(path, width, height, "I420")


def write_yv12(path, width: int, height: int) -> int:
    """YV12（4:2:0 三平面）：Y + V + U —— 与 I420 只差 U/V 平面顺序。"""
    return write_yuv420(path, width, height, "YV12")


def write_nv12(path, width: int, height: int) -> int:
    """NV12（4:2:0 半平面）：Y 平面 + 交错 UV 平面。要求宽高均为偶数。"""
    return write_yuv420(path, width, height, "NV12")


def write_nv21(path, width: int, height: int) -> int:
    """NV21（4:2:0 半平面）：Y 平面 + 交错 VU 平面。要求宽高均为偶数。"""
    return write_yuv420(path, width, height, "NV21")


# ── 图片 / 多帧 ──────────────────────────────────────────────────────


def write_png(path, width: int, height: int) -> int:
    """写一张 BGR 彩条 PNG，作为 image -> RAW/YUV 的输入。"""
    import cv2

    image = np.zeros((height, width, 3), dtype=np.uint8)
    bars = [
        (255, 255, 255), (0, 255, 255), (0, 255, 0),
        (255, 255, 0), (0, 0, 255), (255, 0, 0), (0, 0, 0),
    ]
    bar_width = max(1, width // len(bars))
    for index, colour in enumerate(bars):
        left = index * bar_width
        right = width if index == len(bars) - 1 else left + bar_width
        image[:, left:right] = colour
    ramp = np.linspace(0.4, 1.0, width, dtype=np.float32)[None, :, None]
    image = np.clip(image.astype(np.float32) * ramp, 0, 255).astype(np.uint8)
    # cv2.imwrite 对非 ASCII 路径不可靠，与项目内做法一致：imencode + tofile。
    ok, encoded = cv2.imencode(".png", image)
    if not ok:  # pragma: no cover - 编码失败属异常
        raise OSError(f"failed to encode PNG: {path}")
    encoded.tofile(str(path))
    return Path(path).stat().st_size


def write_multiframe_with_header(
    path, width: int, height: int, frames: int = 3, header_bytes: int = 128
) -> int:
    """写一个"带文件头 + 多帧"的 YOnly10 文件，用于演示 ``--offset``。

    真实设备常在帧数据前放一段头（时间戳、曝光参数……），CLI 用
    ``--offset`` 跳过它。每帧内容是不同的亮度，便于确认取到的是哪一帧。
    """
    header = np.zeros(header_bytes, dtype=np.uint8)
    payload = bytearray()
    for index in range(frames):
        # 每帧整体亮度不同：第 1 帧最暗，最后一帧最亮。
        scale = (index + 1) / frames
        ramp = (gray_ramp(width, height).astype(np.float32) * scale)
        values = (ramp.astype(np.uint32) * 1023 // 255).astype(np.uint16)
        payload += (values << 6).astype("<u2").tobytes()
    data = header.tobytes() + bytes(payload)
    Path(path).write_bytes(data)
    return len(data)
