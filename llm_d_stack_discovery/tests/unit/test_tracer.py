"""Unit tests for StackTracer."""

# pylint: disable=duplicate-code,protected-access,unused-argument

import unittest
from unittest.mock import Mock, patch

import pykube

from llm_d_stack_discovery.discovery.tracer import StackTracer
from llm_d_stack_discovery.discovery.utils import (
    Gateway,
    HTTPRoute,
    InferencePool,
    InferenceModel,
    detect_gaie_version,
    make_inference_pool_class,
    make_inference_model_class,
)


def _make_pod(name, namespace="default", is_vllm=True):
    """Helper to create a mock Pod."""
    pod = Mock(spec=pykube.Pod)
    pod.kind = "Pod"
    pod.name = name
    pod.namespace = namespace

    if is_vllm:
        image = "vllm/vllm-openai:v0.4.0"
        command = ["python", "-m", "vllm.entrypoints.openai.api_server"]
        args = ["--model", "llama-2", "--tensor-parallel-size", "4", "--port", "8000"]
    else:
        image = "nginx:latest"
        command = ["nginx"]
        args = []

    pod.obj = {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "vllm"},
            "annotations": {},
        },
        "spec": {
            "nodeName": "node-0",
            "containers": [
                {
                    "name": "main",
                    "image": image,
                    "command": command,
                    "args": args,
                    "env": [],
                    "resources": {
                        "requests": {"nvidia.com/gpu": "4"},
                        "limits": {"nvidia.com/gpu": "4"},
                    },
                    "ports": [{"containerPort": 8000}],
                }
            ],
        },
    }
    return pod


def _make_service(name, namespace="default", selector=None):
    """Helper to create a mock Service."""
    svc = Mock(spec=pykube.Service)
    svc.kind = "Service"
    svc.name = name
    svc.namespace = namespace
    if selector is None:
        selector = {"app": "vllm"}
    svc.obj = {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {},
            "annotations": {},
        },
        "spec": {"type": "ClusterIP", "selector": selector, "ports": [{"port": 8000}]},
    }
    return svc


