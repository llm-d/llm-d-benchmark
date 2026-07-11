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
        self.logger = _Logger()

    def run_dir(self) -> Path:
        return self._run_dir

    def workload_profiles_dir(self) -> Path:
        path = self._run_dir / "workload" / "profiles"
        path.mkdir(parents=True, exist_ok=True)
        return path


class _Logger:
    def log_info(self, *_: Any, **__: Any) -> None:
        pass


class _StubLogger:
    def log_info(self, _message: str) -> None:
        pass


class _ScriptContext(_StubContext):
    def __init__(self, run_dir: Path, base_dir: Path) -> None:
        super().__init__(run_dir)
        self.base_dir = base_dir
        self.logger = _StubLogger()


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


def test_harness_configmap_includes_repository_analyzers(tmp_path: Path) -> None:
    base_dir = tmp_path / "repo"
    harnesses_dir = base_dir / "workload" / "harnesses"
    analyzers_dir = base_dir / "llmdbenchmark" / "analysis" / "scripts"
    harnesses_dir.mkdir(parents=True)
    analyzers_dir.mkdir(parents=True)
    harness = harnesses_dir / "lm-eval-llm-d-benchmark.sh"
    analyzer = analyzers_dir / "lm-eval-analyze_results.sh"
    ignored = analyzers_dir / "helper.py"
    harness.write_text("#!/bin/sh\n", encoding="utf-8")
    analyzer.write_text("#!/bin/sh\n", encoding="utf-8")
    ignored.write_text("", encoding="utf-8")

    cmd = _StubCmd(
        [
            _Result(success=True, stdout="apiVersion: v1\nkind: ConfigMap\n"),
            _Result(success=True),
        ]
    )
    context = _ScriptContext(tmp_path / "run", base_dir)
    context.run_dir().mkdir()

    ok, _ = CreateProfileConfigmapStep()._create_harness_scripts_configmap(
        context, cmd, "bench"
    )

    assert ok
    create_args = cmd.kube_calls[0][0]
    assert f"--from-file={harness.name}={harness}" in create_args
    assert f"--from-file={analyzer.name}={analyzer}" in create_args
    assert not any(str(ignored) in arg for arg in create_args)


def test_debug_profiles_create_configmap_for_each_harness(tmp_path: Path) -> None:
    context = _StubContext(tmp_path)
    profiles_root = context.workload_profiles_dir()
    (profiles_root / "inference-perf").mkdir()
    (profiles_root / "guidellm").mkdir()
    (profiles_root / "inference-perf" / "a.yaml").write_text("a: 1\n")
    (profiles_root / "guidellm" / "b.yaml").write_text("b: 1\n")
    cmd = _StubCmd(
        [
            _Result(success=True, stdout="apiVersion: v1\nkind: ConfigMap\n"),
            _Result(success=True),
            _Result(success=True, stdout="apiVersion: v1\nkind: ConfigMap\n"),
            _Result(success=True),
        ]
    )

    results = CreateProfileConfigmapStep()._create_debug_profiles_configmaps(
        context,
        cmd,
        "bench",
    )

    assert results == [
        (True, "ConfigMap 'guidellm-profiles' created"),
        (True, "ConfigMap 'inference-perf-profiles' created"),
    ]
    created_configmaps = [
        call[0][2] for call in cmd.kube_calls if call[0][:2] == ("create", "configmap")
    ]
    assert created_configmaps == ["guidellm-profiles", "inference-perf-profiles"]
