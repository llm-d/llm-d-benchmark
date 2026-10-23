"""Harness pods carry the -j pod count, so the report's load parallelism is right.

The v0.2 converter fills ``scenario.load.standardized.parallelism`` from
``LLMDBENCH_HARNESS_LOAD_PARALLELISM``; without it every report says 1 even when
``-j N`` ran N load generators.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from llmdbenchmark.executor.context import ExecutionContext
from llmdbenchmark.run.steps import step_07_deploy_harness as step07
from llmdbenchmark.run.steps.step_07_deploy_harness import (
    DeployHarnessStep,
    _TreatmentSpec,
)

_ROOT = Path(__file__).resolve().parents[1]
_ENV = "LLMDBENCH_HARNESS_LOAD_PARALLELISM"


class _Logger:
    def __getattr__(self, _name: str) -> Any:
        return lambda *_a, **_k: None


class _Applied:
    success = True
    stdout = ""
    stderr = ""


class _KubeCmd:
    def __init__(self) -> None:
        self.applied: list[dict] = []

    def kube(self, *args: str, **_: Any) -> _Applied:
        if args[0] == "apply":
            self.applied.append(yaml.safe_load(Path(args[2]).read_text()))
        return _Applied()


def _pod_template_values() -> dict[str, Any]:
    return {
        "pod_name": "bench-pod",
        "harness_command": "llm-d-benchmark.sh",
        "experiment_id": "exp-1",
        "results_dir": "/requests/exp-1",
        "model_id_label": "model",
        "namespace": {"name": "bench"},
        "model": {"name": "test-model"},
        "images": {
            "benchmark": {"repository": "r", "tag": "t", "pullPolicy": "Always"}
        },
        "harness": {
            "name": "inference-perf",
            "namespace": "bench",
            "podLabel": "llmdbench-harness-launcher",
            "resources": {"cpu": "1", "memory": "1Gi"},
            "inferencePerf": {"rayonNumThreads": "1"},
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
        "load_parallelism": 3,
    }


def test_harness_pod_template_exports_load_parallelism() -> None:
    template = (_ROOT / "config/templates/jinja/20_harness_pod.yaml.j2").read_text()
    pod = yaml.safe_load(
        DeployHarnessStep._render_template(template, _pod_template_values())
    )
    env = {e["name"]: e.get("value") for e in pod["spec"]["containers"][0]["env"]}
    assert env[_ENV] == "3"


def test_every_parallel_pod_gets_the_pod_count(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(step07, "wait_for_pods_by_selector", lambda *_a, **_k: [])
    monkeypatch.setattr(step07, "wait_for_harness_sentinels", lambda *_a, **_k: [])
    monkeypatch.setattr(step07, "delete_pods_by_names", lambda *_a, **_k: None)
    monkeypatch.setattr(
        DeployHarnessStep,
        "_collect_treatment_results_discovery",
        staticmethod(lambda *_a, **_k: (None, {}, [])),
    )
    cmd = _KubeCmd()
    spec = _TreatmentSpec(
        treatment=None,
        index=1,
        total=1,
        group=None,
        cmd=cmd,
        plan_config={"harness": {}},
        harness_name="inference-perf",
        harness_ns="ns",
        deploy_namespace="ns",
        endpoint_url="http://endpoint",
        model_label="model",
        model_name="model",
        stack_type="llm-d",
        profile_name="sanity_random.yaml",
        profile_mounts=[],
        results_dir_prefix="/requests",
        harness_executable="llm-d-benchmark.sh",
        template_content="parallelism: '{{ load_parallelism }}'\n",
        pod_label="llmdbench-harness-launcher",
        parallelism=3,
        timeout=600,
    )
    context = ExecutionContext(plan_dir=tmp_path, workspace=tmp_path, logger=_Logger())
    context.run_dir().mkdir(parents=True, exist_ok=True)

    DeployHarnessStep()._run_treatment(spec, context)

    assert [pod["parallelism"] for pod in cmd.applied] == ["3", "3", "3"]


class _Result:
    def __init__(self, stdout: str = "") -> None:
        self.success = True
        self.stdout = stdout
        self.stderr = ""
        self.dry_run = False


class _Command:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def execute(self, command: str, *_: Any, **__: Any) -> _Result:
        self.commands.append(command)
        return _Result("exited 0\n" if " inspect " in command else "")


def test_nok8s_harness_containers_get_the_pod_count(tmp_path: Path) -> None:
    from llmdbenchmark.run.steps.step_07_deploy_harness_local import (
        DeployHarnessLocalStep,
    )

    stack_path = tmp_path / "plan" / "stack"
    stack_path.mkdir(parents=True)
    (stack_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"name": "test-model"},
                "images": {"benchmark": {"repository": "r", "tag": "t"}},
                "harness": {
                    "name": "inference-perf",
                    "experimentProfile": "sanity_random.yaml",
                },
                "nok8s": {"hfTokenEnv": "HUGGING_FACE_HUB_TOKEN"},
            }
        )
    )
    cmd = _Command()
    context = ExecutionContext(
        plan_dir=tmp_path / "plan",
        workspace=tmp_path,
        base_dir=_ROOT,
        deployed_methods=["nok8s"],
        container_runtime="docker",
        logger=_Logger(),
        cmd=cmd,
        harness_parallelism=2,
    )
    context.deployed_endpoints["stack"] = "http://localhost:8081"
    context.run_results_dir().mkdir(parents=True, exist_ok=True)

    assert DeployHarnessLocalStep().execute(context, stack_path).success

    runs = [c for c in cmd.commands if " run -d --name " in c]
    assert len(runs) == 2
    assert all(f"-e {_ENV}=2 " in run for run in runs)
