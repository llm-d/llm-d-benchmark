"""Unit tests for Generic collector."""

# pylint: disable=protected-access

import unittest
from unittest.mock import Mock

import pykube

from llm_d_stack_discovery.discovery.collectors.generic import GenericCollector


class TestGenericCollector(unittest.TestCase):
    """Test GenericCollector functionality."""

    def setUp(self):
        self.api = Mock(spec=pykube.HTTPClient)
        self.collector = GenericCollector(self.api)

    def _make_service(self, name="my-svc", namespace="default", labels=None):
        svc = Mock(spec=pykube.Service)
        svc.kind = "Service"
        svc.name = name
        svc.namespace = namespace
        if labels is None:
            labels = {}
        svc.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {},
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": "vllm"},
                "ports": [{"port": 8000}],
                "clusterIP": "10.0.0.1",
            },
        }
        return svc

    def _make_configmap(self, name="my-cm", namespace="default", labels=None):
        cm = Mock(spec=pykube.ConfigMap)
        cm.kind = "ConfigMap"
        cm.name = name
        cm.namespace = namespace
        if labels is None:
            labels = {}
        cm.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {},
            },
            "data": {"config.yaml": "key: value", "settings.json": "{}"},
            "binaryData": {},
        }
        return cm

    def _make_deployment(self, name="my-deploy", namespace="default", labels=None):
        deploy = Mock(spec=pykube.Deployment)
        deploy.kind = "Deployment"
        deploy.name = name
        deploy.namespace = namespace
        if labels is None:
            labels = {}
        deploy.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "annotations": {},
            },
            "spec": {
                "replicas": 3,
                "selector": {"matchLabels": {"app": "my-app"}},
                "strategy": {"type": "RollingUpdate"},
                "template": {"spec": {"containers": [{"image": "my-image:v1.0"}]}},
            },
            "status": {
                "readyReplicas": 3,
                "availableReplicas": 3,
                "updatedReplicas": 3,
            },
        }
        return deploy

    def test_collect_service(self):
        """Service produces a Component."""
        svc = self._make_service()
        component = self.collector.collect(svc)

        self.assertIsNotNone(component)
        self.assertEqual(component.metadata.kind, "Service")
        self.assertEqual(component.metadata.name, "my-svc")
        self.assertIn("extracted_info", component.native)
        self.assertEqual(
            component.native["extracted_info"]["service_type"], "ClusterIP"
        )

    def test_collect_configmap(self):
        """ConfigMap produces a Component."""
        cm = self._make_configmap()
        component = self.collector.collect(cm)

        self.assertIsNotNone(component)
        self.assertEqual(component.metadata.kind, "ConfigMap")
        self.assertIn("extracted_info", component.native)
        self.assertIn("config.yaml", component.native["extracted_info"]["data_keys"])

    def test_collect_deployment(self):
        """Deployment produces a Component."""
        deploy = self._make_deployment()
        component = self.collector.collect(deploy)

        self.assertIsNotNone(component)
        self.assertEqual(component.metadata.kind, "Deployment")
        self.assertIn("extracted_info", component.native)
        self.assertEqual(component.native["extracted_info"]["replicas"], 3)
        self.assertIn("my-image:v1.0", component.native["extracted_info"]["images"])

    def test_determine_tool_from_labels(self):
        """Tool name determined from app.kubernetes.io/name label."""
        svc = self._make_service(labels={"app.kubernetes.io/name": "redis"})
        tool = self.collector._determine_tool(svc)
        self.assertEqual(tool, "redis")

    def test_determine_tool_from_app_label(self):
        """Tool name determined from 'app' label."""
        svc = self._make_service(labels={"app": "my-custom-app"})
        tool = self.collector._determine_tool(svc)
        self.assertEqual(tool, "my-custom-app")

    def test_determine_tool_from_kind(self):
        """Tool name falls back to lowercase resource kind."""
        svc = self._make_service(labels={})
        tool = self.collector._determine_tool(svc)
        self.assertEqual(tool, "service")


if __name__ == "__main__":
    unittest.main()
