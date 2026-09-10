"""Run step 02 behavior matrix: what it creates per mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmdbenchmark.executor.command import CommandResult
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_02_harness_namespace import HarnessNamespaceStep


class _Logger:
    def log_info(self, *a: Any, **k: Any) -> None: ...
    def log_warning(self, *a: Any, **k: Any) -> None: ...
    def log_error(self, *a: Any, **k: Any) -> None: ...


class _Cmd:
    def __init__(self) -> None:
        self.kube_calls: list[tuple] = []
        self.pvc_waits = 0
        self.pod_waits = 0

    def kube(self, *args: Any, **kwargs: Any) -> CommandResult:
        self.kube_calls.append(args)
        if args[:2] == ("get", "namespace"):
            return CommandResult(command="get ns", exit_code=1)
        return CommandResult(command=" ".join(str(a) for a in args), exit_code=0)

    def wait_for_pvc(self, **k: Any) -> CommandResult:
        self.pvc_waits += 1
        return CommandResult(command="wait pvc", exit_code=0)

    def wait_for_pods(self, **k: Any) -> CommandResult:
        self.pod_waits += 1
        return CommandResult(command="wait pods", exit_code=0)


def _context(tmp_path: Path, **kwargs: Any) -> ExecutionContext:
    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        logger=_Logger(),
        namespace="bench",
        harness_namespace="bench",
        **kwargs,
    )
    context.cmd = _Cmd()
    return context


def test_no_pvc_mode_still_prepares_namespace(tmp_path) -> None:
    """--no-pvc no longer skips the step: namespace/secret/ConfigMap happen,
    PVC and data-access work do not."""
    ctx = _context(tmp_path, no_pvc=True)
    step = HarnessNamespaceStep()
    assert step.should_skip(ctx) is False
    result = step.execute(ctx)
    assert result.success
    assert any(c[0] == "apply" for c in ctx.cmd.kube_calls)
    assert ctx.cmd.pvc_waits == 0
    assert ctx.cmd.pod_waits == 0


def test_pvc_mode_reaches_data_access_wait(tmp_path) -> None:
    """Default mode: with no rendered PVC yaml present the PVC block is a
    no-op, but the data-access pod wait still runs (existing behavior)."""
    ctx = _context(tmp_path)
    result = HarnessNamespaceStep().execute(ctx)
    assert result.success
    assert ctx.cmd.pod_waits == 1


def test_skip_only_for_nok8s_and_collect_only(tmp_path) -> None:
    step = HarnessNamespaceStep()
    assert step.should_skip(_context(tmp_path, deployed_methods=["nok8s"]))
    assert step.should_skip(_context(tmp_path, harness_skip_run=True))
    assert not step.should_skip(_context(tmp_path, no_pvc=True))
    # kustomize standups no longer skip run-phase harness prep either
    assert not step.should_skip(_context(tmp_path, deployed_methods=["kustomize"]))


def test_step_metadata(tmp_path) -> None:
    from llmdbenchmark.executor.step import Phase

    step = HarnessNamespaceStep()
    assert step.number == 2
    assert step.phase == Phase.RUN
