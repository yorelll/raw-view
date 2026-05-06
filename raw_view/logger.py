"""Logging setup for raw-view — file + console logger."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _default_log_dir() -> Path:
    """Platform-appropriate log directory."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "raw-view" / "logs"


_LOG_DIR = _default_log_dir()
_LOG_FILE = _LOG_DIR / "raw-view.log"

_initialized = False


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
        Global logging level (default ``DEBUG``).
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

    logger.setLevel(level)
    logger.propagate = False

    # Determine log directory
    log_path = _LOG_DIR if log_dir is None else Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    log_file = log_path / "raw-view.log"

    # -- File handler (DEBUG) --
    try:
        fh = RotatingFileHandler(
            str(log_file), maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)
    except OSError:
        pass  # Non-critical — silently skip file logging if dir is unwritable

    # -- Console handler (INFO) --
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
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
