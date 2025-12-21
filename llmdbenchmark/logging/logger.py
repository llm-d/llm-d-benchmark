"""
Custom logging utilities for llmdbenchmark with emoji support.

This module provides a logging system that creates multiple log files per logger instance:
- `{region_name}-stdout.log`: Contains DEBUG and INFO level messages
- `{region_name}-stderr.log`: Contains WARNING and ERROR level messages
- Console output: Clean messages without tracebacks (verbosity controlled by verbose flag)

Each logger instance is isolated by region name, allowing multiple independent loggers
to write to different sets of files simultaneously.
"""

import logging
from logging import StreamHandler, FileHandler
from pathlib import Path


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
        # Emoji: custom or default
        emoji = getattr(record, "emoji", None)
        if emoji is None:
            emoji = self.EMOJI_MAP.get(record.levelno, "")
        level = record.levelname.upper()
        msg = record.getMessage()

        formatted = f"{self.formatTime(record)} - {level} - {emoji} {msg}"

        # Only include exception info if configured to do so
        if self.include_exc_info and record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


class LLMDBenchmarkLogger:
    """Logger with emoji support and separate stdout/stderr files."""

    def __init__(self, log_dir: Path, region_name: str, verbose: bool = False):
        self.logger = logging.getLogger(f"LLMDBenchmarkLogger_{region_name}")

        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        log_path = log_dir / f"{region_name}-stdout.log"
        error_path = log_dir / f"{region_name}-stderr.log"

        # Console: we will ONLY log tracebacks in console if verbose is True
        console_formatter = EmojiFormatter(include_exc_info=verbose)

        # Files: full messages with tracebacks
        file_formatter = EmojiFormatter(include_exc_info=True)

        # Stream handler - clean output, no tracebacks
        sh = StreamHandler()
        sh.setLevel(logging.DEBUG if verbose else logging.INFO)
        sh.setFormatter(console_formatter)

        # General log file - full details
        fh = FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.addFilter(lambda record: record.levelno < logging.WARNING)
        fh.setFormatter(file_formatter)

        # Error log file - full details with tracebacks
        eh = FileHandler(error_path, mode="a", encoding="utf-8")
        eh.setLevel(logging.WARNING)
        eh.setFormatter(file_formatter)

        self.logger.addHandler(sh)
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
