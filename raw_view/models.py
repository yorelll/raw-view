"""Data models, settings persistence, constants, and helper utilities."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PyQt5.QtCore import QSettings


# ── Constants ────────────────────────────────────────────────────────────

BAYER_PATTERNS = ["RGGB", "GRBG", "GBRG", "BGGR"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}
MAX_RECENT_FILES = 10
UI_THEMES = {"light", "dark"}

ACTION_ICON_COLOR = "#3B82F6"
ACTION_ICON_DISABLED_COLOR = "#64748B"
ACTION_ICON_NAMES = {
    "open": "fa5s.folder-open",
    "save": "fa5s.save",
    "convert": "fa5s.exchange-alt",
    "settings": "fa5s.cog",
    "help": "fa5s.question-circle",
}

THEME_PALETTES = {
    "light": {
        "main_bg": "#F1F5F9",
        "text_color": "#1E293B",
        "text_secondary": "#64748B",
        "panel_bg": "#FFFFFF",
        "border_color": "#E2E8F0",
        "input_bg": "#FFFFFF",
        "button_bg": "#2563EB",
        "button_hover_bg": "#1D4ED8",
        "button_text_color": "#FFFFFF",
        "accent": "#3B82F6",
        "accent_light": "#DBEAFE",
        "card_shadow": "rgba(0,0,0,0.06)",
        "success": "#10B981",
        "warning": "#F59E0B",
    },
    "dark": {
        "main_bg": "#0F172A",
        "text_color": "#E2E8F0",
        "text_secondary": "#94A3B8",
        "panel_bg": "#1E293B",
        "border_color": "#334155",
        "input_bg": "#1F2937",
        "button_bg": "#3B82F6",
        "button_hover_bg": "#2563EB",
        "button_text_color": "#FFFFFF",
        "accent": "#60A5FA",
        "accent_light": "#1E3A5F",
        "card_shadow": "rgba(0,0,0,0.25)",
        "success": "#34D399",
        "warning": "#FBBF24",
    },
}


# ── Data models ─────────────────────────────────────────────────────────


@dataclass
class DecodeOptions:
    """Serialisable parameter set for one decode operation."""

    file_path: str = ""
    image_type: str = "RAW"
    format_name: str = "RAW12"
    width: int = 2560
    height: int = 1440
    alignment: str = "msb"
    endianness: str = "little"
    offset: int = 0


@dataclass
class ViewerItem:
    """State container for one opened file tab and its decode/view configuration."""

    options: DecodeOptions = field(default_factory=DecodeOptions)
    current_display: object | None = None
    view: object | None = None
    frame_nav: object | None = None  # FrameNavBar widget
    zoom_percent: int = 100
    current_frame: int = 0
    total_frames: int = 0
    rotation_angle: int = 0  # cumulative rotation in degrees


# ── Settings ─────────────────────────────────────────────────────────────


class AppSettings:
    """Persistence wrapper around QSettings."""

    def __init__(self) -> None:
        self._store = QSettings("yorelll", "raw-view")

    @staticmethod
    def _normalize_dirname(value: str | None) -> str:
        return (value or "out").strip() or "out"

    @property
    def default_output_dirname(self) -> str:
        return self._normalize_dirname(self._store.value("convert/default_output_dirname", "convert_out"))

    @default_output_dirname.setter
    def default_output_dirname(self, value: str) -> None:
        self._store.setValue("convert/default_output_dirname", self._normalize_dirname(value))

    @property
    def output_template(self) -> str:
        return str(self._store.value("convert/output_template", DEFAULT_OUTPUT_TEMPLATE))

    @output_template.setter
    def output_template(self, value: str) -> None:
        self._store.setValue("convert/output_template", value.strip() or DEFAULT_OUTPUT_TEMPLATE)

    @property
    def save_dpi(self) -> int:
        value = self._store.value("save/dpi", 300)
        try:
            return max(72, min(2400, int(value)))
        except (TypeError, ValueError):
            return 300

    @save_dpi.setter
    def save_dpi(self, value: int) -> None:
        self._store.setValue("save/dpi", max(72, min(2400, int(value))))

    @property
    def ui_font_size(self) -> int:
        value = self._store.value("ui/font_size", 13)
        try:
            return max(10, min(24, int(value)))
        except (TypeError, ValueError):
            return 13

    @ui_font_size.setter
    def ui_font_size(self, value: int) -> None:
        self._store.setValue("ui/font_size", max(10, min(24, int(value))))

    @property
    def ui_theme(self) -> str:
        return normalize_ui_theme(self._store.value("ui/theme", "light"))

    @ui_theme.setter
    def ui_theme(self, value: str) -> None:
        self._store.setValue("ui/theme", normalize_ui_theme(value))

    @property
    def recent_files(self) -> list[str]:
        return normalize_recent_files(self._store.value("recent/files", []), MAX_RECENT_FILES)

    def add_recent_file(self, path: str) -> None:
        self._store.setValue(
            "recent/files",
            add_recent_file_entry(self.recent_files, path, MAX_RECENT_FILES),
        )

    def clear_recent_files(self) -> None:
        self._store.setValue("recent/files", [])


# ── Helper functions ─────────────────────────────────────────────────────


def normalize_recent_files(value: object, max_items: int = MAX_RECENT_FILES) -> list[str]:
    """Normalize recent-file values by trimming, deduplicating, and enforcing max length."""
    if value is None:
        return []
    if isinstance(value, str):
        files = [value]
    elif isinstance(value, (list, tuple)):
        files = [str(path) for path in value if str(path).strip()]
    else:
        files = []
    normalized: list[str] = []
    for path in files:
        trimmed = path.strip()
        if trimmed and trimmed not in normalized:
            normalized.append(trimmed)
        if len(normalized) >= max(1, max_items):
            break
    return normalized


def add_recent_file_entry(
    existing: object, path: str, max_items: int = MAX_RECENT_FILES
) -> list[str]:
    """Insert one path at the front of recent files while keeping uniqueness and limits."""
    trimmed = path.strip()
    if not trimmed:
        return normalize_recent_files(existing, max_items)
    result = [trimmed]
    for item in normalize_recent_files(existing, max_items):
        if item != trimmed:
            result.append(item)
        if len(result) >= max(1, max_items):
            break
    return result


def normalize_ui_theme(theme: object) -> str:
    """Normalize UI theme key to one of supported values."""
    if theme is None:
        return "light"
    normalized = str(theme).strip().lower()
    return normalized if normalized in UI_THEMES else "light"


def build_ui_stylesheet(theme: str, font_size: int) -> str:
    """Build the modern card-style UI stylesheet for the given theme and font size."""
    normalized_theme = normalize_ui_theme(theme)
    p = THEME_PALETTES[normalized_theme]
    return f"""
        QMainWindow {{
            background-color: {p["main_bg"]};
            color: {p["text_color"]};
        }}
        QWidget {{
            font-size: {font_size}px;
            color: {p["text_color"]};
        }}
        QWidget#controlPanel {{
            background: {p["panel_bg"]};
            border: 1px solid {p["border_color"]};
            border-radius: 12px;
        }}
        QWidget#controlPanelContent {{
            background: transparent;
            border: none;
        }}
        QTabWidget::pane {{
            border: 1px solid {p["border_color"]};
            background: {p["panel_bg"]};
            border-radius: 12px;
            top: -1px;
        }}
        QTabBar::tab {{
            background: {p["main_bg"]};
            color: {p["text_secondary"]};
            border: 1px solid {p["border_color"]};
            border-bottom: none;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
            padding: 8px 16px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            background: {p["panel_bg"]};
            color: {p["text_color"]};
            border-bottom: 2px solid {p["accent"]};
        }}
        QTabBar::tab:hover:!selected {{
            background: {p["accent_light"]};
            color: {p["text_color"]};
        }}
        QComboBox, QSpinBox, QLineEdit {{
            border: 1px solid {p["border_color"]};
            border-radius: 8px;
            padding: 7px 10px;
            background: {p["input_bg"]};
            color: {p["text_color"]};
            selection-background-color: {p["accent"]};
        }}
        QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
            border-color: {p["accent"]};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 28px;
            border: none;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
        }}
        QPushButton {{
            border-radius: 8px;
            padding: 9px 18px;
            background: {p["button_bg"]};
            color: {p["button_text_color"]};
            border: none;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {p["button_hover_bg"]};
        }}
        QPushButton:pressed {{
            background: {p["accent"]};
        }}
        QPushButton:disabled {{
            background: {p["border_color"]};
            color: {p["text_secondary"]};
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            border: none;
            height: 6px;
            background: {p["border_color"]};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: {p["accent"]};
            border: none;
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {p["button_hover_bg"]};
            width: 18px;
            height: 18px;
            margin: -6px 0;
        }}
        QSlider::sub-page:horizontal {{
            background: {p["accent"]};
            border-radius: 3px;
        }}
        QStatusBar {{
            background: {p["panel_bg"]};
            border-top: 1px solid {p["border_color"]};
            color: {p["text_color"]};
        }}
        QStatusBar::item {{
            border: none;
        }}
        QMenuBar {{
            background: {p["panel_bg"]};
            border-bottom: 1px solid {p["border_color"]};
            padding: 2px;
        }}
        QMenuBar::item {{
            padding: 6px 12px;
            border-radius: 6px;
        }}
        QMenuBar::item:selected {{
            background: {p["accent_light"]};
        }}
        QMenu {{
            background: {p["panel_bg"]};
            border: 1px solid {p["border_color"]};
            border-radius: 10px;
            padding: 6px;
        }}
        QMenu::item {{
            padding: 8px 32px 8px 16px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {p["accent_light"]};
            color: {p["text_color"]};
        }}
        QMenu::separator {{
            height: 1px;
            background: {p["border_color"]};
            margin: 4px 8px;
        }}
        QToolBar {{
            background: {p["panel_bg"]};
            border-bottom: 1px solid {p["border_color"]};
            spacing: 6px;
            padding: 4px 8px;
        }}
        QToolButton {{
            border: none;
            border-radius: 8px;
            padding: 6px;
        }}
        QToolButton:hover {{
            background: {p["accent_light"]};
        }}
        QLabel#frameNavLabel {{
            color: {p["text_secondary"]};
            font-size: {max(font_size - 1, 10)}px;
        }}
    """


@lru_cache(maxsize=2)
def load_qdarkstyle_stylesheet(theme: str) -> str:
    """Load the QDarkStyle stylesheet for the given theme (cached)."""
    import qdarkstyle  # lazy import for headless test compatibility

    if normalize_ui_theme(theme) == "dark":
        return qdarkstyle.load_stylesheet_pyqt5()
    from qdarkstyle.light.palette import LightPalette

    return qdarkstyle.load_stylesheet(qt_api="pyqt5", palette=LightPalette)


# Default output filename template
# Supported placeholders:
#   {date}       — current date as YYYYMMDD (e.g. 20260506)
#   {time}       — current time as HHMMSS (e.g. 143021)
#   {input_stem} — input file name without extension
#   {width}      — output image width
#   {height}     — output image height
#   {ext}        — output file extension (.raw / .yuv)
DEFAULT_OUTPUT_TEMPLATE = "{date}_{time}_{input_stem}_{width}x{height}{ext}"


def format_output_template(
    template: str,
    input_path: str,
    width: int,
    height: int,
    target_type: str,
    output_dir: str | None = None,
    output_ext: str | None = None,
) -> str:
    """Build an output filename from a template string.

    When *output_ext* is provided (e.g. ``.png``) it is used as-is;
    otherwise it is derived from *target_type* (``.raw`` / ``.yuv``).

    Returns a full file path.  When *output_dir* is given the file is
    placed there; otherwise it goes into a sub-directory named after
    the target mode (``convert_out`` / ``view_out``) beside the input.
    """
    from datetime import datetime

    src = Path(input_path)
    now = datetime.now()
    if output_ext is not None:
        ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    else:
        ext = ".raw" if target_type == "RAW" else ".yuv"
    if output_dir is None:
        out_dir = src.parent / "out"
    else:
        out_dir = Path(output_dir) if Path(output_dir).is_absolute() else src.parent / output_dir

    name = template.replace("{date}", now.strftime("%Y%m%d"))
    name = name.replace("{time}", now.strftime("%H%M%S"))
    name = name.replace("{input_stem}", src.stem)
    name = name.replace("{width}", str(width))
    name = name.replace("{height}", str(height))
    name = name.replace("{ext}", ext)

    return str(out_dir / name)


def build_default_output_path(input_path: str, target_type: str, output_dir_name: str) -> str:
    """Build a default output path from an input path, target type, and directory name.

    Uses the output template when available; falls back to simple ``{input_stem}{ext}``.
    """
    if not input_path:
        return ""
    src = Path(input_path)
    suffix = ".raw" if target_type == "RAW" else ".yuv"
    out_dir = src.parent / (output_dir_name or "out")
    return str(out_dir / f"{src.stem}{suffix}")


def dpi_to_dots_per_meter(dpi: int) -> int:
    """Convert DPI to dots-per-meter for QImage metadata."""
    return int(round(max(1, dpi) / 0.0254))
