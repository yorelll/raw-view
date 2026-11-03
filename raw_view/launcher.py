"""``raw-view.exe`` launcher: route argv to the GUI or CLI executable.

The zip bundle ships three executables that share a single ``_internal``
directory::

    raw-view.exe        this launcher (built --windowed, so double-clicking
                        never flashes a console window)
    raw-view-gui.exe    GUI only        (--windowed)
    raw-view-cli.exe    CLI only        (--console)

Why a launcher at all?  Windows decides the console behaviour from the PE
subsystem of the executable that is *started*.  A GUI-subsystem image cannot
write to the user's terminal, and a console-subsystem image shows a console
window when double-clicked.  One binary cannot do both, so the launcher picks
the right child for the arguments it was given.

Two halves live here, deliberately separate:

* :func:`decide_target` / :func:`gui_files` -- **pure** functions over a list of
  arguments.  No Qt, no subprocess, no Windows API, so the routing table is
  unit-testable without packaging anything.
* :func:`run` -- the forwarding half, which is where the console handling
  subtlety lives (see :func:`_wrap_std_handle`).

Routing table (product decision -- arguments are never rewritten for the CLI):

======================  ==============================================
input                   target
======================  ==============================================
(none)                  GUI
``view`` / ``view f…``   GUI
``view -i …``           CLI
``convert …``           CLI
``batch …``             CLI
``-h``/``--help``/…     CLI
any CLI option          CLI
anything else           CLI (argparse reports usage, exit 2)
======================  ==============================================

The last row is deliberate: a bare ``raw-view.exe a.raw`` is *not* silently
turned into ``view a.raw``.  Guessing there would also mean ``--width 800``
(a typo'd option) launches a window and blocks the terminal, which is far worse
than an immediate usage error.
"""

from __future__ import annotations

import os
import subprocess
import sys

__all__ = [
    "CLI_EXE",
    "GUI_EXE",
    "cli_executable",
    "decide_target",
    "gui_files",
    "main",
    "run",
]

#: File names of the sibling executables inside the same onedir bundle.
CLI_EXE = "raw-view-cli.exe"
GUI_EXE = "raw-view-gui.exe"

#: Sub-commands that only ever make sense on the command line.
_CLI_ONLY_MODES = frozenset({"convert", "batch"})
#: The one sub-command that may run either way (GUI with files, or CLI with -i).
_GUI_CAPABLE_MODE = "view"

#: Flags that exist only to print something and exit: always CLI.
_CLI_BOOL_FLAGS = frozenset({"-h", "--help", "--version", "--batch-help"})

#: Every option the CLI parser accepts.  Its presence means the user is asking
#: for a command-line operation, never for a window.
_CLI_OPTIONS = frozenset({
    "-i", "--input",
    "-o", "--output",
    "--width", "--height", "--offset",
    "-t", "--target",
    "--raw-type", "--yuv-type",
    "--alignment", "--endianness",
    "--source-mode", "--bayer-pattern",
    "--preview-mode",
    "-b", "--batch-file",
    # Input-source options added with the YUV transcode support.
    "--input-type", "--input-yuv-type",
    "--input-alignment", "--input-endianness", "--input-raw-type",
})


def _has_cli_option(argv: list[str]) -> bool:
    """Whether any argument is a CLI option (accepts ``--width=800`` too)."""
    for arg in argv:
        if arg in _CLI_OPTIONS:
            return True
        if arg.startswith("--") and "=" in arg:
            if arg.split("=", 1)[0] in _CLI_OPTIONS:
                return True
    return False


def decide_target(argv: list[str]) -> str:
    """Return ``"gui"`` or ``"cli"`` for *argv* (the arguments after the exe).

    Pure and total: every possible input maps to one of the two strings.  See
    the module docstring for the table this implements.
    """
    args = list(argv)
    if not args:
        return "gui"
    # Print-and-exit flags win over everything, including a stray sub-command.
    if any(arg in _CLI_BOOL_FLAGS for arg in args):
        return "cli"

    positional = [arg for arg in args if not arg.startswith("-")]
    mode = positional[0] if positional else None

    if mode == _GUI_CAPABLE_MODE:
        # `view` alone, or `view file…`, means "open the GUI on these files".
        # Any option turns it into a command-line decode.
        if _has_cli_option(args):
            return "cli"
        if any(arg.startswith("-") for arg in args):
            # An option we do not recognise is never treated as "GUI": that is
            # exactly how `--width 800` used to launch a blocking window.
            return "cli"
        return "gui"
    if mode in _CLI_ONLY_MODES:
        return "cli"
    # Bare filename, unknown flags, or anything else: let argparse explain.
    return "cli"


