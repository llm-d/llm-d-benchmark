"""Tests for the llm-d-prism URL shown in the run summary."""

import json
from types import SimpleNamespace

from llmdbenchmark.cli import _find_prism_url


def _result(stdout="", success=True):
    return SimpleNamespace(stdout=stdout, success=success)


def _svc(name="llm-d-prism", port=80):
    return {"metadata": {"name": name}, "spec": {"ports": [{"port": port}]}}


class _FakeCmd:
    """Answers `get service` / `get route` per namespace."""

    def __init__(self, services=None, routes=None, fail=False):
        self.services = services or {}
        self.routes = routes or {}
        self.fail = fail
        self.calls = []

    def kube(self, *args, namespace=None, **_kwargs):
        self.calls.append((args, namespace))
        if self.fail:
            return _result(success=False)
        kind = args[1]
        items = (self.services if kind == "service" else self.routes).get(namespace, [])
        return _result(json.dumps({"items": items}))


def _context(cmd, is_openshift=True, namespace="model-ns", harness_namespace=None):
    return SimpleNamespace(
        require_cmd=lambda: cmd,
        is_openshift=is_openshift,
        namespace=namespace,
        harness_namespace=harness_namespace,
    )


def test_openshift_route_url():
    cmd = _FakeCmd(
        services={"model-ns": [_svc()]},
        routes={
            "model-ns": [
                {"spec": {"host": "other.example", "to": {"name": "something"}}},
                {"spec": {"host": "prism.example", "to": {"name": "llm-d-prism"}}},
            ]
        },
    )
    assert _find_prism_url(_context(cmd)) == "http://prism.example"


def test_openshift_tls_route_uses_https():
    cmd = _FakeCmd(
        services={"model-ns": [_svc()]},
        routes={
            "model-ns": [
                {
                    "spec": {
                        "host": "prism.example",
                        "to": {"name": "llm-d-prism"},
                        "tls": {"termination": "edge"},
                    }
                }
            ]
        },
    )
    assert _find_prism_url(_context(cmd)) == "https://prism.example"


def test_kubernetes_returns_port_forward_hint():
    cmd = _FakeCmd(services={"model-ns": [_svc()]})
    url = _find_prism_url(_context(cmd, is_openshift=False))
    assert url == (
        "kubectl port-forward -n model-ns svc/llm-d-prism 8080:80"
        " then open http://localhost:8080"
    )
    assert all(args[1] != "route" for args, _ in cmd.calls)


def test_found_in_harness_namespace():
    cmd = _FakeCmd(
        services={"harness-ns": [_svc()]},
        routes={
            "harness-ns": [
                {"spec": {"host": "prism.example", "to": {"name": "llm-d-prism"}}}
            ]
        },
    )
    ctx = _context(cmd, harness_namespace="harness-ns")
    assert _find_prism_url(ctx) == "http://prism.example"


def test_not_deployed_returns_none():
    cmd = _FakeCmd()
    assert _find_prism_url(_context(cmd, harness_namespace="harness-ns")) is None


def test_kube_failure_returns_none():
    assert _find_prism_url(_context(_FakeCmd(fail=True))) is None


def test_unexpected_error_returns_none():
    def boom():
        raise RuntimeError("no cluster")

    ctx = SimpleNamespace(
        require_cmd=boom, is_openshift=True, namespace="ns", harness_namespace=None
    )
    assert _find_prism_url(ctx) is None
