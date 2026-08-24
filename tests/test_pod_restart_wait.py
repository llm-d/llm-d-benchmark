"""End-to-end behavior of the restart budget inside CommandExecutor.wait_for_pods."""

from __future__ import annotations

from typing import Any

import pytest

from llmdbenchmark.executor.command import CommandExecutor, CommandResult
from llmdbenchmark.utilities.podstate import PodState, RestartBudget


class _Logger:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def set_indent(self, level: int) -> None:
        pass

    def _add(self, level, msg):
        self.messages.append((level, str(msg)))

    def log_info(self, msg, **_):
        self._add("info", msg)

    def log_debug(self, msg, **_):
        self._add("debug", msg)

    def log_warning(self, msg, **_):
        self._add("warning", msg)

    def log_error(self, msg, **_):
        self._add("error", msg)

    def text(self) -> str:
        return "\n".join(m for _, m in self.messages)


def _pod(name, *, ready=False, reason="CrashLoopBackOff", uid=None, owned=True):
    item = {
        "metadata": {"name": name, "uid": uid or f"uid-{name}", "namespace": "ns"},
        "status": {"phase": "Running", "containerStatuses": []},
    }
    if owned:
        item["metadata"]["ownerReferences"] = [
            {"kind": "ReplicaSet", "name": "rs", "controller": True}
        ]
    if ready:
        item["status"]["containerStatuses"] = [
            {"name": "vllm", "ready": True, "state": {}}
        ]
    else:
        item["status"]["containerStatuses"] = [
            {"name": "vllm", "ready": False, "state": {"waiting": {"reason": reason}}}
        ]
    return PodState.from_api(item)


def _executor(tmp_path, monkeypatch, polls, budget=None):
    """Build an executor whose pod observations follow the scripted *polls*."""
    logger = _Logger()
    cmd = CommandExecutor(
        work_dir=tmp_path,
        dry_run=False,
        verbose=False,
        logger=logger,
        pod_restart_budget=budget,
    )

    observed = iter(polls)
    cmd.observed_calls = 0

    def _observe(_label, _namespace):
        cmd.observed_calls += 1
        try:
            return next(observed)
        except StopIteration:
            return polls[-1]

    monkeypatch.setattr(cmd, "_observe_pods", _observe)

    calls: list[tuple[str, ...]] = []

    def _kube(*args: str, **_: Any) -> CommandResult:
        calls.append(args)
        return CommandResult(command=" ".join(args), exit_code=0, stdout="")

    monkeypatch.setattr(cmd, "kube", _kube)
    monkeypatch.setattr("llmdbenchmark.executor.command.time.sleep", lambda _s: None)

    cmd.kube_calls = calls
    cmd.test_logger = logger
    return cmd


def _deletions(cmd) -> list[str]:
    return [c[2] for c in cmd.kube_calls if c[:2] == ("delete", "pod")]


# ---------------------------------------------------------------------------
# Default (disabled) behavior -- must be byte-for-byte what it always was
# ---------------------------------------------------------------------------


def test_without_budget_a_crashing_pod_still_fails_immediately(tmp_path, monkeypatch):
    cmd = _executor(tmp_path, monkeypatch, [[_pod("decode-0")]])
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is False
    assert "terminal failure state" in result.stderr
    assert "decode-0=CrashLoopBackOff" in result.stderr
    assert _deletions(cmd) == []
    # No budget configured -> no budget noise in the message.
    assert "restart budget" not in result.stderr


def test_without_budget_ready_pods_succeed(tmp_path, monkeypatch):
    cmd = _executor(tmp_path, monkeypatch, [[_pod("decode-0", ready=True)]])
    assert cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1).success


def test_zero_budget_object_behaves_like_no_budget(tmp_path, monkeypatch):
    cmd = _executor(
        tmp_path, monkeypatch, [[_pod("decode-0")]], budget=RestartBudget(0)
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)
    assert result.success is False
    assert _deletions(cmd) == []


# ---------------------------------------------------------------------------
# Restart behavior
# ---------------------------------------------------------------------------


def test_crashing_pod_is_deleted_and_wait_recovers(tmp_path, monkeypatch):
    budget = RestartBudget(3)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [_pod("decode-0")],  # crash detected -> delete
            [],  # replacement not created yet
            [_pod("decode-0", ready=True, uid="uid-new")],  # recovered
        ],
        budget=budget,
    )

    result = cmd.wait_for_pods(
        "app=x", "ns", timeout=600, poll_interval=1, description="decode pods"
    )

    assert result.success is True
    assert _deletions(cmd) == ["decode-0"]
    assert budget.used == 1
    assert "♻️" in cmd.test_logger.text()


def test_deletion_uses_ignore_not_found_and_does_not_block(tmp_path, monkeypatch):
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0")], [_pod("decode-0", ready=True, uid="new")]],
        budget=RestartBudget(1),
    )
    cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    (delete_call,) = [c for c in cmd.kube_calls if c[:2] == ("delete", "pod")]
    assert "--ignore-not-found" in delete_call
    assert "--wait=false" in delete_call


