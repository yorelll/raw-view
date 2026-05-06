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
        "main_bg": "#F8FAFC",
        "text_color": "#1E293B",
        "panel_bg": "#FFFFFF",
        "border_color": "#E2E8F0",
        "input_bg": "#FFFFFF",
        "button_bg": "#2563EB",
        "button_hover_bg": "#1D4ED8",
        "button_text_color": "#FFFFFF",
    },
    "dark": {
        "main_bg": "#0F172A",
        "text_color": "#E2E8F0",
        "panel_bg": "#111827",
        "border_color": "#334155",
        "input_bg": "#1F2937",
        "button_bg": "#2563EB",
        "button_hover_bg": "#1D4ED8",
        "button_text_color": "#FFFFFF",
    },
}


# ── Data models ─────────────────────────────────────────────────────────


@dataclass
class DecodeOptions:
    """Serialisable parameter set for one decode operation."""

    file_path: str = ""
    image_type: str = "RAW"
    format_name: str = "RAW8"
    width: int = 640
    height: int = 480
    alignment: str = "lsb"
    endianness: str = "little"
    offset: int = 0


@dataclass
class ViewerItem:
    """State container for one opened file tab and its decode/view configuration."""

    options: DecodeOptions = field(default_factory=DecodeOptions)
    current_display: object | None = None
    view: object | None = None
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
        return self._normalize_dirname(self._store.value("convert/default_output_dirname", "out"))

    @default_output_dirname.setter
    def default_output_dirname(self, value: str) -> None:
        self._store.setValue("convert/default_output_dirname", self._normalize_dirname(value))

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
    """Build the custom UI stylesheet for the given theme and font size."""
    normalized_theme = normalize_ui_theme(theme)
    palette = THEME_PALETTES[normalized_theme]
    return f"""
        QMainWindow {{
            background-color: {palette["main_bg"]};
            color: {palette["text_color"]};
        }}
        QWidget {{
            font-size: {font_size}px;
            color: {palette["text_color"]};
        }}
        #controlPanel {{
            background: {palette["panel_bg"]};
            border: 1px solid {palette["border_color"]};
            border-radius: 8px;
        }}
        QTabWidget::pane {{
            border: 1px solid {palette["border_color"]};
            background: {palette["panel_bg"]};
            border-radius: 8px;
        }}
        QComboBox, QSpinBox, QLineEdit {{
            border: 1px solid {palette["border_color"]};
            border-radius: 6px;
            padding: 6px 8px;
            background: {palette["input_bg"]};
            color: {palette["text_color"]};
        }}
        QPushButton {{
            border-radius: 6px;
            padding: 8px 14px;
            background: {palette["button_bg"]};
            color: {palette["button_text_color"]};
            border: none;
        }}
        QPushButton:hover {{
            background: {palette["button_hover_bg"]};
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


def build_default_output_path(input_path: str, target_type: str, output_dir_name: str) -> str:
    """Build a default output path from an input path, target type, and directory name."""
    if not input_path:
        return ""
    src = Path(input_path)
    suffix = ".raw" if target_type == "RAW" else ".yuv"
    out_dir = src.parent / (output_dir_name or "out")
    return str(out_dir / f"{src.stem}{suffix}")


def dpi_to_dots_per_meter(dpi: int) -> int:
    """Convert DPI to dots-per-meter for QImage metadata."""
    return int(round(max(1, dpi) / 0.0254))
