"""Tests for harness pod script override behavior."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml

_STEP_PATH = (
    Path(__file__).resolve().parent.parent
    / "llmdbenchmark"
    / "run"
    / "steps"
    / "step_07_deploy_harness.py"
)
_spec = importlib.util.spec_from_file_location(
    "step_07_deploy_harness_isolated", _STEP_PATH
)
_module = importlib.util.module_from_spec(_spec)
sys.modules["step_07_deploy_harness_isolated"] = _module
_spec.loader.exec_module(_module)
DeployHarnessStep = _module.DeployHarnessStep


def _template_values() -> dict[str, Any]:
    return {
        "pod_name": "bench-pod",
        "harness_command": "llm-d-benchmark.sh",
        "deploy_method": "modelservice",
        "cluster_type": "kind",
        "endpoint_url": "http://endpoint",
        "stack_type": "llm-d",
        "experiment_id": "exp-1",
        "results_dir": "/requests/exp-1",
        "model_id_label": "model",
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
        "experiment": {"workspaceDir": "/workspace"},
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


def test_harness_pod_copies_configmap_scripts_before_launch() -> None:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "templates"
        / "jinja"
        / "20_harness_pod.yaml.j2"
    )
    rendered = DeployHarnessStep._render_template(
        template_path.read_text(encoding="utf-8"), _template_values()
    )
    pod = yaml.safe_load(rendered)
    launch_script = pod["spec"]["containers"][0]["args"][0]

    assert "/workspace/harnesses" in launch_script
    assert 'cp "$script" /usr/local/bin/' in launch_script
    assert 'chmod +x "/usr/local/bin/$(basename "$script")"' in launch_script
    assert "/usr/local/bin" in launch_script
    assert "llm-d-benchmark.sh" in launch_script
    assert launch_script.index("/workspace/harnesses") < launch_script.index(
        "llm-d-benchmark.sh"
    )
