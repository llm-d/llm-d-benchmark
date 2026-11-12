"""Coverage for the FMA ``inferencePorts`` pool
(https://github.com/llm-d-incubation/llm-d-fast-model-actuation/pull/790)
and the requester-TCP-proxy wiring it requires
(https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/831).

``fma.launcher.inferencePorts`` renders the pool of ports available to
inference-engine instances in each launcher pod into the LauncherConfig's
``spec.inferencePorts``. Since FMA v0.8.0 the dual-pods controller assigns
instance ports from this pool (upstream [PR #809](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/pull/809), resolving [#779](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/779)). The pool
is the ONLY port source the template renders: the ISC's
``modelServerConfig.port`` is gone, and the InferencePool routing labels
moved from the ISC to the requester pod template -- leaving either on the
ISC makes the InferencePool match the bound launcher and routes EPP traffic
straight to it, bypassing the requester's TCP proxy ([issue #831](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/831)). The CRD
requires the pool to hold at least ``maxInstances`` ports and to skip 8001
(the launcher service port).
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
        """An explicit empty list omits the field. NOTE: with the ISC port
        also gone from the template, a pool-less render leaves the controller
        no port source at all -- it rejects the server-requesting pod. The
        shipped defaults always carry a pool (see TestFmaDefaultsPool)."""
        spec = _launcher_config(_render(_base_values(inference_ports=[])))["spec"]
        assert "inferencePorts" not in spec

    def test_pool_renders_as_list(self):
        spec = _launcher_config(
            _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        )["spec"]
        assert spec["inferencePorts"] == [8000, 8002, 8003, 8004]


class TestFmaRequesterProxyMode:
    """([issue #831](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/831)): in port-pool mode the ports live ONLY on the LauncherConfig
    and the InferencePool routing labels ONLY on the requester pod template."""

    def test_isc_has_no_port_and_no_routing_labels(self):
        """labels/port on the ISC make the InferencePool match the bound
        launcher and bypass the requester's TCP proxy ([issue #831](https://github.com/llm-d-incubation/llm-d-fast-model-actuation/issues/831))."""
        docs = _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        model_server = _isc(docs)["spec"]["modelServerConfig"]
        assert "port" not in model_server
        assert "labels" not in model_server

    def test_requester_carries_inferencepool_labels(self):
        """The EPP selector keys (llm-d.ai/inferenceServing, model, guide)
        move to the requester pod template; readiness gates membership."""
        docs = _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
        tmpl_labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert tmpl_labels["llm-d.ai/inferenceServing"] == "true"
        assert tmpl_labels["llm-d.ai/model"] == "opt-125m-abc123"
        assert tmpl_labels["llm-d.ai/guide"] == "test-scenario"
        # Selector must stay a subset of the template labels.
        for key, value in deployment["spec"]["selector"]["matchLabels"].items():
            assert tmpl_labels[key] == value

    def test_admin_port_and_probe_follow_config(self):
        """The admin-port annotation (where the controller PUTs the proxy
        config) and the readiness probe track spiPort/probePort -- a scenario
        override must not silently desync the controller's wiring."""
        values = _base_values(inference_ports=[8000, 8002, 8003, 8004])
        values["fma"]["requester"]["probePort"] = 9080
        values["fma"]["requester"]["spiPort"] = 9081
        docs = _render(values)
        deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
        tmpl = deployment["spec"]["template"]
        assert (
            tmpl["metadata"]["annotations"]["dual-pods.llm-d.ai/admin-port"] == "9081"
        )
        probe = tmpl["spec"]["containers"][0]["readinessProbe"]["httpGet"]
        assert probe["port"] == 9080

    def test_requester_exposes_proxy_port(self):
        """Proxy port 8000 (= vllmCommon.inferencePort, so the metrics
        fallback and cache-reset POSTs reach vLLM through the proxy): the
        containerPort and the explicit --proxy-port flag must match."""
        docs = _render(_base_values(inference_ports=[8000, 8002, 8003, 8004]))
        deployment = next(d for d in docs if d and d.get("kind") == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        ports = {p["name"]: p["containerPort"] for p in container["ports"]}
        assert ports["proxy"] == 8000
        assert "--proxy-port=8000" in container["command"]


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
