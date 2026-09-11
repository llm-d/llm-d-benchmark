"""Run step 00 preflight: harness namespace existence must not hard-fail.

Under the standup/run phase-separation contract, the harness namespace is
created later in the same run by step 02 (harness prep) -- it no longer has
to pre-exist from a prior standup. Step 00 should note that and continue,
not error out with "Run the standup phase first."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llmdbenchmark.executor.command import CommandResult
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps.step_00_preflight import RunPreflightStep


class _Logger:
    def line_break(self, *a: Any, **k: Any) -> None: ...
    def log_info(self, *a: Any, **k: Any) -> None: ...
    def log_warning(self, *a: Any, **k: Any) -> None: ...
    def log_error(self, *a: Any, **k: Any) -> None: ...


class _Cmd:
    def __init__(self, namespace_exists: bool) -> None:
        self.namespace_exists = namespace_exists
        self.kube_calls: list[tuple] = []

    def kube(self, *args: Any, **kwargs: Any) -> CommandResult:
        self.kube_calls.append(args)
        if args[:2] == ("get", "namespace"):
            exit_code = 0 if self.namespace_exists else 1
            return CommandResult(command="get ns", exit_code=exit_code)
        return CommandResult(command=" ".join(str(a) for a in args), exit_code=0)


def _context(
    tmp_path: Path, *, namespace_exists: bool, **kwargs: Any
) -> ExecutionContext:
    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        logger=_Logger(),
        namespace="bench",
        harness_namespace="bench-harness",
        **kwargs,
    )
    context.cmd = _Cmd(namespace_exists=namespace_exists)
    # Bypass real cluster resolution -- the fake CommandExecutor above
    # already stands in for it.
    context.resolve_cluster = lambda: None  # type: ignore[method-assign]
    context.rebuild_cmd = lambda: context.cmd  # type: ignore[method-assign]
    return context


def test_missing_harness_namespace_does_not_fail_preflight(tmp_path) -> None:
    """Step 02 (harness prep) creates the namespace later in this same run,
    so a missing harness namespace at preflight time is expected, not fatal."""
    ctx = _context(tmp_path, namespace_exists=False)
    step = RunPreflightStep()
    result = step.execute(ctx)
    assert result.success
    assert not result.errors


def test_existing_harness_namespace_still_passes_preflight(tmp_path) -> None:
    ctx = _context(tmp_path, namespace_exists=True)
    step = RunPreflightStep()
    result = step.execute(ctx)
    assert result.success
