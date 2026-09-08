"""Tests for the restart budget and the pod remediation policy seam."""

from __future__ import annotations

import threading

from llmdbenchmark.utilities.podstate import (
    Health,
    PodState,
    RestartBudget,
    RestartBudgetPolicy,
    Verdict,
    WaitContext,
)


def _pod(name, uid=None, reason="CrashLoopBackOff", owned=True, deleting=False):
    item = {
        "metadata": {"name": name, "uid": uid or f"uid-{name}", "namespace": "ns"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "vllm",
                    "ready": False,
                    "state": {"waiting": {"reason": reason}},
                }
            ],
        },
    }
    if owned:
        item["metadata"]["ownerReferences"] = [
            {"kind": "ReplicaSet", "name": "rs", "controller": True}
        ]
    if deleting:
        item["metadata"]["deletionTimestamp"] = "2026-08-19T12:00:00Z"
    return PodState.from_api(item)


def _ctx(description="decode pods", elapsed=10.0, timeout=600.0):
    return WaitContext(
        description=description, namespace="ns", elapsed=elapsed, timeout=timeout
    )


# ---------------------------------------------------------------------------
# Budget accounting
# ---------------------------------------------------------------------------


def test_zero_budget_is_disabled():
    budget = RestartBudget(0)
    assert budget.enabled is False
    assert budget.claim([_pod("a")]) == []


def test_negative_budget_clamps_to_zero():
    assert RestartBudget(-5).limit == 0


def test_budget_grants_up_to_limit_then_stops():
    budget = RestartBudget(2)
    granted = budget.claim([_pod("a"), _pod("b"), _pod("c")])
    assert [g.pod.name for g in granted] == ["a", "b"]
    assert [g.event.sequence for g in granted] == [1, 2]
    assert budget.exhausted is True
    assert budget.remaining == 0


def test_budget_is_global_not_per_pod():
    """The whole point of the knob: one pod can consume the entire allowance."""
    budget = RestartBudget(3)
    for i in range(3):
        assert len(budget.claim([_pod("decode-0", uid=f"uid-{i}")])) == 1
    assert budget.claim([_pod("prefill-0", uid="other")]) == []


def test_budget_is_shared_across_separate_waits():
    budget = RestartBudget(2)
    policy_a = RestartBudgetPolicy(budget=budget)
    policy_b = RestartBudgetPolicy(budget=budget)
    assert policy_a.observe([_pod("a")], _ctx()) is not None
    assert policy_b.observe([_pod("b")], _ctx()) is not None
    assert policy_a.observe([_pod("c")], _ctx()) is None
    assert budget.status() == "2/2"


def test_same_pod_instance_is_charged_only_once():
    """A deleted pod lingers in Terminating; it must not drain the budget."""
    budget = RestartBudget(5)
    pod = _pod("decode-0", uid="uid-stable")
    assert len(budget.claim([pod])) == 1
    assert budget.claim([pod]) == []
    assert budget.used == 1


def test_replacement_pod_with_new_uid_can_be_charged_again():
    budget = RestartBudget(5)
    assert len(budget.claim([_pod("decode-0", uid="uid-1")])) == 1
    assert len(budget.claim([_pod("decode-0", uid="uid-2")])) == 1
    assert budget.used == 2


def test_events_record_pod_reason_and_sequence():
    budget = RestartBudget(2)
    budget.claim([_pod("decode-0")], description="decode pods")
    (event,) = budget.events
    assert (event.pod, event.reason, event.sequence) == (
        "decode-0",
        "CrashLoopBackOff",
        1,
    )
    assert event.description == "decode pods"


def test_concurrent_claims_never_exceed_the_limit():
    """Standup deploys stacks in parallel through one shared executor."""
    budget = RestartBudget(10)
    barrier = threading.Barrier(8)
    results = []

    def worker(n):
        barrier.wait()
        pods = [_pod(f"pod-{n}-{i}", uid=f"uid-{n}-{i}") for i in range(5)]
        results.append(len(budget.claim(pods)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 10
    assert budget.used == 10
    assert len({e.sequence for e in budget.events}) == 10


# ---------------------------------------------------------------------------
# Policy eligibility
# ---------------------------------------------------------------------------


def test_policy_restarts_degraded_owned_pod():
    budget = RestartBudget(1)
    remedy = RestartBudgetPolicy(budget=budget).observe([_pod("decode-0")], _ctx())
    assert remedy is not None
    assert remedy.verdict is Verdict.CONTINUE
    assert remedy.delete_pods == ("decode-0",)
    assert remedy.extend_deadline == 300.0


def test_policy_ignores_terminal_failures():
    """ImagePullBackOff produces an identical replacement; do not spend budget."""
    budget = RestartBudget(3)
    pod = _pod("decode-0", reason="ImagePullBackOff")
    assert pod.health is Health.TERMINAL
    assert RestartBudgetPolicy(budget=budget).observe([pod], _ctx()) is None
    assert budget.used == 0


def test_policy_ignores_uncontrolled_pods():
    """A bare pod would never come back after deletion."""
    budget = RestartBudget(3)
    pod = _pod("smoketest-curl", owned=False)
    assert RestartBudgetPolicy(budget=budget).observe([pod], _ctx()) is None
    assert budget.used == 0


def test_policy_ignores_already_terminating_pods():
    budget = RestartBudget(3)
    pod = _pod("decode-0", deleting=True)
    assert RestartBudgetPolicy(budget=budget).observe([pod], _ctx()) is None
    assert budget.used == 0


def test_policy_ignores_healthy_pods():
    budget = RestartBudget(3)
    item = {
        "metadata": {"name": "ok", "uid": "u", "namespace": "ns"},
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "vllm", "ready": True, "state": {}}],
        },
    }
    assert (
        RestartBudgetPolicy(budget=budget).observe([PodState.from_api(item)], _ctx())
        is None
    )


def test_policy_is_inert_when_budget_disabled():
    budget = RestartBudget(0)
    assert (
        RestartBudgetPolicy(budget=budget).observe([_pod("decode-0")], _ctx()) is None
    )


def test_policy_returns_none_once_budget_exhausted():
    budget = RestartBudget(1)
    policy = RestartBudgetPolicy(budget=budget)
    assert policy.observe([_pod("a")], _ctx()) is not None
    assert policy.observe([_pod("b")], _ctx()) is None


def test_deadline_extension_scales_with_pods_restarted():
    budget = RestartBudget(3)
    policy = RestartBudgetPolicy(budget=budget, grace_seconds=120.0)
    remedy = policy.observe([_pod("a"), _pod("b")], _ctx())
    assert remedy.extend_deadline == 240.0


def test_take_granted_is_consumed_once():
    budget = RestartBudget(2)
    policy = RestartBudgetPolicy(budget=budget)
    policy.observe([_pod("a")], _ctx())
    assert [g.pod.name for g in policy.take_granted()] == ["a"]
    assert policy.take_granted() == []


def test_wait_context_remaining_is_never_negative():
    assert WaitContext("d", "ns", elapsed=700.0, timeout=600.0).remaining == 0.0
