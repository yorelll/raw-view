"""raw-view: RAW/YUV image viewer and format converter."""

from .formats import (
    FormatError,
    ImageSpec,
    decode_raw,
    decode_yuv,
    expected_frame_size_raw,
    expected_frame_size_yuv,
    gray8_to_raw_bytes,
    rgb_to_yuv_bytes,
)

from . import models
from . import gui

# Initialise the root logger on first import
from .logger import setup_logger

setup_logger()

__all__ = [
    "FormatError",
    "ImageSpec",
    "decode_raw",
    "decode_yuv",
    "expected_frame_size_raw",
    "expected_frame_size_yuv",
    "gray8_to_raw_bytes",
    "rgb_to_yuv_bytes",
    "models",
    "gui",
]
