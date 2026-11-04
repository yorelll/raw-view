"""Logging setup for raw-view — file + console logger."""

from __future__ import annotations

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path


#: Environment variable that redirects the log directory.  Used by tests to run
#: a real ``python -m raw_view`` against a temporary log (mirrors the existing
#: ``RAW_VIEW_TEST_SETTINGS_FILE`` isolation for QSettings) without writing to
#: the developer's own log file.
LOG_DIR_ENV = "RAW_VIEW_LOG_DIR"


def _platform_log_dir() -> Path:
    """The platform's conventional log directory (no override applied)."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "raw-view" / "logs"


def _usable_override(raw: str | None) -> Path | None:
    """Validate ``RAW_VIEW_LOG_DIR``; ``None`` means "ignore it".

    An environment variable must never be able to stop the program from
    starting.  ``python -m raw_view --version`` does not need logging at all, so
    a typo'd or unwritable ``RAW_VIEW_LOG_DIR`` previously produced a
    ``PermissionError`` traceback out of the package import -- a fake crash on
    the very command a user runs to diagnose a real one.

    Rules (mirroring ``models._explicit_test_settings_file``):
    * a relative path is ignored -- otherwise the log location silently follows
      whatever working directory the process happens to have;
    * the parent directory must already exist;
    * the directory must be creatable and writable, verified up front instead of
      failing later inside ``RotatingFileHandler``.
    """
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        return None
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if not os.access(candidate, os.W_OK):
        return None
    return candidate


def _default_log_dir() -> Path:
    """Platform-appropriate log directory (overridable via ``RAW_VIEW_LOG_DIR``).

    Falls back to the platform default whenever the override is unusable, so the
    CLI always starts; file logging degrades, never the command.
    """
    return _usable_override(os.environ.get(LOG_DIR_ENV)) or _platform_log_dir()


_LOG_DIR = _default_log_dir()
_LOG_FILE = _LOG_DIR / "raw-view.log"

_initialized = False

#: 环境变量名：以数字或级别名（DEBUG/INFO/WARNING/...）控制全局 logger 级别，
#: 覆盖 setup_logger 的默认 DEBUG。不设置时行为与旧版一致（DEBUG）。
_LEVEL_ENV = "RAW_VIEW_LOG_LEVEL"


# ---------------------------------------------------------------------------
# File handler: a rotation that cannot rename the log must never reach stderr
# ---------------------------------------------------------------------------

#: Minimum seconds between two "rotation failed" notices, so a permanently
#: locked log cannot turn the file itself into a flood.
_ROTATE_NOTICE_INTERVAL = 60.0


class SafeRotatingFileHandler(RotatingFileHandler):
    """``RotatingFileHandler`` whose failures stay out of the user's terminal.

    Two independent problems are handled here.

    **1. A failed rotation keeps the handler alive.**  ``doRollover()`` renames
    ``raw-view.log`` to ``raw-view.log.1``.  On Windows that rename fails with
    ``WinError 32`` whenever another process holds the file open -- a second
    running instance, a log viewer, a backup agent.  ``RotatingFileHandler``
    then propagates the ``PermissionError`` out of ``emit()``, ``logging`` calls
    ``Handler.handleError``, and Python prints a full ``--- Logging error ---``
    traceback *to stderr*.  For a CLI that promises one readable ``Error: ...``
    line (see ``__main__._log_failure``) that is a contract break: the user gets
    five stack traces instead of one sentence.  A failed rotation is therefore
    swallowed and the handler keeps appending to the current file -- the log
    stays complete, it just does not get rotated this time.

    **2. ``handleError`` is also neutered for this handler only.**  Rotation is
    not the only way ``emit()`` can fail (the disk can fill up, the file can be
    deleted mid-run).  ``Handler.handleError`` is a *global* switch
    (``logging.raiseExceptions``) that affects every handler, so changing it
    would silence real problems elsewhere.  Overriding it on this one instance
    keeps the blast radius to file logging.

    Errors are not hidden: they are counted, and the next *successful* write
    records a one-line notice (rate-limited) so the log itself shows the gap.
    That is why ``handleError`` sets a flag instead of simply returning.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        #: Set whenever an emit/rotation went wrong; consumed by the next write.
        self._pending_notice: str | None = None
        self._last_notice_at = 0.0
        self._writing_notice = False

    # -- emission / rotation ----------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Rotate if needed, then write -- and keep writing when rotation fails.

        ``RotatingFileHandler.emit`` wraps ``doRollover()`` and
        ``FileHandler.emit()`` in one ``try``: a failed rollover therefore skips
        the write *and* escapes to ``handleError``.  Splitting the two lets a
        locked file degrade to "no rotation this time, record still appended",
        which keeps the log complete.
        """
        try:
            if self.shouldRollover(record):
                try:
                    if os.environ.get("RAW_VIEW_TEST_FAIL_ROLLOVER"):
                        # Test-only: reproduce the Windows "file in use" failure
                        # on every platform.  POSIX renames an open file happily,
                        # so an end-to-end test cannot create this condition
                        # otherwise -- and skipping it on CI is how the Linux job
                        # silently lost coverage.
                        raise OSError(32, "injected rollover failure")
                    self.doRollover()
                except OSError as exc:
                    self._note_failure(
                        f"log rotation failed ({exc.__class__.__name__}: {exc}); "
                        f"continuing to append to {self.baseFilename}"
                    )
            # Deliberately the FileHandler implementation: the record is written
            # even when the rollover above gave up.
            logging.FileHandler.emit(self, record)
        except Exception:  # noqa: BLE001 - mirrors logging's own catch-all
            self.handleError(record)
        else:
            self._flush_pending_notice()

    # -- error reporting --------------------------------------------------

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        """Record that formatting/writing failed; never write to stderr.

        Deliberately does **not** call ``super().handleError``: the base
        implementation prints ``--- Logging error ---`` plus a traceback and a
        call stack to ``sys.stderr``.  Scoping the override to this handler (as
        opposed to the global ``logging.raiseExceptions``) leaves every other
        handler unaffected.
        """
        self._note_failure(
            f"could not write log record from {record.name}; "
            "file logging may be incomplete"
        )

    def _note_failure(self, message: str) -> None:
        """Remember a failure so the next successful write can mention it."""
        # Last one wins: a burst of identical rotation errors is one problem.
        self._pending_notice = message

    def _flush_pending_notice(self) -> None:
        """Write the deferred notice, at most once per ``_ROTATE_NOTICE_INTERVAL``.

        This is what keeps a swallowed failure observable: the log itself gains
        a ``logging degraded: ...`` line, so "the log has a gap" is discoverable
        without ever writing to the user's terminal.
        """
        notice = self._pending_notice
        if notice is None or self._writing_notice:
            return
        now = time.monotonic()
        if now - self._last_notice_at < _ROTATE_NOTICE_INTERVAL:
            return
        self._last_notice_at = now
        self._pending_notice = None
        self._writing_notice = True
        try:
            # Write through the file handler directly: going through the logger
            # would re-enter this emit().
            logging.FileHandler.emit(
                self,
                logging.LogRecord(
                    name="raw_view.logger",
                    level=logging.WARNING,
                    pathname=__file__,
                    lineno=0,
                    msg="logging degraded: %s",
                    args=(notice,),
                    exc_info=None,
                ),
            )
        except Exception:  # noqa: BLE001 - the notice must never break logging
            pass
        finally:
            self._writing_notice = False


def _parse_level(level: int | str | None) -> int:
    """把 level 参数/RAW_VIEW_LOG_LEVEL 解析成合法的 logging 级别（int）。

    - 传入数字或 None 时原样返回（None 由调用方按默认值处理）。
    - 传入字符串时兼容 ``logging.getLevelName`` 的数字形式（"20"）与
      级别名（"DEBUG"），大小写不敏感；解析失败（未知名字/空串）回退默认
      ``logging.DEBUG``。
    """
    if level is None:
        return logging.DEBUG
    if isinstance(level, int):
        return level
    text = str(level).strip()
    if not text:
        return logging.DEBUG
    # "20" / "10" 这类数字字符串由 getLevelName 直接给出 LEVELNAME 查询串，
    # 无法用于反向解析，先按数字转换。
    if text.isdigit():
        return int(text)
    return getattr(logging, text.upper(), logging.DEBUG) or logging.DEBUG


def setup_logger(
    name: str = "raw_view",
    level: int = logging.DEBUG,
    log_dir: str | None = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> logging.Logger:
    """Configure the root ``raw_view`` logger.

    Sets up both a **RotatingFileHandler** (debug level, written to
    *log_dir*/raw-view.log) and a **StreamHandler** (info level, stderr).

    Parameters
    ----------
    name : str
        Logger name (default ``raw_view``).
    level : int
        Global logging level (default ``DEBUG``). May be overridden by the
        ``RAW_VIEW_LOG_LEVEL`` environment variable (numeric or a level name
        such as ``DEBUG``/``INFO``/``WARNING``, case-insensitive).
    log_dir : str or None
        Directory for log files.  ``None`` ⇒ platform-appropriate default.
    max_bytes : int
        Maximum size per log file before rotation.
    backup_count : int
        Number of rotated log files to keep.
    """
    global _initialized
    logger = logging.getLogger(name)

    # Avoid duplicate handlers on repeated calls
    if _initialized and logger.handlers:
        return logger

    # 环境变量优先：RAW_VIEW_LOG_LEVEL 覆盖全局级别（向后兼容，
    # 未设置时保持默认 DEBUG）。
    level = _parse_level(os.environ.get(_LEVEL_ENV, level))

    logger.setLevel(level)
    logger.propagate = False

    # Determine log directory.  Creating it must never raise: an unwritable
    # location means "no file log", not "the program cannot start".  Console
    # logging (installed below) keeps working either way.
    log_path = _LOG_DIR if log_dir is None else Path(log_dir)
    log_file = log_path / "raw-view.log"
    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("Cannot create log directory %s; file logging disabled", log_path)

    # -- File handler (loglevel 级别的文件过滤) --
    # 文件 handler 固定不高于全局级别，避免低级别日志写满磁盘。
    try:
        fh = SafeRotatingFileHandler(
            str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
    except OSError:
        pass  # Non-critical — silently skip file logging if dir is unwritable

    # -- Console handler (INFO, minus the duplicated ERRORs) --
    #
    # Progress lines (``INFO: Convert: a.png -> a.raw (...)``) are wanted on the
    # terminal: they are the only feedback during a long conversion, and
    # ``RAW_VIEW_LOG_LEVEL`` is documented to control *both* the console and the
    # file handler, which only holds if this stays at INFO.  Raising the level
    # to WARNING silenced every progress line and made the environment variable a
    # no-op for the console.
    #
    # What must not reach the terminal is the *ERROR* duplicate: a rejected input
    # is logged with ``logger.error`` so it stays visible at ERROR severity in the
    # file, but the CLI already printed the same sentence as ``Error: ...``.
    # ``docs/cli.md`` promises "失败时终端只打一行 Error: ..."; the filter below
    # is what makes that true without giving up the progress lines.
    class _ConsoleFilter(logging.Filter):
        """Drop ERROR records -- the CLI prints those itself (see module docs)."""

        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno != logging.ERROR

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.addFilter(_ConsoleFilter())
    ch.setFormatter(logging.Formatter(
        "%(levelname)s: %(message)s",
    ))
    logger.addHandler(ch)

    _initialized = True
    logger.debug("Logger initialised — logging to %s", log_file)
    return logger


def get_logger(name: str = "raw_view") -> logging.Logger:
    """Return the *name* logger, setting it up on first access."""
    logger = logging.getLogger(name)
    if not _initialized:
        return setup_logger(name)
    return logger