class TestStackTracer(unittest.TestCase):
    """Test StackTracer BFS tracing."""

    def _make_tracer(self, gaie_version=None):
        """Create a StackTracer with mocked external calls."""
        api = Mock(spec=pykube.HTTPClient)
        k8s_client = Mock()
        version_api = Mock()
        version_info = Mock()
        version_info.git_version = "v1.28.0"
        version_api.get_code.return_value = version_info
        k8s_client.VersionApi.return_value = version_api

        with (
            patch(
                "llm_d_stack_discovery.discovery.tracer.is_openshift",
                return_value=False,
            ),
            patch(
                "llm_d_stack_discovery.discovery.tracer.detect_gaie_version",
                return_value=gaie_version,
            ),
        ):
            tracer = StackTracer(api, k8s_client)

        return tracer

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_trace_service_to_pods(self, mock_get, mock_list):
        """Service -> 2 vLLM pods returns 3 components (Service + 2 Pods)."""
        tracer = self._make_tracer()

        svc = _make_service("my-svc")
        pod1 = _make_pod("vllm-0")
        pod2 = _make_pod("vllm-1")

        # Mock: get_resource_by_name returns None (no Gateway lookup)
        mock_get.return_value = None

        # Mock: list_resources_by_selector returns pods for service
        def list_side_effect(api, resource_class, namespace=None, selector=None):
            if resource_class == pykube.Pod and selector == {"app": "vllm"}:
                return [pod1, pod2]
            return []

        mock_list.side_effect = list_side_effect

        # Patch _get_gpu_info to avoid node lookups
        with patch.object(
            tracer.vllm_collector,
            "_get_gpu_info",
            return_value={"count": 4, "model": "A100-80GB"},
        ):
            components = tracer._trace_from_entry_point(svc, "default")

        # Service + 2 Pods = 3 components
        self.assertEqual(len(components), 3)
        kinds = [c.metadata.kind for c in components]
        self.assertIn("Service", kinds)
        pod_components = [c for c in components if c.metadata.kind == "Pod"]
        self.assertEqual(len(pod_components), 2)

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_trace_gateway_to_httproute_to_pool(self, mock_get, mock_list):
        """Gateway -> HTTPRoute -> InferencePool -> Service -> Pods."""
        tracer = self._make_tracer()

        gw = Mock(spec=Gateway)
        gw.kind = "Gateway"
        gw.name = "my-gw"
        gw.namespace = "default"
        gw.obj = {
            "metadata": {
                "name": "my-gw",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "gatewayClassName": "istio",
                "listeners": [
                    {
                        "name": "http",
                        "protocol": "HTTP",
                        "port": 80,
                        "hostname": "model.example.com",
                    }
                ],
            },
            "status": {"addresses": [], "conditions": []},
        }

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "default"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [{"name": "my-gw", "namespace": "default"}],
                "hostnames": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "my-pool",
                                "namespace": "default",
                            }
                        ]
                    }
                ],
            },
        }

        pool = Mock(spec=InferencePool)
        pool.kind = "InferencePool"
        pool.name = "my-pool"
        pool.namespace = "default"
        pool.obj = {
            "metadata": {
                "name": "my-pool",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {"selector": {"matchLabels": {"app": "vllm"}}},
            "status": {},
        }

        svc = _make_service("vllm-svc")
        pod = _make_pod("vllm-0")

        def get_side_effect(api, resource_class, name, namespace=None):
            if resource_class == InferencePool and name == "my-pool":
                return pool
            return None

        mock_get.side_effect = get_side_effect

        def list_side_effect(api, resource_class, namespace=None, selector=None):
            if resource_class == HTTPRoute:
                return [hr]
            if resource_class == pykube.Service and selector == {"app": "vllm"}:
                return [svc]
            if resource_class == pykube.Pod and selector == {"app": "vllm"}:
                return [pod]
            return []

        mock_list.side_effect = list_side_effect

        with patch.object(
            tracer.gateway_collector, "_get_gateway_class", return_value=None
        ):
            with patch.object(
                tracer.vllm_collector,
                "_get_gpu_info",
                return_value={"count": 4, "model": "A100-80GB"},
            ):
                components = tracer._trace_from_entry_point(gw, "default")

        # Should have: Gateway, HTTPRoute, InferencePool, Service, Pod
        self.assertGreaterEqual(len(components), 3)
        component_kinds = {c.metadata.kind for c in components}
        self.assertIn("Gateway", component_kinds)

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_visited_prevents_loops(self, mock_get, mock_list):
        """Same resource queued twice, only collected once."""
        tracer = self._make_tracer()

        svc = _make_service("loop-svc", selector={"app": "loop"})

        # No pods match, no gateways
        mock_get.return_value = None
        mock_list.return_value = []

        # Trace twice from the same entry point
        components1 = tracer._trace_from_entry_point(svc, "default")
        # Should get exactly 1 component (the service itself)
        self.assertEqual(len(components1), 1)
        self.assertEqual(components1[0].metadata.name, "loop-svc")

    @patch("llm_d_stack_discovery.discovery.tracer.is_openshift", return_value=False)
    def test_entry_point_not_found(self, _mock_ocp):
        """Returns DiscoveryResult with error when entry point not found."""
        api = Mock(spec=pykube.HTTPClient)
        k8s_client = Mock()
        version_api = Mock()
        version_info = Mock()
        version_info.git_version = "v1.28.0"
        version_api.get_code.return_value = version_info
        k8s_client.VersionApi.return_value = version_api

        tracer = StackTracer(api, k8s_client)

        with patch.object(tracer, "_find_entry_point", return_value=(None, None)):
            result = tracer.trace("https://nonexistent.example.com/v1")

        self.assertEqual(len(result.components), 0)
        self.assertTrue(len(result.errors) > 0)
        self.assertIn("Could not find entry point", result.errors[0])

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_dns_entry_point(self, mock_get, mock_list):
        """svc.ns.svc.cluster.local resolves to Service."""
        tracer = self._make_tracer()

        svc = _make_service("my-svc", namespace="model-ns")

        def get_side_effect(api, resource_class, name, namespace=None):
            if (
                resource_class == pykube.Service
                and name == "my-svc"
                and namespace == "model-ns"
            ):
                return svc
            return None

        mock_get.side_effect = get_side_effect
        mock_list.return_value = []

        entry_point, namespace = tracer._find_entry_point(
            {
                "hostname": "my-svc.model-ns.svc.cluster.local",
                "port": 8000,
                "scheme": "http",
                "path": "/v1",
            }
        )

        self.assertIsNotNone(entry_point)
        self.assertEqual(namespace, "model-ns")

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_inferencepool_model_servers_discovers_pods(self, mock_get, mock_list):
        """InferencePool with spec.modelServers.matchLabels discovers Pods."""
        tracer = self._make_tracer()

        pool = Mock(spec=InferencePool)
        pool.kind = "InferencePool"
        pool.name = "my-pool"
        pool.namespace = "default"
        pool.obj = {
            "metadata": {
                "name": "my-pool",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "targetPortNumber": 8000,
                "modelServers": {"matchLabels": {"app": "vllm"}},
            },
            "status": {},
        }

        pod1 = _make_pod("vllm-0")
        pod2 = _make_pod("vllm-1")

        mock_get.return_value = None

        def list_side_effect(api, resource_class, namespace=None, selector=None):
            if resource_class == pykube.Pod and selector == {"app": "vllm"}:
                return [pod1, pod2]
            return []

        mock_list.side_effect = list_side_effect

        backends = tracer._get_inferencepool_backends(pool, "default")
        self.assertEqual(len(backends), 2)
        for b in backends:
            self.assertEqual(b.kind, "Pod")

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_inferencemodel_traversal(self, mock_get, mock_list):
        """HTTPRoute -> InferenceModel -> InferencePool chain discovers all three."""
        tracer = self._make_tracer()

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "default"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferenceModel",
                                "name": "my-model",
                                "namespace": "default",
                            }
                        ]
                    }
                ],
            },
        }

        im = Mock(spec=InferenceModel)
        im.kind = "InferenceModel"
        im.name = "my-model"
        im.namespace = "default"
        im.obj = {
            "metadata": {
                "name": "my-model",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {"poolRef": {"name": "my-pool"}},
        }

        pool = Mock(spec=InferencePool)
        pool.kind = "InferencePool"
        pool.name = "my-pool"
        pool.namespace = "default"
        pool.obj = {
            "metadata": {
                "name": "my-pool",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {},
            "status": {},
        }

        def get_side_effect(api, resource_class, name, namespace=None):
            if resource_class == InferenceModel and name == "my-model":
                return im
            if resource_class == InferencePool and name == "my-pool":
                return pool
            return None

        mock_get.side_effect = get_side_effect
        mock_list.return_value = []

        with patch.object(
            tracer.gateway_collector, "_get_gateway_class", return_value=None
        ):
            components = tracer._trace_from_entry_point(hr, "default")

        # Should discover HTTPRoute, InferenceModel, InferencePool
        component_kinds = [c.metadata.kind for c in components]
        self.assertIn("HTTPRoute", component_kinds)
        self.assertIn("InferenceModel", component_kinds)
        self.assertIn("InferencePool", component_kinds)
        self.assertEqual(len(components), 3)


class TestDetectGaieVersion(unittest.TestCase):
    """Tests for GAIE API version auto-detection."""

    def test_detect_preferred_version(self):
        """Returns preferred version when available."""
        api = Mock()
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "preferredVersion": {
                "groupVersion": "inference.networking.x-k8s.io/v1alpha3",
            },
            "versions": [
                {"groupVersion": "inference.networking.x-k8s.io/v1alpha3"},
                {"groupVersion": "inference.networking.x-k8s.io/v1alpha2"},
            ],
        }
        api.session.get.return_value = response
        api.url = "https://k8s.example.com"
        api.kwargs = {}

        result = detect_gaie_version(api)
        self.assertEqual(result, "inference.networking.x-k8s.io/v1alpha3")

    def test_detect_falls_back_to_first_version(self):
        """Falls back to first version when no preferred version."""
        api = Mock()
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "versions": [
                {"groupVersion": "inference.networking.x-k8s.io/v1alpha1"},
            ],
        }
        api.session.get.return_value = response
        api.url = "https://k8s.example.com"
        api.kwargs = {}

        result = detect_gaie_version(api)
        self.assertEqual(result, "inference.networking.x-k8s.io/v1alpha1")

    def test_detect_returns_none_when_not_installed(self):
        """Returns None when GAIE CRDs are not installed (404)."""
        api = Mock()
        response = Mock()
        response.ok = False
        api.session.get.return_value = response
        api.url = "https://k8s.example.com"
        api.kwargs = {}

        result = detect_gaie_version(api)
        self.assertIsNone(result)

    def test_detect_returns_none_on_exception(self):
        """Returns None when API call raises an exception."""
        api = Mock()
        api.session.get.side_effect = ConnectionError("connection refused")
        api.url = "https://k8s.example.com"
        api.kwargs = {}

        result = detect_gaie_version(api)
        self.assertIsNone(result)


