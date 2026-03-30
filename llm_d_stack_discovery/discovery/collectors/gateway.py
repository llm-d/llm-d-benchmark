"""Gateway and Route collectors."""

import logging
from typing import Any, Dict, Optional, List

import pykube

from .base import BaseCollector
from ..utils import Route, Gateway, HTTPRoute, ClusterVersion, get_resource_by_name
from ...models.components import Component

logger = logging.getLogger(__name__)


class GatewayCollector(BaseCollector):
    """Collector for Gateway API and OpenShift Route components."""

    def collect(self, resource: pykube.objects.APIObject) -> Optional[Component]:
        """Collect configuration from a Gateway resource.

        Args:
            resource: Kubernetes resource (Route, Gateway, or HTTPRoute)

        Returns:
            Component object or None
        """
        if isinstance(resource, Route):
            return self.collect_route(resource)
        if isinstance(resource, Gateway):
            return self.collect_gateway(resource)
        if isinstance(resource, HTTPRoute):
            return self.collect_httproute(resource)
        logger.warning(
            "GatewayCollector cannot handle resource type: %s", type(resource)
        )
        return None

    def collect_route(self, route: Route) -> Optional[Component]:
        """Collect configuration from an OpenShift Route.

        Args:
            route: OpenShift Route resource

        Returns:
            Component object or None
        """
        try:
            spec = route.obj.get("spec", {})
            status = route.obj.get("status", {})

            # Extract route configuration
            route_config = {
                "host": spec.get("host"),
                "path": spec.get("path", "/"),
                "to": spec.get("to", {}),
                "port": spec.get("port", {}),
                "tls": spec.get("tls", {}),
                "wildcardPolicy": spec.get("wildcardPolicy", "None"),
            }

            # Get backend service info
            backend = self._extract_route_backend(spec)

            native = {
                "route": route.obj,
                "route_type": "openshift",
                "route_config": route_config,
                "backend": backend,
                "ingress": status.get("ingress", []),
            }

            return self.create_component(
                resource=route,
                tool="openshift-route",
                tool_version=self._get_openshift_version(),
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to collect Route config from %s: %s", route.name, e)
            return None

    def collect_gateway(self, gateway: Gateway) -> Optional[Component]:
        """Collect configuration from a Gateway API Gateway.

        Args:
            gateway: Gateway API Gateway resource

        Returns:
            Component object or None
        """
        try:
            spec = gateway.obj.get("spec", {})
            status = gateway.obj.get("status", {})

            # Extract listeners
            listeners = self._extract_listeners(spec.get("listeners", []))

            # Extract gateway class info
            gateway_class_name = spec.get("gatewayClassName")
            gateway_class = self._get_gateway_class(gateway_class_name)

            native = {
                "gateway": gateway.obj,
                "gateway_type": "gateway-api",
                "gateway_class": gateway_class_name,
                "gateway_class_config": gateway_class,
                "listeners": listeners,
                "addresses": status.get("addresses", []),
                "conditions": status.get("conditions", []),
            }

            return self.create_component(
                resource=gateway,
                tool="gateway-api",
                tool_version="v1",
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to collect Gateway config from %s: %s", gateway.name, e
            )
            return None

    def collect_httproute(self, httproute: HTTPRoute) -> Optional[Component]:
        """Collect configuration from an HTTPRoute.

        Args:
            httproute: Gateway API HTTPRoute resource

        Returns:
            Component object or None
        """
        try:
            spec = httproute.obj.get("spec", {})

            # Extract parent references (gateways)
            parent_refs = self._extract_parent_refs(spec.get("parentRefs", []))

            # Extract routing rules
            rules = self._extract_route_rules(spec.get("rules", []))

            # Extract hostnames
            hostnames = spec.get("hostnames", [])

            native = {
                "httproute": httproute.obj,
                "route_type": "httproute",
                "parent_refs": parent_refs,
                "hostnames": hostnames,
                "rules": rules,
            }

            return self.create_component(
                resource=httproute,
                tool="gateway-api-httproute",
                tool_version="v1",
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to collect HTTPRoute config from %s: %s", httproute.name, e
            )
            return None

    def _extract_route_backend(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Extract backend information from Route spec.

        Args:
            spec: Route spec

        Returns:
            Backend configuration
        """
        to = spec.get("to", {})
        backend = {
            "kind": to.get("kind", "Service"),
            "name": to.get("name"),
            "weight": to.get("weight", 100),
        }

        # Check for alternate backends
        alternate_backends = []
        for alt in spec.get("alternateBackends", []):
            alternate_backends.append(
                {
                    "kind": alt.get("kind", "Service"),
                    "name": alt.get("name"),
                    "weight": alt.get("weight", 0),
                }
            )

        if alternate_backends:
            backend["alternates"] = alternate_backends

        return backend

    def _extract_listeners(
        self, listeners: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract listener configurations from Gateway.

        Args:
            listeners: List of listener specs

        Returns:
            List of listener configurations
        """
        extracted = []

        for listener in listeners:
            config = {
                "name": listener.get("name"),
                "protocol": listener.get("protocol"),
                "port": listener.get("port"),
                "hostname": listener.get("hostname"),
            }

            # Extract TLS config if present
            if "tls" in listener:
                tls = listener["tls"]
                config["tls"] = {
                    "mode": tls.get("mode", "Terminate"),
                    "certificateRefs": tls.get("certificateRefs", []),
                    "options": tls.get("options", {}),
                }

            # Extract allowed routes
            if "allowedRoutes" in listener:
                config["allowedRoutes"] = listener["allowedRoutes"]

            extracted.append(config)

        return extracted

    def _extract_parent_refs(
        self, parent_refs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract parent references from HTTPRoute.

        Args:
            parent_refs: List of parent reference specs

        Returns:
            List of parent reference configurations
        """
        extracted = []

        for ref in parent_refs:
            config = {
                "group": ref.get("group", "gateway.networking.k8s.io"),
                "kind": ref.get("kind", "Gateway"),
                "name": ref.get("name"),
                "namespace": ref.get("namespace"),
                "sectionName": ref.get("sectionName"),
                "port": ref.get("port"),
            }
            extracted.append(config)

        return extracted

    def _extract_route_rules(self, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract routing rules from HTTPRoute.

        Args:
            rules: List of rule specs

        Returns:
            List of rule configurations
        """
        extracted = []

        for rule in rules:
            config = {
                "matches": [],
                "filters": [],
                "backendRefs": [],
            }

            # Extract matches
            for match in rule.get("matches", []):
                match_config = {
                    "path": match.get("path"),
                    "headers": match.get("headers", []),
                    "queryParams": match.get("queryParams", []),
                    "method": match.get("method"),
                }
                config["matches"].append(match_config)

            # Extract filters
            for filter_spec in rule.get("filters", []):
                filter_config = {
                    "type": filter_spec.get("type"),
                    "config": {k: v for k, v in filter_spec.items() if k != "type"},
                }
                config["filters"].append(filter_config)

            # Extract backend references
            for backend_ref in rule.get("backendRefs", []):
                backend_config = {
                    "group": backend_ref.get("group", ""),
                    "kind": backend_ref.get("kind", "Service"),
                    "name": backend_ref.get("name"),
                    "namespace": backend_ref.get("namespace"),
                    "port": backend_ref.get("port"),
                    "weight": backend_ref.get("weight", 1),
                }

                # Check for InferencePool reference
                if backend_config["kind"] == "InferencePool":
                    backend_config["inferencePool"] = True

                config["backendRefs"].append(backend_config)

            extracted.append(config)

        return extracted

    def _get_gateway_class(self, class_name: str) -> Optional[Dict[str, Any]]:
        """Get GatewayClass configuration.

        Args:
            class_name: GatewayClass name

        Returns:
            GatewayClass configuration or None
        """
        try:
            # Define GatewayClass as a custom resource
            class GatewayClass(pykube.objects.APIObject):
                """Gateway API GatewayClass resource."""

                version = "gateway.networking.k8s.io/v1"
                endpoint = "gatewayclasses"
                kind = "GatewayClass"

            gateway_class = get_resource_by_name(self.api, GatewayClass, class_name)
            if gateway_class:
                spec = gateway_class.obj.get("spec", {})
                return {
                    "controllerName": spec.get("controllerName"),
                    "parametersRef": spec.get("parametersRef"),
                    "description": spec.get("description"),
                }
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to get GatewayClass %s: %s", class_name, e)

        return None

    def _get_openshift_version(self) -> str:
        """Get OpenShift version from cluster.

        Returns:
            OpenShift version string
        """
        try:
            cv = ClusterVersion.objects(self.api).get(name="version")
            return cv.obj.get("status", {}).get("desired", {}).get("version", "4.x")
        except Exception:  # pylint: disable=broad-exception-caught
            return "4.x"
