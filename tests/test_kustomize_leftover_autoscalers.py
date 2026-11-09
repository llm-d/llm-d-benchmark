"""Tests for the leftover-autoscaler cleanup in step_05_kustomize_deploy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from llmdbenchmark.standup.steps.step_05_kustomize_deploy import (
    KustomizeDeployStep,
)


@dataclass
class FakeResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeCommandExecutor:
    get_handler: Any = field(default_factory=lambda: lambda *a, **k: FakeResult(True))
    delete_handler: Any = field(
        default_factory=lambda: lambda *a, **k: FakeResult(True)
    )
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = field(default_factory=list)

    def kube(self, *args: str, **kwargs: Any) -> FakeResult:
        self.calls.append((tuple(args), dict(kwargs)))
        if args[0] == "get":
            return self.get_handler(*args, **kwargs)
        if args[0] == "delete":
            return self.delete_handler(*args, **kwargs)
        return FakeResult(True)


def _fake_context() -> Any:
    ctx = MagicMock()
    ctx.logger = MagicMock()
    return ctx


def test_leftover_scaledobjects_deleted_with_hpa_sweep():
    cmd = FakeCommandExecutor(
        get_handler=lambda *a, **k: FakeResult(
            True, stdout="scaledobject.keda.sh/queue-a\nscaledobject.keda.sh/queue-b\n"
        ),
    )

    KustomizeDeployStep._remove_leftover_autoscalers(cmd, _fake_context(), "my-ns")

    deletes = [args for args, _ in cmd.calls if args[0] == "delete"]
    assert deletes == [
        (
            "delete",
            "scaledobject.keda.sh/queue-a",
            "-n",
            "my-ns",
            "--ignore-not-found=true",
        ),
        (
            "delete",
            "scaledobject.keda.sh/queue-b",
            "-n",
            "my-ns",
            "--ignore-not-found=true",
        ),
        (
            "delete",
            "hpa",
            "-n",
            "my-ns",
            "-l",
            "scaledobject.keda.sh/name",
            "--ignore-not-found=true",
        ),
    ]


@pytest.mark.parametrize(
    "get_result",
    [
        # No scaledobjects.keda.sh CRD (non-KEDA guide).
        FakeResult(False, stderr="the server doesn't have a resource type"),
        # Fresh namespace, nothing left over.
        FakeResult(True, stdout=""),
    ],
    ids=["no-crd", "empty"],
)
def test_nothing_to_remove_is_a_no_op(get_result):
    cmd = FakeCommandExecutor(get_handler=lambda *a, **k: get_result)

    KustomizeDeployStep._remove_leftover_autoscalers(cmd, _fake_context(), "my-ns")

    assert [args[0] for args, _ in cmd.calls] == ["get"]


def test_failed_delete_warns_instead_of_raising():
    cmd = FakeCommandExecutor(
        get_handler=lambda *a, **k: FakeResult(True, stdout="scaledobject.keda.sh/s\n"),
        delete_handler=lambda *a, **k: FakeResult(False, stderr="boom"),
    )
    ctx = _fake_context()

    KustomizeDeployStep._remove_leftover_autoscalers(cmd, ctx, "my-ns")

    ctx.logger.log_warning.assert_called_once()
