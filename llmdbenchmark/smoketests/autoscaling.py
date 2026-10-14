"""Autoscaler discovery for smoketests.

Reads the autoscalers in a namespace (KEDA ScaledObjects, plain HPAs) and pairs
each with its workload's live replicas, so validators can check the
autoscaler's state instead of the plan's. Add a parser to ``PROBES`` to support
another autoscaler.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from json import JSONDecodeError, loads

# kubectl's stderr for a missing CRD: expected, so not worth a warning.
_MISSING_RESOURCE = "the server doesn't have a resource type"


@dataclass(frozen=True)
class ScaleTarget:
    """An autoscaled workload together with its live replica state."""

    kind: str
    name: str
    autoscaler: str
    owner: str
    min_replicas: int
    max_replicas: int
    # The workload's spec.replicas; None when the workload was not found.
    current_replicas: int | None = None
    pod_labels: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.kind}/{self.name}"

    @property
    def scaled_to_zero(self) -> bool:
        """True when the autoscaler both allows and currently holds zero."""
        return self.min_replicas == 0 and self.current_replicas == 0

    def matches(self, selector: str) -> bool:
        """True when this workload's pods carry every ``k=v`` in *selector*."""
        for term in filter(None, (t.strip() for t in selector.split(","))):
            key, sep, value = term.partition("=")
            if not sep or self.pod_labels.get(key) != value:
                return False
        return True

    def describe(self) -> str:
        current = "?" if self.current_replicas is None else self.current_replicas
        return (
            f"{self.key} ({self.autoscaler} {self.owner}, "
            f"min={self.min_replicas} max={self.max_replicas} current={current})"
        )


def _int(value, default: int) -> int:
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def _target(
    item: dict, autoscaler: str, owner_kind: str, min_replicas: int, max_replicas: int
) -> ScaleTarget | None:
    ref = (item.get("spec") or {}).get("scaleTargetRef") or {}
    if not ref.get("name"):
        return None
    return ScaleTarget(
        kind=ref.get("kind") or "Deployment",
        name=ref["name"],
        autoscaler=autoscaler,
        owner=f"{owner_kind}/{(item.get('metadata') or {}).get('name', '?')}",
        min_replicas=min_replicas,
        max_replicas=max_replicas,
    )


def _keda_target(item: dict) -> ScaleTarget | None:
    """A KEDA ScaledObject (also how WVA scales); KEDA defaults to 0..100."""
    spec = item.get("spec") or {}
    return _target(
        item,
        "keda",
        "scaledobject",
        _int(spec.get("minReplicaCount"), 0),
        _int(spec.get("maxReplicaCount"), 100),
    )


def _hpa_target(item: dict) -> ScaleTarget | None:
    """A plain HPA (e.g. the prometheus-adapter path)."""
    # A ScaledObject's own HPA; the KEDA probe reports its real floor.
    owners = (item.get("metadata") or {}).get("ownerReferences") or []
    if any(o.get("kind") == "ScaledObject" for o in owners):
        return None
    spec = item.get("spec") or {}
    return _target(
        item,
        "hpa",
        "hpa",
        _int(spec.get("minReplicas"), 1),
        _int(spec.get("maxReplicas"), 1),
    )


# Resource -> parser, in claim order: the first to claim a workload wins.
PROBES: dict[str, Callable[[dict], ScaleTarget | None]] = {
    "scaledobjects.keda.sh": _keda_target,
    "horizontalpodautoscalers": _hpa_target,
}


def _list_items(cmd, namespace: str, resource: str, logger=None) -> list[dict] | None:
    """Items of *resource* in *namespace*; None when they cannot be listed."""
    result = cmd.kube(
        "get", resource, "--namespace", namespace, "-o", "json", check=False
    )
    if not result.success:
        stderr = result.stderr or ""
        if logger is not None and _MISSING_RESOURCE not in stderr:
            logger.log_warning(
                f"Cannot list {resource} in ns/{namespace} (autoscaler-aware "
                f"smoketest checks fall back to the plan): {stderr[:200]}"
            )
        return None
    if not (result.stdout or "").strip():
        return []
    try:
        return loads(result.stdout).get("items", [])
    except JSONDecodeError:
        return None


def _with_live_state(target: ScaleTarget, workload: dict | None) -> ScaleTarget:
    if workload is None:
        return target
    spec = workload.get("spec") or {}
    labels = ((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}
    # spec.replicas defaults to 1 when unset on a live workload.
    return replace(
        target,
        current_replicas=_int(spec.get("replicas"), 1),
        pod_labels=dict(labels),
    )


def discover_scale_targets(cmd, namespace: str, logger=None) -> dict[str, ScaleTarget]:
    """Autoscaled workloads in *namespace*, keyed by ``"<kind>/<name>"``.

    Unlistable resources are skipped, so the result is empty in dry-run or
    without autoscalers/RBAC.
    """
    if cmd is None or getattr(cmd, "dry_run", False):
        return {}
    claims: dict[str, ScaleTarget] = {}
    for resource, parse in PROBES.items():
        for item in _list_items(cmd, namespace, resource, logger) or []:
            target = parse(item)
            if target is not None:
                claims.setdefault(target.key, target)
    if not claims:
        return {}

    workloads = {
        f"{w.get('kind', '')}/{w.get('metadata', {}).get('name', '')}": w
        for w in _list_items(cmd, namespace, "deployments,statefulsets", logger) or []
    }
    return {key: _with_live_state(t, workloads.get(key)) for key, t in claims.items()}


def targets_matching(
    targets: dict[str, ScaleTarget], selector: str
) -> list[ScaleTarget]:
    """Targets whose pods carry every label in *selector*."""
    return [t for t in targets.values() if t.matches(selector)]


def zeroed_matching(
    targets: dict[str, ScaleTarget], selector: str
) -> list[ScaleTarget]:
    """Targets matching *selector* that their autoscaler holds at zero."""
    return [t for t in targets_matching(targets, selector) if t.scaled_to_zero]
