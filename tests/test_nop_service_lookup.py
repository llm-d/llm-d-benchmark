"""Tests for nop harness service lookup."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from llmdbenchmark.parser.render_plans import RenderPlans

_HARNESS_PATH = (
    Path(__file__).resolve().parents[1] / "workload" / "harnesses" / "nop_functions.py"
)
_spec = importlib.util.spec_from_file_location("nop_functions_test", _HARNESS_PATH)
nop_functions = importlib.util.module_from_spec(_spec)
sys.modules["nop_functions_test"] = nop_functions
_spec.loader.exec_module(nop_functions)


class _CoreV1:
    def __init__(self) -> None:
        self.all_namespaces_called = False
        self.namespaces: list[str] = []

    def list_service_for_all_namespaces(self):
        self.all_namespaces_called = True
        raise AssertionError("cluster-scoped service listing should not be used")

    def list_namespaced_service(self, namespace: str):
        self.namespaces.append(namespace)
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="other", namespace=namespace),
                    spec=SimpleNamespace(cluster_ip="10.0.0.1"),
                ),
                SimpleNamespace(
                    metadata=SimpleNamespace(name="target", namespace=namespace),
                    spec=SimpleNamespace(cluster_ip="10.0.0.2"),
                ),
            ]
        )


def test_find_service_by_cluster_ip_uses_namespace_scope() -> None:
    v1 = _CoreV1()

    service = nop_functions.find_service_by_cluster_ip(v1, "bench", "10.0.0.2")

    assert service.metadata.name == "target"
    assert v1.namespaces == ["bench"]
    assert not v1.all_namespaces_called


def test_namespace_rbac_grants_services_without_cluster_viewer() -> None:
    template_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "templates"
        / "jinja"
        / "05_namespace_sa_rbac_secret.yaml.j2"
    )
    renderer = RenderPlans.__new__(RenderPlans)
    renderer.logger = None
    renderer._jinja_env = None

    rendered = renderer._render_template(
        template_path.read_text(encoding="utf-8"),
        {
            "namespace": {
                "name": "bench",
                "labels": {
                    "podSecurity": {
                        "audit": "restricted",
                        "warn": "restricted",
                    }
                },
            },
            "serviceAccount": {"name": "inference-perf-runner"},
            "router": {
                "monitoring": {
                    "secretName": "inference-gateway-sa-metrics-reader-secret"
                }
            },
            "fma": {"enabled": False},
            "huggingface": {"enabled": False},
            "nonAdmin": False,
        },
    )
    docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
    names = {doc["metadata"]["name"] for doc in docs}

    assert "inference-perf-service-viewer-bench" not in names

    role = next(doc for doc in docs if doc["kind"] == "Role")
    assert {
        "apiGroups": [""],
        "resources": ["services"],
        "verbs": ["get", "list"],
    } in role["rules"]
