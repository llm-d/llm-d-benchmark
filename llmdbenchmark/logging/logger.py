"""
logger.py - Custom logging utilities for llmdbenchmark with emoji support and Unix-style stream separation.

This module provides a flexible logging system tailored for llmdbenchmark, designed to:

- Separate console output by stream:
    - DEBUG and INFO messages → `stdout`
    - WARNING, ERROR, and CRITICAL messages → `stderr`
- Provide optional emoji prefixes for log levels to improve readability
- Include millisecond-precision timestamps in all messages
- Log to files with full tracebacks for later inspection:
    - `{log_name}-stdout.log`: DEBUG and INFO messages
    - `{log_name}-stderr.log`: WARNING, ERROR, and CRITICAL messages
- Keep console output clean by suppressing tracebacks unless verbose mode is enabled
- Isolate logger instances by `log_name` to avoid conflicts in multi-region or multi-run scenarios

Features:

- **Multi-stream console logging**: Uses separate `StreamHandler`s for stdout and stderr with level filters.
- **File logging**: Uses `FileHandler`s to persist logs per severity level with full traceback support.
- **Emoji support**: Prepend emojis to messages by default or via optional overrides.
- **Thread-safety note**: Handlers themselves are thread-safe, but the logger does not synchronize access across
    multiple threads writing to the same files. Use with care in concurrent scenarios.
"""

import logging
import sys
import uuid

from logging import StreamHandler, FileHandler
from pathlib import Path
from datetime import datetime
from typing import Optional

from llmdbenchmark.utilities.os.platform import get_user_id


class EmojiFormatter(logging.Formatter):
    """Formatter that prepends emoji and uses millisecond timestamps."""

    EMOJI_MAP = {
        logging.ERROR: "❌",
        logging.WARNING: "⚠️",
        logging.INFO: "ℹ️",
        logging.DEBUG: "🔍",
    }

    def __init__(self, include_exc_info=True):
        super().__init__()
        self.include_exc_info = include_exc_info

    def formatTime(self, record, datefmt=None):
        ct = self.converter(record.created)
        return (
            f"{ct.tm_year:04d}-{ct.tm_mon:02d}-{ct.tm_mday:02d} "
            f"{ct.tm_hour:02d}:{ct.tm_min:02d}:{ct.tm_sec:02d},{int(record.msecs):03d}"
        )

    def format(self, record):
        emoji = getattr(record, "emoji", None)
        if emoji is None:
            emoji = self.EMOJI_MAP.get(record.levelno, "")
        level = record.levelname.upper()
        msg = record.getMessage()

        formatted = f"{self.formatTime(record)} - {level} - {emoji} {msg}"
        if self.include_exc_info and record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


class LLMDBenchmarkLogger:
    """Logger with emoji support and separate stdout/stderr files."""

    def __init__(self, log_dir: Path, log_name: str, verbose: bool = False):
        short_uuid = uuid.uuid4().hex[:4]
        log_name_with_uuid = f"{log_name}-{short_uuid}"
        self.logger = logging.getLogger(f"LLMDBenchmarkLogger_{log_name_with_uuid}")
        if self.logger.hasHandlers():
            self.logger.handlers.clear()
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        log_path = log_dir / f"{log_name_with_uuid}-stdout.log"
        error_path = log_dir / f"{log_name_with_uuid}-stderr.log"

        # Formatters
        console_formatter = EmojiFormatter(include_exc_info=verbose)
        file_formatter = EmojiFormatter(include_exc_info=True)

        # Console handlers
        sh_out = StreamHandler(sys.stdout)
        sh_out.setLevel(logging.DEBUG)
        sh_out.addFilter(lambda r: r.levelno <= logging.INFO)
        sh_out.setFormatter(console_formatter)

        sh_err = StreamHandler(sys.stderr)
        sh_err.setLevel(logging.WARNING)
        sh_err.setFormatter(console_formatter)

        # File handlers
        fh = FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.addFilter(lambda r: r.levelno < logging.WARNING)
        fh.setFormatter(file_formatter)

        eh = FileHandler(error_path, mode="a", encoding="utf-8")
        eh.setLevel(logging.WARNING)
        eh.setFormatter(file_formatter)

        # Attach handlers
        self.logger.addHandler(sh_out)
        self.logger.addHandler(sh_err)
        self.logger.addHandler(fh)
        self.logger.addHandler(eh)

    def log_debug(self, msg, emoji=None):
        """
        Log a debug message.

        Args:
            msg: The message to log.
            emoji: Optional custom emoji to override the default 🔍.
        """
        self.logger.debug(msg, extra={"emoji": emoji} if emoji else {})

    def log_info(self, msg, emoji=None):
        """
        Log an info message.

        Args:
            msg: The message to log.
            emoji: Optional custom emoji to override the default ℹ️.
        """
        self.logger.info(msg, extra={"emoji": emoji} if emoji else {})

    def log_warning(self, msg, emoji=None):
        """
        Log a warning message.

        Args:
            msg: The message to log.
            emoji: Optional custom emoji to override the default ⚠️.
        """
        self.logger.warning(msg, extra={"emoji": emoji} if emoji else {})

    def log_error(self, msg, emoji=None, exc_info=False):
        """
        Log an error message.

        Args:
            msg: The message to log.
            emoji: Optional custom emoji to override the default ❌.
            exc_info: If True, include exception traceback in file logs
                      (not console to make logs clean).
        """
        self.logger.error(
            msg, extra={"emoji": emoji} if emoji else {}, exc_info=exc_info
        )

    def line_break(self) -> None:
        """Insert a completely blank line in the log (no timestamp or level)."""
        for handler in self.logger.handlers:
            handler.stream.write("\n")
            handler.flush()


def get_logger(
    log_dir: Path, verbose: bool, log_name: Optional[str] = None
) -> LLMDBenchmarkLogger:
    """
    Create or retrieve a configured LLMDBenchmarkLogger instance.

    This function simplifies logger creation by automatically generating a unique
    `log_name` if none is provided, ensuring that multiple logger instances
    remain isolated and do not conflict.

    Args:
        log_dir (Path): Directory where log files will be stored. Must exist or be
            creatable by the caller.
        verbose (bool): If True, console output will include full tracebacks for
            warnings and errors; otherwise, console output remains clean.
        log_name (Optional[str]): Optional name for the logger and its log files.
            If not provided, a unique name is generated using the current user ID
            and timestamp (format: YYYYMMDD-HHMMSS-fff).

    Returns:
        LLMDBenchmarkLogger: A fully configured logger instance with:
            - Console logging split by stream (stdout/stderr)
            - File logging split by severity (stdout/stderr log files)
            - Emoji-prefixed messages
            - Millisecond-precision timestamps

    Example:
        ```python
        from llmdbenchmark.utilities.logging import get_logger
        from pathlib import Path

        log_dir = Path("/tmp/llmd_logs")
        logger = get_logger(log_dir, verbose=True)

        logger.log_info("Benchmark started")
        logger.log_warning("Potential performance issue")
        logger.log_error("Benchmark failed", exc_info=True)
        ```

    Notes:
        - Each call with a unique `log_name` creates an independent logger instance.
        - Log files will be created under `log_dir` with names based on `log_name`:
            `{log_name}-stdout.log` and `{log_name}-stderr.log`.
        - Console output adheres to Unix conventions:
            - DEBUG/INFO → stdout
            - WARNING/ERROR/CRITICAL → stderr
    """
    if not log_name:
        prefix = get_user_id()
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        log_name = f"{prefix}-{suffix}"
    return LLMDBenchmarkLogger(log_dir, log_name, verbose)
