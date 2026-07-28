"""Regression test for find_service_by_cluster_ip namespace scoping.

The 'nop' harness looks up the standalone vLLM Service by ClusterIP. It must
call the namespace-scoped list_namespaced_service(), not the cluster-scoped
list_service_for_all_namespaces() -- the harness SA is only granted a
namespaced Role, so the cluster-scoped call 403s on RBAC-strict clusters
(GKE/CKS). See issue #1278.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

# nop_functions.py uses bare imports that assume workload/harnesses is on the
# path, which is how it runs in the harness container. Mirror that here.
_HARNESS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "workload", "harnesses")
)
if _HARNESS_DIR not in sys.path:
    sys.path.insert(0, _HARNESS_DIR)

import nop_functions as nf  # noqa: E402


def _fake_service(name, namespace, cluster_ip):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, namespace=namespace),
        spec=SimpleNamespace(cluster_ip=cluster_ip),
    )


class TestFindServiceByClusterIp:
    def test_calls_list_namespaced_service_not_all_namespaces(self):
        v1 = MagicMock()
        v1.list_namespaced_service.return_value = SimpleNamespace(items=[])

        nf.find_service_by_cluster_ip(v1, "my-ns", "10.0.0.1")

        v1.list_namespaced_service.assert_called_once_with(namespace="my-ns")
        v1.list_service_for_all_namespaces.assert_not_called()

    def test_finds_matching_service_in_namespace(self):
        svc = _fake_service("vllm-svc", "my-ns", "10.0.0.5")
        v1 = MagicMock()
        v1.list_namespaced_service.return_value = SimpleNamespace(items=[svc])

        found = nf.find_service_by_cluster_ip(v1, "my-ns", "10.0.0.5")

        assert found is svc

    def test_returns_none_when_no_match(self):
        svc = _fake_service("other-svc", "my-ns", "10.0.0.9")
        v1 = MagicMock()
        v1.list_namespaced_service.return_value = SimpleNamespace(items=[svc])

        found = nf.find_service_by_cluster_ip(v1, "my-ns", "10.0.0.5")

        assert found is None