class TestMakeInferenceClasses(unittest.TestCase):
    """Tests for dynamic InferencePool/InferenceModel class factories."""

    def test_make_inference_pool_class(self):
        """Factory creates class with correct attributes."""
        cls = make_inference_pool_class("inference.networking.x-k8s.io/v1alpha3")
        self.assertEqual(cls.version, "inference.networking.x-k8s.io/v1alpha3")
        self.assertEqual(cls.endpoint, "inferencepools")
        self.assertEqual(cls.kind, "InferencePool")
        self.assertTrue(cls.namespaced)
        self.assertTrue(issubclass(cls, pykube.objects.APIObject))

    def test_make_inference_model_class(self):
        """Factory creates class with correct attributes."""
        cls = make_inference_model_class("inference.networking.x-k8s.io/v1alpha3")
        self.assertEqual(cls.version, "inference.networking.x-k8s.io/v1alpha3")
        self.assertEqual(cls.endpoint, "inferencemodels")
        self.assertEqual(cls.kind, "InferenceModel")
        self.assertTrue(cls.namespaced)
        self.assertTrue(issubclass(cls, pykube.objects.APIObject))


class TestGaieVersionIntegration(TestStackTracer):
    """Tests for GAIE version detection integration in StackTracer."""

    def test_tracer_uses_detected_version(self):
        """StackTracer uses detected GAIE version for InferencePool class."""
        tracer = self._make_tracer(
            gaie_version="inference.networking.x-k8s.io/v1alpha3"
        )
        self.assertEqual(
            tracer.InferencePool.version,
            "inference.networking.x-k8s.io/v1alpha3",
        )
        self.assertEqual(
            tracer.InferenceModel.version,
            "inference.networking.x-k8s.io/v1alpha3",
        )

    def test_tracer_falls_back_to_default(self):
        """StackTracer falls back to static classes when detection fails."""
        tracer = self._make_tracer(gaie_version=None)
        self.assertIs(tracer.InferencePool, InferencePool)
        self.assertIs(tracer.InferenceModel, InferenceModel)

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_detected_version_used_in_pool_lookup(self, mock_get, mock_list):
        """InferencePool lookup uses the detected API version class."""
        tracer = self._make_tracer(
            gaie_version="inference.networking.x-k8s.io/v1alpha3"
        )

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "default"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "my-pool",
                                "namespace": "default",
                            }
                        ]
                    }
                ],
            },
        }

        mock_get.return_value = None
        mock_list.return_value = []

        tracer._get_httproute_backends(hr, "default")

        # Verify get_resource_by_name was called with the v1alpha3 class
        call_args = mock_get.call_args
        resource_class = call_args[0][1]
        self.assertEqual(
            resource_class.version,
            "inference.networking.x-k8s.io/v1alpha3",
        )


