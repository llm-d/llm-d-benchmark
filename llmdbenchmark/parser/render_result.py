"""Error tracking and result types for the plan rendering pipeline."""

from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class StackErrors:
    """Per-stack error accumulator for rendering, YAML validation, and missing fields."""

    render_errors: list[str] = field(default_factory=list)
    yaml_errors: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return bool(self.render_errors or self.yaml_errors or self.missing_fields)


@dataclass
class RenderResult:
    """Aggregated rendering result with global + per-stack errors and output paths."""

    global_errors: list[str] = field(default_factory=list)
    stacks: dict[str, StackErrors] = field(default_factory=dict)
    rendered_paths: list[Path] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        if self.global_errors:
            return True
        return any(stack.has_errors for stack in self.stacks.values())

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict."""
        return {
            "has_errors": self.has_errors,
            "global": self.global_errors,
            "stacks": {
                name: {
                    "render_errors": stack.render_errors,
                    "yaml_errors": stack.yaml_errors,
                    "missing_fields": stack.missing_fields,
                    "validation_warnings": stack.validation_warnings,
                }
                for name, stack in self.stacks.items()
            },
            "rendered_paths": [str(p) for p in self.rendered_paths],
        }
