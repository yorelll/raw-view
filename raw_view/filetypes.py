"""File-suffix policy shared by every entry point (GUI and CLI).

Three rules live here because they must not diverge between front ends:

* **Executable / script suffixes are never treated as image data.**  A user who
  drops ``notepad.exe`` on the window (or hands it to ``python -m raw_view
  view -i notepad.exe``) gets a readable refusal instead of a decoded-exe PNG.
  This is the one rule the CLI consumes today
  (``raw_view.__main__`` imports :func:`is_dangerous_extension`).
* **Automatic directory scans only accept known suffixes.**  The GUI's directory
  drop-scan filters to :data:`SUPPORTED_EXTENSIONS` through
  :func:`is_supported_file`; ``view``/``convert`` no longer scan a directory at
  all (a folder is ``batch``'s job), so the CLI does not use this rule.
* The support check is deliberately **narrow**: no suffix at all, or an unknown
  suffix such as ``.dat``/``.bin``, stays allowed -- those are legitimate RAW
  files.

The module exists so the CLI does not have to import ``raw_view.gui.app`` (which
would pull PyQt5 into a headless batch run just for a suffix set) and so the two
entry points cannot drift apart: the CLI imports the dangerous-suffix rule and
the GUI imports the whole policy, both from here.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "DANGEROUS_EXTENSIONS",
    "IMAGE_EXTENSIONS",
    "RAW_EXTENSIONS",
    "SUPPORTED_EXTENSIONS",
    "YUV_EXTENSIONS",
    "is_dangerous_extension",
    "is_supported_file",
]

#: Suffixes that are clearly executables or scripts.  Kept identical to the
#: historical GUI list so no file that used to open stops opening.
DANGEROUS_EXTENSIONS = frozenset({
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1",
    ".pif", ".vbs", ".vbe", ".jse", ".wsf", ".cpl", ".ocx",
})

#: Suffixes OpenCV can decode as a still image.
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp"})

#: RAW / raw-binary suffixes.  No suffix at all is also accepted (a bare
#: ``sensor`` file is a legitimate RAW dump) -- see :func:`is_supported_file`.
RAW_EXTENSIONS = frozenset({".raw", ".bin"})

#: YUV suffixes.  Kept here (rather than only in ``gui.app``) so the GUI's
#: suffix policy can be read without importing PyQt5.
YUV_EXTENSIONS = frozenset({
    ".yuv", ".nv12", ".nv21", ".i420", ".yv12", ".yuyv", ".uyvy", ".yvyu",
    ".vyuy", ".nv16", ".nv61", ".y", ".grey",
})

#: Everything the viewer treats as a supported file when *scanning* a directory.
#: The GUI's directory drop-scan filters to this set, and
#: :func:`is_supported_file` is the predicate over it.
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | YUV_EXTENSIONS | RAW_EXTENSIONS


def is_dangerous_extension(path: str | os.PathLike[str]) -> bool:
    """Whether *path* ends with a clearly executable/script suffix."""
    return Path(os.fspath(path)).suffix.lower() in DANGEROUS_EXTENSIONS


def is_supported_file(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a file the viewer supports (by extension or lack thereof).

    - suffix in :data:`SUPPORTED_EXTENSIONS` -> supported;
    - **no** suffix -> supported (a bare ``sensor`` file is a legitimate RAW
      dump; callers that enumerate a directory filter obvious text with a NUL
      sniff before accepting it);
    - any other suffix (``.txt``, ``.exe``, ...) -> not supported, so unrelated
      files are never pulled in by an automatic scan.

    This is the shared definition; :func:`gui.app._is_supported_file` delegates
    to it, so the GUI's drop-scan and any future caller cannot drift apart.
    """
    suffix = Path(os.fspath(path)).suffix.lower()
    return not suffix or suffix in SUPPORTED_EXTENSIONS
