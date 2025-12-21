"""
Logging utilities for llmdbenchmark.
"""

import logging
import os
import sys
import json

from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Union

from llmdbenchmark.logging.exceptions import (
    LLMDBenchmarkError,
    TemplateError,
    ConfigurationError,
    ExecutionError,
)


class EmojiFormatter(logging.Formatter):
    EMOJI_MAP = {
        logging.ERROR: "❌",
        logging.WARNING: "⚠️",
        logging.INFO: "ℹ️",
        logging.DEBUG: "🔍",
    }

    def format(self, record: logging.LogRecord) -> str:
        emoji = getattr(record, "emoji", None)
        if emoji is None:
            emoji = self.EMOJI_MAP.get(record.levelno, "")

        if emoji:
            msg = record.getMessage()
            record.msg = f"{emoji} - {msg}"
            record.args = ()

        return super().format(record)


# ============================================================================
# Error Handlers
# ============================================================================


def raise_on_error(
    message: str, exception: Optional[LLMDBenchmarkError] = None
) -> None:
    """
    Default error handler that raises an exception.

    Args:
        message: The error message
        exception: Optional exception instance with context. If None, raises generic LLMDBenchmarkError

    Raises:
        LLMDBenchmarkError: Always raises with the provided message or exception
    """
    if exception:
        raise exception
    raise LLMDBenchmarkError(message)


def exit_on_error(message: str, exception: Optional[LLMDBenchmarkError] = None) -> None:
    """
    Error handler that exits the process.

    Args:
        message: The error message (logged before exit)
        exception: Optional exception instance (unused but kept for signature consistency)
    """
    sys.exit(1)


def ignore_error(message: str, exception: Optional[LLMDBenchmarkError] = None) -> None:
    """
    Error handler that does nothing (ignores the error).

    Args:
        message: The error message (unused)
        exception: Optional exception instance (unused)
    """


# ============================================================================
# Logger Setup
# ============================================================================


def setup_logger(name: str = "llmdbenchmark", verbose: bool = False) -> logging.Logger:
    """
    Set up a logger with console and file handlers.

    Args:
        name: Logger name

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = EmojiFormatter(
        fmt="%(asctime)s,%(msecs)03d - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def log_blank(logger: Optional[logging.Logger] = None):
    logger = logger or setup_logger()
    for handler in logger.handlers:
        handler.stream.write("\n")
        handler.flush()


def get_log_path(logfile: Optional[str] = None) -> Path:
    """
    Get the full path to the log file.

    Args:
        logfile: Optional log filename. If None, uses current step name.

    Returns:
        Path object for the log file
    """
    work_dir = os.getenv("LLMDBENCH_CONTROL_WORK_DIR", ".")
    log_dir = Path(work_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if not logfile:
        cur_step = os.getenv("CURRENT_STEP_NAME", "step")
        logfile = f"{cur_step}.log"

    return log_dir / logfile


# ============================================================================
# Core Logging Functions
# ============================================================================


def announce(
    message: str,
    level: Union[int, str] = logging.INFO,
    logfile: Optional[str] = None,
    error_handler: Optional[Callable[[str, Optional[LLMDBenchmarkError]], None]] = None,
    exception: Optional[LLMDBenchmarkError] = None,
    logger: Optional[logging.Logger] = None,
    verbose: bool = False,
    **log_kwargs,
) -> None:
    """
    Log a message to console and file with appropriate emoji prefix.

    Args:
        message: The message to log
        level: Log level (logging.INFO, logging.ERROR, etc. or 'INFO', 'ERROR', etc.)
        logfile: Optional log filename. If None, uses current step name
        error_handler: Callback function to handle errors. If None, raises exception.
                      Use ignore_error, exit_on_error, or a custom handler.
        exception: Optional LLMDBenchmarkError instance with additional context
        logger: Optional logger instance. If None, uses default logger
    """
    if logger is None:
        logger = setup_logger(verbose=verbose)

    if error_handler is None:
        error_handler = raise_on_error

    # Normalize level to integer
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # If we have an exception with context, log the full details at debug level
    if exception and exception.context:
        logger.debug(
            "Error context: %s", json.dumps(exception.to_dict(), indent=2, **log_kwargs)
        )

    logger.log(level, message, **log_kwargs)

    # Write everything BUT ERROR to main log
    if level < logging.ERROR:
        logpath = get_log_path(logfile)
        _write_to_logfile(logpath, level, message, exception, logger)

    if level == logging.ERROR:
        logpath = get_log_path("stderr")
        _write_to_logfile(logpath, level, message, exception, logger)
        error_handler(message, exception)


def _write_to_logfile(
    logpath: Path,
    level: int,
    message: str,
    exception: Optional[LLMDBenchmarkError],
    logger: logging.Logger,
) -> None:
    """
    Write a log message to a file with timestamp and emoji.

    Args:
        logpath: Path to the log file
        level: Logging level (e.g., logging.ERROR)
        message: The message to write
        exception: Optional exception with additional context
        logger: Logger instance for error reporting
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        emoji = EmojiFormatter.EMOJI_MAP.get(level, "")
        if emoji:
            log_line = f"{timestamp} : {emoji}  {message}"
        else:
            log_line = f"{timestamp} : {message}"

        # If we have an exception, add context to the log file
        if exception:
            log_line += f"\n  Exception Type: {exception.__class__.__name__}"
            log_line += f"\n  Step: {exception.step}"
            if exception.context:
                log_line += f"\n  Context: {json.dumps(exception.context, indent=4)}"

        with open(logpath, "a", encoding="utf-8") as log_file:
            log_file.write(log_line + "\n")

    except IOError as exc:
        logger.error("Could not write to log file '%s'. Reason: %s", logpath, exc)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error(
            "An unexpected error occurred with logfile '%s'. Reason: %s", logpath, exc
        )


# ============================================================================
# Convenience Functions
# ============================================================================


def log_info(message: str, **kwargs) -> None:
    """Log an info message."""
    announce(message, level=logging.INFO, **kwargs)


def log_warning(message: str, **kwargs) -> None:
    """Log a warning message."""
    announce(message, level=logging.WARNING, **kwargs)


def log_error(
    message: str,
    error_handler: Optional[Callable[[str, Optional[LLMDBenchmarkError]], None]] = None,
    exception: Optional[LLMDBenchmarkError] = None,
    **kwargs,
) -> None:
    """
    Log an error message.

    Args:
        message: The error message
        error_handler: Optional error handler (default raises exception)
        exception: Optional LLMDBenchmarkError with additional context
        **kwargs: Additional arguments passed to announce
    """
    announce(
        message,
        level=logging.ERROR,
        error_handler=error_handler,
        exception=exception,
        **kwargs,
    )


def log_debug(message: str, **kwargs) -> None:
    """Log a debug message."""
    announce(message, level=logging.DEBUG, **kwargs)