def test_diagnostics_are_captured_before_deletion(tmp_path, monkeypatch):
    """Deleting a pod destroys its logs, so evidence must be gathered first."""
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0")], [_pod("decode-0", ready=True, uid="new")]],
        budget=RestartBudget(1),
    )
    cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    verbs = [c[0] for c in cmd.kube_calls]
    assert verbs.index("describe") < verbs.index("delete")
    assert "logs" in verbs
    previous = [c for c in cmd.kube_calls if c[0] == "logs" and "--previous" in c]
    assert previous, "previous-container logs are the ones that explain a crash loop"


def test_budget_exhaustion_fails_with_an_actionable_message(tmp_path, monkeypatch):
    budget = RestartBudget(1)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [_pod("decode-0", uid="uid-1")],  # restart 1/1
            [_pod("decode-0", uid="uid-2")],  # crashes again, no budget left
        ],
        budget=budget,
    )

    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is False
    assert "restart budget exhausted (1/1)" in result.stderr
    assert "--pod-restart-budget" in result.stderr
    assert _deletions(cmd) == ["decode-0"]


def test_terminal_failure_is_not_restarted_even_with_budget(tmp_path, monkeypatch):
    budget = RestartBudget(5)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0", reason="ImagePullBackOff")]],
        budget=budget,
    )

    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is False
    assert "ImagePullBackOff" in result.stderr
    assert _deletions(cmd) == []
    assert budget.used == 0


def test_uncontrolled_pod_is_not_deleted(tmp_path, monkeypatch):
    budget = RestartBudget(5)
    cmd = _executor(
        tmp_path, monkeypatch, [[_pod("bare-pod", owned=False)]], budget=budget
    )

    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)

    assert result.success is False
    assert _deletions(cmd) == []
    assert budget.used == 0


def test_multiple_pods_each_consume_one_restart(tmp_path, monkeypatch):
    budget = RestartBudget(5)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [_pod("decode-0"), _pod("decode-1")],
            [
                _pod("decode-0", ready=True, uid="n0"),
                _pod("decode-1", ready=True, uid="n1"),
            ],
        ],
        budget=budget,
    )

    assert cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1).success
    assert sorted(_deletions(cmd)) == ["decode-0", "decode-1"]
    assert budget.used == 2


def test_terminating_pod_is_not_repeatedly_deleted(tmp_path, monkeypatch):
    """The replacement takes several polls to appear; budget must not drain."""
    budget = RestartBudget(5)
    crashing = _pod("decode-0", uid="uid-1")
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [
            [crashing],
            [crashing],
            [crashing],
            [_pod("decode-0", ready=True, uid="uid-2")],
        ],
        budget=budget,
    )

    assert cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1).success
    assert budget.used == 1
    assert _deletions(cmd) == ["decode-0"]


def test_pod_that_is_simply_in_error_phase_is_restarted(tmp_path, monkeypatch):
    """The reported case: a pod sitting in Error that only recovers if deleted."""
    failed = PodState.from_api(
        {
            "metadata": {
                "name": "decode-0",
                "uid": "uid-1",
                "namespace": "ns",
                "ownerReferences": [
                    {"kind": "ReplicaSet", "name": "rs", "controller": True}
                ],
            },
            "status": {"phase": "Failed", "reason": "Error"},
        }
    )
    budget = RestartBudget(2)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[failed], [_pod("decode-0", ready=True, uid="uid-2")]],
        budget=budget,
    )

    assert cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1).success
    assert _deletions(cmd) == ["decode-0"]


def test_restart_extends_the_deadline(tmp_path, monkeypatch):
    """A replacement pod re-pulls and reloads; it needs time budget of its own."""
    clock = {"t": 0.0}
    monkeypatch.setattr("llmdbenchmark.executor.command.time.time", lambda: clock["t"])

    budget = RestartBudget(1)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0")], [_pod("decode-0", ready=True, uid="new")]],
        budget=budget,
    )

    original = cmd._observe_pods

    def _advance(label, namespace):
        # Crash surfaces at t=90s of a 100s wait; without the grace extension
        # the replacement would never get a chance.
        clock["t"] += 90.0
        return original(label, namespace)

    monkeypatch.setattr(cmd, "_observe_pods", _advance)

    result = cmd.wait_for_pods("app=x", "ns", timeout=100, poll_interval=1)
    assert result.success is True


