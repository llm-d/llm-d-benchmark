"""Structured pod state, observation, remediation policy, and diagnostics.

Public surface:

* :class:`PodState` / :class:`ContainerState` -- what a pod is doing.
* :class:`Health` -- a graded verdict (healthy / starting / degraded /
  terminal) that distinguishes failures a restart can clear from those it
  cannot.
* :func:`parse_pod_list` / :func:`observe_pods` -- the single parser for
  ``kubectl get pods -o json``.
* :class:`PodPolicy` / :class:`Remedy` -- the seam for reacting to unhealthy
  pods; :class:`RestartBudgetPolicy` is the first implementation.
* :mod:`diagnostics` -- evidence capture and end-of-phase reporting.
"""

from llmdbenchmark.utilities.podstate.diagnostics import (
    capture_pod_evidence,
    evidence_dir,
    render_restart_summary,
)
from llmdbenchmark.utilities.podstate.observer import observe_pods, parse_pod_list
from llmdbenchmark.utilities.podstate.policy import (
    GrantedRestart,
    PodPolicy,
    Remedy,
    RestartBudget,
    RestartBudgetPolicy,
    RestartEvent,
    Verdict,
    WaitContext,
)
from llmdbenchmark.utilities.podstate.state import (
    CRASH_STATES,
    DEGRADED_STATES,
    TERMINAL_STATES,
    ContainerKind,
    ContainerState,
    Health,
    OwnerRef,
    PodState,
    Termination,
    summarize_container_states,
)

__all__ = [
    "CRASH_STATES",
    "DEGRADED_STATES",
    "TERMINAL_STATES",
    "ContainerKind",
    "ContainerState",
    "GrantedRestart",
    "Health",
    "OwnerRef",
    "PodPolicy",
    "PodState",
    "Remedy",
    "RestartBudget",
    "RestartBudgetPolicy",
    "RestartEvent",
    "Termination",
    "Verdict",
    "WaitContext",
    "capture_pod_evidence",
    "evidence_dir",
    "observe_pods",
    "parse_pod_list",
    "render_restart_summary",
    "summarize_container_states",
]
