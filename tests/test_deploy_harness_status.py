"""Tests for harness deployment status reporting."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.context import ExecutionContext

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_07_deploy_harness.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_07_deploy_harness_status", _STEP_PATH
)
deploy_harness = importlib.util.module_from_spec(_spec)
sys.modules["step_07_deploy_harness_status"] = deploy_harness
_spec.loader.exec_module(deploy_harness)
DeployHarnessStep = deploy_harness.DeployHarnessStep


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

    def line_break(self) -> None:
        pass


class _Result:
    success = True
    stdout = ""
    stderr = ""
    dry_run = False


class _Command:
    def kube(self, *args: str, **_: Any) -> _Result:
        assert args[:2] == ("apply", "-f")
        return _Result()


def _plan_config() -> dict[str, Any]:
    return {
        "namespace": {"name": "bench"},
        "model": {"name": "test-model"},
        "images": {
            "benchmark": {
                "repository": "example.com/bench",
                "tag": "latest",
                "pullPolicy": "IfNotPresent",
            }
        },
        "harness": {
            "name": "inference-perf",
            "namespace": "bench",
            "podLabel": "llmdbench-harness-launcher",
            "resources": {"cpu": "1", "memory": "1Gi"},
            "inferencePerf": {"rayonNumThreads": "1"},
            "resultsDirPrefix": "/requests",
            "stackName": "model",
        },
        "experiment": {"workspaceDir": "/workspace", "resultsDir": "/requests"},
        "vllmCommon": {"inferencePort": 8000},
        "standalone": {
            "enabled": False,
            "launcher": {"enabled": False},
            "vllm": {"loadFormat": "auto"},
        },
        "fma": {"enabled": False},
        "storage": {"workloadPvc": {"name": "workload-pvc"}},
        "huggingface": {"enabled": False},
    }


def test_treatment_with_wait_errors_is_reported_failed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stack_path = tmp_path / "plan" / "stack"
    stack_path.mkdir(parents=True)
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(_plan_config()),
        encoding="utf-8",
    )

    logger = _Logger()
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        base_dir=Path(__file__).resolve().parents[1],
        namespace="bench",
        harness_namespace="bench",
        logger=logger,
        cmd=_Command(),
    )
    context.deployed_endpoints["stack"] = "http://endpoint"

    monkeypatch.setattr(
        deploy_harness,
        "wait_for_pods_by_label",
        lambda *_args, **_kwargs: ["harness pod failed"],
    )
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        deploy_harness,
        "delete_pods_by_names",
        lambda *_args, **_kwargs: None,
    )

    result = DeployHarnessStep().execute(context, stack_path)

    assert not result.success
    assert "harness pod failed" in result.errors
    assert any("Treatment 'default' failed" in error for error in logger.errors)
    assert not any("Treatment 'default' complete" in info for info in logger.infos)
