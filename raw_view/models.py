"""Data models, settings persistence, constants, and helper utilities."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PyQt5.QtCore import QSettings

from .formats import RAW_BITS, YUV_BYTES_PER_PIXEL
from .fourcc_data import FourCCEntry


# ── Constants ────────────────────────────────────────────────────────────

BAYER_PATTERNS = ["RGGB", "GRBG", "GBRG", "BGGR"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}

# Sensor-preset format choices share the actual codec registries instead of a
# separately maintained UI list.  ``YOnly`` is the UI alias for YOnly8; the
# other valid stored YOnly<N> values remain explicit choices so imported / old
# presets are never hidden by an artificial RAW-vs-YUV grouping.
PRESET_RAW_FORMATS = tuple(RAW_BITS)
PRESET_YUV_FORMATS = tuple(YUV_BYTES_PER_PIXEL)
PRESET_STANDARD_FORMATS = ("N/A",)
PRESET_IMAGE_TYPES = ("RAW", "YUV", "Standard Image")
PRESET_FORMATS = PRESET_RAW_FORMATS + PRESET_YUV_FORMATS + PRESET_STANDARD_FORMATS

# Preset browsing is intentionally coarse-grained.  Exact decoder formats
# remain available in the decode/preset editor but are not exposed as a noisy
# selector filter (which previously surfaced internal N/A and malformed values
# such as "12").
PRESET_CATEGORY_ALL = "All"
PRESET_CATEGORY_RAW = "RAW"
PRESET_CATEGORY_YUV = "YUV"
PRESET_CATEGORY_STANDARD = "Standard images"
PRESET_CATEGORY_CHOICES = (
    PRESET_CATEGORY_ALL,
    PRESET_CATEGORY_RAW,
    PRESET_CATEGORY_YUV,
    PRESET_CATEGORY_STANDARD,
)

# Legacy exact-format helpers are retained for persisted-data/manager editing
# compatibility.  They are no longer used as user-facing filter choices.
PRESET_FILTER_ALL = "All formats"
PRESET_FILTER_CURRENT = "Current format"


def normalize_preset_image_type(value: object) -> str:
    """Coerce legacy/programmatic preset image types to stable text."""
    return str(value or "").strip()


def normalize_preset_format_name(value: object) -> str:
    """Coerce legacy/programmatic preset format values to stable text.

    Old QSettings/JSON may contain an integer, null, or another non-string
    value.  Format names are identifiers, so a predictable stripped string is
    safer than letting filter/UI code call ``.strip()`` on arbitrary objects.
    """
    return str(value or "").strip()


def is_supported_preset_format(value: object) -> bool:
    """Whether *value* is one of the actual RAW/YUV/standard decoder formats.

    This is intentionally type-agnostic for format-filter population.  Decode
    UI and runtime validation must use :func:`is_supported_preset_format_for_type`
    so a globally real format (for example YUYV) cannot be used as RAW.
    """
    return normalize_preset_format_name(value) in PRESET_FORMATS


def is_supported_preset_format_for_type(image_type: object, format_name: object) -> bool:
    """Whether a stored format is legitimate for the given preset image type.

    Presets imported from older/manual JSON may pair otherwise valid identifiers
    with the wrong type (RAW/YUYV, YUV/RAW8, Standard Image/RAW8).  Such a pair
    is not decoder-safe and must never be allowed to silently route through the
    other type's decode branch.
    """
    image_kind = normalize_preset_image_type(image_type)
    fmt = normalize_preset_format_name(format_name)
    if image_kind == "RAW":
        return fmt in PRESET_RAW_FORMATS
    if image_kind == "YUV":
        return fmt in PRESET_YUV_FORMATS
    if image_kind == "Standard Image":
        return fmt in PRESET_STANDARD_FORMATS
    return False


def preset_format_filter_choices(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...] = (),
    *,
    include_current: bool = False,
) -> list[str]:
    """Return stable, complete format-filter choices for sensor presets.

    Registry formats always come first (so an empty list or future imports still
    expose every valid format); unknown legacy/imported ``format_name`` values
    are appended in first-seen order to keep them selectable rather than hiding
    user data.  The UI-only *Current format* sentinel is optional because the
    manager does not have a live decode-panel format to resolve.
    """
    choices = [PRESET_FILTER_ALL]
    if include_current:
        choices.append(PRESET_FILTER_CURRENT)
    choices.extend(PRESET_FORMATS)
    seen = set(choices)
    for preset in presets:
        fmt = normalize_preset_format_name(getattr(preset, "format_name", ""))
        if fmt and fmt not in seen:
            choices.append(fmt)
            seen.add(fmt)
    return choices


def preset_category_for(image_type: object, format_name: object = "") -> str | None:
    """Return the simplified browsing category for a preset, if coherent.

    Unknown legacy values stay loadable/editable but are deliberately omitted
    from category-only dropdowns.  They remain discoverable via the all-type
    name search, which never treats malformed identifiers as categories.
    """
    if is_supported_preset_format_for_type(image_type, format_name):
        kind = normalize_preset_image_type(image_type)
        if kind == "RAW":
            return PRESET_CATEGORY_RAW
        if kind == "YUV":
            return PRESET_CATEGORY_YUV
        if kind == "Standard Image":
            return PRESET_CATEGORY_STANDARD
    return None


def filter_sensor_presets_by_category(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...],
    category: str = PRESET_CATEGORY_ALL,
) -> list["SensorPreset"]:
    """Filter coherent presets by one of the four user-facing categories."""
    selected = category if category in PRESET_CATEGORY_CHOICES else PRESET_CATEGORY_ALL
    if selected == PRESET_CATEGORY_ALL:
        return list(presets)
    return [
        preset for preset in presets
        if preset_category_for(
            getattr(preset, "image_type", ""), getattr(preset, "format_name", ""),
        ) == selected
    ]


def _normalized_preset_search_value(value: object) -> str:
    """Normalize a structured-search identifier without changing stored data."""
    return "".join(str(value or "").casefold().split())


_PRESET_SEARCH_KEY_RE = re.compile(
    r"^(type|t|format|f)\s*:\s*(.*)$", re.IGNORECASE | re.DOTALL,
)
_PRESET_SEARCH_TYPE_ALIASES = {
    "raw": "raw",
    "yuv": "yuv",
    "standard image": "standard image",
}


def _parse_preset_search_query(query: object) -> tuple[str, dict[str, str] | None]:
    """Parse the official ``<type:... format:...>name`` search grammar.

    Inside one leading ``<...>`` block, requirements may be separated by spaces
    (official) or ``+`` (legacy-compatible).  The returned pair is
    ``(outside_name_term, requirements)``.  ``None`` for *requirements* means
    malformed control syntax, while an empty mapping means a normal name-only
    search.  Requirements are intentionally recognized only inside exactly one
    leading angle-bracket block, so bare ``type:yuv`` remains a literal name
    search rather than an undocumented filter syntax.
    """
    text = str(query or "").strip()
    if not text:
        return "", {}
    if not text.startswith("<"):
        return text, {}

    close_at = text.find(">", 1)
    if close_at < 0:
        return "", None
    # A second bracket pair or delimiter anywhere in the remaining name term is
    # not part of the small official grammar; fail closed rather than broadening.
    if "<" in text[1:close_at] or "<" in text[close_at + 1:] or ">" in text[close_at + 1:]:
        return "", None

    controls = text[1:close_at].strip()
    if not controls:
        return "", None
    # A leading/trailing/repeated '+' has no requirement after it and was
    # malformed under the previous grammar; keep failing closed instead of
    # silently treating it as whitespace.
    if controls.startswith("+") or controls.endswith("+") or "++" in controls:
        return "", None
    # Legacy-compatible '+' tokens are equivalent to whitespace separators, and
    # whitespace around ':' is insignificant (`type : raw` == `type:raw`).
    normalized_controls = re.sub(r"\s*:\s*", ":", controls).replace("+", " ")
    tokens = normalized_controls.split()
    if not tokens:
        return "", None

    requirements: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        match = _PRESET_SEARCH_KEY_RE.match(tokens[index])
        if match is None:
            return "", None
        key = match.group(1).casefold()
        key = "type" if key in {"type", "t"} else "format"
        raw_value = [match.group(2)]
        index += 1
        while index < len(tokens) and _PRESET_SEARCH_KEY_RE.match(tokens[index]) is None:
            raw_value.append(tokens[index])
            index += 1
        value = " ".join(raw_value).strip()
        if not value or key in requirements:
            return "", None
        requirements[key] = value
    return text[close_at + 1:].strip(), requirements


def search_sensor_presets(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...], query: str,
) -> list["SensorPreset"]:
    """Search presets by official bracketed requirements and/or name.

    Supported control syntax is ``<type:VALUE format:VALUE>NAME``; requirements
    may also keep the legacy ``+`` separator.  Aliases ``t`` and ``f`` are
    accepted, spacing is optional, and every requirement ANDs with the optional
    case-insensitive outside name term.  Invalid syntax, unknown keys, repeated
    keys, and unknown type values fail closed to an empty result.  Bare text
    (including bare ``type:yuv``) is only a name search.  Format comparisons
    ignore case and whitespace, but stored names and identifiers remain
    untouched.
    """
    name_term, requirements = _parse_preset_search_query(query)
    if requirements is None:
        return []

    normalized_type = ""
    if "type" in requirements:
        type_value = requirements["type"].strip().casefold()
        normalized_type = _PRESET_SEARCH_TYPE_ALIASES.get(type_value, "")
        if not normalized_type:
            return []

    normalized_format = ""
    if "format" in requirements:
        normalized_format = _normalized_preset_search_value(requirements["format"])
        if not normalized_format:
            return []

    needle = name_term.casefold()
    return [
        preset for preset in presets
        if (
            not needle
            or needle in str(getattr(preset, "name", "") or "").casefold()
        )
        and (
            not normalized_type
            or normalize_preset_image_type(getattr(preset, "image_type", "")).casefold()
            == normalized_type
        )
        and (
            not normalized_format
            or _normalized_preset_search_value(getattr(preset, "format_name", ""))
            == normalized_format
        )
    ]


def preset_name_sort_key(name: object) -> tuple[str, str, str]:
    """Stable alphabet/pinyin sort key for preset names.

    Latin text is case-insensitive A-Z; Chinese characters are converted to
    pinyin syllables so they sort predictably instead of by code point.  The
    original casefolded name and raw name are tie-breakers, keeping equal
    pinyin readings deterministic.  Numbers/symbols simply participate in the
    normalized string at their natural position.
    """
    text = str(name or "")
    try:
        from pypinyin import lazy_pinyin

        normalized = "".join(lazy_pinyin(text)).casefold()
    except Exception:
        # The dependency is declared; this fallback keeps a broken/partial
        # install from preventing the preset manager from opening.
        normalized = text.casefold()
    return normalized, text.casefold(), text


def sort_presets_by_name(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...],
) -> list["SensorPreset"]:
    """Return presets sorted A-Z with pinyin ordering for Chinese names."""
    return sorted(presets, key=lambda p: preset_name_sort_key(getattr(p, "name", "")))


def sort_presets_for_browse(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...],
    usage: dict[str, dict] | None = None,
) -> list["SensorPreset"]:
    """Sort the main-selector list: recently used first, then unused by name.

    Usage records are advisory.  Unknown/malformed timestamps are ignored so a
    corrupted settings value can never prevent the selector from rendering.
    """
    usage = usage if isinstance(usage, dict) else {}
    used: list[tuple[float, str, "SensorPreset"]] = []
    unused: list["SensorPreset"] = []
    for preset in presets:
        record = usage.get(getattr(preset, "name", ""))
        last_used = 0.0
        if isinstance(record, dict):
            try:
                last_used = float(record.get("last_used", 0) or 0)
            except (TypeError, ValueError):
                last_used = 0.0
        if last_used > 0:
            used.append((last_used, getattr(preset, "name", ""), preset))
        else:
            unused.append(preset)
    used.sort(key=lambda item: (-item[0], preset_name_sort_key(item[1])))
    return [preset for _ts, _name, preset in used] + sort_presets_by_name(unused)


def filter_sensor_presets(
    presets: list["SensorPreset"] | tuple["SensorPreset", ...],
    query: str = "",
    format_filter: str = PRESET_FILTER_ALL,
    current_format: str = "",
) -> list["SensorPreset"]:
    """Legacy exact-format filter retained for non-UI compatibility callers."""
    needle = str(query or "").strip().casefold()
    selected_format = normalize_preset_format_name(format_filter) or PRESET_FILTER_ALL
    if selected_format == PRESET_FILTER_CURRENT:
        selected_format = normalize_preset_format_name(current_format)
        if not selected_format or selected_format == "N/A":
            return []
    return [
        preset for preset in presets
        if (
            not needle
            or needle in str(getattr(preset, "name", "") or "").casefold()
        )
        and (
            selected_format == PRESET_FILTER_ALL
            or normalize_preset_format_name(getattr(preset, "format_name", "")) == selected_format
        )
    ]


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
#: 每文件参数记忆的上限（LRU：超出时丢弃 last_used 最旧的条目）。
FILE_PARAM_HISTORY_LIMIT = 100
UI_THEMES = {"light", "dark"}

#: 应用版本号（单一来源，ENG-6）。About / Help / CLI --version 都从这里取，
#: 发布新版本时只需改这一处。
APP_VERSION = "0.5.0"

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


def decode_options_from_mapping(value: object) -> DecodeOptions | None:
    """Coerce *value* into a :class:`DecodeOptions`, or ``None`` if unusable.

    Accepts a ``DecodeOptions``, a ``SensorPreset``, or a mapping produced by
    ``to_dict()``.  Numeric fields are coerced defensively and string fields
    normalized; ``file_path`` is intentionally never persisted (history is
    keyed by basename and the file path belongs to the opened item).
    """
    if isinstance(value, DecodeOptions):
        data = asdict(value)
    elif isinstance(value, SensorPreset) or hasattr(value, "to_dict"):
        try:
            data = value.to_dict()
        except (TypeError, ValueError):
            return None
    elif isinstance(value, dict):
        data = dict(value)
    else:
        return None
    if not isinstance(data, dict):
        return None
    defaults = DecodeOptions()
    merged = {}
    for name in defaults.__dataclass_fields__:
        if name == "file_path":
            merged[name] = ""
            continue
        fallback = getattr(defaults, name)
        raw = data.get(name, fallback)
        if isinstance(fallback, int):
            try:
                merged[name] = int(raw)
            except (TypeError, ValueError):
                return None
        else:
            text = normalize_preset_image_type(raw) if name == "image_type" else str(raw or "").strip()
            merged[name] = text or fallback
    # Enum-like fields are whitelisted (invalid values fall back to the default)
    # instead of silently persisting e.g. alignment="GARBAGE", matching the
    # defensive level of SensorPreset.from_dict (0.5.0 L-2).
    merged["alignment"] = (
        merged["alignment"] if merged["alignment"] in ("lsb", "msb") else defaults.alignment
    )
    merged["endianness"] = (
        merged["endianness"] if merged["endianness"] in ("little", "big")
        else defaults.endianness
    )
    merged["preview_mode"] = (
        merged["preview_mode"] if merged["preview_mode"] in ("Bayer Color", "Grayscale")
        else defaults.preview_mode
    )
    merged["bayer_pattern"] = (
        merged["bayer_pattern"] if merged["bayer_pattern"] in BAYER_PATTERNS
        else defaults.bayer_pattern
    )
    # Width/height/offset must stay positive/sane; a corrupt record must not
    # resurrect a zero-sized frame that can never decode.
    if merged["width"] <= 0 or merged["height"] <= 0 or merged["offset"] < 0:
        return None
    return DecodeOptions(**merged)


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
        """Build a preset from a possibly partial/legacy mapping.

        Persisted settings should not prevent the GUI from starting merely
        because an older/manual JSON entry contains a non-mapping value.
        """
        if not isinstance(data, dict):
            data = {}
        defaults = cls()
        merged = {f: data.get(f, getattr(defaults, f)) for f in defaults.__dataclass_fields__}
        # Sanity-coerce numeric fields.
        for k in ("width", "height", "offset"):
            try:
                merged[k] = int(merged[k])
            except (TypeError, ValueError):
                merged[k] = getattr(defaults, k)
        merged["name"] = str(merged.get("name", "") or "").strip()
        merged["image_type"] = normalize_preset_image_type(merged.get("image_type", ""))
        merged["format_name"] = normalize_preset_format_name(merged.get("format_name", ""))
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
    """State container for one opened file tab and its decode/view configuration.

    ``options`` is the item's **own** decode parameter set.  ``applied_options``
    is a snapshot of the parameters of the last successful decode for this item
    (``None`` until an Apply/decode actually succeeded).  It is the only signal
    used by the per-file parameter memory, so merely editing the panel or
    picking a preset (which only copies values into ``options``) can never be
    mistaken for "the user applied these parameters".
    """

    options: DecodeOptions = field(default_factory=DecodeOptions)
    current_display: object | None = None
    view: object | None = None
    frame_nav: object | None = None  # FrameNavBar widget
    zoom_percent: int = 100
    current_frame: int = 0
    total_frames: int = 0
    applied_options: DecodeOptions | None = None
    # Set when the initial options came from the per-file parameter memory, so
    # the automatic decode result can report "restored ..." in the status bar.
    remembered_params_pending: bool = False
    remembered_params_failed: bool = False


# ── Settings ─────────────────────────────────────────────────────────────


def _explicit_test_settings_file() -> str:
    """Return the explicitly contracted, usable test INI path, if supplied.

    Test isolation is intentionally a caller/test-harness responsibility.  A
    production process must never be classified as a test process from loaded
    modules, stack frames, or any other implicit heuristic.  Requiring an
    existing parent directory also keeps a malformed inherited environment
    value from silently selecting an unusable file-backed store.
    """
    explicit = os.environ.get("RAW_VIEW_TEST_SETTINGS_FILE", "").strip()
    if not explicit:
        return ""
    try:
        path = Path(explicit).expanduser()
        if not path.name or not path.parent.is_dir() or path.is_dir():
            return ""
    except (OSError, ValueError):
        return ""
    return str(path)


class AppSettings:
    """Persistence wrapper around QSettings.

    Tests can inject an isolated ``QSettings`` object; production continues to
    use the user's ``yorelll/raw-view`` registry location.  This removes the
    accidental production-settings writes that previously made test fixtures
    appear as real sensor presets.
    """

    def __init__(self, store: QSettings | None = None) -> None:
        if store is not None:
            self._store = store
            return
        # Only an explicit test-harness contract selects an INI file.  In every
        # other case (including embedded hosts that happen to import test
        # modules), preserve the user's native yorelll/raw-view settings.
        isolated_path = _explicit_test_settings_file()
        self._store = (
            QSettings(isolated_path, QSettings.IniFormat)
            if isolated_path
            else QSettings("yorelll", "raw-view")
        )

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
        self.prune_preset_usage()
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
        self.prune_preset_usage(seen)

    # ── Sensor preset usage (main-selector ordering) ───────────────────

    @property
    def preset_category(self) -> str:
        """Persisted main-selector browse category (invalid data -> All)."""
        value = str(self._store.value("presets/browse_category", PRESET_CATEGORY_ALL) or "")
        return value if value in PRESET_CATEGORY_CHOICES else PRESET_CATEGORY_ALL

    @preset_category.setter
    def preset_category(self, value: str) -> None:
        category = str(value or "")
        if category not in PRESET_CATEGORY_CHOICES:
            category = PRESET_CATEGORY_ALL
        self._store.setValue("presets/browse_category", category)

    def _load_preset_usage_raw(self) -> dict[str, dict]:
        """Return a safe ``{name: {"last_used", "count"}}`` mapping.

        Anything malformed (non-JSON, non-dict, invalid timestamps/counters) is
        silently ignored rather than preventing startup or the selector.
        """
        raw = self._store.value("presets/usage", "")
        if not raw:
            return {}
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        cleaned: dict[str, dict] = {}
        for name, record in data.items():
            if not isinstance(name, str) or not name or not isinstance(record, dict):
                continue
            try:
                last_used = float(record.get("last_used", 0) or 0)
                count = int(record.get("count", 0) or 0)
            except (TypeError, ValueError):
                continue
            if last_used < 0 or count < 0:
                continue
            cleaned[name] = {"last_used": last_used, "count": count}
        return cleaned

    @property
    def preset_usage(self) -> dict[str, dict]:
        """Read-only snapshot of persisted preset usage records."""
        return self._load_preset_usage_raw()

    def record_preset_use(self, name: str, timestamp: float | None = None) -> None:
        """Record one real user selection of *name* for recent-first ordering."""
        target = (name or "").strip()
        if not target:
            return
        usage = self._load_preset_usage_raw()
        record = usage.get(target, {"last_used": 0.0, "count": 0})
        try:
            stamp = float(time.time() if timestamp is None else timestamp)
        except (TypeError, ValueError):
            return
        usage[target] = {
            "last_used": stamp,
            "count": max(0, int(record.get("count", 0))) + 1,
        }
        self._save_preset_usage_raw(usage)

    def _save_preset_usage_raw(self, usage: dict[str, dict]) -> None:
        self._store.setValue("presets/usage", json.dumps(usage, ensure_ascii=False))

    def prune_preset_usage(self, valid_names: object | None = None) -> None:
        """Drop usage records whose preset no longer exists (deletions)."""
        if valid_names is None:
            valid = {p.name for p in self.sensor_presets}
        else:
            valid = {str(n) for n in valid_names if str(n or "").strip()}
        usage = self._load_preset_usage_raw()
        if not usage:
            return
        cleaned = {name: record for name, record in usage.items() if name in valid}
        if cleaned != usage:
            self._save_preset_usage_raw(cleaned)

    def rename_preset_usage(self, old_name: str, new_name: str) -> None:
        """Preserve usage across a manager rename; conflicting target wins max."""
        old = (old_name or "").strip()
        new = (new_name or "").strip()
        if not old or not new or old == new:
            return
        usage = self._load_preset_usage_raw()
        record = usage.pop(old, None)
        if record is None:
            return
        existing = usage.get(new)
        if existing is not None:
            usage[new] = {
                "last_used": max(
                    float(existing.get("last_used", 0) or 0),
                    float(record.get("last_used", 0) or 0),
                ),
                "count": int(existing.get("count", 0) or 0) + int(record.get("count", 0) or 0),
            }
        else:
            usage[new] = record
        self._save_preset_usage_raw(usage)

    def clear_preset_usage(self) -> None:
        """Remove all usage records (diagnostics/tests use injected stores)."""
        self._store.remove("presets/usage")

    # ── Per-file parameter memory ──────────────────────────────────────
    #
    # Keyed by *basename only* (never the full path) so the same capture name
    # opened from a different folder reuses its parameters.  Keys are
    # ``os.path.normcase``-normalized to match Windows' case-insensitive file
    # system semantics; the originally typed case is irrelevant because every
    # lookup/write goes through the same normalization.
    #
    # Stored as one JSON object under "file_params/history":
    #   {"<normcase(basename)>": {<full decode parameters>, "last_used": float}}
    # Capped at FILE_PARAM_HISTORY_LIMIT entries (LRU by ``last_used``).

    @staticmethod
    def _decode_options_dict(params: DecodeOptions) -> dict:
        """Serialise decode parameters (``DecodeOptions`` has no ``to_dict``)."""
        return asdict(params)

    @staticmethod
    def normalize_file_key(name: object) -> str:
        """Return the canonical history key for *name* (basename, normcase)."""
        text = str(name or "").strip()
        if not text:
            return ""
        # Accept either a bare name or a full path; only the basename is keyed.
        base = os.path.basename(text.replace("\\", os.sep).replace("/", os.sep))
        return os.path.normcase(base) if base and base not in (".", "..") else ""

    def _load_file_params_raw(self) -> dict[str, dict]:
        """Return a defensively-parsed ``{key: {"params": {...}, "last_used"}}``.

        Any malformed JSON, non-mapping entries, or invalid parameter values are
        dropped individually so one corrupt record can never break startup.
        """
        raw = self._store.value("file_params/history", "")
        if not raw:
            return {}
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        cleaned: dict[str, dict] = {}
        for key, record in data.items():
            if not isinstance(key, str) or not key or not isinstance(record, dict):
                continue
            params = decode_options_from_mapping(record)
            if params is None:
                continue
            try:
                last_used = float(record.get("last_used", 0) or 0)
            except (TypeError, ValueError):
                last_used = 0.0
            cleaned[key] = {"params": params, "last_used": max(0.0, last_used)}
        return cleaned

    def _save_file_params_raw(self, history: dict[str, dict]) -> None:
        payload = {
            key: {
                **self._decode_options_dict(record["params"]),
                "last_used": float(record["last_used"]),
            }
            for key, record in history.items()
        }
        self._store.setValue("file_params/history", json.dumps(payload, ensure_ascii=False))

    @property
    def file_param_history(self) -> dict[str, DecodeOptions]:
        """Public read-only snapshot: ``{normalized basename: DecodeOptions}``."""
        return {
            key: record["params"] for key, record in self._load_file_params_raw().items()
        }

    def get_file_params(self, name: object) -> DecodeOptions | None:
        """Return remembered parameters for *name* (basename), or ``None``."""
        key = self.normalize_file_key(name)
        if not key:
            return None
        record = self._load_file_params_raw().get(key)
        if record is None:
            return None
        # Copy so callers can mutate item.options without touching the store.
        return DecodeOptions(**asdict(record["params"]))

    def remember_file_params(
        self, name: object, options: object, timestamp: float | None = None,
    ) -> bool:
        """Persist *options* for *name*, refreshing LRU position.

        Returns ``False`` for an empty/invalid name or an invalid parameter set
        (nothing written).  Existing keys are refreshed and moved to newest; the
        history is trimmed to :data:`FILE_PARAM_HISTORY_LIMIT` by oldest
        ``last_used`` first.
        """
        key = self.normalize_file_key(name)
        params = decode_options_from_mapping(options)
        if not key or params is None:
            return False
        try:
            stamp = float(time.time() if timestamp is None else timestamp)
        except (TypeError, ValueError):
            return False
        history = self._load_file_params_raw()
        history[key] = {"params": params, "last_used": stamp}
        if len(history) > FILE_PARAM_HISTORY_LIMIT:
            ordered = sorted(
                history.items(), key=lambda item: item[1]["last_used"], reverse=True,
            )
            history = dict(ordered[:FILE_PARAM_HISTORY_LIMIT])
        self._save_file_params_raw(history)
        return True

    def forget_file_params(self, name: object) -> bool:
        """Drop the remembered record for *name*; ``True`` if one existed."""
        key = self.normalize_file_key(name)
        if not key:
            return False
        history = self._load_file_params_raw()
        if key not in history:
            return False
        del history[key]
        self._save_file_params_raw(history)
        return True

    def clear_file_param_history(self) -> None:
        """Remove every remembered per-file parameter record."""
        self._store.remove("file_params/history")

    # ── Pinned parameters ──────────────────────────────────────────────

    @property
    def pinned_params(self) -> DecodeOptions | None:
        """Globally pinned parameter set, or ``None`` when the pin is off."""
        raw = self._store.value("params/pinned", "")
        if not raw:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            return None
        return decode_options_from_mapping(data)

    @pinned_params.setter
    def pinned_params(self, value: object | None) -> None:
        params = decode_options_from_mapping(value) if value is not None else None
        if params is None:
            self._store.remove("params/pinned")
            return
        self._store.setValue(
            "params/pinned", json.dumps(asdict(params), ensure_ascii=False)
        )

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
        /*
         * Help icons are deliberately borderless in every native state.  The
         * Settings dialog gives the buttons a more specific object name after
         * construction (templateHelp/variantHelp), so style the shared
         * suffix as well as the factory names.  ``outline: none`` is included
         * for native styles that paint a focus ring outside the widget rect.
         * Focus remains enabled; keyboard activation and the accessible name
         * are intentionally unaffected.
         */
        QPushButton#infoButton, QPushButton#variantInfoButton,
        QPushButton#templateHelp, QPushButton#variantHelp {{
            background: transparent;
            border: none;
            outline: none;
            border-radius: 4px;
        }}
        QPushButton#infoButton:hover, QPushButton#variantInfoButton:hover,
        QPushButton#templateHelp:hover, QPushButton#variantHelp:hover,
        QPushButton#infoButton:focus, QPushButton#variantInfoButton:focus,
        QPushButton#templateHelp:focus, QPushButton#variantHelp:focus,
        QPushButton#infoButton:pressed, QPushButton#variantInfoButton:pressed,
        QPushButton#templateHelp:pressed, QPushButton#variantHelp:pressed {{
            background: transparent;
            border: none;
            outline: none;
        }}
        QPushButton#infoButton:disabled, QPushButton#variantInfoButton:disabled,
        QPushButton#templateHelp:disabled, QPushButton#variantHelp:disabled {{
            background: transparent;
            border: none;
            outline: none;
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
