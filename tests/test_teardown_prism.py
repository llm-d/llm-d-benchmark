"""Tests for ``teardown --prism``.

The llm-d-prism dashboard survives normal teardown. ``--prism`` (off by
default) removes it without requiring ``--deep``.

Behavior under test:
- The flag defaults to off and maps to ``remove_prism``.
- It does not pick up ``LLMDBENCH_PRISM`` (that env var enables the deploy).
- ``_delete_prism`` deletes by the prism app label, adding ``route`` on
  OpenShift, and never touches PVCs.
- A failed delete is reported as an error.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from llmdbenchmark.interface import teardown
from llmdbenchmark.teardown.steps.step_03_delete_resources import (
    PRISM_APP_LABEL,
    DeleteResourcesStep,
)


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


@dataclass
class _StubCmd:
    fail: bool = False
    kube_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        if self.fail:
            return _StubResult(success=False, stderr="boom")
        return _StubResult(success=True)


@dataclass
class _StubContext:
    is_openshift: bool = False
    logger: _StubLogger = field(default_factory=_StubLogger)


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    teardown.add_subcommands(subparsers)
    return parser.parse_args(["teardown", *argv])


def test_flag_defaults_off():
    args = _parse([])
    assert args.remove_prism is False
    assert not hasattr(args, "prism")


def test_flag_enables_removal():
    assert _parse(["--prism"]).remove_prism is True


def test_deletes_by_label_without_route_off_openshift():
    cmd, context, errors = _StubCmd(), _StubContext(), []
    DeleteResourcesStep._delete_prism(cmd, context, "ns1", errors)
    assert errors == []
    assert len(cmd.kube_calls) == 1
    call = cmd.kube_calls[0]
    assert call[0] == "delete"
    assert call[1] == "deployment,service"
    assert PRISM_APP_LABEL in call
    assert "ns1" in call
    assert not any("pvc" in str(a) for a in call)


def test_deletes_route_on_openshift():
    cmd, context = _StubCmd(), _StubContext(is_openshift=True)
    DeleteResourcesStep._delete_prism(cmd, context, "ns1", [])
    assert cmd.kube_calls[0][1] == "deployment,service,route"


def test_failed_delete_is_an_error():
    cmd, context, errors = _StubCmd(fail=True), _StubContext(), []
    DeleteResourcesStep._delete_prism(cmd, context, "ns1", errors)
    assert len(errors) == 1
    assert any(m.startswith("WARN:") for m in context.logger.messages)
