"""Regression coverage for #1853: the FMA launcher should support tensor
parallelism (TP>1) for models that do not fit on a single GPU.

``fma.launcher.tensorParallelSize`` (default 1) feeds ``--tensor-parallel-size``
on the InferenceServerConfig's vLLM options, and, because TP workers exchange
over a shared-memory message queue, provisions a 16Gi in-memory ``/dev/shm``
emptyDir on the LauncherConfig pod when it is > 1 (the pod default 64Mi crashes
NCCL at init). ``fma.launcher.enforceEager`` (default off) appends
``--enforce-eager``. Both default so single-GPU runs render exactly as before.
"""

from __future__ import annotations

import yaml
from jinja2 import Environment

from llmdbenchmark.parser.render_plans import RenderPlans

_TEMPLATE_PATH = "config/templates/jinja/24_fma-deployment.yaml.j2"


def _render(values: dict) -> list[dict]:
    env = Environment(
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=False,
    )
    env.filters["toyaml"] = RenderPlans._toyaml_filter
    with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
        template = env.from_string(fh.read())
    out = template.render(**values)
    return [yaml.safe_load(doc) for doc in out.split("\n---\n") if doc.strip()]


def _base_values(tensor_parallel_size: int = 1, enforce_eager: bool = False) -> dict:
    return {
        "model_id_label": "qwen3-32b-abc123",
        "model": {
            "name": "Qwen/Qwen3-32B",
            "path": "models/Qwen/Qwen3-32B",
            "maxModelLen": 32768,
            "gpuMemoryUtilization": 0.95,
        },
        "namespace": {"name": "bench"},
        "labels": {"inferenceServing": "true"},
        "huggingface": {"enabled": False},
        "scenarioName": "test-scenario",
        "fma": {
            "enabled": True,
            "modelMountPath": "/model-cache",
            "modelPvcName": "model-pvc",
            "mountModelVolume": True,
            "launcher": {
                "loadModelFromLocalDir": False,
                "tensorParallelSize": tensor_parallel_size,
                "enforceEager": enforce_eager,
                "maxInstances": 4,
                "image": {
                    "repository": "example.com/launcher",
                    "tag": "v0.6.4",
                    "pullPolicy": "IfNotPresent",
                },
                "podTemplate": {"metadata": {}},
                "customPreprocessCommands": [],
            },
            "launcherConfigurator": {"port": 8001},
            "requester": {
                "image": {"repository": "example.com/requester", "tag": "v0.6.4"},
                "probePort": 8080,
                "spiPort": 8081,
                "limitsGPU": 2,
                "limitsCPU": "1",
                "limitsMemory": "250Mi",
                "replicas": 0,
            },
        },
    }


def _options(docs: list[dict]) -> str:
    isc = next(d for d in docs if d and d.get("kind") == "InferenceServerConfig")
    return isc["spec"]["modelServerConfig"]["options"]


def _launcher_pod_spec(docs: list[dict]) -> dict:
    lc = next(d for d in docs if d and d.get("kind") == "LauncherConfig")
    return lc["spec"]["podTemplate"]["spec"]


class TestFmaLauncherTensorParallel:
    def test_default_tp_is_one(self):
        docs = _render(_base_values())
        assert "--tensor-parallel-size 1" in _options(docs)

    def test_tp_greater_than_one_sets_flag(self):
        docs = _render(_base_values(tensor_parallel_size=2))
        assert "--tensor-parallel-size 2" in _options(docs)

    def test_default_tp_renders_no_dshm(self):
        spec = _launcher_pod_spec(_render(_base_values()))
        volume_names = {v["name"] for v in spec.get("volumes", [])}
        mount_names = {
            m["name"]
            for c in spec.get("containers", [])
            for m in c.get("volumeMounts", [])
        }
        assert "dshm" not in volume_names
        assert "dshm" not in mount_names

    def test_tp_greater_than_one_provisions_dshm(self):
        spec = _launcher_pod_spec(_render(_base_values(tensor_parallel_size=2)))
        dshm_vol = next(v for v in spec["volumes"] if v["name"] == "dshm")
        assert dshm_vol["emptyDir"]["medium"] == "Memory"
        assert dshm_vol["emptyDir"]["sizeLimit"] == "16Gi"
        dshm_mount = next(
            m
            for c in spec["containers"]
            for m in c.get("volumeMounts", [])
            if m["name"] == "dshm"
        )
        assert dshm_mount["mountPath"] == "/dev/shm"

    def test_enforce_eager_default_off(self):
        assert "--enforce-eager" not in _options(_render(_base_values()))

    def test_enforce_eager_opt_in_appends_flag(self):
        docs = _render(_base_values(enforce_eager=True))
        assert "--enforce-eager" in _options(docs)
