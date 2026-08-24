"""Tests for the structured pod state model (llmdbenchmark.utilities.podstate.state).

Includes an oracle check: the pre-refactor implementations of
``_summarize_container_status``, the ``_get_pod_statuses`` status/ready
derivation, and ``_pod_crash_details`` are reproduced verbatim here and
asserted to agree with the new model on a corpus of payloads.
"""

from __future__ import annotations

import pytest

from llmdbenchmark.utilities.podstate import (
    CRASH_STATES,
    DEGRADED_STATES,
    TERMINAL_STATES,
    Health,
    PodState,
    summarize_container_states,
)
from llmdbenchmark.utilities.kube_helpers import _pod_crash_details


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _cs(name, ready=False, waiting=None, terminated=None, last=None, restarts=0):
    status = {"name": name, "ready": ready, "restartCount": restarts, "state": {}}
    if waiting is not None:
        status["state"]["waiting"] = waiting
    if terminated is not None:
        status["state"]["terminated"] = terminated
    if last is not None:
        status["lastState"] = {"terminated": last}
    return status


def _pod(name="pod-a", phase="Running", containers=None, **status_extra):
    item = {
        "metadata": {"name": name, "uid": f"uid-{name}"},
        "status": {"phase": phase},
    }
    if containers is not None:
        item["status"]["containerStatuses"] = containers
    item["status"].update(status_extra)
    return item


def _owned(item, kind="ReplicaSet", name="rs-1"):
    item["metadata"]["ownerReferences"] = [
        {"kind": kind, "name": name, "controller": True}
    ]
    return item


# ---------------------------------------------------------------------------
# State sets
# ---------------------------------------------------------------------------


def test_crash_states_is_exactly_degraded_plus_terminal():
    assert CRASH_STATES == DEGRADED_STATES | TERMINAL_STATES
    assert not DEGRADED_STATES & TERMINAL_STATES


def test_crash_states_matches_historical_set():
    """The flat set predates the split; existing callers must see it unchanged."""
    assert set(CRASH_STATES) == {
        "CrashLoopBackOff",
        "Error",
        "OOMKilled",
        "CreateContainerConfigError",
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
    }


# ---------------------------------------------------------------------------
# Readiness and summary
# ---------------------------------------------------------------------------


def test_pod_ready_only_when_all_containers_ready():
    pod = PodState.from_api(
        _pod(containers=[_cs("vllm", ready=True), _cs("sidecar", ready=True)])
    )
    assert pod.ready is True
    assert pod.summary == "Ready"
    assert pod.health is Health.HEALTHY


def test_multi_container_crash_not_masked_by_ready_sibling():
    """A ready routing sidecar must not hide a crash-looping serving container."""
    pod = PodState.from_api(
        _pod(
            containers=[
                _cs("routing-proxy", ready=True),
                _cs("vllm", waiting={"reason": "CrashLoopBackOff"}),
            ]
        )
    )
    assert pod.ready is False
    assert pod.summary == "CrashLoopBackOff"
    assert pod.crashing is True


def test_no_container_statuses_falls_back_to_phase():
    pod = PodState.from_api(_pod(phase="Pending"))
    assert pod.summary == "Pending"
    assert pod.ready is False


