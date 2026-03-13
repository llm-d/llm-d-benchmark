"""Step definitions and result types for the executor framework."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import re

import yaml


class Phase(Enum):
    """Benchmark lifecycle phases."""

    STANDUP = "standup"
    RUN = "run"
    TEARDOWN = "teardown"


@dataclass
class StepResult:
    """Result of executing a single step."""

    step_number: int
    step_name: str
    success: bool
    message: str = ""
    errors: list[str] = field(default_factory=list)
    stack_name: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        """True if this step failed or has recorded errors."""
        return not self.success or len(self.errors) > 0

    def __str__(self) -> str:
        status = "OK" if self.success else "FAILED"
        prefix = f"[{self.step_number:02d}] {self.step_name}: {status}"
        if self.stack_name:
            prefix = f"[{self.step_number:02d}] {self.step_name} ({self.stack_name}): {status}"
        if self.message:
            return f"{prefix} - {self.message}"
        return prefix


@dataclass
class StackExecutionResult:
    """Aggregated results for a single stack's step execution."""

    stack_name: str
    stack_path: Path
    step_results: list[StepResult] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """True if any step in this stack failed."""
        return any(r.has_errors for r in self.step_results)

    @property
    def failed_steps(self) -> list[StepResult]:
        """Steps that failed."""
        return [r for r in self.step_results if r.has_errors]


