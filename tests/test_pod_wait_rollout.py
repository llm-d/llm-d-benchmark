"""wait_for_pods during a rolling update, where old and new pods coexist.

The outgoing pod stays Running and Ready while it terminates, so counting it
can report success before its replacement exists -- the caller then runs a
benchmark against a pod that is about to die.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from llmdbenchmark.executor import command as command_module
from llmdbenchmark.executor.command import (
    ROLLOUT_QUERY_ATTEMPTS,
    CommandExecutor,
    CommandResult,
    _selector_terms,
)
from llmdbenchmark.standup.steps.step_08_deploy_modelservice import (
    decode_autoscaled,
)
from llmdbenchmark.utilities.podstate import PodState


class _Logger:
    def __init__(self):
        self.messages: list[str] = []

    def set_indent(self, level: int) -> None:
        pass

    def log_info(self, msg, **_):
        self.messages.append(str(msg))

    def log_debug(self, msg, **_):
        self.messages.append(str(msg))

    def log_warning(self, msg, **_):
        self.messages.append(str(msg))

    def log_error(self, msg, **_):
        self.messages.append(str(msg))

    def text(self) -> str:
        return "\n".join(self.messages)


def _pod(name, *, ready=True, deleting=False, crashing=False, unschedulable=False):
    metadata = {"name": name, "uid": f"uid-{name}", "namespace": "ns"}
    if deleting:
        metadata["deletionTimestamp"] = "2026-10-01T10:00:00Z"
    if unschedulable:
        return PodState.from_api(
            {
                "metadata": metadata,
                "status": {
                    "phase": "Pending",
                    "conditions": [
                        {
                            "type": "PodScheduled",
                            "status": "False",
                            "reason": "Unschedulable",
                        }
                    ],
                },
            }
        )
    if crashing:
        containers = [
            {
                "name": "vllm",
                "ready": False,
                "state": {"waiting": {"reason": "CrashLoopBackOff"}},
            }
        ]
    else:
        containers = [{"name": "vllm", "ready": ready, "state": {"running": {}}}]
    return PodState.from_api(
        {
            "metadata": metadata,
            "status": {"phase": "Running", "containerStatuses": containers},
        }
    )


def _executor(tmp_path, monkeypatch, polls, rollouts=None):
    logger = _Logger()
    cmd = CommandExecutor(
        work_dir=tmp_path, dry_run=False, verbose=False, logger=logger
    )

    observed = iter(polls)
    cmd_polls = []

    def _observe(_label, _namespace):
        try:
            result = next(observed)
        except StopIteration:
            result = polls[-1]
        cmd_polls.append(result)
        return result

    rollout_answers = iter(rollouts or [])
    rollout_polls = []

    def _rollouts(_label, _namespace):
        answer = next(rollout_answers, [])
        rollout_polls.append(answer)
        return answer

    monkeypatch.setattr(cmd, "_observe_pods", _observe)
    monkeypatch.setattr(cmd, "_pending_rollouts", _rollouts)
    monkeypatch.setattr(
        cmd, "kube", lambda *a, **k: CommandResult(command="", exit_code=0, stdout="")
    )
    monkeypatch.setattr("llmdbenchmark.executor.command.time.sleep", lambda _s: None)
    cmd.test_logger = logger
    cmd.poll_log = cmd_polls
    cmd.rollout_log = rollout_polls
    return cmd


def _clock(monkeypatch, step):
    """Each time.time() call moves the clock on by *step* seconds."""
    now = {"t": 0.0}

    def _time():
        now["t"] += step
        return now["t"]

    monkeypatch.setattr(command_module.time, "time", _time)


# ---------------------------------------------------------------------------
# A terminating pod must not stand in for its replacement
# ---------------------------------------------------------------------------


def test_a_lone_terminating_ready_pod_does_not_satisfy_the_wait(tmp_path, monkeypatch):
    """The window this exists for: old pod Ready and going away, new one not
    created yet. Counting it returns while nothing is actually serving."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [_pod("decode-old", ready=True, deleting=True)],
            [
                _pod("decode-old", ready=True, deleting=True),
                _pod("decode-new", ready=False),
            ],
            [_pod("decode-new", ready=True)],
        ],
    )
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1
    )

    assert result.success is True
    # Not on the first poll: that one held only the pod that is going away.
    assert len(cmd.poll_log) == 3, f"returned after {len(cmd.poll_log)} poll(s)"
    assert "1/1 Ready" in cmd.test_logger.text()


