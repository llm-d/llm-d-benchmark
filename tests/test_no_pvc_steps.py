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
