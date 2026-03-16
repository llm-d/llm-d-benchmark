"""Parse DoE experiment YAML files into structured data.

Experiment files follow Design of Experiments (DoE) methodology with two
runtime sections:

- ``setup`` — infrastructure treatments (consumed by the experiment
  orchestrator).  Each setup treatment triggers a
  standup → run → teardown cycle.
- ``treatments`` — workload treatments (consumed by step_04 render_profiles).
  Multiple run treatments execute against a single stood-up stack.

The ``setup`` section is optional.  When absent, the experiment file
behaves identically to the existing ``--experiments`` run-only flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SetupTreatment:
    """A single setup treatment — infrastructure config overrides for deep_merge."""

    name: str
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentPlan:
    """Parsed experiment definition following DoE structure."""

    name: str
    harness: str | None
    profile: str | None
    setup_treatments: list[SetupTreatment]
    run_treatments_count: int
    experiment_file: Path
    has_setup_phase: bool

    @property
    def total_matrix(self) -> int:
        """Total number of runs: setup treatments × run treatments."""
        setup_count = max(len(self.setup_treatments), 1)
        return setup_count * max(self.run_treatments_count, 1)


def dotted_to_nested(flat: dict[str, Any]) -> dict[str, Any]:
    """Convert a flat dict with dotted keys to a nested dict.

    Example::

        >>> dotted_to_nested({"a.b.c": 1, "a.b.d": 2, "x": 3})
        {"a": {"b": {"c": 1, "d": 2}}, "x": 3}
    """
    nested: dict[str, Any] = {}
    for dotted_key, value in flat.items():
        parts = dotted_key.split(".")
        target = nested
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    return nested


def _parse_setup_treatments(setup_data: dict) -> list[SetupTreatment]:
    """Parse the ``setup`` section into a list of SetupTreatment objects.

    Merges ``setup.constants`` into every treatment's overrides before
    treatment-specific values.  Converts dotted keys to nested dicts.
    """
    constants: dict[str, Any] = {}
    raw_constants = setup_data.get("constants")
    if isinstance(raw_constants, dict):
        constants = dict(raw_constants)

    raw_treatments = setup_data.get("treatments", [])
    if not isinstance(raw_treatments, list):
        return []

    treatments: list[SetupTreatment] = []
    for i, item in enumerate(raw_treatments):
        if not isinstance(item, dict):
            continue

        # Constants first, then treatment-specific overrides
        flat_overrides = dict(constants)
        flat_overrides.update(
            {k: v for k, v in item.items() if k != "name"}
        )

        treatments.append(
            SetupTreatment(
                name=item.get("name", f"setup-{i}"),
                overrides=dotted_to_nested(flat_overrides),
            )
        )

    return treatments


def _count_run_treatments(exp_data: dict) -> int:
    """Count the number of run treatments in the experiment file."""
    raw = exp_data.get("treatments") or exp_data.get("run", [])
    if isinstance(raw, list):
        return len(raw)
    return 0


def parse_experiment(path: Path) -> ExperimentPlan:
    """Parse an experiment YAML file into an ExperimentPlan."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Experiment file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Experiment file must be a YAML mapping, got {type(data).__name__}"
        )

    experiment_meta = data.get("experiment", {})
    if not isinstance(experiment_meta, dict):
        experiment_meta = {}

    name = experiment_meta.get("name", path.stem)
    harness = experiment_meta.get("harness")
    profile = experiment_meta.get("profile")

    setup_data = data.get("setup")
    setup_treatments: list[SetupTreatment] = []
    has_setup = False

    if isinstance(setup_data, dict) and "treatments" in setup_data:
        setup_treatments = _parse_setup_treatments(setup_data)
        has_setup = len(setup_treatments) > 0

    run_count = _count_run_treatments(data)

    return ExperimentPlan(
        name=name,
        harness=harness,
        profile=profile,
        setup_treatments=setup_treatments,
        run_treatments_count=run_count,
        experiment_file=path,
        has_setup_phase=has_setup,
    )
