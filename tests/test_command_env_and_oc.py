"""Environment variables passed to a single command must not leak to the
process, and a missing oc must fail clearly instead of reading as an
empty answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from llmdbenchmark.executor.command import OC_MISSING_MESSAGE, CommandExecutor


@dataclass
class _RunCapture:
    calls: list[dict[str, Any]] = field(default_factory=list)


def _patch_run(monkeypatch) -> _RunCapture:
    capture = _RunCapture()

    def fake_run(cmd, **kwargs):
        capture.calls.append({"cmd": cmd, **kwargs})
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    monkeypatch.setattr("llmdbenchmark.executor.command.subprocess.run", fake_run)
    return capture


def _executor(tmp_path: Path, **kwargs) -> CommandExecutor:
    return CommandExecutor(work_dir=tmp_path, dry_run=False, verbose=False, **kwargs)


def test_helmfile_env_reaches_only_that_command(tmp_path, monkeypatch):
    import os

    capture = _patch_run(monkeypatch)
    cmd = _executor(tmp_path)

    cmd.helmfile("apply", env={"HELM_DIFF_THREE_WAY_MERGE": "true"})
    cmd.helmfile("apply")

    first, second = capture.calls
    assert first["env"]["HELM_DIFF_THREE_WAY_MERGE"] == "true"
    # the merged environment still carries the caller's PATH
    assert first["env"]["PATH"] == os.environ["PATH"]
    # the next call inherits the plain environment again
    assert second["env"] is None


def test_kube_without_oc_fails_clearly_on_every_call(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "llmdbenchmark.executor.command.shutil.which", lambda _name: None
    )
    cmd = _executor(tmp_path, openshift=True)

    first = cmd.kube("get", "pods")
    second = cmd.kube("get", "namespaces")

    # the first call is often a local config read; the message must not
    # be used up there
    assert not first.success
    assert first.stderr == OC_MISSING_MESSAGE
    assert not second.success
    assert second.stderr == OC_MISSING_MESSAGE


def test_the_oc_check_blocks_neither_dry_run_nor_an_installed_oc(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "llmdbenchmark.executor.command.shutil.which", lambda _name: None
    )
    capture = _patch_run(monkeypatch)
    dry_run = CommandExecutor(
        work_dir=tmp_path, dry_run=True, verbose=False, openshift=True
    )

    assert dry_run.kube("get", "pods").dry_run

    monkeypatch.setattr(
        "llmdbenchmark.executor.command.shutil.which", lambda _name: "/usr/bin/oc"
    )
    live = _executor(tmp_path, openshift=True)

    assert live.kube("get", "pods").success
    assert capture.calls
