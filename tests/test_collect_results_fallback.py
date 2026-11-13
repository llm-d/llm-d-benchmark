"""Tests for step 09's fallback collection from the data-access pod.

Step 07 runs pod N of a treatment in ``<results prefix>/<experiment_id>_<N>``,
so the directories step 09 copies must be the ones carrying a recorded
experiment ID, not a directory named after the bare ID.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps import step_09_collect_results
from llmdbenchmark.run.steps.step_09_collect_results import CollectResultsStep

EXP_ID = "inference-perf-1761800000-a1b2c3"


class _Result:
    def __init__(self, success: bool = True, stdout: str = "", stderr: str = ""):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr


class _FakeLogger:
    def __init__(self):
        self.warnings: list[str] = []

    def log_info(self, msg, **kwargs):
        pass

    def log_warning(self, msg, **kwargs):
        self.warnings.append(msg)

    def log_error(self, msg, **kwargs):
        pass


class _FakeCmd:
    """Answers the ``ls`` of the results prefix on the data-access pod."""

    def __init__(self, ls_result: _Result):
        self.ls_result = ls_result

    def kube(self, *args, **kwargs):
        return self.ls_result


@pytest.fixture
def copied(monkeypatch):
    """Record the remote dirs ``_copy`` is asked for; each yields one file."""
    calls: list[tuple[str, Path]] = []

    def fake_copy(cmd, data_pod, namespace, prefix, dir_name, local_path, context):
        calls.append((f"{prefix}/{dir_name}", local_path))
        (local_path / "benchmark_report_v0.2.yaml").write_text("{}\n")
        return _Result()

    monkeypatch.setattr(CollectResultsStep, "_copy", staticmethod(fake_copy))
    monkeypatch.setattr(
        step_09_collect_results,
        "find_data_access_pod",
        lambda *args, **kwargs: "data-pod",
    )
    return calls


def _execute(tmp_path, ls_result: _Result, experiment_ids: list[str]):
    context = ExecutionContext(
        plan_dir=tmp_path,
        workspace=tmp_path,
        logger=_FakeLogger(),
        namespace="bench",
    )
    context.cmd = _FakeCmd(ls_result)
    context.experiment_ids = experiment_ids
    stack_path = tmp_path / "stack"
    stack_path.mkdir()
    return context, CollectResultsStep().execute(context, stack_path)


def test_skips_debug_runs(tmp_path) -> None:
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path)
    context.harness_debug = True
    assert CollectResultsStep().should_skip(context)


def test_copies_each_parallel_pod_dir_of_a_recorded_id(tmp_path, copied) -> None:
    ls_output = f"{EXP_ID}_1\n{EXP_ID}_2\ninference-perf-1761700000-zzzzzz_1\n"

    context, result = _execute(tmp_path, _Result(stdout=ls_output), [EXP_ID])

    assert result.success
    assert [remote for remote, _ in copied] == [
        f"/requests/{EXP_ID}_1",
        f"/requests/{EXP_ID}_2",
    ]
    assert [local.name for _, local in copied] == [f"{EXP_ID}_1", f"{EXP_ID}_2"]
    assert not (context.run_results_dir() / EXP_ID).exists()


def test_recorded_id_without_dirs_is_reported(tmp_path, copied) -> None:
    context, result = _execute(
        tmp_path, _Result(stdout="inference-perf-1761700000-zzzzzz_1\n"), [EXP_ID]
    )

    assert copied == []
    assert result.success
    assert any(EXP_ID in w for w in context.logger.warnings)


def test_unlistable_pvc_fails_the_step(tmp_path, copied) -> None:
    _, result = _execute(
        tmp_path, _Result(success=False, stderr="connection refused"), [EXP_ID]
    )

    assert copied == []
    assert not result.success
    assert any("Could not list /requests" in e for e in result.errors)
