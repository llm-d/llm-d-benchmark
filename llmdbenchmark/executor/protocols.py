"""Protocol definitions for the executor framework.

Provides structural typing (duck-typing with IDE support) for components
that are passed around via the ``ExecutionContext`` but may have multiple
implementations (e.g. rich CLI logger vs. minimal fallback logger).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LoggerProtocol(Protocol):
    """Structural interface for loggers used throughout the pipeline.

    Any object that implements these four methods (with compatible
    signatures) satisfies the protocol -- no explicit inheritance needed.
    Both ``_MinimalLogger`` in ``command.py`` and the rich CLI logger
    in ``cli.py`` conform to this interface.
    """

    def log_info(self, msg: str, *, emoji: str = "") -> None:
        """Log an informational message."""
        ...

    def log_warning(self, msg: str) -> None:
        """Log a warning message."""
        ...

    def log_error(self, msg: str) -> None:
        """Log an error message."""
        ...

    def set_indent(self, level: int) -> None:
        """Set the indentation level for subsequent messages."""
        ...
