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
    assert "for script in /workspace/harnesses/*" in launch_script
    assert '[ -f "$script" ]' in launch_script
    assert 'cp "$script" /usr/local/bin/' in launch_script
    assert 'chmod +x "/usr/local/bin/$(basename "$script")"' in launch_script
    assert "/usr/local/bin" in launch_script
    assert "llm-d-benchmark.sh" in launch_script
    assert launch_script.index("/workspace/harnesses") < launch_script.index(
        "llm-d-benchmark.sh"
    )


def test_harness_pod_receives_configured_time_series_metrics() -> None:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "templates"
        / "jinja"
        / "20_harness_pod.yaml.j2"
    )
    values = _template_values()
    values.update(
        {
            "monitoring": {
                "metricsScrapeEnabled": True,
                "metricsPath": "/metrics",
                "timeSeriesMetrics": ["vllm:custom_metric"],
            },
            "decode": {"vllm": {"port": 8000}},
            "router": {"monitoring": {}},
        }
    )

    rendered = DeployHarnessStep._render_template(
        template_path.read_text(encoding="utf-8"), values
    )
    pod = yaml.safe_load(rendered)
    env = {
        item["name"]: item.get("value") for item in pod["spec"]["containers"][0]["env"]
    }

    assert env["LLMDBENCH_TIME_SERIES_METRICS"] == '["vllm:custom_metric"]'


def test_harness_pod_mounts_all_debug_profile_configmaps() -> None:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "templates"
        / "jinja"
        / "20_harness_pod.yaml.j2"
    )
    values = _template_values()
    values["profile_mounts"] = ["guidellm", "inference-perf"]

    rendered = DeployHarnessStep._render_template(
        template_path.read_text(encoding="utf-8"), values
    )
    pod = yaml.safe_load(rendered)
    container = pod["spec"]["containers"][0]
    mounts = {mount["name"]: mount["mountPath"] for mount in container["volumeMounts"]}
    volumes = {
        volume["name"]: volume["configMap"]["name"]
        for volume in pod["spec"]["volumes"]
        if "configMap" in volume
    }

    assert mounts["guidellm-profiles"] == "/workspace/profiles/guidellm"
    assert mounts["inference-perf-profiles"] == "/workspace/profiles/inference-perf"
    assert volumes["guidellm-profiles"] == "guidellm-profiles"
    assert volumes["inference-perf-profiles"] == "inference-perf-profiles"


def _render_pod(values: dict[str, Any]) -> dict[str, Any]:
    template_path = (
        Path(__file__).resolve().parent.parent
        / "config"
        / "templates"
        / "jinja"
        / "20_harness_pod.yaml.j2"
    )
    rendered = DeployHarnessStep._render_template(
        template_path.read_text(encoding="utf-8"), values
    )
    return yaml.safe_load(rendered)


def _pod_env(values: dict[str, Any]) -> dict[str, Any]:
    pod = _render_pod(values)
    return {
        item["name"]: item.get("value") for item in pod["spec"]["containers"][0]["env"]
    }


def test_harness_pod_ships_the_run_description() -> None:
    """The description reaches the harness so it can record it as run.description."""
    values = _template_values()
    values["description"] = {"text": "Sweep A", "keywords": ["kv-cache", "p-d"]}

    env = _pod_env(values)

    assert env["LLMDBENCH_DESCRIPTION_TEXT"] == "Sweep A"
    assert env["LLMDBENCH_DESCRIPTION_KEYWORDS"] == "kv-cache,p-d"


def test_harness_pod_quotes_a_hostile_run_description() -> None:
    """Free text must not break the manifest: a bare "{{ ... }}" would produce
    unparseable YAML for any of these, so the parse below is the assertion."""
    hostile = 'has "quotes", a back\\slash, a: colon and #hash'
    values = _template_values()
    values["description"] = {"text": hostile, "keywords": ['k "1"']}

    env = _pod_env(values)

    assert env["LLMDBENCH_DESCRIPTION_TEXT"] == hostile
    assert env["LLMDBENCH_DESCRIPTION_KEYWORDS"] == 'k "1"'


def test_harness_pod_tolerates_a_missing_description_block() -> None:
    """Callers rendering a hand-built context need not supply the block."""
    values = _template_values()
    values.pop("description", None)

    env = _pod_env(values)

    assert env["LLMDBENCH_DESCRIPTION_TEXT"] == ""
    assert env["LLMDBENCH_DESCRIPTION_KEYWORDS"] == ""
