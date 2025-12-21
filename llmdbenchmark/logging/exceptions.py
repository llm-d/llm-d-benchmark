"""
Custom exceptions for llmdbenchmark.
"""

from datetime import datetime
from typing import Optional, Dict, Any


class LLMDBenchmarkError(Exception):
    """
    Base exception for LLMDBench errors.

    Attributes:
        message: The error message
        step: Optional step name where the error occurred
        timestamp: When the error occurred
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        step: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the exception.

        Args:
            message: The error message
            step: The step name where the error occurred
            context: Additional context information (e.g., config values, file paths)
        """
        super().__init__(message)
        self.message = message
        self.step = step
        self.context = context or {}

        self.timestamp = datetime.now()

    def __str__(self) -> str:
        """Return a formatted error message."""
        base_msg = f"[{self.step}] {self.message}"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base_msg} (Context: {context_str})"
        return base_msg

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for logging/serialization."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step": self.step,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
        }


class TemplateError(LLMDBenchmarkError):
    """
    Raised when there are template rendering errors.
    While attempting to render templates

    Examples:
        - Missing template variables
        - Invalid Jinja2 syntax
        - Template file not found
    """

    def __init__(
        self,
        message: str,
        template_file: Optional[str] = None,
        missing_vars: Optional[list] = None,
        **kwargs,
    ):
        """
        Initialize template error.

        Args:
            message: The error message
            template_file: Path to the template file
            missing_vars: List of missing template variables
            **kwargs: Additional context passed to parent
        """
        context = kwargs.get("context", {})
        if template_file:
            context["template_file"] = template_file
        if missing_vars:
            context["missing_vars"] = missing_vars
        kwargs["context"] = context
        super().__init__(message, **kwargs)


class ConfigurationError(LLMDBenchmarkError):
    """
    Raised when there are configuration-related errors.
    This is AFTER the contents are rendered.

    Examples:
        - Invalid YAML syntax
        - Missing required configuration keys
        - Invalid configuration values
        - Conflicting configuration options
    """

    def __init__(
        self,
        message: str,
        config_file: Optional[str] = None,
        invalid_key: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize configuration error.

        Args:
            message: The error message
            config_file: Path to the configuration file
            invalid_key: The configuration key that caused the error
            **kwargs: Additional context passed to parent
        """
        context = kwargs.get("context", {})
        if config_file:
            context["config_file"] = config_file
        if invalid_key:
            context["invalid_key"] = invalid_key
        kwargs["context"] = context
        super().__init__(message, **kwargs)


class ExecutionError(LLMDBenchmarkError):
    """
    Raised when there are execution-related errors.

    Examples:
        - Command execution failures
        - Resource allocation failures
        - Runtime errors during benchmarking
        - Service connection failures
    """

    def __init__(
        self,
        message: str,
        command: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize execution error.

        Args:
            message: The error message
            command: The command that failed
            exit_code: Exit code from the failed command
            stdout: Standard output from the command
            stderr: Standard error from the command
            **kwargs: Additional context passed to parent
        """
        context = kwargs.get("context", {})
        if command:
            context["command"] = command
        if exit_code is not None:
            context["exit_code"] = exit_code
        if stdout:
            context["stdout"] = stdout
        if stderr:
            context["stderr"] = stderr
        kwargs["context"] = context
        super().__init__(message, **kwargs)
