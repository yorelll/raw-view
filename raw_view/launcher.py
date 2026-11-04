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

This routing table is about *which executable* runs.  The "never a window for a
command line" promise is enforced one layer down as well:
``raw_view.__main__._enforce_mode_word`` rejects a command line that carries CLI
options but no mode word (exit 2).  Both halves are needed -- routing decides
which binary, the guard decides what it does.

Console ownership
-----------------
``raw-view.exe`` is built ``--windowed`` (PE subsystem 2) so double-clicking it
never flashes a console window.  The side effect is that **cmd does not wait for
it**: Windows decides "wait for this process" from the subsystem of the image it
started, and an interactive ``cmd.exe`` prompt returns as soon as a GUI-subsystem
image is launched.  (Inside a ``.bat`` file cmd waits for everything, so the
symptom only shows at the prompt -- which is exactly where users hit it.)

Everything printed by the CLI therefore has to finish *before* the launcher
exits, or it lands after cmd has already drawn the next prompt -- output appears
to arrive in the wrong place, and the terminal looks stuck until a keypress
flushes it.  Two rules keep that window closed:

* the forwarding child is waited for (``subprocess.run``), and
* a console we attached ourselves is released (``FreeConsole``) as soon as the
  child exits, instead of lingering for the launcher's remaining lifetime.

Redirection and piping are unaffected: those handles are inherited, never
attached, and are left exactly as the parent gave them.
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
_CLI_BOOL_FLAGS = frozenset({
    "-h", "--help", "--version",
    "--help-view", "--help-convert", "--help-batch", "--batch-help",
})

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
        fd = msvcrt.open_osfhandle(
            handle, os.O_RDONLY if index == -10 else os.O_WRONLY
        )
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

    Two further rules, both learned from interactive-cmd behaviour
    (see the module docstring, "Console ownership"):

    3. The console is only ever attached for the *duration* of the child and is
       released before returning, so the launcher never keeps a console it did
       not start with.
    4. Whatever we attached, we release; whatever the parent gave us, we leave
       alone.  Tearing down an inherited console would kill the user's terminal.
    """
    console = _ConsoleHandles()
    try:
        completed = subprocess.run(
            _cli_command(argv),
            stdin=console.stream(-10),
            stdout=console.stream(-11),
            stderr=console.stream(-12),
        )
    finally:
        console.release()
    return completed.returncode


class _ConsoleHandles:
    """The std streams a child should inherit, and the console's fate.

    Kept as a small object (rather than five locals in :func:`_forward`) because
    the lifetime rule is subtle: whatever we attach we must release, and only
    what we attached.
    """

    def __init__(self) -> None:
        self._streams: dict[int, object] = {}
        self._attached = False
        if sys.platform != "win32":
            return
        for index in (-10, -11, -12):
            stream = _wrap_std_handle(index)
            if stream is not None:
                self._streams[index] = stream
        if -11 not in self._streams:
            # No usable stdout: we were probably double-clicked (or launched
            # from a shell that gave us no handles).  Borrow the parent's
            # console if there is one -- and remember that we did, so the
            # console is released again before this process exits.
            try:
                attached = _kernel32().AttachConsole(-1)  # ATTACH_PARENT_PROCESS
            except (AttributeError, OSError):
                attached = 0
            if attached:
                self._attached = True
                # GetStdHandle now returns the *console* handles; wrap those.
                for index in (-10, -11, -12):
                    stream = _wrap_std_handle(index)
                    if stream is not None:
                        self._streams[index] = stream

    def stream(self, index: int):
        """The text stream for std handle *index*, or ``None``."""
        return self._streams.get(index)

    def release(self) -> None:
        """Flush what we hold and release a console we attached ourselves.

        ``FreeConsole`` is the part interactive cmd cares about.  A GUI-subsystem
        image that attaches to its parent's console keeps a console *reference*
        for its whole lifetime; while that reference is alive the shell cannot
        hand the console back to the prompt, so the user sees the prompt return
        (cmd does not wait for a GUI-subsystem image) while the child is still
        writing -- output lands after the prompt, out of order.  Releasing the
        console as soon as the child is done removes that window.

        Only a console *we* attached is released: calling ``FreeConsole`` on one
        inherited from the parent would tear down the user's terminal.
        """
        for stream in self._streams.values():
            try:
                stream.flush()
            except Exception:  # noqa: BLE001 - flushing is best effort
                pass
        self._streams.clear()
        if not self._attached:
            return
        self._attached = False
        try:
            _kernel32().FreeConsole()
        except (AttributeError, OSError):  # pragma: no cover - defensive
            pass


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
