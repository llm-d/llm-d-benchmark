"""Discover EPP flow-control priority bands and build per-band treatments.

``run --per-band-traffic`` uses this module instead of a hand-written
``--experiments`` file when the operator wants one inference-perf instance
per traffic class. Discovery works in two layers:

- **Bands** come from the rendered stack config: the EPP plugins config
  under ``modelservice.router.epp.pluginsCustomConfig`` holds the
  ``EndpointPickerConfig``. Flow control is on when its ``featureGates``
  list contains ``flowControl``; the bands are the ``flowControl
  .priorityBands[].priority`` values.
- **Objectives** come from the cluster: ``kubectl get
  inferenceobjectives.llm-d.ai``. An instance can only target a band
  through the ``x-llm-d-inference-objective`` header, and the header
  resolves through an InferenceObjective whose ``spec.priority`` selects
  the band. Objective names are therefore the targetable class names.

Each (objective, band) pair becomes one workload treatment carrying the
matching header override, and all treatments share one group so they run
concurrently -- one harness pod per band.

When the plan config carries no EPP block (run-only mode), discovery
degrades to objectives-only: one treatment per objective, with a warning
that the bands could not be cross-checked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from llmdbenchmark.executor.command import CommandExecutor

logger = logging.getLogger(__name__)

#: Profile override that pins a treatment's requests to one traffic class.
#: The last segment is the header the EPP resolves through the
#: InferenceObjective CRs; ``apply_overrides`` splits on ``.`` only, so the
#: hyphens in the header name are safe.
OBJECTIVE_HEADER_KEY = "api.headers.x-llm-d-inference-objective"

#: All band treatments share one group, so they run concurrently.
BAND_GROUP_NAME = "bands"

#: Treatment name for the header-less instance that covers the default band.
DEFAULT_TREATMENT_NAME = "default"

#: Kubernetes object name grammar: lowercase alphanumeric with ``-`` or ``.``.
_NAME_FORBIDDEN = set("!@#$%^&*()+=[]{}|\\:;\"'<>?,/ \t\n_")


@dataclass(frozen=True)
class FlowControlBands:
    """The band structure of one EPP flow-control configuration."""

    #: Explicit ``priorityBands[].priority`` values, sorted ascending.
    priorities: list[int] = field(default_factory=list)
    #: ``defaultPriorityBand.priority`` when the default band names one.
    default_priority: int | None = None
    #: Whether a ``defaultPriorityBand`` block is present at all.
    has_default_band: bool = False


@dataclass(frozen=True)
class BandInstance:
    """One targetable traffic class: an objective, or the default band.

    ``objective_name`` is ``None`` only for the default-band instance, whose
    treatment sends no header so its requests fall through to the default
    band.
    """

    band_priority: int | None
    objective_name: str | None
    treatment_name: str


def find_endpoint_picker_config(plan_config: dict | None) -> dict | None:
    """Extract the EndpointPickerConfig from a rendered stack config.

    Walks ``router.epp.pluginsCustomConfig`` and returns the first parsed
    document that looks like an EndpointPickerConfig. A scenario nests the
    block under ``modelservice.router`` but the rendered ``config.yaml``
    normalizes it to a top-level ``router`` key, so both paths are checked.
    Values are YAML strings in scenario files but may already be dicts
    after merging; both are accepted. Returns ``None`` when the config has
    no EPP block (run-only / standalone stacks).
    """
    if not isinstance(plan_config, dict):
        return None
    epp_blocks: list[Any] = [
        plan_config.get("router", {}).get("epp", {}),
        plan_config.get("modelservice", {}).get("router", {}).get("epp", {}),
    ]
    for epp in epp_blocks:
        custom = epp.get("pluginsCustomConfig") if isinstance(epp, dict) else None
        if not isinstance(custom, dict):
            continue
        for value in custom.values():
            if isinstance(value, str):
                try:
                    value = yaml.safe_load(value)
                except yaml.YAMLError:
                    continue
            if isinstance(value, dict) and (
                value.get("kind") == "EndpointPickerConfig"
                or "flowControl" in value
                or "featureGates" in value
            ):
                return value
    return None


def extract_flow_control(picker_config: dict | None) -> FlowControlBands | None:
    """Read the band structure from an EndpointPickerConfig.

    Returns ``None`` when flow control is off (the feature gate is absent
    or the ``flowControl`` block carries no bands), so callers can fail
    loudly instead of launching class-less traffic. The default band's
    priority is read tolerantly: a ``defaultPriorityBand`` without a
    ``priority`` key is recorded as present but of unknown priority.
    """
    if not isinstance(picker_config, dict):
        return None
    gates = picker_config.get("featureGates")
    if not isinstance(gates, list) or "flowControl" not in gates:
        return None
    flow_control = picker_config.get("flowControl")
    if not isinstance(flow_control, dict):
        return None

    priorities: list[int] = []
    raw_bands = flow_control.get("priorityBands")
    if isinstance(raw_bands, list):
        for band in raw_bands:
            if isinstance(band, dict):
                try:
                    priorities.append(int(band["priority"]))
                except KeyError, TypeError, ValueError:
                    logger.warning(
                        "priorityBands entry without an integer 'priority' "
                        "was skipped: %r",
                        band,
                    )
    if not priorities:
        return None

    default_block = flow_control.get("defaultPriorityBand")
    has_default = isinstance(default_block, dict)
    default_priority: int | None = None
    if has_default:
        try:
            default_priority = int(default_block["priority"])
        except KeyError, TypeError, ValueError:
            default_priority = None

    return FlowControlBands(
        priorities=sorted(set(priorities)),
        default_priority=default_priority,
        has_default_band=has_default,
    )


def list_inference_objectives(
    cmd: "CommandExecutor | None", namespace: str | None
) -> list[tuple[str, int]]:
    """List ``(name, spec.priority)`` for the InferenceObjectives on the cluster.

    Read-only and forced past dry-run, so a ``--dry-run`` render still sees
    the real classes. Returns ``[]`` -- never raises -- when the executor is
    missing, the CRD is absent, or the API call fails; callers decide
    whether an empty list is fatal.
    """
    if cmd is None:
        return []
    result = cmd.kube(
        "get",
        "inferenceobjectives.llm-d.ai",
        "-o",
        "json",
        namespace=namespace or None,
        check=False,
        force=True,
    )
    if result.exit_code != 0:
        logger.warning(
            "Could not list InferenceObjectives (exit %d); does the cluster "
            "have the llm-d CRDs applied?",
            result.exit_code,
        )
        return []
    try:
        data = yaml.safe_load(result.stdout) or {}
    except yaml.YAMLError:
        logger.warning("kubectl output for InferenceObjectives was not valid")
        return []

    objectives: list[tuple[str, int]] = []
    for item in data.get("items", []):
        try:
            name = str(item["metadata"]["name"])
            priority = int(item["spec"]["priority"])
        except KeyError, TypeError, ValueError:
            continue
        objectives.append((name, priority))
    return objectives


def pair_bands_with_objectives(
    bands: FlowControlBands | None,
    objectives: list[tuple[str, int]],
) -> tuple[list[BandInstance], list[str]]:
    """Pair targetable objectives with the bands they select.

    One instance per objective whose priority matches an explicit band
    (every objective, when no band structure was found). Objectives that
    match no band are skipped with a warning: the operator asked for
    per-band traffic, and an instance that provably misses every band
    would report contention that never reached the band under study. The
    default band gets one header-less instance when it names an explicit
    priority that no objective already covers.

    Returns ``(instances, warnings)``.
    """
    warnings: list[str] = []
    instances: list[BandInstance] = []

    if bands is None:
        for name, priority in objectives:
            instances.append(BandInstance(priority, name, _safe_treatment_name(name)))
        return instances, warnings

    band_set = set(bands.priorities)
    if bands.default_priority is not None:
        band_set.add(bands.default_priority)

    for name, priority in objectives:
        if priority in band_set:
            instances.append(BandInstance(priority, name, _safe_treatment_name(name)))
        else:
            warnings.append(
                f"InferenceObjective '{name}' (priority {priority}) matches "
                f"no flow-control band (bands: {sorted(band_set)}); no "
                f"instance was generated for it. Give the objective a "
                f"priority that matches a band, or add a band for it."
            )
    for priority in bands.priorities:
        if not any(i.band_priority == priority for i in instances):
            warnings.append(
                f"Band priority {priority} has no InferenceObjective, so no "
                f"traffic can target it. Define an InferenceObjective with "
                f"spec.priority: {priority} to exercise this band."
            )

    if (
        bands.default_priority is not None
        and bands.has_default_band
        and bands.default_priority not in bands.priorities
        and not any(i.band_priority == bands.default_priority for i in instances)
    ):
        instances.append(
            BandInstance(bands.default_priority, None, DEFAULT_TREATMENT_NAME)
        )
    elif bands.has_default_band and bands.default_priority is None:
        warnings.append(
            "defaultPriorityBand carries no explicit 'priority'; no "
            "header-less default-band instance was generated because the "
            "band header-less traffic lands in cannot be confirmed."
        )

    return instances, warnings


def _safe_treatment_name(objective_name: str) -> str:
    """Make an objective name safe as a treatment name and pod label value.

    Objective names already follow the DNS subdomain grammar, so this only
    guards against path separators and whitespace slipping through.
    """
    return "".join("-" if ch in _NAME_FORBIDDEN else ch for ch in objective_name)


def build_band_treatments(
    instances: list[BandInstance],
    base_overrides: dict[str, Any] | None,
    per_band_overrides: dict[str, dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build one treatment dict per band instance.

    Override precedence, weakest to strongest: ``-o`` base overrides, the
    per-band overrides for this instance, then the objective header -- the
    header goes last so it always wins and the instance provably targets
    its band. Each treatment is marked ``band: True`` so step_05 can create
    missing parent paths (a base profile without ``api.headers``) and hard
    -fail if the header override does not land.

    Returns ``(treatments, errors)``; errors name per-band override keys
    that match no instance.
    """
    base = dict(base_overrides or {})
    per_band = per_band_overrides or {}

    known = {i.treatment_name for i in instances}
    errors = [
        f"per-band overrides key '{key}' matches no band instance "
        f"(known: {sorted(known)})"
        for key in per_band
        if key not in known
    ]
    if errors:
        return [], errors

    treatments: list[dict[str, Any]] = []
    for instance in instances:
        overrides = dict(base)
        overrides.update(per_band.get(instance.treatment_name, {}))
        if instance.objective_name is not None:
            # Last write wins: the header must never be overridden away.
            overrides[OBJECTIVE_HEADER_KEY] = instance.objective_name
        treatments.append(
            {
                "name": instance.treatment_name,
                "group": BAND_GROUP_NAME,
                "overrides": overrides,
                "band": True,
            }
        )
    return treatments, []


def load_per_band_overrides(
    path: str | None,
) -> dict[str, dict[str, Any]] | None:
    """Load the optional ``--per-band-overrides`` YAML file.

    The file maps a treatment name (an objective name, or ``default``) to
    a mapping of dotted profile overrides applied only to that band's
    instance, e.g. to split the offered rate across classes::

        premium:
          load.stages.0.rate: 5
        besteffort:
          load.stages.0.rate: 1

    Returns ``None`` when no path is given; raises ``ValueError`` on a
    malformed file so the run fails before any pod is deployed.
    """
    if not path:
        return None
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ValueError(f"per-band overrides file not found: {file_path}")
    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return None
    if not isinstance(data, dict) or not all(
        isinstance(v, dict) for v in data.values()
    ):
        raise ValueError(
            f"{file_path}: per-band overrides must map treatment names to "
            f"mappings of dotted profile overrides, got: {type(data).__name__}"
        )
    return {str(k): v for k, v in data.items()}
