"""Custom exceptions for llmdbenchmark."""

from datetime import datetime
from typing import Optional, Dict, Any


class LLMDBenchmarkError(Exception):
    """Base exception for all llmdbenchmark errors."""

    def __init__(
        self,
        message: str,
        step: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.step = step
        self.context = context or {}

        self.timestamp = datetime.now()

    def __str__(self) -> str:
        base_msg = f"[{self.step}] {self.message}"
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{base_msg} (Context: {context_str})"
        return base_msg

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the exception to a dict."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "step": self.step,
            "timestamp": self.timestamp.isoformat(),
            "context": self.context,
        }


class TemplateError(LLMDBenchmarkError):
    """Raised on template rendering failures (missing vars, bad syntax, etc.)."""

    def __init__(
        self,
        message: str,
        template_file: Optional[str] = None,
        missing_vars: Optional[list] = None,
        **kwargs,
    ):
        context = kwargs.get("context", {})
        if template_file:
            context["template_file"] = template_file
        if missing_vars:
            context["missing_vars"] = missing_vars
        kwargs["context"] = context
        super().__init__(message, **kwargs)


class ConfigurationError(LLMDBenchmarkError):
    """Raised on post-render configuration errors (bad YAML, missing keys, invalid values)."""

    def __init__(
        self,
        message: str,
        config_file: Optional[str] = None,
        invalid_key: Optional[str] = None,
        **kwargs,
    ):
        context = kwargs.get("context", {})
        if config_file:
            context["config_file"] = config_file
        if invalid_key:
            context["invalid_key"] = invalid_key
        kwargs["context"] = context
        super().__init__(message, **kwargs)


class ExecutionError(LLMDBenchmarkError):
    """Raised on command or runtime execution failures."""

    def __init__(
        self,
        message: str,
        command: Optional[str] = None,
        exit_code: Optional[int] = None,
        stdout: Optional[str] = None,
        stderr: Optional[str] = None,
        **kwargs,
    ):
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