def test_timeout_message_reports_budget_usage(tmp_path, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr("llmdbenchmark.executor.command.time.time", lambda: clock["t"])
    budget = RestartBudget(4)
    cmd = _executor(
        tmp_path,
        monkeypatch,
        [[_pod("decode-0")], [_pod("decode-0", uid="u2")]],
        budget=budget,
    )
    original = cmd._observe_pods

    def _advance(label, namespace):
        clock["t"] += 400.0
        return original(label, namespace)

    monkeypatch.setattr(cmd, "_observe_pods", _advance)

    result = cmd.wait_for_pods("app=x", "ns", timeout=100, poll_interval=1)
    assert result.success is False
    assert "Timed out" in result.stderr
    assert "restart budget used: 2/4" in result.stderr.lower()


def test_dry_run_never_deletes(tmp_path):
    logger = _Logger()
    cmd = CommandExecutor(
        work_dir=tmp_path,
        dry_run=True,
        verbose=False,
        logger=logger,
        pod_restart_budget=RestartBudget(5),
    )
    result = cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)
    assert result.dry_run is True
    assert "would have executed" in logger.text()


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_budget_caps_total_restarts_across_waits(tmp_path, monkeypatch, limit):
    """The cap is phase-wide: repeated waits draw from the same pool."""
    budget = RestartBudget(limit)
    for i in range(limit + 2):
        cmd = _executor(
            tmp_path,
            monkeypatch,
            [
                [_pod("decode-0", uid=f"uid-{i}")],
                [_pod("decode-0", ready=True, uid=f"ready-{i}")],
            ],
            budget=budget,
        )
        cmd.wait_for_pods("app=x", "ns", timeout=600, poll_interval=1)
    assert budget.used == limit


# ---------------------------------------------------------------------------
# Full chain: real kubectl JSON -> parse -> policy -> delete
# ---------------------------------------------------------------------------


def _kubectl_payload(name, *, ready, reason="CrashLoopBackOff", uid="uid-1"):
    container = (
        {"name": "vllm", "ready": True, "restartCount": 0, "state": {"running": {}}}
        if ready
        else {
            "name": "vllm",
            "ready": False,
            "restartCount": 4,
            "state": {"waiting": {"reason": reason, "message": "back-off 5m0s"}},
            "lastState": {"terminated": {"reason": "Error", "exitCode": 1}},
        }
    )
    return {
        "apiVersion": "v1",
        "kind": "List",
        "items": [
            {
                "metadata": {
                    "name": name,
                    "namespace": "llmdbench",
                    "uid": uid,
                    "ownerReferences": [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "ReplicaSet",
                            "name": "decode-7f9",
                            "uid": "rs-uid",
                            "controller": True,
                            "blockOwnerDeletion": True,
                        }
                    ],
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [container],
                },
            }
        ],
    }


def test_full_chain_from_kubectl_json_to_deletion(tmp_path, monkeypatch):
    """Exercises the real parse path, not a stubbed observer."""
    import json
    import subprocess as _sp

    payloads = [
        json.dumps(_kubectl_payload("decode-0", ready=False, uid="uid-1")),
        json.dumps(_kubectl_payload("decode-0", ready=True, uid="uid-2")),
    ]
    seen = []

    class _Completed:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def _run(cmd_str, **_):
        seen.append(cmd_str)
        return _Completed(payloads[min(len(seen) - 1, len(payloads) - 1)])

    monkeypatch.setattr(_sp, "run", _run)
    monkeypatch.setattr("llmdbenchmark.executor.command.time.sleep", lambda _s: None)

    logger = _Logger()
    budget = RestartBudget(2)
    cmd = CommandExecutor(
        work_dir=tmp_path,
        dry_run=False,
        verbose=False,
        logger=logger,
        pod_restart_budget=budget,
    )

    deletes = []
    real_kube = cmd.kube

    def _kube(*args, **kwargs):
        if args[:2] == ("delete", "pod"):
            deletes.append(args[2])
            return CommandResult(command="delete", exit_code=0)
        return real_kube(*args, **kwargs)

    monkeypatch.setattr(cmd, "kube", _kube)

    result = cmd.wait_for_pods(
        "llm-d.ai/role=decode",
        "llmdbench",
        timeout=600,
        poll_interval=1,
        description="decode pods",
    )

    assert result.success is True
    assert deletes == ["decode-0"]
    assert budget.used == 1
    assert "-l llm-d.ai/role=decode" in seen[0]


def test_get_pod_statuses_keeps_its_legacy_dict_shape(tmp_path, monkeypatch):
    """wait_for_job and other callers still consume plain dicts."""
    import json
    import subprocess as _sp

    class _Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps(_kubectl_payload("decode-0", ready=False))

    monkeypatch.setattr(_sp, "run", lambda *_a, **_k: _Completed())

    cmd = CommandExecutor(
        work_dir=tmp_path, dry_run=False, verbose=False, logger=_Logger()
    )
    statuses = cmd._get_pod_statuses("app=x", "ns")

    assert statuses == [
        {
            "name": "decode-0",
            "status": "CrashLoopBackOff",
            "ready": False,
            "phase": "Running",
        }
    ]


def test_get_pod_statuses_returns_none_when_query_fails(tmp_path, monkeypatch):
    """A failed query must not read as 'the deployment has no pods'."""
    import subprocess as _sp

    class _Failed:
        returncode = 1
        stdout = ""
        stderr = "the server was unable to return a response"

    monkeypatch.setattr(_sp, "run", lambda *_a, **_k: _Failed())

    cmd = CommandExecutor(
        work_dir=tmp_path, dry_run=False, verbose=False, logger=_Logger()
    )
    assert cmd._get_pod_statuses("app=x", "ns") is None
