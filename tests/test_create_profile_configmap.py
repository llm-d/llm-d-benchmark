"""Tests for creating workload profile ConfigMaps."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_06_create_profile_configmap.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_06_create_profile_configmap_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_06_create_profile_configmap_isolated"] = _module
_spec.loader.exec_module(_module)
CreateProfileConfigmapStep = _module.CreateProfileConfigmapStep


@dataclass
class _Result:
    success: bool
    stdout: str = ""
    stderr: str = ""


class _StubCmd:
    def __init__(self, results: list[_Result]) -> None:
        self._results = results
        self.kube_calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def kube(self, *args: str, **kwargs: Any) -> _Result:
        self.kube_calls.append((args, kwargs))
        return self._results.pop(0)


class _StubContext:
    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def run_dir(self) -> Path:
        return self._run_dir


def test_kubectl_create_configmap_uses_server_side_apply(tmp_path: Path) -> None:
    cmd = _StubCmd(
        [
            _Result(
                success=True,
                stdout="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: profiles\n",
            ),
            _Result(success=True),
        ]
    )
    context = _StubContext(tmp_path)

    ok, msg = CreateProfileConfigmapStep._kubectl_create_configmap(
        cmd,
        "profiles",
        ["--from-file=profile.yaml=/tmp/profile.yaml"],
        "bench",
        context,
    )

    assert ok
    assert msg == "ConfigMap 'profiles' created"
    assert (
        (tmp_path / "profiles.yaml")
        .read_text(encoding="utf-8")
        .startswith("apiVersion: v1")
    )
    assert cmd.kube_calls[0] == (
        (
            "create",
            "configmap",
            "profiles",
            "--from-file=profile.yaml=/tmp/profile.yaml",
            "--namespace",
            "bench",
            "--dry-run=client",
            "-o",
            "yaml",
        ),
        {"check": False},
    )
    assert cmd.kube_calls[1] == (
        (
            "apply",
            "--server-side",
            "-f",
            str(tmp_path / "profiles.yaml"),
            "--namespace",
            "bench",
        ),
        {"check": False},
    )


def test_kubectl_create_configmap_returns_generation_error(tmp_path: Path) -> None:
    cmd = _StubCmd([_Result(success=False, stderr="bad profile")])
    context = _StubContext(tmp_path)

    ok, msg = CreateProfileConfigmapStep._kubectl_create_configmap(
        cmd,
        "profiles",
        ["--from-file=profile.yaml=/tmp/profile.yaml"],
        "bench",
        context,
    )

    assert not ok
    assert msg == "Failed to generate ConfigMap 'profiles' YAML: bad profile"
    assert len(cmd.kube_calls) == 1


def test_kubectl_create_configmap_returns_apply_error(tmp_path: Path) -> None:
    cmd = _StubCmd(
        [
            _Result(success=True, stdout="apiVersion: v1\nkind: ConfigMap\n"),
            _Result(success=False, stderr="annotation too long"),
        ]
    )
    context = _StubContext(tmp_path)

    ok, msg = CreateProfileConfigmapStep._kubectl_create_configmap(
        cmd,
        "profiles",
        ["--from-file=profile.yaml=/tmp/profile.yaml"],
        "bench",
        context,
    )

    assert not ok
    assert msg == "Failed to apply ConfigMap 'profiles': annotation too long"
    assert len(cmd.kube_calls) == 2