def test_unschedulable_pod_surfaces_scheduling_reason():
    pod = PodState.from_api(
        _pod(
            phase="Pending",
            conditions=[
                {"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}
            ],
        )
    )
    assert pod.summary == "Unschedulable"
    assert pod.health is Health.STARTING


def test_summarize_prefers_terminated_crash_over_waiting():
    pod = PodState.from_api(
        _pod(
            containers=[
                _cs("a", waiting={"reason": "PodInitializing"}),
                _cs("b", terminated={"reason": "OOMKilled", "exitCode": 137}),
            ]
        )
    )
    assert pod.summary == "OOMKilled"


def test_summarize_empty_list_is_not_ready():
    assert summarize_container_states([]) == "NotReady"


def test_summarize_falls_back_to_notready_without_reasons():
    pod = PodState.from_api(_pod(containers=[_cs("a"), _cs("b")]))
    assert pod.summary == "NotReady"


# ---------------------------------------------------------------------------
# Health grading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", sorted(DEGRADED_STATES))
def test_degraded_states_grade_as_degraded(reason):
    pod = PodState.from_api(_pod(containers=[_cs("vllm", waiting={"reason": reason})]))
    assert pod.health is Health.DEGRADED


@pytest.mark.parametrize("reason", sorted(TERMINAL_STATES))
def test_terminal_states_grade_as_terminal(reason):
    """A wrong image name is not fixed by deleting the pod."""
    pod = PodState.from_api(_pod(containers=[_cs("vllm", waiting={"reason": reason})]))
    assert pod.health is Health.TERMINAL


def test_failed_pod_without_container_statuses_is_degraded():
    """The 'pod is just in Error' case: no containerStatuses to inspect."""
    pod = PodState.from_api(_pod(phase="Failed", reason="Error"))
    assert pod.health is Health.DEGRADED
    # ...but the legacy container-level crash test still does not fire, so the
    # historical wait behavior is unchanged when no budget is configured.
    assert pod.crashing is False


def test_evicted_pod_is_degraded_via_phase():
    pod = PodState.from_api(_pod(phase="Failed", reason="Evicted"))
    assert pod.health is Health.DEGRADED


def test_starting_pod_is_not_degraded():
    pod = PodState.from_api(
        _pod(
            phase="Pending",
            containers=[_cs("vllm", waiting={"reason": "PodInitializing"})],
        )
    )
    assert pod.health is Health.STARTING


# ---------------------------------------------------------------------------
# Ownership, identity, restarts
# ---------------------------------------------------------------------------


def test_controlled_requires_controller_owner_reference():
    assert PodState.from_api(_pod()).controlled is False
    assert PodState.from_api(_owned(_pod())).controlled is True


def test_non_controller_owner_reference_does_not_count():
    item = _pod()
    item["metadata"]["ownerReferences"] = [{"kind": "Node", "name": "n1"}]
    assert PodState.from_api(item).controlled is False


def test_deletion_timestamp_marks_pod_deleting():
    item = _pod()
    item["metadata"]["deletionTimestamp"] = "2026-08-19T12:00:00Z"
    assert PodState.from_api(item).deleting is True


def test_total_restarts_spans_init_and_app_containers():
    item = _pod(containers=[_cs("vllm", restarts=3), _cs("sidecar", restarts=1)])
    item["status"]["initContainerStatuses"] = [_cs("wait-for-model", restarts=2)]
    pod = PodState.from_api(item)
    assert pod.total_restarts == 6
    assert pod.restarts_for("vllm") == 3


# ---------------------------------------------------------------------------
# Crash details
# ---------------------------------------------------------------------------


def test_crash_details_reports_current_and_previous_failure():
    pod = PodState.from_api(
        _pod(
            name="inference-perf-abc",
            containers=[
                _cs(
                    "harness",
                    waiting={"reason": "CrashLoopBackOff"},
                    last={"reason": "OOMKilled", "exitCode": 137},
                )
            ],
        )
    )
    (detail,) = pod.crash_details
    assert "inference-perf-abc/harness" in detail
    assert "CrashLoopBackOff" in detail
    assert "last terminated: OOMKilled, exit_code=137" in detail


def test_crash_details_flags_nonzero_exit_without_crash_reason():
    pod = PodState.from_api(
        _pod(
            containers=[
                _cs("harness", terminated={"reason": "Completed", "exitCode": 2})
            ]
        )
    )
    assert "exit_code=2" in pod.crash_details[0]


def test_crash_details_ignores_clean_exit():
    pod = PodState.from_api(
        _pod(
            containers=[
                _cs("harness", terminated={"reason": "Completed", "exitCode": 0})
            ]
        )
    )
    assert pod.crash_details == []


def test_crash_details_falls_back_to_pod_reason():
    pod = PodState.from_api(_pod(name="p1", phase="Failed", reason="Error"))
    assert pod.crash_details == ["p1 (Error)"]


def test_crash_details_covers_init_containers():
    item = _pod(containers=[_cs("vllm", ready=True)])
    item["status"]["initContainerStatuses"] = [
        _cs("fetch-model", terminated={"reason": "Error", "exitCode": 1})
    ]
    assert "fetch-model" in PodState.from_api(item).crash_details[0]


# ---------------------------------------------------------------------------
# Oracle: the pre-refactor implementations, reproduced verbatim
# ---------------------------------------------------------------------------


def _legacy_summarize(not_ready):
    def _state_reason(cs, key):
        return (cs.get("state", {}).get(key) or {}).get("reason", "") or ""

    if not not_ready:
        return "NotReady"
    for cs in not_ready:
        reason = _state_reason(cs, "terminated")
        if reason in CRASH_STATES:
            return reason
    for cs in not_ready:
        reason = _state_reason(cs, "waiting")
        if reason in CRASH_STATES:
            return reason
    for cs in not_ready:
        reason = _state_reason(cs, "waiting") or _state_reason(cs, "terminated")
        if reason:
            return reason
    return "NotReady"


def _legacy_status_and_ready(item):
    phase = item.get("status", {}).get("phase", "Unknown")
    status = phase
    ready = False
    container_statuses = item.get("status", {}).get("containerStatuses", [])
    if container_statuses:
        if all(cs.get("ready", False) for cs in container_statuses):
            ready = True
            status = "Ready"
        else:
            not_ready = [cs for cs in container_statuses if not cs.get("ready", False)]
            status = _legacy_summarize(not_ready)
    elif phase == "Pending":
        for cond in item.get("status", {}).get("conditions", []):
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                status = cond.get("reason", "Unschedulable")
                break
    return status, ready


def _legacy_terminated_detail(prefix, state):
    reason = state.get("reason") or "unknown reason"
    detail = f"{prefix}{reason}"
    if state.get("exitCode") is not None:
        detail += f", exit_code={state['exitCode']}"
    return detail


def _legacy_crash_details(pod):
    metadata = pod.get("metadata", {})
    status = pod.get("status", {})
    pod_name = metadata.get("name", "unknown-pod")
    failures = []
    status_groups = (
        status.get("initContainerStatuses", []),
        status.get("containerStatuses", []),
        status.get("ephemeralContainerStatuses", []),
    )
    for container_statuses in status_groups:
        for container_status in container_statuses or []:
            state = container_status.get("state", {})
            details = []
            waiting = state.get("waiting") or {}
            waiting_reason = waiting.get("reason")
            if waiting_reason in CRASH_STATES:
                details.append(waiting_reason)
            terminated = state.get("terminated") or {}
            terminated_reason = terminated.get("reason")
            terminated_exit_code = terminated.get("exitCode")
            if terminated and (
                terminated_reason in CRASH_STATES
                or (terminated_exit_code is not None and terminated_exit_code != 0)
            ):
                details.append(_legacy_terminated_detail("terminated: ", terminated))
            if not details:
                continue
            last_terminated = (container_status.get("lastState") or {}).get(
                "terminated"
            )
            if last_terminated:
                details.append(
                    _legacy_terminated_detail("last terminated: ", last_terminated)
                )
            container_name = container_status.get("name", "unknown-container")
            failures.append(f"{pod_name}/{container_name} ({', '.join(details)})")
    if not failures and status.get("reason") in CRASH_STATES:
        failures.append(f"{pod_name} ({status['reason']})")
    return failures


def _corpus():
    pods = [
        _pod(containers=[_cs("a", ready=True)]),
        _pod(containers=[_cs("a", ready=True), _cs("b", ready=True)]),
        _pod(
            containers=[
                _cs("a", ready=True),
                _cs("b", waiting={"reason": "CrashLoopBackOff"}),
            ]
        ),
        _pod(containers=[_cs("a", waiting={"reason": "PodInitializing"})]),
        _pod(containers=[_cs("a", waiting={"reason": "ImagePullBackOff"})]),
        _pod(
            containers=[_cs("a", terminated={"reason": "OOMKilled", "exitCode": 137})]
        ),
        _pod(containers=[_cs("a", terminated={"reason": "Completed", "exitCode": 0})]),
        _pod(containers=[_cs("a", terminated={"exitCode": 3})]),
        _pod(containers=[_cs("a")]),
        _pod(
            containers=[
                _cs("a", waiting={"reason": "PodInitializing"}),
                _cs("b", terminated={"reason": "Error", "exitCode": 1}),
            ]
        ),
        _pod(
            containers=[
                _cs(
                    "a",
                    waiting={"reason": "CrashLoopBackOff"},
                    last={"reason": "OOMKilled", "exitCode": 137},
                )
            ]
        ),
        _pod(containers=[_cs("a", waiting={"reason": "CrashLoopBackOff"}, last={})]),
        _pod(phase="Pending"),
        _pod(phase="Failed", reason="Error"),
        _pod(phase="Succeeded"),
        _pod(
            phase="Pending",
            conditions=[
                {"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}
            ],
        ),
        _pod(phase="Pending", conditions=[{"type": "PodScheduled", "status": "False"}]),
        _pod(phase="Pending", conditions=[{"type": "Ready", "status": "False"}]),
    ]
    with_init = _pod(containers=[_cs("a", ready=True)])
    with_init["status"]["initContainerStatuses"] = [
        _cs("init", terminated={"reason": "Error", "exitCode": 1})
    ]
    pods.append(with_init)

    with_ephemeral = _pod(containers=[_cs("a", ready=True)])
    with_ephemeral["status"]["ephemeralContainerStatuses"] = [
        _cs("debug", waiting={"reason": "CrashLoopBackOff"})
    ]
    pods.append(with_ephemeral)
    return pods


@pytest.mark.parametrize("item", _corpus())
def test_matches_legacy_status_and_ready(item):
    pod = PodState.from_api(item)
    legacy_status, legacy_ready = _legacy_status_and_ready(item)
    assert (pod.summary, pod.ready) == (legacy_status, legacy_ready)


@pytest.mark.parametrize("item", _corpus())
def test_matches_legacy_crash_details(item):
    assert PodState.from_api(item).crash_details == _legacy_crash_details(item)
    assert _pod_crash_details(item) == _legacy_crash_details(item)


@pytest.mark.parametrize("item", _corpus())
def test_legacy_crash_matching_is_unchanged(item):
    """The historical abort rule (`status in CRASH_STATES`) must be preserved."""
    legacy_status, _ = _legacy_status_and_ready(item)
    assert PodState.from_api(item).crashing == (legacy_status in CRASH_STATES)
