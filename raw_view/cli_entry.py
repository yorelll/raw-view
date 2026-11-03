"""Entry point for ``raw-view-cli.exe`` -- command line only, never a window.

Thin wrapper so PyInstaller has a stable module to point the console build at
(the alternative, ``-c "import ..."``, does not work with ``--console``/onedir).
It delegates to :func:`raw_view.__main__._cli_main`, which marks the process as
"no GUI allowed" *before* parsing.  That marker is what turns the two
"fall back to the GUI" paths -- no sub-command, and ``view`` without ``--input``
-- into an immediate usage error instead of a silently blocking window.
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    from raw_view.__main__ import _cli_main

    _cli_main()


if __name__ == "__main__":
    main()
