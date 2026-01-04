"""
llmdbenchmark.parser.render_result

Structured data classes for error tracking and results of rendering llmdbenchmark stack plans.

This module provides two primary dataclasses:

- StackErrors:
    Tracks errors for a single stack during the rendering process.
    Includes rendering errors, YAML validation errors, and missing required fields.

- RenderResult:
    Aggregates global errors and per-stack errors.
    Tracks successfully rendered paths.
    Provides helper methods and properties to check for errors and serialize to dictionary.

These classes are used by RenderPlans to report structured results of
template rendering, YAML validation, and stack processing.
"""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class StackErrors:
    """
    Track errors for a single stack during the rendering process.

    Attributes:
        render_errors (list[str]): Errors encountered while rendering templates.
        yaml_errors (list[str]): Errors found during YAML validation of rendered output.
        missing_fields (list[str]): Any required fields missing from the stack configuration.

    Properties:
        has_errors (bool): True if any errors are present for this stack.
    """

    render_errors: list[str] = field(default_factory=list)
    yaml_errors: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """
        Check if the stack has any errors.

        Returns:
            bool: True if there are any rendering errors, YAML validation errors,
                or missing required fields; False otherwise.
        """
        return bool(self.render_errors or self.yaml_errors or self.missing_fields)


@dataclass
class RenderResult:
    """
    Structured result of a rendering operation, including error tracking and rendered file paths.

    Attributes:
        global_errors (list[str]): Errors not tied to a specific stack (e.g., file load issues).
        stacks (dict[str, StackErrors]): Mapping of stack names to their corresponding errors.
        rendered_paths (list[Path]): Paths to successfully rendered files.

    Properties:
        has_errors (bool): True if there are any global or stack-specific errors.

    Methods:
        to_dict() -> dict:
            Convert the RenderResult into a dictionary suitable for JSON serialization.
    """

    global_errors: list[str] = field(default_factory=list)
    stacks: dict[str, StackErrors] = field(default_factory=dict)
    rendered_paths: list[Path] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """
        Check if the rendering operation has any errors.

        Returns:
            bool: True if there are any global errors or any stack has errors.
        """
        if self.global_errors:
            return True
        return any(stack.has_errors for stack in self.stacks.values())

    def to_dict(self) -> dict:
        """
        Convert the RenderResult to a dictionary for JSON serialization.

        Returns:
            dict: Dictionary containing:
                - 'has_errors': bool
                - 'global': list of global errors
                - 'stacks': dictionary mapping stack names to their errors
                - 'rendered_paths': list of rendered file paths as strings
        """
        return {
            "has_errors": self.has_errors,
            "global": self.global_errors,
            "stacks": {
                name: {
                    "render_errors": stack.render_errors,
                    "yaml_errors": stack.yaml_errors,
                    "missing_fields": stack.missing_fields,
                }
                for name, stack in self.stacks.items()
            },
            "rendered_paths": [str(p) for p in self.rendered_paths],
        }
