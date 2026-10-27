"""Coverage for the FMA v0.7.1 ``inferencePorts`` pool
(https://github.com/llm-d-incubation/llm-d-fast-model-actuation/pull/790).

``fma.launcher.inferencePorts`` (default empty) renders the pool of ports
available to inference-engine instances in each launcher pod into the
LauncherConfig's ``spec.inferencePorts``. The CRD requires the pool to hold
at least ``maxInstances`` ports and to skip 8001 (the launcher service port).
At v0.7.1 the dual-pods controller validates but does not yet consume the
pool, so the InferenceServerConfig's ``modelServerConfig.port`` stays
load-bearing and must not be dropped when a pool is set.
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
            "tag": "v0.7.1",
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
                "image": {"repository": "example.com/requester", "tag": "v0.7.1"},
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
        """An explicit empty list (the defaults.yaml value) omits the field."""
        spec = _launcher_config(_render(_base_values(inference_ports=[])))["spec"]
        assert "inferencePorts" not in spec

    def test_pool_renders_as_list(self):
        spec = _launcher_config(
            _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        )["spec"]
        assert spec["inferencePorts"] == [8000, 8002, 8003, 8004]

    def test_pool_does_not_drop_isc_port(self):
        """At v0.7.1 the controller still reads the ISC port, so setting a pool
        must keep modelServerConfig.port. Pool consumption is planned for 0.8.0
        per https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/779
        (not yet a committed release). We keep the pool unset until a release
        whose controller consumes it; revisit this test on that bump."""
        docs = _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        assert _isc(docs)["spec"]["modelServerConfig"]["port"] == 8000
