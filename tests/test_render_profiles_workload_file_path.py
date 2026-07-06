"""Tests for local workload profile path rendering."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_05_render_profiles.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_05_render_profiles_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_05_render_profiles_isolated"] = _module
_spec.loader.exec_module(_module)
RenderProfilesStep = _module.RenderProfilesStep


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")


@dataclass
class _Context:
    workspace: Path
    base_dir: Path
    workload_file_path: str | None = None
    harness_name: str | None = "inference-perf"
    harness_profile: str | None = None
    deployed_endpoints: dict[str, str] = field(default_factory=dict)
    model_name: str | None = None
    dataset_url: str | None = None
    dry_run: bool = False
    experiment_treatments_file: str | None = None
    profile_overrides: str | None = None
    experiment_treatments: list[dict] | None = None
    logger: _Logger = field(default_factory=_Logger)

    def workload_profiles_dir(self) -> Path:
        path = self.workspace / "workload" / "profiles"
        path.mkdir(parents=True, exist_ok=True)
        return path


def _write_stack(tmp_path: Path) -> Path:
    stack_path = tmp_path / "stack"
    stack_path.mkdir()
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "harness": {"name": "inference-perf"},
                "namespace": {"name": "bench"},
            }
        ),
        encoding="utf-8",
    )
    return stack_path


def test_render_profiles_uses_local_workload_file_path(tmp_path: Path) -> None:
    workload_file = tmp_path / "custom" / "local_profile.yaml.in"
    workload_file.parent.mkdir()
    workload_file.write_text("target: local\n", encoding="utf-8")
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(workload_file),
    )

    result = RenderProfilesStep().execute(context, _write_stack(tmp_path))

    assert result.success
    rendered = context.workload_profiles_dir() / "inference-perf" / "local_profile.yaml"
    assert rendered.read_text(encoding="utf-8") == "target: local\n"
    assert context.harness_profile == "local_profile.yaml"


def test_render_profiles_resolves_relative_workload_file_path(
    tmp_path: Path, monkeypatch: Any
) -> None:
    workload_file = tmp_path / "custom" / "relative_profile.yaml.in"
    workload_file.parent.mkdir()
    workload_file.write_text("target: relative\n", encoding="utf-8")
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path="custom/relative_profile.yaml.in",
    )
    monkeypatch.chdir(tmp_path)

    result = RenderProfilesStep().execute(context, _write_stack(tmp_path))

    assert result.success
    rendered = (
        context.workload_profiles_dir() / "inference-perf" / "relative_profile.yaml"
    )
    assert rendered.read_text(encoding="utf-8") == "target: relative\n"
    assert context.harness_profile == "relative_profile.yaml"


def test_render_profiles_falls_back_to_workload_directory(tmp_path: Path) -> None:
    base_dir = tmp_path / "base"
    profiles_dir = base_dir / "workload" / "profiles" / "inference-perf"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "sanity_random.yaml.in").write_text(
        "target: default\n", encoding="utf-8"
    )
    context = _Context(workspace=tmp_path / "workspace", base_dir=base_dir)

    result = RenderProfilesStep().execute(context, _write_stack(tmp_path))

    assert result.success
    rendered = context.workload_profiles_dir() / "inference-perf" / "sanity_random.yaml"
    assert rendered.read_text(encoding="utf-8") == "target: default\n"
    assert context.harness_profile is None


def test_render_profiles_returns_error_for_missing_local_workload_file(
    tmp_path: Path,
) -> None:
    missing_file = tmp_path / "missing.yaml"
    context = _Context(
        workspace=tmp_path / "workspace",
        base_dir=tmp_path / "base",
        workload_file_path=str(missing_file),
    )

    result = RenderProfilesStep().execute(context, _write_stack(tmp_path))

    assert not result.success
    assert result.message == "Workload profile file not found"
    assert result.errors == [f"Workload profile file not found: {missing_file}"]
