"""Unit tests for Gateway collector."""

# pylint: disable=duplicate-code,protected-access

import unittest
from unittest.mock import Mock, patch

import pykube

from llm_d_stack_discovery.discovery.collectors.gateway import GatewayCollector
from llm_d_stack_discovery.discovery.utils import Route, Gateway, HTTPRoute


class TestGatewayCollector(unittest.TestCase):
    """Test GatewayCollector functionality."""

    def setUp(self):
        self.api = Mock(spec=pykube.HTTPClient)
        self.collector = GatewayCollector(self.api)

    def _make_route(
        self, name="my-route", namespace="default", host="model.example.com"
    ):
        route = Mock(spec=Route)
        route.kind = "Route"
        route.name = name
        route.namespace = namespace
        route.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "host": host,
                "path": "/",
                "to": {"kind": "Service", "name": "backend-svc", "weight": 100},
                "port": {"targetPort": 8000},
                "tls": {"termination": "edge"},
                "wildcardPolicy": "None",
            },
            "status": {"ingress": [{"host": host}]},
        }
        return route

    def _make_gateway(self, name="my-gw", namespace="default", listeners=None):
        gw = Mock(spec=Gateway)
        gw.kind = "Gateway"
        gw.name = name
        gw.namespace = namespace
        if listeners is None:
            listeners = [
                {
                    "name": "http",
                    "protocol": "HTTP",
                    "port": 80,
                    "hostname": "model.example.com",
                }
            ]
        gw.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {},
                "annotations": {},
            },
            "spec": {"gatewayClassName": "istio", "listeners": listeners},
            "status": {"addresses": [{"value": "1.2.3.4"}], "conditions": []},
        }
        return gw

    def _make_httproute(self, name="my-hr", namespace="default"):
        hr = Mock(spec=HTTPRoute)
        hr.kind = "HTTPRoute"
        hr.name = name
        hr.namespace = namespace
        hr.obj = {
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {},
                "annotations": {},
            },
            "spec": {
                "parentRefs": [
                    {"name": "my-gw", "namespace": namespace, "kind": "Gateway"}
                ],
                "hostnames": ["model.example.com"],
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/v1"}}],
                        "backendRefs": [
                            {
                                "kind": "InferencePool",
                                "name": "pool-1",
                                "namespace": namespace,
                                "port": 8000,
                                "weight": 1,
                            }
                        ],
                    }
                ],
            },
        }
        return hr

    def test_collect_route(self):
        """OpenShift Route produces Component with tool='openshift-route'."""
        route = self._make_route()
        component = self.collector.collect(route)

        self.assertIsNotNone(component)
        self.assertEqual(component.tool, "openshift-route")
        self.assertEqual(component.metadata.kind, "Route")
        self.assertIn("route_config", component.native)
        self.assertEqual(component.native["route_config"]["host"], "model.example.com")

    @patch.object(GatewayCollector, "_get_gateway_class", return_value=None)
    def test_collect_gateway(self, _mock_gc):
        """Gateway API Gateway produces Component with tool='gateway-api'."""
        gw = self._make_gateway()
        component = self.collector.collect(gw)

        self.assertIsNotNone(component)
        self.assertEqual(component.tool, "gateway-api")
        self.assertEqual(component.metadata.kind, "Gateway")
        self.assertIn("listeners", component.native)

    def test_collect_httproute(self):
        """HTTPRoute produces Component with tool='gateway-api-httproute'."""
        hr = self._make_httproute()
        component = self.collector.collect(hr)

        self.assertIsNotNone(component)
        self.assertEqual(component.tool, "gateway-api-httproute")
        self.assertEqual(component.metadata.kind, "HTTPRoute")
        self.assertIn("rules", component.native)

    def test_extract_listeners(self):
        """Listener extraction from Gateway spec."""
        listeners_spec = [
            {
                "name": "https",
                "protocol": "HTTPS",
                "port": 443,
                "hostname": "api.example.com",
                "tls": {"mode": "Terminate", "certificateRefs": [{"name": "tls-cert"}]},
                "allowedRoutes": {"namespaces": {"from": "Same"}},
            },
        ]
        result = self.collector._extract_listeners(listeners_spec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["port"], 443)
        self.assertEqual(result[0]["protocol"], "HTTPS")
        self.assertIn("tls", result[0])
        self.assertEqual(result[0]["tls"]["mode"], "Terminate")
        self.assertIn("allowedRoutes", result[0])

    def test_extract_route_rules(self):
        """Route rules extraction from HTTPRoute."""
        rules_spec = [
            {
                "matches": [
                    {"path": {"type": "PathPrefix", "value": "/v1"}, "method": "POST"}
                ],
                "filters": [
                    {
                        "type": "RequestHeaderModifier",
                        "requestHeaderModifier": {
                            "set": [{"name": "x-foo", "value": "bar"}]
                        },
                    }
                ],
                "backendRefs": [
                    {"kind": "Service", "name": "svc-a", "port": 8000, "weight": 1}
                ],
            }
        ]
        result = self.collector._extract_route_rules(rules_spec)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["matches"]), 1)
        self.assertEqual(result[0]["matches"][0]["method"], "POST")
        self.assertEqual(len(result[0]["filters"]), 1)
        self.assertEqual(result[0]["filters"][0]["type"], "RequestHeaderModifier")
        self.assertEqual(len(result[0]["backendRefs"]), 1)
        self.assertEqual(result[0]["backendRefs"][0]["name"], "svc-a")

    def test_extract_parent_refs(self):
        """Parent reference extraction from HTTPRoute."""
        refs_spec = [
            {
                "name": "my-gw",
                "namespace": "gw-ns",
                "kind": "Gateway",
                "sectionName": "https",
                "port": 443,
            },
        ]
        result = self.collector._extract_parent_refs(refs_spec)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "my-gw")
        self.assertEqual(result[0]["namespace"], "gw-ns")
        self.assertEqual(result[0]["sectionName"], "https")
        self.assertEqual(result[0]["port"], 443)


if __name__ == "__main__":
    unittest.main()
