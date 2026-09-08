"""Turn ``kubectl get pods -o json`` payloads into :class:`PodState` objects.

Every consumer of pod state in the codebase should route through here, so
there is one parser to fix when the API shape or our interpretation of it
changes.
"""

from __future__ import annotations

import json

from llmdbenchmark.utilities.podstate.state import PodState


def parse_pod_list(payload: str, namespace: str = "") -> list[PodState] | None:
    """Parse a pod list payload.

    Returns ``None`` (rather than an empty list) when the payload is unusable,
    so callers can distinguish "the query failed" from "no pods matched" -- a
    poll loop must not treat an apiserver hiccup as "the deployment vanished".
    """
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    return [
        PodState.from_api(item, namespace=namespace)
        for item in data.get("items", []) or []
    ]


def observe_pods(
    cmd,
    namespace: str,
    label: str | None = None,
    field_selector: str | None = None,
) -> list[PodState] | None:
    """Query pods through a CommandExecutor and return their parsed state.

    Returns ``None`` when the query itself failed.  Uses ``check=False`` so a
    transient failure surfaces as ``None`` instead of raising.
    """
    args = ["get", "pods", "--namespace", namespace, "-o", "json"]
    if label:
        args.extend(["-l", label])
    if field_selector:
        args.extend(["--field-selector", field_selector])

    result = cmd.kube(*args, check=False)
    if not getattr(result, "success", False):
        return None
    return parse_pod_list(result.stdout or "", namespace=namespace)