def test_terminating_pod_is_not_counted_toward_the_total(tmp_path, monkeypatch):
    """Old terminating + new Ready is one live pod, so the wait ends there
    rather than waiting on the pod that is leaving."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [
                _pod("decode-old", ready=True, deleting=True),
                _pod("decode-new", ready=True),
            ]
        ],
    )
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1
    )

    assert result.success is True
    assert "1/1 Ready" in cmd.test_logger.text()


def test_a_terminating_pod_does_not_abort_the_wait(tmp_path, monkeypatch):
    """A pod torn down mid-roll may report a failing container on its way out;
    that is not a reason to fail the wait for its replacement."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [
                _pod("decode-old", crashing=True, deleting=True),
                _pod("decode-new", ready=False),
            ],
            [_pod("decode-new", ready=True)],
        ],
    )
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1
    )

    assert result.success is True, result.stderr


def test_a_live_crashing_pod_still_fails_fast(tmp_path, monkeypatch):
    cmd = _executor(tmp_path, monkeypatch, [[_pod("decode-0", crashing=True)]])
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1
    )

    assert result.success is False
    assert "terminal failure state" in result.stderr


# ---------------------------------------------------------------------------
# expected: how many pods should end up Ready
# ---------------------------------------------------------------------------


def test_one_ready_pod_does_not_satisfy_a_two_replica_wait(tmp_path, monkeypatch):
    """Without expected, the first Ready pod of a scale-up ends the wait."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [_pod("decode-0", ready=True)],
            [_pod("decode-0", ready=True), _pod("decode-1", ready=False)],
            [_pod("decode-0", ready=True), _pod("decode-1", ready=True)],
        ],
    )
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1, expected=2
    )

    assert result.success is True
    assert "2/2 Ready" in cmd.test_logger.text()


def test_expected_is_optional(tmp_path, monkeypatch):
    """Callers without a replica count keep the old any-Ready behaviour."""
    cmd = _executor(tmp_path, monkeypatch, [[_pod("p", ready=True)]])
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is True


def test_expected_times_out_when_a_replica_never_arrives(tmp_path, monkeypatch):
    _clock(monkeypatch, 10.0)
    cmd = _executor(tmp_path, monkeypatch, [[_pod("decode-0", ready=True)]])
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=100, poll_interval=1, expected=2
    )

    assert result.success is False
    assert "Timed out" in result.stderr
    # The pod was seen, so this is not the "no pods" timeout.
    assert "no pods found" not in result.stderr
    assert cmd.poll_log


# ---------------------------------------------------------------------------
# The Deployment rollout must be done, not just the pods Ready
# ---------------------------------------------------------------------------


def test_old_pod_does_not_satisfy_the_wait_before_the_rollout_starts(
    tmp_path, monkeypatch
):
    """Right after the apply the old pod is Ready and not yet going away."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-old", ready=True)], [_pod("decode-new", ready=True)]],
        rollouts=[["decode"], []],
    )
    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode", "ns", timeout=600, poll_interval=1, expected=1
    )

    assert result.success is True
    assert len(cmd.poll_log) == 2
    assert cmd.rollout_log == [["decode"], []]


def test_a_failed_rollout_query_keeps_waiting(tmp_path, monkeypatch):
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0", ready=True)]],
        rollouts=[None, []],
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is True
    assert len(cmd.poll_log) == 2


