"""FourCC format registry — built-in entries, data model, and search/store logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class FourCCEntry:
    """One row in the FourCC lookup table."""

    fourcc: str
    description: str
    mbus_name: str
    mbus_value: int
    aliases: list[str] = field(default_factory=list)
    builtin: bool = True


# ---------------------------------------------------------------------------
# Built-in format table  (sorted: YUV → Bayer 8 → 10 → 12 → 16)
# ---------------------------------------------------------------------------
# Each entry: (fourcc, [aliases], description, mbus_name, mbus_value)

_RAW_ENTRIES: list[tuple[str, list[str], str, str, int]] = [
    # ---- YUV 4:2:0 ----
    ("I420", [], "YUV 4:2:0 planar (I420)",                           "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("YV12", [], "YUV 4:2:0 planar (YV12, V before U)",              "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NV12", [], "Y/UV 4:2:0 Semi-planar",                           "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NV21", [], "Y/VU 4:2:0 Semi-planar",                           "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NM12", [], "Y/UV 4:2:0 (N-C) Semi-planar",                     "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    # ---- YUV 4:2:2 ----
    ("YUYV", [], "YUYV 4:2:2",                                        "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("YVYU", [], "YVYU 4:2:2",                                        "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("UYVY", [], "UYVY 4:2:2",                                        "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("VYUY", [], "VYUY 4:2:2",                                        "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NV16", [], "Y/UV 4:2:2 Semi-planar",                            "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NV61", [], "Y/VU 4:2:2 Semi-planar",                            "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NM16", [], "Y/UV 4:2:2 (N-C) Semi-planar",                     "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    ("NM61", [], "Y/VU 4:2:2 (N-C) Semi-planar",                     "MEDIA_BUS_FMT_YUYV8_2X8", 0x2008),
    # ---- Monochrome (raw sensor) ----
    ("GREY", [], "8-bit monochrome",                                 "MEDIA_BUS_FMT_Y8_1X8",   0x2001),
    ("Y10",  [], "10-bit monochrome",                                "MEDIA_BUS_FMT_Y10_1X10", 0x2003),
    ("Y12",  [], "12-bit monochrome",                                "MEDIA_BUS_FMT_Y12_1X12", 0x2004),
    # ---- Bayer 8-bit ----
    ("BA81", ["BGGR8"],  "8-bit Bayer BGGR",                          "MEDIA_BUS_FMT_SBGGR8_1X8", 0x3001),
    ("GBRG", ["GBRG8"],  "8-bit Bayer GBRG",                          "MEDIA_BUS_FMT_SGBRG8_1X8", 0x3013),
    ("GRBG", ["GRBG8"],  "8-bit Bayer GRBG",                          "MEDIA_BUS_FMT_SGRBG8_1X8", 0x3002),
    ("RGGB", ["RGGB8"],  "8-bit Bayer RGGB",                          "MEDIA_BUS_FMT_SRGGB8_1X8", 0x3014),
    # ---- Bayer 10-bit packed ----
    ("pBAA", ["BGGR10P"], "10-bit Bayer BGGR Packed",                 "MEDIA_BUS_FMT_SBGGR10_1X10", 0x3007),
    ("pGAA", ["GBRG10P"], "10-bit Bayer GBRG Packed",                 "MEDIA_BUS_FMT_SGBRG10_1X10", 0x300e),
    ("pgAA", ["GRBG10P"], "10-bit Bayer GRBG Packed",                 "MEDIA_BUS_FMT_SGRBG10_1X10", 0x300a),
    ("pRAA", ["RGGB10P"], "10-bit Bayer RGGB Packed",                 "MEDIA_BUS_FMT_SRGGB10_1X10", 0x300f),
    # ---- Bayer 10-bit @ 16-bit (unpacked) ----
    ("BG10", ["BGGR10"],  "10-bit Bayer BGGR",                        "MEDIA_BUS_FMT_SBGGR10_1X10", 0x3007),
    ("GB10", ["GBRG10"],  "10-bit Bayer GBRG",                        "MEDIA_BUS_FMT_SGBRG10_1X10", 0x300e),
    ("BA10", ["GRBG10"],  "10-bit Bayer GRBG",                        "MEDIA_BUS_FMT_SGRBG10_1X10", 0x300a),
    ("RG10", ["RGGB10"],  "10-bit Bayer RGGB",                        "MEDIA_BUS_FMT_SRGGB10_1X10", 0x300f),
    # ---- Bayer 12-bit packed ----
    ("pBCC", ["BGGR12P"], "12-bit Bayer BGGR Packed",                 "MEDIA_BUS_FMT_SBGGR12_1X12", 0x3008),
    ("pGCC", ["GBRG12P"], "12-bit Bayer GBRG Packed",                 "MEDIA_BUS_FMT_SGBRG12_1X12", 0x3010),
    ("pgCC", ["GRBG12P"], "12-bit Bayer GRBG Packed",                 "MEDIA_BUS_FMT_SGRBG12_1X12", 0x3011),
    ("pRCC", ["RGGB12P"], "12-bit Bayer RGGB Packed",                 "MEDIA_BUS_FMT_SRGGB12_1X12", 0x3012),
    # ---- Bayer 12-bit @ 16-bit (unpacked) ----
    ("BG12", ["BGGR12"],  "12-bit Bayer BGGR",                        "MEDIA_BUS_FMT_SBGGR12_1X12", 0x3008),
    ("GB12", ["GBRG12"],  "12-bit Bayer GBRG",                        "MEDIA_BUS_FMT_SGBRG12_1X12", 0x3010),
    ("BA12", ["GRBG12"],  "12-bit Bayer GRBG",                        "MEDIA_BUS_FMT_SGRBG12_1X12", 0x3011),
    ("RG12", ["RGGB12"],  "12-bit Bayer RGGB",                        "MEDIA_BUS_FMT_SRGGB12_1X12", 0x3012),
    # ---- Bayer 16-bit ----
    ("BYR2", ["BGGR16"],  "16-bit Bayer BGGR",                        "MEDIA_BUS_FMT_SBGGR16_1X16", 0x301d),
    ("GB16", ["GBRG16"],  "16-bit Bayer GBRG",                        "MEDIA_BUS_FMT_SGBRG16_1X16", 0x301e),
    ("GR16", ["GRBG16"],  "16-bit Bayer GRBG",                        "MEDIA_BUS_FMT_SGRBG16_1X16", 0x301f),
    ("RG16", ["RGGB16"],  "16-bit Bayer RGGB",                        "MEDIA_BUS_FMT_SRGGB16_1X16", 0x3020),
]

BUILTIN_FORMATS: list[FourCCEntry] = [
    FourCCEntry(fourcc=fc, aliases=list(als), description=desc,
                mbus_name=mbus, mbus_value=val, builtin=True)
    for fc, als, desc, mbus, val in _RAW_ENTRIES
]


# ── FourCC ↔ Alias lookup ────────────────────────────────────────────────

def _build_alias_map() -> dict[str, str]:
    m: dict[str, str] = {}
    for entry in BUILTIN_FORMATS:
        for alias in entry.aliases:
            m[alias.upper()] = entry.fourcc
    return m


ALIAS_TO_FOURCC: dict[str, str] = _build_alias_map()


# ── Store: builtin + custom ───────────────────────────────────────────────

class FourCCStore:
    """In-memory registry combining built-in and user-custom formats."""

    def __init__(self, custom_entries: list[FourCCEntry] | None = None) -> None:
        self._builtin: list[FourCCEntry] = list(BUILTIN_FORMATS)
        self._custom: list[FourCCEntry] = list(custom_entries) if custom_entries else []

    # ── accessors ──────────────────────────────────────────────────────

    @property
    def all_formats(self) -> list[FourCCEntry]:
        return self._builtin + self._custom

    @property
    def custom_formats(self) -> list[FourCCEntry]:
        return list(self._custom)

    # ── CRUD for custom entries ────────────────────────────────────────

    def add_custom(self, entry: FourCCEntry) -> None:
        self._custom.append(entry)

    def update_custom(self, index: int, entry: FourCCEntry) -> None:
        if 0 <= index < len(self._custom):
            self._custom[index] = entry

    def delete_custom(self, index: int) -> None:
        if 0 <= index < len(self._custom):
            del self._custom[index]

    # ── search ─────────────────────────────────────────────────────────

    def search(self, query: str) -> list[FourCCEntry]:
        """Filter *all_formats* by any field.  Empty query returns everything."""
        if not query.strip():
            return self.all_formats
        q = query.strip().lower()
        results: list[FourCCEntry] = []
        for fmt in self.all_formats:
            if (q in fmt.fourcc.lower()
                    or q in fmt.description.lower()
                    or q in fmt.mbus_name.lower()
                    or _match_value(q, fmt.mbus_value)
                    or any(q in a.lower() for a in fmt.aliases)):
                results.append(fmt)
        return results

    # ── exact lookup ───────────────────────────────────────────────────

    def find_by_fourcc(self, fourcc: str) -> FourCCEntry | None:
        """Case-insensitive lookup (used for search/display)."""
        key = fourcc.strip().upper()
        for fmt in self.all_formats:
            if fmt.fourcc.upper() == key:
                return fmt
        return None

    def has_fourcc_exact(self, fourcc: str) -> bool:
        """Case-SENSITIVE check — treat 'ABC' and 'abc' as different."""
        key = fourcc.strip()
        if not key:
            return False
        for fmt in self.all_formats:
            if fmt.fourcc == key:
                return True
        return False

    def find_by_alias(self, alias: str) -> FourCCEntry | None:
        key = alias.strip().upper()
        for fmt in self.all_formats:
            if any(a.upper() == key for a in fmt.aliases):
                return fmt
        return None


# ── helpers ───────────────────────────────────────────────────────────────

def _match_value(query: str, value: int) -> bool:
    """Check if *query* matches *value* in any hex format."""
    if query.startswith("0x"):
        return query[2:].lstrip("0") == f"{value:x}" or query == f"{value:#x}"
    stripped = query.lstrip("0")
    return stripped == f"{value:x}" or stripped == str(value)
