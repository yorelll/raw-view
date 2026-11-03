"""Logging setup for raw-view — file + console logger."""

from __future__ import annotations

import logging
import os
import sys
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
        fh = RotatingFileHandler(
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
