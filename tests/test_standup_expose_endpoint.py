"""Tests for standup step 09 (``externally_accessible``).

Behavior under test:
- On by default; ``externally_accessible: false`` exposes nothing.
- OpenShift: an existing Route to the model Service is reused; otherwise one
  is created with ``oc expose --port=<service port name>``.
- Kubernetes: a detached port-forward is started to the model Service and the
  localhost URL is recorded.
- Gateway classes resolve to the data-plane Service (``<gateway>-istio``).
- Stacks sharing one Service share one exposure.
- nok8s is skipped; FMA-only is a non-fatal no-op.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from llmdbenchmark.standup.steps import step_09_expose_endpoint as mod
from llmdbenchmark.standup.steps.step_09_expose_endpoint import ExposeEndpointStep


class _StubLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log_info(self, msg: str, **_: Any) -> None:
        self.messages.append(msg)

    def log_warning(self, msg: str, **_: Any) -> None:
        self.messages.append(f"WARN: {msg}")


@dataclass
class _StubResult:
    success: bool = True
    stdout: str = ""
    stderr: str = ""


def _svc(name: str, ports: list[dict]) -> dict:
    return {
        "metadata": {"name": name},
        "spec": {"clusterIP": "10.0.0.1", "ports": ports},
    }


@dataclass
class _StubCmd:
    """Serves canned services/routes; `expose` adds a route."""

    work_dir: Path
    services: dict[str, dict] = field(default_factory=dict)
    routes: list[dict] = field(default_factory=list)
    kube_calls: list[tuple] = field(default_factory=list)

    def kube(self, *args: str, **_: Any) -> _StubResult:
        self.kube_calls.append(args)
        if args[:2] == ("get", "service") and len(args) > 2 and args[2] != "-l":
            svc = self.services.get(args[2])
            return _StubResult(True, json.dumps(svc)) if svc else _StubResult(False)
        if args[:2] == ("get", "service"):  # standalone label lookup
            svc = next(iter(self.services.values()), None)
            if not svc:
                return _StubResult(True, "")
            port = svc["spec"]["ports"][0]["port"]
            return _StubResult(True, f"10.0.0.1:{svc['metadata']['name']}:{port}")
        if args[:2] == ("get", "route"):
            return _StubResult(True, json.dumps({"items": self.routes}))
        if args[0] == "expose":
            svc = args[1].split("/", 1)[1]
            name = args[2].split("=", 1)[1]
            self.routes.append(
                {"spec": {"host": f"{name}.apps.example", "to": {"name": svc}}}
            )
        return _StubResult(True)

    def kube_argv(self, *args: str) -> list[str]:
        return ["kubectl", *args]

    def exposes(self) -> list[tuple]:
        return [c for c in self.kube_calls if c[0] == "expose"]


@dataclass
class _StubContext:
    cmd: _StubCmd
    deployed_methods: list[str] = field(default_factory=lambda: ["modelservice"])
    is_openshift: bool = True
    dry_run: bool = False
    namespace: str = "ns1"
    release: str = "llmdbench"
    logger: _StubLogger = field(default_factory=_StubLogger)
    external_endpoints: dict[str, str] = field(default_factory=dict)

    def require_cmd(self) -> _StubCmd:
        return self.cmd

    def require_namespace(self) -> str:
        return self.namespace


def _step(plan_config: dict) -> ExposeEndpointStep:
    step = ExposeEndpointStep()
    step._load_stack_config = lambda _path: plan_config  # type: ignore[method-assign]
    return step


EPPONLY = {
    "externally_accessible": True,
    "gateway": {"className": "epponly"},
    "model_id_label": "m1",
}


def test_disabled_when_false(tmp_path):
    cmd = _StubCmd(work_dir=tmp_path)
    ctx = _StubContext(cmd=cmd)
    plan = {"externally_accessible": False, "gateway": {"className": "epponly"}}
    result = _step(plan).execute(ctx, tmp_path / "stack-a")
    assert result.success
    assert cmd.kube_calls == []
    assert ctx.external_endpoints == {}


def test_enabled_when_key_absent(tmp_path):
    cmd = _StubCmd(
        work_dir=tmp_path,
        services={"m1-router-epp": _svc("m1-router-epp", [{"port": 80}])},
    )
    ctx = _StubContext(cmd=cmd)
    plan = {"gateway": {"className": "epponly"}, "model_id_label": "m1"}
    _step(plan).execute(ctx, tmp_path / "stack-a")
    assert "stack-a" in ctx.external_endpoints


def test_openshift_creates_route_using_port_name(tmp_path):
    cmd = _StubCmd(
        work_dir=tmp_path,
        services={
            "m1-router-epp": _svc("m1-router-epp", [{"name": "http", "port": 80}])
        },
    )
    ctx = _StubContext(cmd=cmd)
    result = _step(EPPONLY).execute(ctx, tmp_path / "stack-a")
    assert result.success
    (expose,) = cmd.exposes()
    assert expose[1] == "service/m1-router-epp"
    assert "--port=http" in expose
    assert ctx.external_endpoints == {
        "stack-a": "http://m1-router-epp-external.apps.example"
    }


def test_openshift_reuses_existing_route(tmp_path):
    cmd = _StubCmd(
        work_dir=tmp_path,
        services={"m1-router-epp": _svc("m1-router-epp", [{"port": 80}])},
        routes=[
            {
                "spec": {
                    "host": "pre.apps.example",
                    "tls": {},
                    "to": {"name": "m1-router-epp"},
                }
            }
        ],
    )
    ctx = _StubContext(cmd=cmd)
    _step(EPPONLY).execute(ctx, tmp_path / "stack-a")
    assert cmd.exposes() == []
    assert ctx.external_endpoints["stack-a"] == "https://pre.apps.example"


def test_gateway_class_resolves_istio_service(tmp_path):
    gw = "infra-llmdbench-inference-gateway-istio"
    cmd = _StubCmd(work_dir=tmp_path, services={gw: _svc(gw, [{"port": 80}])})
    ctx = _StubContext(cmd=cmd)
    plan = {"externally_accessible": True, "gateway": {"className": "istio"}}
    _step(plan).execute(ctx, tmp_path / "stack-a")
    assert cmd.exposes()[0][1] == f"service/{gw}"


def test_stacks_sharing_a_service_share_one_route(tmp_path):
    gw = "infra-llmdbench-inference-gateway"
    cmd = _StubCmd(work_dir=tmp_path, services={gw: _svc(gw, [{"port": 80}])})
    ctx = _StubContext(cmd=cmd)
    step = _step(
        {"externally_accessible": True, "gateway": {"className": "agentgateway"}}
    )
    step.execute(ctx, tmp_path / "stack-a")
    step.execute(ctx, tmp_path / "stack-b")
    assert len(cmd.exposes()) == 1
    assert ctx.external_endpoints["stack-a"] == ctx.external_endpoints["stack-b"]


class _FakeProc:
    pid = 4242

    def __init__(self, argv, **_kwargs):
        self.argv = argv

    def poll(self):
        return None


def test_kubernetes_starts_port_forward(tmp_path, monkeypatch):
    started: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        started.append(argv)
        assert kwargs.get("start_new_session") is True
        return _FakeProc(argv)

    monkeypatch.setattr(mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "_free_local_port", lambda _start: 8123)

    cmd = _StubCmd(
        work_dir=tmp_path,
        services={"vllm-standalone-x": _svc("vllm-standalone-x", [{"port": 8000}])},
    )
    ctx = _StubContext(cmd=cmd, is_openshift=False, deployed_methods=["standalone"])
    _step({"externally_accessible": True}).execute(ctx, tmp_path / "stack-a")

    assert started == [
        [
            "kubectl",
            "port-forward",
            "--namespace",
            "ns1",
            "svc/vllm-standalone-x",
            "8123:8000",
        ]
    ]
    assert ctx.external_endpoints == {"stack-a": "http://localhost:8123"}
    pid_file = tmp_path / "setup" / "port-forward-ns1-vllm-standalone-x.pid"
    assert pid_file.read_text().strip() == "4242"


def test_skipped_for_nok8s(tmp_path):
    ctx = _StubContext(cmd=_StubCmd(work_dir=tmp_path), deployed_methods=["nok8s"])
    assert ExposeEndpointStep().should_skip(ctx)


@pytest.mark.parametrize("openshift", [True, False])
def test_fma_only_is_a_noop(tmp_path, openshift):
    cmd = _StubCmd(work_dir=tmp_path)
    ctx = _StubContext(cmd=cmd, deployed_methods=["fma"], is_openshift=openshift)
    result = _step({"externally_accessible": True}).execute(ctx, tmp_path / "s")
    assert result.success
    assert cmd.kube_calls == []
    assert ctx.external_endpoints == {}
