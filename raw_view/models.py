"""Data models, settings persistence, constants, and helper utilities."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PyQt5.QtCore import QSettings

from .fourcc_data import FourCCEntry


# ── Constants ────────────────────────────────────────────────────────────

BAYER_PATTERNS = ["RGGB", "GRBG", "GBRG", "BGGR"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}

# Common output resolutions offered as one-click checkboxes in the
# multi-variant generator. Users can also add custom sizes.
COMMON_SIZES = [
    (2560, 1440),
    (1920, 1080),
    (1280, 720),
    (640, 480),
    (320, 240),
]
MAX_RECENT_FILES = 10
UI_THEMES = {"light", "dark"}

#: 应用版本号（单一来源，ENG-6）。About / Help / CLI --version 都从这里取，
#: 发布新版本时只需改这一处。
APP_VERSION = "0.4.0"

ACTION_ICON_COLOR = "#4A90D9"
ACTION_ICON_DISABLED_COLOR = "#9E9E9E"
ACTION_ICON_NAMES = {
    "open": "fa5s.folder-open",
    "save": "fa5s.save",
    "convert": "fa5s.exchange-alt",
    "settings": "fa5s.cog",
    "help": "fa5s.question-circle",
}

# Indigo / violet palette, tuned to harmonize with the qt-material
# light_purple / dark_purple base themes. These values feed the thin QSS
# overlay in ``build_ui_stylesheet`` (cards, panels, accents) that sits on
# top of the qt-material base.
THEME_PALETTES = {
    "light": {
        "main_bg": "#F4F6FA",
        "text_color": "#212121",
        "text_secondary": "#6B7280",
        "panel_bg": "#FFFFFF",
        "border_color": "#D5D9E3",
        "input_bg": "#FFFFFF",
        "button_bg": "#1976D2",
        "button_hover_bg": "#1565C0",
        "button_text_color": "#FFFFFF",
        "accent": "#1976D2",
        "accent_light": "#E3F2FD",
        "card_shadow": "rgba(30,27,46,0.08)",
        "success": "#2E7D32",
        "warning": "#F59E0B",
        # Frame navigation bar button palette (light theme).
        "nav_button_bg": "#FFFFFF",
        "nav_button_border": "#D5D9E3",
        "nav_button_text": "#212121",
        "nav_button_hover_bg": "#E3F2FD",
        "nav_button_hover_border": "#1976D2",
        "nav_button_hover_text": "#1565C0",
        "nav_button_pressed_bg": "#D5D9E3",
        "nav_button_pressed_border": "#9AA0AC",
        "nav_button_pressed_text": "#000000",
        "nav_button_disabled_bg": "#F1F3F8",
        "nav_button_disabled_border": "#E5E8EF",
        "nav_button_disabled_text": "#B0B6C4",
    },
    "dark": {
        "main_bg": "#171A24",
        "text_color": "#E8EAED",
        "text_secondary": "#9AA0AC",
        "panel_bg": "#21242F",
        "border_color": "#3A3D4E",
        "input_bg": "#2A2D3E",
        "button_bg": "#1976D2",
        "button_hover_bg": "#1E88E5",
        "button_text_color": "#FFFFFF",
        "accent": "#4A90D9",
        "accent_light": "#26304A",
        "card_shadow": "rgba(0,0,0,0.35)",
        "success": "#66BB6A",
        "warning": "#FBBF24",
        # Frame navigation bar button palette (dark theme).
        "nav_button_bg": "#2A2D4A",
        "nav_button_border": "#3A3D5C",
        "nav_button_text": "#C8CCDC",
        "nav_button_hover_bg": "#363A5E",
        "nav_button_hover_border": "#5B6080",
        "nav_button_hover_text": "#E8EAED",
        "nav_button_pressed_bg": "#1E2035",
        "nav_button_pressed_border": "#2A2D4A",
        "nav_button_pressed_text": "#FFFFFF",
        "nav_button_disabled_bg": "#1E2035",
        "nav_button_disabled_border": "#2A2D4A",
        "nav_button_disabled_text": "#4A5070",
    },
}

# qt-material base themes mapped to our two logical themes, plus shared
# ``extra`` options passed to ``apply_stylesheet`` (see gui/app._apply_theme).
# These are custom Material-blue (#1976D2) themes shipped in assets/, resolved
# to absolute paths at runtime, so the whole UI shares one professional accent
# instead of qt-material's default magenta-purple.
THEME_XML = {"dark": "theme_dark_blue.xml", "light": "theme_light_blue.xml"}
MATERIAL_EXTRA = {"font_family": "Segoe UI", "density_scale": "-1"}


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
    preview_mode: str = "Bayer Color"  # "Bayer Color" / "Grayscale" (RAW only)
    bayer_pattern: str = "RGGB"        # one of BAYER_PATTERNS (RAW only)


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
    def multi_variant_enabled(self) -> bool:
        """When True, the Convert / Batch dialogs expose a multi-variant
        generator (one source image → many format/bayer/size outputs)."""
        return bool(self._store.value("convert/multi_variant", False, type=bool))

    @multi_variant_enabled.setter
    def multi_variant_enabled(self, value: bool) -> None:
        self._store.setValue("convert/multi_variant", bool(value))

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
        return normalize_ui_theme(self._store.value("ui/theme", "dark"))

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

    # ── FourCC custom formats ──────────────────────────────────────────
    #
    # Stored as a single JSON-encoded array under "fourcc/custom". The
    # same JSON-serialisation reasoning as sensor presets applies.

    def _load_fourcc_raw(self) -> list[dict]:
        raw = self._store.value("fourcc/custom", "")
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def _save_fourcc_raw(self, items: list[dict]) -> None:
        self._store.setValue("fourcc/custom", json.dumps(items, ensure_ascii=False))

    @property
    def fourcc_custom_formats(self) -> list[FourCCEntry]:
        """User-defined custom FourCC entries, in insertion order."""
        result: list[FourCCEntry] = []
        for d in self._load_fourcc_raw():
            try:
                entry = FourCCEntry(
                    fourcc=str(d.get("fourcc", "")),
                    description=str(d.get("description", "")),
                    mbus_name=str(d.get("mbus_name", "")),
                    mbus_value=int(d.get("mbus_value", 0)),
                    aliases=list(d.get("aliases", [])),
                    builtin=False,
                )
                if entry.fourcc:
                    result.append(entry)
            except (TypeError, ValueError):
                continue
        return result

    def save_fourcc_custom_list(self, entries: list[FourCCEntry]) -> None:
        """Replace the entire custom FourCC list (used by the manage dialog)."""
        out = []
        for e in entries:
            out.append({
                "fourcc": e.fourcc,
                "description": e.description,
                "mbus_name": e.mbus_name,
                "mbus_value": e.mbus_value,
                "aliases": list(e.aliases),
            })
        self._save_fourcc_raw(out)


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
    """Build the thin card/accent overlay layered on top of the qt-material base.

    qt-material styles all the standard widgets; this overlay only adds the
    rounded-card panels, indigo/violet accents, and the font size. Rules are
    scoped to our own containers (``#controlPanel``, ``#card``, ``#centralRoot``)
    so they complement — rather than fight — the qt-material stylesheet.
    """
    normalized_theme = normalize_ui_theme(theme)
    p = THEME_PALETTES[normalized_theme]
    return f"""
        QWidget {{
            font-size: {font_size}px;
        }}
        QWidget#centralRoot, DropCentralWidget {{
            background: {p["main_bg"]};
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
        QFrame#card {{
            background: {p["panel_bg"]};
            border: 1px solid {p["border_color"]};
            border-radius: 12px;
        }}
        QFrame#groupDivider {{
            background: {p["border_color"]};
            border: none;
            max-height: 1px;
        }}
        QLabel {{
            color: {p["text_color"]};
            background: transparent;
        }}
        QComboBox, QSpinBox, QLineEdit, QAbstractSpinBox {{
            background: {p["input_bg"]};
            color: {p["text_color"]};
            border: 1px solid {p["border_color"]};
            border-radius: 6px;
            padding: 4px 8px;
        }}
        QComboBox:focus, QSpinBox:focus, QLineEdit:focus, QAbstractSpinBox:focus {{
            border: 1px solid {p["accent"]};
        }}
        QComboBox:disabled, QSpinBox:disabled, QLineEdit:disabled,
        QAbstractSpinBox:disabled {{
            color: {p["text_secondary"]};
            background: {p["accent_light"]};
        }}
        QComboBox {{
            combobox-popup: 0;
        }}
        QComboBox::drop-down {{
            width: 22px;
            border-left: 1px solid {p["border_color"]};
        }}
        QSplitter#mainSplitter::handle:horizontal {{
            background: transparent;
        }}
        QSplitter#mainSplitter::handle:horizontal:hover {{
            background: {p["accent_light"]};
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 18px;
            height: 18px;
            border: 1px solid {p["border_color"]};
            border-radius: 4px;
            background: {p["input_bg"]};
        }}
        QRadioButton::indicator {{
            border-radius: 9px;
        }}
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
            border: 1px solid {p["accent"]};
        }}
        QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
            background: {p["accent"]};
            border: 1px solid {p["accent"]};
            image: none;
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {p["border_color"]};
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {p["accent"]};
            border-radius: 3px;
        }}
        QSlider::add-page:horizontal {{
            background: {p["border_color"]};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            background: #FFFFFF;
            border: 2px solid {p["accent"]};
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 9px;
        }}
        QSlider::handle:horizontal:hover {{
            border: 2px solid {p["button_hover_bg"]};
        }}
        QTabWidget::pane {{
            border: 1px solid {p["border_color"]};
            border-radius: 12px;
        }}
        QTabBar::tab {{
            padding: 10px 14px 14px 16px;
            margin-right: 2px;
        }}
        QTabBar::tab:selected {{
            color: {p["text_color"]};
            border-bottom: 2px solid {p["accent"]};
        }}
        QTabBar::tab:hover:!selected {{
            background: {p["accent_light"]};
        }}
        QTabBar::close-button {{
            subcontrol-position: right;
        }}
        QPushButton {{
            text-transform: none;
        }}
        QPushButton#accentButton {{
            background: {p["button_bg"]};
            color: {p["button_text_color"]};
            border: none;
            border-radius: 8px;
            padding: 9px 18px;
            font-weight: 600;
        }}
        QPushButton#accentButton:hover {{
            background: {p["button_hover_bg"]};
        }}
        QPushButton#accentButton:disabled {{
            background: {p["border_color"]};
            color: {p["text_secondary"]};
        }}
        QPushButton#secondaryButton {{
            background: transparent;
            color: {p["accent"]};
            border: 1px solid {p["accent"]};
            border-radius: 8px;
            padding: 8px 16px;
            font-weight: 600;
        }}
        QPushButton#secondaryButton:hover {{
            background: {p["accent_light"]};
        }}
        QPushButton#textButton {{
            background: transparent;
            color: {p["accent"]};
            border: none;
            padding: 8px 12px;
            font-weight: 600;
        }}
        QPushButton#textButton:hover {{
            color: {p["button_hover_bg"]};
            text-decoration: underline;
        }}
        QPushButton#iconButton {{
            background: transparent;
            border: 1px solid {p["border_color"]};
            border-radius: 6px;
            padding: 4px;
        }}
        QPushButton#iconButton:hover {{
            background: {p["accent_light"]};
            border: 1px solid {p["accent"]};
        }}
        QPushButton#dangerButton {{
            background: transparent;
            color: #E53935;
            border: 1px solid #E53935;
            border-radius: 8px;
            padding: 8px 16px;
            font-weight: 600;
        }}
        QPushButton#dangerButton:hover {{
            background: #E53935;
            color: #FFFFFF;
        }}
        QLabel#previewThumb {{
            background: {p["accent_light"]};
            border: 1px solid {p["border_color"]};
            border-radius: 6px;
            color: {p["text_secondary"]};
        }}
        QMenuBar {{
            background: {p["panel_bg"]};
            color: {p["text_color"]};
        }}
        QMenuBar::item {{
            padding: 6px 12px;
            border-radius: 6px;
        }}
        QMenuBar::item:selected {{
            background: {p["accent_light"]};
            color: {p["text_color"]};
        }}
        QMenu {{
            background: {p["panel_bg"]};
            color: {p["text_color"]};
            border: 1px solid {p["border_color"]};
            border-radius: 8px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 28px 6px 14px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background: {p["accent_light"]};
            color: {p["text_color"]};
        }}
        QMenu::separator {{
            height: 1px;
            background: {p["border_color"]};
            margin: 5px 10px;
        }}
        QLabel#frameNavLabel {{
            color: {p["text_secondary"]};
            font-size: {max(font_size - 1, 10)}px;
        }}
        QWidget#frameNavBar {{
            background: transparent;
        }}
        QWidget#frameNavBar QPushButton {{
            background: {p["nav_button_bg"]};
            border: 1px solid {p["nav_button_border"]};
            border-radius: 6px;
            font-weight: bold;
            color: {p["nav_button_text"]};
            font-size: 14px;
        }}
        QWidget#frameNavBar QPushButton:hover {{
            background: {p["nav_button_hover_bg"]};
            border: 1px solid {p["nav_button_hover_border"]};
            color: {p["nav_button_hover_text"]};
        }}
        QWidget#frameNavBar QPushButton:pressed {{
            background: {p["nav_button_pressed_bg"]};
            border: 1px solid {p["nav_button_pressed_border"]};
            color: {p["nav_button_pressed_text"]};
        }}
        QWidget#frameNavBar QPushButton:disabled {{
            background: {p["nav_button_disabled_bg"]};
            border: 1px solid {p["nav_button_disabled_border"]};
            color: {p["nav_button_disabled_text"]};
        }}
        QWidget#frameNavBar QSpinBox {{
            padding: 2px 4px;
        }}
        QWidget#frameNavBar QLabel {{
            color: {p["text_secondary"]};
            font-size: 13px;
        }}
        QLabel#statusPlaceholder {{
            color: {p["text_secondary"]};
            font-style: italic;
        }}
    """


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
