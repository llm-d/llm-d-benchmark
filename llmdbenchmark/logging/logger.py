"""Logging utilities with emoji support, stream separation, and per-instance file output."""

import logging
import sys
import uuid

from logging import StreamHandler, FileHandler
from pathlib import Path
from datetime import datetime
from typing import Optional, Union


from llmdbenchmark import __package_name__
from llmdbenchmark.utilities.os.platform import get_user_id
from llmdbenchmark.exceptions.exceptions import ConfigurationError


class EmojiFormatter(logging.Formatter):
    """Formatter that prepends emoji and uses millisecond timestamps."""

    EMOJI_MAP = {
        logging.ERROR: "❌",
        logging.WARNING: "⚠️",
        logging.INFO: "",
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
        level = f"{record.levelname:<7}"
        msg = record.getMessage()

        prefix = f"{emoji} " if emoji else ""
        formatted = f"{self.formatTime(record)} - {level} - {prefix}{msg}"
        if self.include_exc_info and record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


class LLMDBenchmarkLogger:
    """Logger with emoji support, per-instance files, console streams, and combined stdout/stderr logs."""

    _shared_stdout_handler: FileHandler | None = None
    _shared_stderr_handler: FileHandler | None = None
    _shared_log_dir: Path | None = None

    INDENT_PREFIX = "    │ "

    def __init__(self, log_dir: Path, log_name: str, verbose: bool = False):
        self._indent_level = 0
        short_uuid = uuid.uuid4().hex[:4]
        log_name_with_uuid = f"{log_name}-{short_uuid}"
        self.logger = logging.getLogger(f"{__package_name__}-{log_name_with_uuid}")

        if self.logger.hasHandlers():
            self.logger.handlers.clear()

        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        console_formatter = EmojiFormatter(include_exc_info=verbose)

        sh_out = StreamHandler(sys.stdout)
        sh_out.setLevel(logging.DEBUG if verbose else logging.INFO)
        sh_out.addFilter(lambda r: r.levelno <= logging.INFO)
        sh_out.setFormatter(console_formatter)

        sh_err = StreamHandler(sys.stderr)
        sh_err.setLevel(logging.WARNING)
        sh_err.setFormatter(console_formatter)

        self.logger.addHandler(sh_out)
        self.logger.addHandler(sh_err)

        log_path = log_dir / f"{log_name_with_uuid}-stdout.log"
        error_path = log_dir / f"{log_name_with_uuid}-stderr.log"

        file_formatter = EmojiFormatter(include_exc_info=True)

        try:
            fh = FileHandler(log_path, mode="a", encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.addFilter(lambda r: r.levelno <= logging.INFO)
            fh.setFormatter(file_formatter)

            eh = FileHandler(error_path, mode="a", encoding="utf-8")
            eh.setLevel(logging.WARNING)
            eh.setFormatter(file_formatter)

        except Exception as e:
            raise ConfigurationError(
                message="Failed to initialize per-instance file logging.",
                step="logging",
                context={"log_dir": str(log_dir), "error": str(e)},
            ) from e

        self.logger.addHandler(fh)
        self.logger.addHandler(eh)

        try:
            if self.__class__._shared_log_dir != log_dir:
                combined_stdout = FileHandler(
                    log_dir / f"{__package_name__}-stdout.log",
                    mode="a",
                    encoding="utf-8",
                )
                combined_stdout.setLevel(logging.DEBUG)
                combined_stdout.addFilter(lambda r: r.levelno <= logging.INFO)
                combined_stdout.setFormatter(file_formatter)

                combined_stderr = FileHandler(
                    log_dir / f"{__package_name__}-stderr.log",
                    mode="a",
                    encoding="utf-8",
                )
                combined_stderr.setLevel(logging.WARNING)
                combined_stderr.setFormatter(file_formatter)

                self.__class__._shared_stdout_handler = combined_stdout
                self.__class__._shared_stderr_handler = combined_stderr
                self.__class__._shared_log_dir = log_dir

            self.logger.addHandler(self.__class__._shared_stdout_handler)
            self.logger.addHandler(self.__class__._shared_stderr_handler)

        except Exception as e:
            raise ConfigurationError(
                message=f"Failed to initialize shared combined {__package_name__} stdout/stderr logs.",
                step="logging",
                context={"log_dir": str(log_dir), "error": str(e)},
            ) from e

    def set_indent(self, level: int) -> None:
        """Set the indentation level for subsequent log messages."""
        self._indent_level = max(0, level)

    def _apply_indent(self, msg: str) -> str:
        """Prepend indent prefix if indent level is set."""
        if self._indent_level > 0:
            return self.INDENT_PREFIX * self._indent_level + msg
        return msg

    def log_debug(self, msg, emoji=None):
        """Log a debug-level message with an optional emoji override."""
        self.logger.debug(self._apply_indent(msg), extra={"emoji": emoji} if emoji else {})

    def log_info(self, msg, emoji=None):
        """Log an info-level message with an optional emoji override."""
        self.logger.info(self._apply_indent(msg), extra={"emoji": emoji} if emoji else {})

    def log_warning(self, msg, emoji=None):
        """Log a warning-level message with an optional emoji override."""
        self.logger.warning(self._apply_indent(msg), extra={"emoji": emoji} if emoji else {})

    def log_error(self, msg, emoji=None, exc_info=False):
        """Log an error-level message with an optional emoji override."""
        self.logger.error(
            self._apply_indent(msg), extra={"emoji": emoji} if emoji else {}, exc_info=exc_info
        )

    def line_break(self) -> None:
        """Insert a completely blank line in the log (no timestamp or level)."""
        for handler in self.logger.handlers:
            handler.stream.write("\n")
            handler.flush()

    def log_plain(self, msg: str) -> None:
        """Write *msg* verbatim to every handler — no timestamp or level prefix.

        For human-facing payloads where the log formatting would be more
        noise than signal: copy-paste blocks, banners, ASCII art. Still
        routed through every logger handler, so the text lands in both
        the terminal and any attached FileHandler(s) — unlike print(),
        which only reaches stdout.
        """
        for handler in self.logger.handlers:
            handler.stream.write(str(msg) + "\n")
            handler.flush()


def get_logger(
    log_dir: Union[str, Path],
    verbose: Optional[bool] = False,
    log_name: Optional[str] = None,
) -> LLMDBenchmarkLogger:
    """Create a configured LLMDBenchmarkLogger, auto-generating a log_name if not provided."""
    if not log_name:
        prefix = get_user_id()
        suffix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        log_name = f"{prefix}-{suffix}"

    if log_dir is None:
        raise ConfigurationError(
            message="log_dir is required and cannot be None",
            step="logging",
            invalid_key="log_dir",
        )

    p = Path(log_dir)
    return LLMDBenchmarkLogger(p, log_name, verbose)
