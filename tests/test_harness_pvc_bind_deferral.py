"""Tests for carrying a deferred PVC bind budget into the data-access wait."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.command import CommandExecutor, CommandResult
from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.interface import standup
from llmdbenchmark.standup.steps.step_05_harness_namespace import HarnessNamespaceStep


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.errors: list[str] = []

    def log_info(self, message: str, *_: Any, **__: Any) -> None:
        self.infos.append(message)

    def log_warning(self, *_: Any, **__: Any) -> None:
        pass

    def log_error(self, message: str, *_: Any, **__: Any) -> None:
        self.errors.append(message)

    def log_debug(self, *_: Any, **__: Any) -> None:
        pass

    def set_indent(self, *_: Any, **__: Any) -> None:
        pass

    def line_break(self) -> None:
        pass


class _Command:
    """Fake CommandExecutor: records the timeout handed to wait_for_pods."""

    def __init__(self, bind_skipped: bool, pods_ready: bool = True) -> None:
        self._bind_skipped = bind_skipped
        self._pods_ready = pods_ready
        self.pod_wait_timeout: int | None = None

    def kube(self, *args: str, **_: Any) -> CommandResult:
        return CommandResult(command=" ".join(args), exit_code=0)

    def wait_for_pvc(self, **_: Any) -> CommandResult:
        return CommandResult(
            command="wait pvc", exit_code=0, wait_skipped=self._bind_skipped
        )

    def wait_for_pods(self, **kwargs: Any) -> CommandResult:
        self.pod_wait_timeout = kwargs["timeout"]
        if self._pods_ready:
            return CommandResult(command="wait pods", exit_code=0)
        return CommandResult(
            command="wait pods",
            exit_code=1,
            stderr=f"Timed out after {kwargs['timeout']}s waiting for pods",
        )


def _context(tmp_path: Path, cmd: _Command, logger: _Logger) -> ExecutionContext:
    stack = tmp_path / "plan" / "stack"
    stack.mkdir(parents=True)
    (stack / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "namespace": {"name": "bench"},
                "harness": {"namespace": "bench"},
                # Skip the secret-copy path; it is not what these tests cover.
                "huggingface": {"enabled": False},
                "storage": {"workloadPvc": {"name": "workload-pvc", "size": "20Gi"}},
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "01_pvc_workload-pvc.yaml",
        "06_pod_access_to_harness_data.yaml",
        "07_service_access_to_harness_data.yaml",
    ):
        (stack / name).write_text("kind: Placeholder\n", encoding="utf-8")

    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path / "ws",
        rendered_stacks=[stack],
        namespace="bench",
        logger=logger,
    )
    context.cmd = cmd
    return context


def test_deferred_bind_extends_data_access_wait(tmp_path: Path) -> None:
    """A skipped bind wait hands its unspent budget to the pod wait."""
    cmd = _Command(bind_skipped=True)
    logger = _Logger()
    context = _context(tmp_path, cmd, logger)

    result = HarnessNamespaceStep().execute(context)

    assert result.success, result.errors
    assert cmd.pod_wait_timeout == (
        context.harness_data_access_timeout + context.pvc_bind_timeout
    )
    assert any("PVC bind wait was skipped" in msg for msg in logger.infos)


def test_immediate_bind_keeps_data_access_wait(tmp_path: Path) -> None:
    """An immediate-binding StorageClass leaves the pod wait untouched."""
    cmd = _Command(bind_skipped=False)
    context = _context(tmp_path, cmd, _Logger())

    result = HarnessNamespaceStep().execute(context)

    assert result.success, result.errors
    assert cmd.pod_wait_timeout == context.harness_data_access_timeout


def test_wait_for_pvc_flags_skipped_bind(tmp_path: Path, monkeypatch) -> None:
    """The short-circuit succeeds and reports that it skipped the wait."""
    executor = CommandExecutor(work_dir=tmp_path, dry_run=False, verbose=False)
    monkeypatch.setattr(
        executor,
        "_resolve_pvc_binding_mode",
        lambda *_a, **_k: "WaitForFirstConsumer",
    )

    result = executor.wait_for_pvc(
        pvc_name="workload-pvc", namespace="bench", timeout=240
    )

    assert result.success
    assert result.wait_skipped


def test_deferred_bind_failure_names_the_cause(tmp_path: Path) -> None:
    """A pod timeout after a deferred bind explains itself and names the knobs."""
    cmd = _Command(bind_skipped=True, pods_ready=False)
    context = _context(tmp_path, cmd, _Logger())

    result = HarnessNamespaceStep().execute(context)

    assert not result.success
    joined = " ".join(result.errors)
    assert "WaitForFirstConsumer" in joined
    assert "--data-access-timeout" in joined


def test_standup_accepts_the_timeout_the_hint_names() -> None:
    """The knob the failure hint recommends exists in the phase that prints it."""
    parser = argparse.ArgumentParser()
    standup.add_subcommands(parser.add_subparsers(dest="command"))

    args = parser.parse_args(["standup", "--data-access-timeout", "900"])

    assert args.data_access_timeout == 900
