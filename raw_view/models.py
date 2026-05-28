"""Data models, settings persistence, constants, and helper utilities."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
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
class SensorPreset:
    """A named sensor configuration that can be one-click applied.

    Stores every panel field needed to decode a RAW/YUV file. ``name`` is the
    user-visible identifier and is used as the key for save/load/delete.
    """

    name: str = ""
    image_type: str = "RAW"           # "RAW" / "YUV" / "Standard Image"
    format_name: str = "RAW12"        # depends on image_type
    width: int = 2560
    height: int = 1440
    alignment: str = "msb"            # "lsb" / "msb"   (RAW only)
    endianness: str = "little"        # "little" / "big" (RAW only)
    offset: int = 0
    preview_mode: str = "Bayer Color"  # "Bayer Color" / "Grayscale" (RAW only)
    bayer_pattern: str = "BGGR"       # one of BAYER_PATTERNS (RAW only)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SensorPreset":
        """Build a preset from a (possibly partial) dict, falling back to defaults."""
        defaults = cls()
        merged = {f: data.get(f, getattr(defaults, f)) for f in defaults.__dataclass_fields__}
        # Sanity-coerce numeric fields.
        for k in ("width", "height", "offset"):
            try:
                merged[k] = int(merged[k])
            except (TypeError, ValueError):
                merged[k] = getattr(defaults, k)
        merged["name"] = str(merged.get("name", "")).strip()
        return cls(**merged)


@dataclass
class ImportResult:
    """Summary returned by :meth:`AppSettings.import_sensor_presets`."""

    added: int = 0
    overwritten: int = 0
    skipped: int = 0
    conflicts: list[str] = field(default_factory=list)

    @property
    def total_changed(self) -> int:
        return self.added + self.overwritten


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
        stored = self._store.value("convert/output_template", DEFAULT_OUTPUT_TEMPLATE)
        text = str(stored) if stored is not None else ""
        # One-shot migration: users who launched an older build still have
        # the previous default stored in QSettings, so simply changing
        # DEFAULT_OUTPUT_TEMPLATE in code wouldn't take effect for them.
        # Detect any of the known legacy defaults and rewrite to the new
        # default in place, so existing installs pick up the new naming
        # automatically without losing custom user templates.
        if text.strip() in LEGACY_OUTPUT_TEMPLATES:
            text = DEFAULT_OUTPUT_TEMPLATE
            self._store.setValue("convert/output_template", text)
        return text

    @output_template.setter
    def output_template(self, value: str) -> None:
        self._store.setValue("convert/output_template", value.strip() or DEFAULT_OUTPUT_TEMPLATE)

    def reset_output_template(self) -> str:
        """Force the template back to :data:`DEFAULT_OUTPUT_TEMPLATE`."""
        self._store.setValue("convert/output_template", DEFAULT_OUTPUT_TEMPLATE)
        return DEFAULT_OUTPUT_TEMPLATE

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

    # ── Sensor presets ──────────────────────────────────────────────
    #
    # Stored as a single JSON-encoded array under "presets/sensors". Using
    # JSON keeps the list atomic (no half-written entries on crash) and
    # avoids QSettings type-coercion quirks across platforms.

    def _load_presets_raw(self) -> list[dict]:
        raw = self._store.value("presets/sensors", "")
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def _save_presets_raw(self, items: list[dict]) -> None:
        self._store.setValue("presets/sensors", json.dumps(items, ensure_ascii=False))

    @property
    def sensor_presets(self) -> list[SensorPreset]:
        """All saved sensor presets, in insertion order."""
        return [SensorPreset.from_dict(d) for d in self._load_presets_raw() if d.get("name")]

    def get_sensor_preset(self, name: str) -> SensorPreset | None:
        target = (name or "").strip()
        if not target:
            return None
        for p in self.sensor_presets:
            if p.name == target:
                return p
        return None

    def save_sensor_preset(self, preset: SensorPreset) -> None:
        """Insert or update a preset by name. Empty name is rejected."""
        name = (preset.name or "").strip()
        if not name:
            raise ValueError("preset name must not be empty")
        preset = SensorPreset.from_dict({**preset.to_dict(), "name": name})
        items = self._load_presets_raw()
        for i, existing in enumerate(items):
            if str(existing.get("name", "")).strip() == name:
                items[i] = preset.to_dict()
                break
        else:
            items.append(preset.to_dict())
        self._save_presets_raw(items)

    def delete_sensor_preset(self, name: str) -> bool:
        target = (name or "").strip()
        if not target:
            return False
        items = self._load_presets_raw()
        new_items = [d for d in items if str(d.get("name", "")).strip() != target]
        if len(new_items) == len(items):
            return False
        self._save_presets_raw(new_items)
        return True

    def export_sensor_presets(self, path: str) -> int:
        """Write all current sensor presets to *path* as pretty JSON.

        Returns the number of presets exported. Overwrites the target file.
        """
        items = [p.to_dict() for p in self.sensor_presets]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        return len(items)

    def import_sensor_presets(
        self, path: str, mode: str = "merge", on_conflict: str = "overwrite"
    ) -> "ImportResult":
        """Read presets from *path* and merge them into settings.

        Parameters
        ----------
        path : str
            JSON file produced by ``export_sensor_presets`` (an array of
            preset dicts) or any compatible structure.
        mode : {"merge", "replace"}
            ``merge`` keeps existing presets and adds/overwrites by name.
            ``replace`` discards all existing presets first.
        on_conflict : {"overwrite", "skip"}
            Only consulted when ``mode == "merge"`` and the imported list
            contains a name that already exists locally.

        Returns
        -------
        ImportResult
            Counters describing what was added / overwritten / skipped, plus
            a list of conflicting names (useful for UI confirmation flows).
        """
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            raise ValueError("preset JSON must be a list of preset objects")
        incoming = [
            SensorPreset.from_dict(d) for d in raw
            if isinstance(d, dict) and str(d.get("name", "")).strip()
        ]

        if mode == "replace":
            self.replace_sensor_presets(incoming)
            return ImportResult(
                added=len(incoming), overwritten=0, skipped=0, conflicts=[],
            )

        if mode != "merge":
            raise ValueError(f"unknown import mode: {mode!r}")

        existing = {p.name: p for p in self.sensor_presets}
        added = overwritten = skipped = 0
        conflicts: list[str] = []
        merged: dict[str, SensorPreset] = dict(existing)
        for p in incoming:
            if p.name in existing:
                conflicts.append(p.name)
                if on_conflict == "skip":
                    skipped += 1
                    continue
                if on_conflict != "overwrite":
                    raise ValueError(f"unknown on_conflict: {on_conflict!r}")
                overwritten += 1
                merged[p.name] = p
            else:
                added += 1
                merged[p.name] = p
        # Preserve original ordering of existing presets, then append new ones.
        ordered = [merged[name] for name in existing if name in merged]
        ordered.extend(merged[name] for name in (p.name for p in incoming) if name not in existing)
        self.replace_sensor_presets(ordered)
        return ImportResult(
            added=added, overwritten=overwritten, skipped=skipped, conflicts=conflicts,
        )

    def replace_sensor_presets(self, presets: list[SensorPreset]) -> None:
        """Persist the given list as the full preset set (used by the manage dialog)."""
        seen: set[str] = set()
        out: list[dict] = []
        for p in presets:
            name = (p.name or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(SensorPreset.from_dict({**p.to_dict(), "name": name}).to_dict())
        self._save_presets_raw(out)


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


# Default output filename template.
#
# Supported placeholders (see ``format_output_template`` for details):
#
#   ── Always available ──
#   {input_stem} — input file name without extension
#   {width}      — output image width
#   {height}     — output image height
#   {ext}        — output file extension (.raw / .yuv / .png / .jpg …)
#   {date}       — current date as YYYYMMDD (e.g. 20260506)
#   {time}       — current time as HHMMSS (e.g. 143021)
#
#   ── Format-aware (RAW only) ──
#   {bayer}      — Bayer pattern (RGGB / BGGR / GRBG / GBRG); empty for
#                  gray source mode or YUV
#   {bits}       — bit depth as a number (8/10/12/14/16); empty for YUV
#   {packed}     — "P" when the RAW format is MIPI packed, else empty
#   {raw_type}   — the raw type with spaces removed, e.g. "RAW10Packed"
#
#   ── Format-aware (YUV only) ──
#   {yuv_type}   — YUV subformat in upper case (YUYV / NV12 / I420 / ...)
#
#   ── Combined short tag ──
#   {format}     — for RAW with Bayer source: "{bayer}{bits}{packed}",
#                  e.g. "BGGR10P"; for RAW gray source: "{raw_type}",
#                  e.g. "RAW12"; for YUV: "{yuv_type}", e.g. "YUYV"
#
#   ── Optional, off by default ──
#   {alignment}  — "lsb" / "msb" (RAW)
#   {endianness} — "little" / "big" (RAW)
#
# The default template intentionally embeds Bayer + bit-depth + packed
# flag so that converting one source image with different RAW formats
# produces distinct output files (e.g. ``image_2560x1440_BGGR10P.raw``
# vs ``image_2560x1440_RGGB12.raw``). Earlier defaults only included
# ``{date}`` / ``{time}`` and were prone to collisions.
DEFAULT_OUTPUT_TEMPLATE = "{input_stem}_{width}x{height}_{format}{ext}"

# Templates that earlier versions of raw-view shipped as their default.
# When ``output_template`` is read and the stored value matches one of
# these, AppSettings silently rewrites it to ``DEFAULT_OUTPUT_TEMPLATE``
# so existing installs pick up the new naming without losing any
# *user-customised* template (those won't be in this set).
LEGACY_OUTPUT_TEMPLATES = frozenset({
    "{date}_{time}_{input_stem}_{width}x{height}{ext}",
    "{input_stem}_{width}x{height}{ext}",
})


def format_output_template(
    template: str,
    input_path: str,
    width: int,
    height: int,
    target_type: str,
    output_dir: str | None = None,
    output_ext: str | None = None,
    *,
    raw_type: str = "",
    yuv_type: str = "",
    bayer_pattern: str = "",
    source_mode: str = "",
    alignment: str = "",
    endianness: str = "",
) -> str:
    """Build an output filename from a template string.

    Parameters
    ----------
    template
        The user-configurable template, e.g. ``{input_stem}_{format}{ext}``.
        See :data:`DEFAULT_OUTPUT_TEMPLATE` for the full placeholder list.
    input_path, width, height, target_type
        Core inputs that drive the basic placeholders.
    output_dir, output_ext
        Override the directory and extension; otherwise derived from
        ``target_type`` (``.raw`` / ``.yuv``).
    raw_type, yuv_type, bayer_pattern, source_mode, alignment, endianness
        Optional; consumed by the format-aware placeholders. Unknown
        values resolve to empty strings, so unused placeholders just
        disappear without raising.

    Returns
    -------
    str
        A full file path. When *output_dir* is given the file is placed
        there; otherwise it goes into a sub-directory beside the input.
    """
    from datetime import datetime

    # Local import avoids a top-level cycle between models <-> formats.
    from raw_view.formats import RAW_BITS

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

    # ── Format-aware placeholders ────────────────────────────────────
    # All values default to "" so a template can omit any of them and
    # still render cleanly even when the caller didn't pass info.
    bits_str = ""
    packed_flag = ""
    if raw_type:
        bits_val = RAW_BITS.get(raw_type)
        bits_str = str(bits_val) if bits_val is not None else ""
        packed_flag = "P" if "Packed" in raw_type else ""

    raw_token = raw_type.replace(" ", "") if raw_type else ""
    yuv_token = (yuv_type or "").upper()

    # {bayer} only renders when the source is actually a Bayer pattern.
    # Gray source images don't have a colour mosaic, so emitting the
    # combo's last value would be misleading.
    is_bayer_source = (
        target_type == "RAW"
        and bayer_pattern
        and (source_mode or "bayer").lower() == "bayer"
    )
    bayer_token = bayer_pattern.upper() if is_bayer_source else ""

    if target_type == "RAW":
        if bayer_token:
            format_tag = f"{bayer_token}{bits_str}{packed_flag}"
        else:
            # Gray source / no Bayer info: fall back to the raw-type token
            # so the filename still reflects bit depth and packed flag.
            format_tag = raw_token or "RAW"
    elif target_type == "YUV":
        format_tag = yuv_token or "YUV"
    else:
        format_tag = ""

    name = template
    name = name.replace("{date}", now.strftime("%Y%m%d"))
    name = name.replace("{time}", now.strftime("%H%M%S"))
    name = name.replace("{input_stem}", src.stem)
    name = name.replace("{width}", str(width))
    name = name.replace("{height}", str(height))
    name = name.replace("{ext}", ext)
    name = name.replace("{format}", format_tag)
    name = name.replace("{bayer}", bayer_token)
    name = name.replace("{bits}", bits_str)
    name = name.replace("{packed}", packed_flag)
    name = name.replace("{raw_type}", raw_token)
    name = name.replace("{yuv_type}", yuv_token)
    name = name.replace("{alignment}", (alignment or "").lower())
    name = name.replace("{endianness}", (endianness or "").lower())

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