def test_a_rollout_query_that_keeps_failing_is_given_up(tmp_path, monkeypatch):
    """Without the right to list Deployments the wait must not time out."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0", ready=True)]],
        rollouts=[None] * 10,
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is True
    assert cmd.rollout_log == [None] * ROLLOUT_QUERY_ATTEMPTS


def test_only_terminating_pods_time_out_as_no_pods(tmp_path, monkeypatch):
    """The replacement was never created, which is what the message must say."""
    _clock(monkeypatch, 10.0)
    cmd = _executor(
        tmp_path, monkeypatch, [[_pod("decode-old", ready=True, deleting=True)]]
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=100, poll_interval=1)

    assert result.success is False
    assert "no pods found" in result.stderr


def test_timeout_names_a_pod_that_cannot_be_scheduled(tmp_path, monkeypatch):
    _clock(monkeypatch, 10.0)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-old", ready=True), _pod("decode-new", unschedulable=True)]],
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=100, poll_interval=1)

    assert result.success is False
    assert "decode-new cannot be scheduled" in result.stderr


@pytest.mark.parametrize(
    "label, terms",
    [
        ("app=x", {"app": "x"}),
        ("a=1,b==2", {"a": "1", "b": "2"}),
        ("app!=x", None),
        ("app", None),
        ("env in (a,b)", None),
    ],
)
def test_selector_terms(label, terms):
    assert _selector_terms(label) == terms


def _deployment(name, labels, *, replicas=1, generation=1, status=None):
    return {
        "metadata": {"name": name, "generation": generation},
        "spec": {
            "replicas": replicas,
            "template": {"metadata": {"labels": labels}},
        },
        "status": status
        if status is not None
        else {
            "observedGeneration": generation,
            "replicas": replicas,
            "updatedReplicas": replicas,
            "availableReplicas": replicas,
        },
    }


def _pending_for(tmp_path, monkeypatch, items, label="app=x"):
    cmd = CommandExecutor(
        work_dir=tmp_path, dry_run=False, verbose=False, logger=_Logger()
    )
    payload = json.dumps({"items": items})
    monkeypatch.setattr(
        command_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=payload),
    )
    return cmd._pending_rollouts(label, "ns")


def test_a_done_rollout_is_not_pending(tmp_path, monkeypatch):
    assert _pending_for(tmp_path, monkeypatch, [_deployment("d", {"app": "x"})]) == []


@pytest.mark.parametrize(
    "status",
    [
        # Not seen by the controller yet.
        {
            "observedGeneration": 1,
            "replicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 1,
        },
        # Old pod still counted.
        {
            "observedGeneration": 2,
            "replicas": 2,
            "updatedReplicas": 1,
            "availableReplicas": 1,
        },
        # New pod not available yet.
        {
            "observedGeneration": 2,
            "replicas": 1,
            "updatedReplicas": 1,
            "availableReplicas": 0,
        },
    ],
)
def test_an_unfinished_rollout_is_pending(tmp_path, monkeypatch, status):
    items = [_deployment("d", {"app": "x"}, generation=2, status=status)]
    assert _pending_for(tmp_path, monkeypatch, items) == ["d"]


def test_deployments_of_other_pods_are_ignored(tmp_path, monkeypatch):
    items = [_deployment("other", {"app": "y"}, generation=2, status={})]
    assert _pending_for(tmp_path, monkeypatch, items) == []


@pytest.mark.parametrize(
    "config, autoscaled",
    [
        ({}, False),
        ({"wva": {"enabled": True}}, True),
        ({"eppKedaSaturation": {"enabled": True}}, True),
        ({"keda": {"scaledObjects": [{"name": "so"}]}}, True),
        ({"keda": {"scaledObjects": []}}, False),
        ({"wva": {"enabled": True}, "multinode": {"enabled": True}}, False),
    ],
)
def test_an_autoscaled_decode_count_is_not_waited_for(config, autoscaled):
    """An autoscaler may hold decode below decode.replicas."""
    assert decode_autoscaled(config) is autoscaled
