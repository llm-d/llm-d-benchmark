"""Coverage for the FMA ``inferencePorts`` pool
(https://github.com/llm-d-incubation/llm-d-fast-model-actuation/pull/790).

``fma.launcher.inferencePorts`` renders the pool of ports available to
inference-engine instances in each launcher pod into the LauncherConfig's
``spec.inferencePorts``. Since FMA v0.8.0 the dual-pods controller assigns
instance ports from this pool (upstream PR #809, resolving #779); it takes
precedence over the InferenceServerConfig's ``modelServerConfig.port``,
which is kept as a deprecated fallback for pool-less LauncherConfigs. The
CRD requires the pool to hold at least ``maxInstances`` ports and to skip
8001 (the launcher service port).
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


def _base_values(inference_ports: list | None = None) -> dict:
    launcher: dict = {
        "maxInstances": 4,
        "image": {
            "repository": "example.com/launcher",
            "tag": "v0.8.0",
            "pullPolicy": "IfNotPresent",
        },
        "podTemplate": {"metadata": {}},
        "customPreprocessCommands": [],
    }
    if inference_ports is not None:
        launcher["inferencePorts"] = inference_ports
    return {
        "model_id_label": "opt-125m-abc123",
        "model": {
            "name": "facebook/opt-125m",
            "path": "models/facebook/opt-125m",
            "maxModelLen": 16384,
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
            "launcher": launcher,
            "launcherConfigurator": {"port": 8001},
            "requester": {
                "image": {"repository": "example.com/requester", "tag": "v0.8.0"},
                "probePort": 8080,
                "spiPort": 8081,
                "limitsGPU": 1,
                "limitsCPU": "1",
                "limitsMemory": "250Mi",
                "replicas": 0,
            },
        },
    }


def _launcher_config(docs: list[dict]) -> dict:
    return next(d for d in docs if d and d.get("kind") == "LauncherConfig")


def _isc(docs: list[dict]) -> dict:
    return next(d for d in docs if d and d.get("kind") == "InferenceServerConfig")


class TestFmaLauncherInferencePorts:
    def test_absent_key_omits_field(self):
        """The default (no inferencePorts key) must not render the field."""
        spec = _launcher_config(_render(_base_values()))["spec"]
        assert "inferencePorts" not in spec

    def test_empty_pool_omits_field(self):
        """An explicit empty list omits the field (pool-less LauncherConfig,
        served by the deprecated ISC-port fallback)."""
        spec = _launcher_config(_render(_base_values(inference_ports=[])))["spec"]
        assert "inferencePorts" not in spec

    def test_pool_renders_as_list(self):
        spec = _launcher_config(
            _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        )["spec"]
        assert spec["inferencePorts"] == [8000, 8002, 8003, 8004]

    def test_pool_keeps_deprecated_isc_port_fallback(self):
        """Since v0.8.0 the pool takes precedence, but modelServerConfig.port
        must still render: it is the (deprecated) fallback the controller uses
        for LauncherConfigs without a pool."""
        docs = _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        assert _isc(docs)["spec"]["modelServerConfig"]["port"] == 8000


class TestFmaDefaultsPool:
    """defaults.yaml ships a pool by default (v0.8.0 bump); guard it against
    the CRD's CEL rules so a maxInstances bump without extending the pool
    fails here rather than at LauncherConfig apply time."""

    def test_defaults_pool_satisfies_crd_rules(self):
        with open("config/templates/values/defaults.yaml", encoding="utf-8") as fh:
            fma = (yaml.safe_load(fh) or {})["fma"]
        launcher = fma["launcher"]
        pool = launcher["inferencePorts"]
        assert pool, "defaults.yaml must set fma.launcher.inferencePorts"
        assert len(pool) >= launcher["maxInstances"]
        assert len(set(pool)) == len(pool), "pool must be a set (no duplicates)"
        assert 8001 not in pool, "8001 is reserved for the launcher service"
        assert 8000 in pool, "first port should match the legacy ISC port"
