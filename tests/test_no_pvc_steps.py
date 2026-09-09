"""--no-pvc: run steps that create or read the workload PVC must skip."""

from __future__ import annotations

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_02_harness_namespace import (
    HarnessNamespaceStep,
)
from llmdbenchmark.run.steps.step_09_collect_results import CollectResultsStep


def _context(tmp_path, **kwargs) -> ExecutionContext:
    return ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, **kwargs)


def test_harness_namespace_step_skips_with_no_pvc(tmp_path) -> None:
    assert HarnessNamespaceStep().should_skip(_context(tmp_path, no_pvc=True))


def test_harness_namespace_step_runs_by_default(tmp_path) -> None:
    assert not HarnessNamespaceStep().should_skip(_context(tmp_path))


def test_collect_results_step_skips_with_no_pvc(tmp_path) -> None:
    assert CollectResultsStep().should_skip(_context(tmp_path, no_pvc=True))


def test_collect_results_step_runs_by_default(tmp_path) -> None:
    # Empty results dir + k8s mode: the fallback collector should run.
    assert not CollectResultsStep().should_skip(_context(tmp_path))


import time

from llmdbenchmark.run.steps.step_07_deploy_harness import DeployHarnessStep
from llmdbenchmark.utilities.kube_helpers import (
    HARNESS_DONE_SENTINEL,
    wait_for_harness_sentinels,
)


class _Result:
    def __init__(self, success: bool = True, stdout: str = "", stderr: str = ""):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = 0 if success else 1


class _FakeLogger:
    def __init__(self):
        self.messages: list[str] = []

    def log_info(self, msg, **kwargs):
        self.messages.append(msg)

    def log_warning(self, msg, **kwargs):
        self.messages.append(msg)

    def log_error(self, msg, **kwargs):
        self.messages.append(msg)


class _FakeCmd:
    """Replays scripted results; records every kube() call."""

    def __init__(self, results: list[_Result]):
        self._results = list(results)
        self.calls: list[tuple] = []

    def kube(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._results:
            return self._results.pop(0)
        return _Result(success=False, stderr="script exhausted")


def _wait_context(tmp_path):
    return ExecutionContext(
        plan_dir=tmp_path, workspace=tmp_path, logger=_FakeLogger()
    )


def test_keepalive_command_wraps_and_sleeps() -> None:
    wrapped = DeployHarnessStep._no_pvc_keepalive_command(
        "export A=1; llm-d-benchmark.sh", "/requests"
    )
    assert wrapped == (
        "(export A=1; llm-d-benchmark.sh); "
        "echo $? > /requests/.llmdbench_harness_done; sleep infinity"
    )


def test_sentinel_wait_success(tmp_path) -> None:
    cmd = _FakeCmd([_Result(success=True, stdout="0\n")])
    errors = wait_for_harness_sentinels(
        cmd, ["pod-a"], "ns", f"/requests/{HARNESS_DONE_SENTINEL}", 60,
        _wait_context(tmp_path),
    )
    assert errors == []


def test_sentinel_wait_nonzero_exit_is_an_error(tmp_path) -> None:
    cmd = _FakeCmd([_Result(success=True, stdout="2\n")])
    errors = wait_for_harness_sentinels(
        cmd, ["pod-a"], "ns", f"/requests/{HARNESS_DONE_SENTINEL}", 60,
        _wait_context(tmp_path),
    )
    assert len(errors) == 1
    assert "exited with code 2" in errors[0]


def test_sentinel_wait_terminal_pod_fails_fast(tmp_path) -> None:
    # exec fails (no sentinel), pod phase is Failed -> crash before sentinel.
    cmd = _FakeCmd(
        [
            _Result(success=False, stderr="error"),
            _Result(success=True, stdout="Failed"),
        ]
    )
    errors = wait_for_harness_sentinels(
        cmd, ["pod-a"], "ns", f"/requests/{HARNESS_DONE_SENTINEL}", 600,
        _wait_context(tmp_path),
    )
    assert len(errors) == 1
    assert "Failed" in errors[0]


def test_sentinel_wait_times_out(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    # Pod stays Running and never writes the sentinel.
    cmd = _FakeCmd(
        [_Result(success=False), _Result(success=True, stdout="Running")] * 50
    )
    errors = wait_for_harness_sentinels(
        cmd, ["pod-a"], "ns", f"/requests/{HARNESS_DONE_SENTINEL}", 10,
        _wait_context(tmp_path),
    )
    assert len(errors) == 1
    assert "did not write" in errors[0]


def test_copy_dir_from_pod_uses_kubectl_cp_with_retries(tmp_path) -> None:
    cmd = _FakeCmd([_Result(success=True)])
    result = DeployHarnessStep._copy_dir_from_pod(
        cmd,
        "harness-pod-1",
        "ns",
        "/requests/exp-1_1",
        tmp_path / "exp-1_1",
        _wait_context(tmp_path),
        fast_collect=False,
        dir_compressed=False,
    )
    assert result.success
    args, kwargs = cmd.calls[0]
    assert args == (
        "cp",
        "--retries=5",
        "harness-pod-1:/requests/exp-1_1",
        str(tmp_path / "exp-1_1"),
    )
    assert kwargs == {"namespace": "ns", "check": False}
