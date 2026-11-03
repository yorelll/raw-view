"""Entry point for ``raw-view-gui.exe`` -- start the GUI and nothing else.

The zip bundle ships a dedicated GUI executable so the launcher never has to
guess: this module takes file paths (and only file paths) and opens them.

It exists instead of reusing ``__main__.py`` because that module parses argv as
a full CLI.  Running it under a GUI-subsystem image means ``--help`` would try
to print -- with no console to print to -- instead of failing loudly.
"""

from __future__ import annotations

import sys

__all__ = ["main"]


def main() -> None:
    # Keep a terminal's UTF-8 preference when one exists; a GUI-subsystem build
    # usually has no streams at all, and _make_utf8_stdio() already tolerates
    # that (it getattr-guards `reconfigure`).
    from raw_view.__main__ import _make_utf8_stdio

    _make_utf8_stdio()

    from raw_view.gui.app import run

    run(sys.argv[1:])


if __name__ == "__main__":
    main()