def gui_files(argv: list[str]) -> list[str]:
    """Arguments to open in the GUI: *argv* without a leading ``view``."""
    args = list(argv)
    if args and args[0] == _GUI_CAPABLE_MODE:
        return args[1:]
    return args


def cli_executable() -> str:
    """Path of the CLI executable that sits next to this one.

    Under PyInstaller ``sys.executable`` is the running ``raw-view.exe``, so the
    sibling lives in the same directory.  A source checkout has no sibling
    bundle; :func:`_cli_command` falls back to ``python -m raw_view`` there, so
    the same forwarding logic is testable without building anything.
    """
    return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), CLI_EXE)


def _wrap_std_handle(index: int):
    """Wrap an inherited Windows std handle as a text stream, or ``None``.

    Must run **before** any ``AttachConsole`` call: attaching replaces the
    inherited handles, discarding redirected/piped ones.
    """
    import msvcrt

    kernel32 = _kernel32()
    handle = kernel32.GetStdHandle(index)
    # GetStdHandle returns 0 (NULL) or -1 (INVALID_HANDLE_VALUE) when the
    # process has no console; a GUI-subsystem image always starts that way.
    if handle in (None, 0, -1):
        return None
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if index == -10 else os.O_WRONLY)
    except OSError:
        return None
    return os.fdopen(
        fd,
        "r" if index == -10 else "w",
        buffering=1,
        closefd=False,  # the handle belongs to the parent / console
    )


def _kernel32():
    """The ``kernel32`` DLL handle (Windows only)."""
    import ctypes

    return ctypes.windll.kernel32


def _forward(argv: list[str]) -> int:
    """Run the CLI executable with *argv*, wired to whatever std streams exist.

    The ordering here is the whole point:

    1. Wrap the inherited handles **first**.  If the process was started with
       ``> out.txt`` or ``| findstr``, those handles are already valid and
       wrapping them preserves the redirection.
    2. Only when there is no usable stdout *and* ``AttachConsole`` succeeds do
       we re-wrap.  That is the double-click-from-a-terminal case: attaching
       gives the child a console, at the cost of replacing inherited handles --
       hence step 1 must not be skipped.

    Doing it the other way round (attach, then wrap) yields zero bytes in both
    the redirect and pipe cases, which is what naive versions of this file get
    wrong.
    """
    stdin = stdout = stderr = None
    if sys.platform == "win32":
        stdin = _wrap_std_handle(-10)
        stdout = _wrap_std_handle(-11)
        stderr = _wrap_std_handle(-12)
        if stdout is None:
            try:
                attached = _kernel32().AttachConsole(-1)  # ATTACH_PARENT_PROCESS
            except (AttributeError, OSError):
                attached = 0
            if attached:
                stdin = _wrap_std_handle(-10)
                stdout = _wrap_std_handle(-11)
                stderr = _wrap_std_handle(-12)

    command = _cli_command(argv)
    try:
        completed = subprocess.run(
            command,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        # Attaching a console makes it ours; releasing it lets the shell prompt
        # return instead of hanging behind an apparently-live child.
        for stream in (stdin, stdout, stderr):
            if stream is not None:
                try:
                    stream.flush()
                except Exception:  # noqa: BLE001 - flushing is best effort
                    pass
    return completed.returncode


def _cli_command(argv: list[str]) -> list[str]:
    """The command line for the CLI child (bundle exe, or source fallback)."""
    if getattr(sys, "frozen", False):
        return [cli_executable(), *argv]
    # Source checkout: behave like `python -m raw_view.cli_entry <args>`.
    return [sys.executable, "-m", "raw_view.cli_entry", *argv]


def run(argv: list[str] | None = None) -> int:
    """Dispatch *argv* and return the process exit code.

    GUI: start ``raw-view-gui.exe`` detached and return 0 immediately, so a
    terminal that launched us is not held open for the lifetime of the window.
    CLI: forward and propagate the child's exit code.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if decide_target(args) == "gui":
        return _launch_gui(gui_files(args))
    return _forward(args)


def _launch_gui(files: list[str]) -> int:
    """Start the GUI executable without waiting for it."""
    if getattr(sys, "frozen", False):
        command = [os.path.join(
            os.path.dirname(os.path.abspath(sys.executable)), GUI_EXE
        ), *files]
    else:  # pragma: no cover - only in a source checkout
        command = [sys.executable, "-m", "raw_view.gui_entry", *files]
    try:
        subprocess.Popen(command, close_fds=True)
    except OSError as exc:
        print(f"Error: cannot start the GUI executable: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