class TestFallbackPodDiscovery(TestStackTracer):
    """Tests for fallback pod discovery when InferencePool not found."""

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_fallback_discovers_pods_when_pool_not_found(self, mock_get, mock_list):
        """Fallback finds vLLM pods when InferencePool lookup fails."""
        tracer = self._make_tracer()

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "model-ns"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "model-ns",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "missing-pool",
                                "namespace": "model-ns",
                            }
                        ]
                    }
                ],
            },
        }

        pod1 = _make_pod("vllm-prefill-0", namespace="model-ns")
        pod2 = _make_pod("vllm-decode-0", namespace="model-ns")

        # InferencePool not found
        mock_get.return_value = None

        def list_side_effect(api, resource_class, namespace=None, selector=None):
            if (
                resource_class == pykube.Pod
                and namespace == "model-ns"
                and selector == {"llm-d.ai/inferenceServing": "true"}
            ):
                return [pod1, pod2]
            return []

        mock_list.side_effect = list_side_effect

        backends = tracer._get_httproute_backends(hr, "model-ns")

        # Should find 2 pods via fallback
        self.assertEqual(len(backends), 2)
        for resource, ns in backends:
            self.assertEqual(resource.kind, "Pod")
            self.assertEqual(ns, "model-ns")

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_fallback_error_surfaced_in_result(self, mock_get, mock_list):
        """Chain-break warning appears in DiscoveryResult.errors."""
        tracer = self._make_tracer()

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "default"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "missing-pool",
                                "namespace": "default",
                            }
                        ]
                    }
                ],
            },
        }

        mock_get.return_value = None
        mock_list.return_value = []

        with patch.object(tracer, "_find_entry_point", return_value=(hr, "default")):
            with patch.object(
                tracer.gateway_collector, "_get_gateway_class", return_value=None
            ):
                result = tracer.trace("https://model.example.com/v1")

        # Should have the fallback warning in errors
        self.assertTrue(len(result.errors) > 0)
        error_text = " ".join(result.errors)
        self.assertIn("InferencePool", error_text)
        self.assertIn("not found", error_text)
        self.assertIn("Falling back", error_text)

    @patch("llm_d_stack_discovery.discovery.tracer.list_resources_by_selector")
    @patch("llm_d_stack_discovery.discovery.tracer.get_resource_by_name")
    def test_no_fallback_when_pool_found(self, mock_get, mock_list):
        """No fallback triggered when InferencePool is found normally."""
        tracer = self._make_tracer()

        pool = Mock(spec=InferencePool)
        pool.kind = "InferencePool"
        pool.name = "my-pool"
        pool.namespace = "default"
        pool.obj = {
            "metadata": {
                "name": "my-pool",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {},
            "status": {},
        }

        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = "my-hr"
        hr.namespace = "default"
        hr.obj = {
            "metadata": {
                "name": "my-hr",
                "namespace": "default",
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [],
                "rules": [
                    {
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "my-pool",
                                "namespace": "default",
                            }
                        ]
                    }
                ],
            },
        }

        def get_side_effect(api, resource_class, name, namespace=None):
            if name == "my-pool":
                return pool
            return None

        mock_get.side_effect = get_side_effect
        mock_list.return_value = []

        backends = tracer._get_httproute_backends(hr, "default")

        # Should find the pool directly, no fallback
        self.assertEqual(len(backends), 1)
        resource, _ = backends[0]
        self.assertEqual(resource.kind, "InferencePool")
        # No errors should be recorded
        self.assertEqual(len(tracer._errors), 0)


if __name__ == "__main__":
    unittest.main()