@dataclass
class ExecutionResult:
    """Aggregate result of a full phase execution."""

    phase: Phase
    global_results: list[StepResult] = field(default_factory=list)
    stack_results: list[StackExecutionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        """True if any global or per-stack step failed."""
        if self.errors:
            return True
        if any(r.has_errors for r in self.global_results):
            return True
        return any(sr.has_errors for sr in self.stack_results)

    def summary(self) -> str:
        """Human-readable execution summary."""
        lines = [f"Phase: {self.phase.value}"]

        if self.errors:
            lines.append(f"Global errors: {len(self.errors)}")
            for err in self.errors:
                lines.append(f"  - {err}")

        failed_global = [r for r in self.global_results if r.has_errors]
        if failed_global:
            lines.append(f"Failed global steps: {len(failed_global)}")
            for r in failed_global:
                lines.append(f"  - {r}")

        for sr in self.stack_results:
            if sr.has_errors:
                lines.append(f"Stack '{sr.stack_name}' failures:")
                for r in sr.failed_steps:
                    lines.append(f"  - {r}")

        if not self.has_errors:
            total = len(self.global_results) + sum(
                len(sr.step_results) for sr in self.stack_results
            )
            lines.append(f"All {total} step(s) completed successfully.")

        return "\n".join(lines)


class Step(ABC):
    """Base class for execution steps in the standup/run/teardown pipeline."""

    number: int
    name: str
    description: str
    phase: Phase
    per_stack: bool

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        number: int,
        name: str,
        description: str,
        phase: Phase,
        per_stack: bool = False,
    ):
        self.number = number
        self.name = name
        self.description = description
        self.phase = phase
        self.per_stack = per_stack

    @abstractmethod
    def execute(self, context: "ExecutionContext", stack_path: Path | None = None) -> StepResult:
        """Execute this step and return a StepResult."""

    def should_skip(self, context: "ExecutionContext") -> bool:  # pylint: disable=unused-argument
        """Override to implement conditional skip logic."""
        return False

    @staticmethod
    def _require_config(config: dict, *keys: str) -> Any:
        """Traverse a nested key path in the rendered config, raising KeyError if missing."""
        current = config
        traversed: list[str] = []
        for key in keys:
            traversed.append(key)
            if not isinstance(current, dict) or key not in current:
                path = ".".join(traversed)
                raise KeyError(
                    f"Required config key '{path}' not found in rendered "
                    f"config. Ensure it is set in defaults.yaml or your "
                    f"scenario spec."
                )
            current = current[key]
        return current

    def _load_plan_config(self, context: "ExecutionContext") -> dict | None:
        """Load the merged config.yaml from the first rendered stack."""
        for stack_path in context.rendered_stacks:
            config_file = stack_path / "config.yaml"
            if config_file.exists():
                with open(config_file, encoding="utf-8") as f:
                    return yaml.safe_load(f)
        return None

    def _load_stack_config(self, stack_path: Path) -> dict:
        """Load config.yaml from a specific stack directory."""
        config_file = stack_path / "config.yaml"
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _all_target_namespaces(self, context: "ExecutionContext") -> list[str]:
        """Collect deduplicated namespaces from all rendered stacks, with context-level fallback."""
        seen: list[str] = []

        def _add(ns: str | None) -> None:
            if ns and ns not in seen:
                seen.append(ns)

        for stack_path in context.rendered_stacks:
            cfg = self._load_stack_config(stack_path)
            if cfg:
                _add(cfg.get("namespace", {}).get("name"))
                _add(cfg.get("harness", {}).get("namespace"))

        _add(context.namespace)
        _add(context.harness_namespace)

        if not seen:
            raise RuntimeError(
                "No namespace configured. Set 'namespace.name' in your "
                "scenario YAML, defaults.yaml, or pass --namespace on the CLI."
            )
        return seen

    def _find_rendered_yaml(
        self, context: "ExecutionContext", prefix: str
    ) -> Path | None:
        """Find a rendered YAML file by prefix across all stacks."""
        for stack_path in context.rendered_stacks:
            for yaml_file in sorted(stack_path.glob(f"{prefix}*")):
                return yaml_file
        return None

    def _find_yaml(self, stack_path: Path, prefix: str) -> Path | None:
        """Find a YAML file by prefix in a stack directory."""
        for yaml_file in sorted(stack_path.glob(f"{prefix}*")):
            return yaml_file
        return None

    @staticmethod
    def _parse_size_gi(size_str: str) -> float | None:
        """Parse a K8s quantity string (e.g. '300Gi', '1Ti', '500Mi') to GiB."""
        if not size_str:
            return None
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(Ti|Gi|Mi|Ki|T|G|M|K)?$", size_str.strip())
        if not m:
            return None
        value = float(m.group(1))
        unit = m.group(2) or ""
        multipliers = {
            "Ki": 1 / 1024 / 1024, "Mi": 1 / 1024, "Gi": 1, "Ti": 1024,
            "K": 1e3 / (1024**3), "M": 1e6 / (1024**3),
            "G": 1e9 / (1024**3), "T": 1e12 / (1024**3),
            "": 1 / (1024**3),
        }
        return value * multipliers.get(unit, 1)

    def _check_existing_pvc(
        self, cmd, context, pvc_name: str, requested_size: str,
        namespace: str, errors: list,
    ) -> bool:
        """Check if a PVC already exists and validate its size.

        Returns True if the PVC exists (caller should skip creation),
        False if it doesn't exist (caller should create it).
        Appends to *errors* if existing size is too small or unparseable.
        """
        result = cmd.kube(
            "get", "pvc", pvc_name,
            "--namespace", namespace,
            "-o", "jsonpath={.spec.resources.requests.storage}",
            check=False,
        )
        if not result.success or not result.stdout.strip():
            return False

        existing_size_str = result.stdout.strip()
        existing_gi = self._parse_size_gi(existing_size_str)
        requested_gi = self._parse_size_gi(requested_size)

        if existing_gi is None or requested_gi is None:
            errors.append(
                f"PVC '{pvc_name}' exists ({existing_size_str}) but could not "
                f"parse size to compare with requested {requested_size}"
            )
            return True

        if existing_gi < requested_gi:
            errors.append(
                f"PVC '{pvc_name}' exists with size {existing_size_str} but "
                f"{requested_size} is required — existing PVC is too small"
            )
            return True

        if existing_gi > requested_gi:
            context.logger.log_warning(
                f"PVC '{pvc_name}' exists with size {existing_size_str} "
                f"(requested {requested_size}) — larger than needed, continuing"
            )
            return True

        context.logger.log_info(
            f"PVC '{pvc_name}' already exists with correct size "
            f"({existing_size_str}) ✓"
        )
        return True

    @staticmethod
    def _has_yaml_content(yaml_path: Path) -> bool:
        """True if the file has non-empty YAML content (skips conditionally-empty templates)."""
        try:
            content = yaml_path.read_text(encoding="utf-8").strip()
            return bool(content)
        except OSError:
            return False

    def __repr__(self) -> str:
        return (
            f"Step({self.number:02d}, {self.name!r}, "
            f"phase={self.phase.value}, per_stack={self.per_stack})"
        )
