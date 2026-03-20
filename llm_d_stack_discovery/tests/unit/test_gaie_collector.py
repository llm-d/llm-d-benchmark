"""Unit tests for GAIE collector."""

# pylint: disable=duplicate-code,protected-access

import unittest
from unittest.mock import Mock

import pykube

from llm_d_stack_discovery.discovery.collectors.gaie import GAIECollector
from llm_d_stack_discovery.discovery.utils import InferencePool


class TestGAIECollector(unittest.TestCase):
    """Test GAIECollector functionality."""

    def setUp(self):
        self.api = Mock(spec=pykube.HTTPClient)
        self.collector = GAIECollector(self.api)

    def _make_inference_pool(self, name="my-pool", namespace="default", spec=None):
        pool = Mock(spec=InferencePool)
        pool.kind = "InferencePool"
        pool.name = name
        pool.namespace = namespace

        if spec is None:
            spec = {
                "selector": {"matchLabels": {"app": "vllm"}},
                "plugin": {"name": "epp-plugin", "version": "v1.0", "config": {}},
            }

        pool.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {},
                "annotations": {},
            },
            "spec": spec,
            "status": {},
        }
        return pool

    def _make_gaie_pod(
        self,
        name="gaie-controller-0",
        namespace="default",
        labels=None,
        image="gaie-controller:v1.0",
    ):
        pod = Mock(spec=pykube.Pod)
        pod.kind = "Pod"
        pod.name = name
        pod.namespace = namespace

        if labels is None:
            labels = {"app.kubernetes.io/name": "gaie"}

        pod.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {},
            },
            "spec": {
                "containers": [
                    {
                        "name": "gaie-controller",
                        "image": image,
                        "command": ["./gaie-controller"],
                        "args": ["--leader-elect", "--metrics-bind-address", ":8080"],
                        "env": [
                            {"name": "GAIE_NAMESPACE", "value": "model-ns"},
                        ],
                        "resources": {},
                        "ports": [{"containerPort": 8080}],
                    }
                ],
                "volumes": [],
            },
        }
        return pod

    def test_collect_inference_pool(self):
        """InferencePool resource produces Component with tool='gaie'."""
        pool = self._make_inference_pool()
        component = self.collector.collect(pool)

        self.assertIsNotNone(component)
        self.assertEqual(component.tool, "gaie")
        self.assertEqual(component.metadata.kind, "InferencePool")
        self.assertEqual(component.metadata.name, "my-pool")

    def test_collect_gaie_pod(self):
        """Pod with gaie label produces Component with tool='gaie-controller'."""
        pod = self._make_gaie_pod()
        component = self.collector.collect(pod)

        self.assertIsNotNone(component)
        self.assertEqual(component.tool, "gaie-controller")
        self.assertEqual(component.metadata.kind, "Pod")

    def test_is_gaie_pod_by_label(self):
        """Detection via app.kubernetes.io/name=gaie label."""
        pod = self._make_gaie_pod(labels={"app.kubernetes.io/name": "gaie"})
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_is_gaie_pod_by_image(self):
        """Detection via 'gaie' in image name."""
        pod = self._make_gaie_pod(
            labels={}, image="registry.example.com/gaie-controller:latest"
        )
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_is_gaie_pod_by_inferencepool_label(self):
        """Detection via inferencepool label ending with -epp."""
        pod = self._make_gaie_pod(
            labels={"inferencepool": "my-pool-gaie-epp"},
            image="registry.example.com/custom-scheduler:v1.0",
        )
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_is_gaie_pod_by_component_label(self):
        """Detection via app.kubernetes.io/component=epp label."""
        pod = self._make_gaie_pod(
            labels={"app.kubernetes.io/component": "epp"},
            image="registry.example.com/custom:v1.0",
        )
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_is_gaie_pod_by_epp_image(self):
        """Detection via 'epp' in image name."""
        pod = self._make_gaie_pod(
            labels={},
            image="registry.example.com/epp-server:latest",
        )
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_is_gaie_pod_by_inference_scheduler_image(self):
        """Detection via 'inference-scheduler' in image name."""
        pod = self._make_gaie_pod(
            labels={},
            image="registry.example.com/inference-scheduler:v2.0",
        )
        self.assertTrue(self.collector._is_gaie_pod(pod))

    def test_non_gaie_pod_returns_none(self):
        """Non-GAIE pod returns None from collect."""
        pod = Mock(spec=pykube.Pod)
        pod.kind = "Pod"
        pod.name = "nginx-pod"
        pod.obj = {
            "metadata": {
                "name": "nginx-pod",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "containers": [
                    {
                        "name": "nginx",
                        "image": "nginx:latest",
                        "command": ["nginx"],
                        "args": [],
                        "env": [],
                        "resources": {},
                    }
                ]
            },
        }
        component = self.collector.collect(pod)
        self.assertIsNone(component)

    def test_extract_backends_explicit(self):
        """Explicit backend list extraction."""
        spec = {
            "backends": [
                {
                    "name": "backend-a",
                    "service": {"name": "svc-a", "namespace": "ns-a", "port": 8000},
                    "weight": 50,
                    "labels": {"tier": "prefill"},
                },
            ],
        }
        backends = self.collector._extract_backends(spec)
        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0]["service"], "svc-a")
        self.assertEqual(backends[0]["weight"], 50)

    def test_extract_backends_selector(self):
        """Selector-based backend extraction."""
        spec = {
            "selector": {
                "matchLabels": {"app": "vllm"},
                "namespace": "model-ns",
            }
        }
        backends = self.collector._extract_backends(spec)
        self.assertEqual(len(backends), 1)
        self.assertEqual(backends[0]["type"], "selector")
        self.assertEqual(backends[0]["selector"], {"app": "vllm"})

    def test_extract_routing_config(self):
        """Routing config extraction with profiles."""
        spec = {
            "routing": {"type": "least-request", "rules": [{"match": "prefix"}]},
            "profiles": [
                {
                    "name": "prefill-profile",
                    "match": {"header": "x-role"},
                    "backend": "prefill-svc",
                    "config": {"timeout": "30s"},
                }
            ],
        }
        routing = self.collector._extract_routing_config(spec)
        self.assertEqual(routing["type"], "least-request")
        self.assertEqual(len(routing["profiles"]), 1)
        self.assertEqual(routing["profiles"][0]["name"], "prefill-profile")

    def test_get_gaie_version_from_annotation(self):
        """Version extraction from annotation."""
        pool = self._make_inference_pool()
        pool.obj["metadata"]["annotations"] = {
            "gaie.llm-d-toolkit.io/version": "v1.3.0"
        }
        version = self.collector._get_gaie_version(pool)
        self.assertEqual(version, "v1.3.0")

    def test_get_gaie_version_fallback(self):
        """Version fallback when no annotation or status."""
        pool = self._make_inference_pool()
        version = self.collector._get_gaie_version(pool)
        self.assertEqual(version, "unknown")


if __name__ == "__main__":
    unittest.main()
