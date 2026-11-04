"""Run the unittest suite under a watchdog that dumps stacks when it hangs.

Why this exists
---------------
The suite occasionally stops dead -- no assertion failure, no traceback, no
further log lines -- because a single ``QApplication.processEvents()`` inside a
test's wait loop never returns.  ``tests/test_batch_open_and_chrome.py``'s
``_pump`` only checks its deadline *between* ``processEvents()`` calls, so a
call that blocks forever defeats the timeout entirely (see the CI runs where a
job sat in_progress for over an hour).

A plain ``timeout-minutes`` in the workflow caps the waste but tells us nothing:
the job is killed with no evidence about *where* it stopped.  This wrapper adds
that evidence -- after ``RAW_VIEW_TEST_WATCHDOG_SECONDS`` the process prints
every thread's Python stack and exits non-zero, so the next failure names the
blocking frame instead of just "timed out".

Usage (CI):
    python tools/ci_test_watchdog.py

Locally the same effect is available without this wrapper:
    python -X faulthandler -m unittest discover -s tests -q
"""

from __future__ import annotations

import faulthandler
import os
import pathlib
import runpy
import sys

#: Seconds of silence before the stack dump.  The full suite takes roughly
#: 3-4 minutes on CI, so 10 minutes reliably separates "slow" from "hung".
DEFAULT_TIMEOUT = 600


def main(argv: list[str]) -> int:
    timeout = int(os.environ.get("RAW_VIEW_TEST_WATCHDOG_SECONDS", DEFAULT_TIMEOUT))
    faulthandler.enable()

    # ``exit=True`` terminates the interpreter after dumping; the dump goes to
    # stderr, which the runner captures, so the stack survives into the job log.
    faulthandler.dump_traceback_later(timeout, exit=True)

    # ``runpy`` sets sys.path[0] to the *module's* directory, which is not the
    # project root here -- `unittest discover -s tests` then fails to import the
    # test modules.  Put the repo root (and tests/, which holds the shared
    # helpers the modules import directly) back on the path explicitly.
    root = pathlib.Path(__file__).resolve().parent.parent
    for entry in (str(root), str(root / "tests")):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    sys.argv = ["unittest", "discover", "-s", "tests", "-q", *argv]
    try:
        runpy.run_module("unittest", run_name="__main__")
    except SystemExit as exc:  # unittest exits non-zero when tests fail
        return int(exc.code or 0)
    finally:
        faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
