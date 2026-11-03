"""File-suffix policy shared by every entry point (GUI and CLI).

Two rules live here because they must not diverge between front ends:

* **Executable / script suffixes are never treated as image data.**  A user who
  drops ``notepad.exe`` on the window (or hands it to ``python -m raw_view
  view -i notepad.exe``) gets a readable refusal instead of a decoded-exe PNG.
* The check is deliberately **narrow**: no suffix at all, or an unknown suffix
  such as ``.dat``/``.bin``, stays allowed -- those are legitimate RAW files.

The module exists so the CLI does not have to import ``raw_view.gui.app`` (which
would pull PyQt5 into a headless batch run just for a suffix set) and so the two
entry points cannot drift apart.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "DANGEROUS_EXTENSIONS",
    "is_dangerous_extension",
]

#: Suffixes that are clearly executables or scripts.  Kept identical to the
#: historical GUI list so no file that used to open stops opening.
DANGEROUS_EXTENSIONS = frozenset({
    ".exe", ".dll", ".sys", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1",
    ".pif", ".vbs", ".vbe", ".jse", ".wsf", ".cpl", ".ocx",
})


def is_dangerous_extension(path: str | os.PathLike[str]) -> bool:
    """Whether *path* ends with a clearly executable/script suffix."""
    return Path(os.fspath(path)).suffix.lower() in DANGEROUS_EXTENSIONS
