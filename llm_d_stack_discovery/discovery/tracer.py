"""Stack tracer for discovering components from an endpoint URL."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pykube

from .utils import (
    Route,
    Gateway,
    HTTPRoute,
    InferencePool,
    InferenceModel,
    detect_gaie_version,
    detect_gaie_version_for_group,
    make_inference_pool_class,
    make_inference_model_class,
    get_resource_by_name,
    list_resources_by_selector,
    parse_endpoint_url,
    is_openshift,
)
from .collectors.vllm import VLLMCollector
from .collectors.gaie import GAIECollector
from .collectors.gateway import GatewayCollector
from .collectors.generic import GenericCollector
from ..models.components import Component, DiscoveryResult

logger = logging.getLogger(__name__)


class StackTracer:  # pylint: disable=too-many-instance-attributes
    """Traces from URL to discover all stack components."""

    def __init__(self, api: pykube.HTTPClient, k8s_client: Any):
        """Initialize tracer with Kubernetes clients.

        Args:
            api: Pykube HTTP client
            k8s_client: Kubernetes Python client
        """
        self.api = api
        self.k8s_client = k8s_client
        self.is_openshift = is_openshift(api)
        self._errors: List[str] = []

        # Detect GAIE API version and create appropriate classes
        gaie_version = detect_gaie_version(api)
        if gaie_version:
            logger.info("Detected GAIE API version: %s", gaie_version)
            self.InferencePool = make_inference_pool_class(
                gaie_version
            )  # pylint: disable=invalid-name
            self.InferenceModel = make_inference_model_class(
                gaie_version
            )  # pylint: disable=invalid-name
        else:
            logger.warning(
                "GAIE CRDs not found on cluster; using default %s",
                InferencePool.version,
            )
            self.InferencePool = InferencePool  # pylint: disable=invalid-name
            self.InferenceModel = InferenceModel  # pylint: disable=invalid-name

        # Initialize collectors
        self.vllm_collector = VLLMCollector(api)
        self.gaie_collector = GAIECollector(api)
        self.gateway_collector = GatewayCollector(api)
        self.generic_collector = GenericCollector(api)

    def trace(self, url: str) -> DiscoveryResult:
        """Trace from URL to discover all stack components.

        Args:
            url: OpenAI-style endpoint URL

        Returns:
            DiscoveryResult with discovered components
        """
        logger.info("Starting stack discovery from URL: %s", url)

        # Parse URL
        parsed_url = parse_endpoint_url(url)
        logger.info("Parsed URL: %s", parsed_url)

        # Initialize result
        result = DiscoveryResult(
            url=url,
            timestamp=datetime.now(timezone.utc).isoformat(),
            cluster_info=self._get_cluster_info(),
            components=[],
            errors=[],
        )

        # Start tracing
        self._errors = []
        try:
            entry_point, namespace = self._find_entry_point(parsed_url)
            if not entry_point:
                result.errors.append("Could not find entry point for URL")
                return result

            logger.info("Found entry point: %s/%s", entry_point.kind, entry_point.name)

            # Trace through the stack
            components = self._trace_from_entry_point(entry_point, namespace)
            result.components = components

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Error during tracing: %s", e)
            result.errors.append(f"Tracing error: {e}")

        # Surface any chain-break warnings collected during tracing
        result.errors.extend(self._errors)

        logger.info("Discovery complete. Found %d components", len(result.components))
        return result

    def _get_cluster_info(self) -> Dict[str, str]:
        """Get basic cluster information.

        Returns:
            Dict with cluster info
        """
        info = {
            "platform": "openshift" if self.is_openshift else "kubernetes",
        }

        try:
            # Get cluster version
            version_api = self.k8s_client.VersionApi()
            version_info = version_api.get_code()
            info["version"] = version_info.git_version
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Could not get cluster version: %s", e)

        return info

    def _find_entry_point(
        self, parsed_url: Dict[str, Any]
    ) -> Tuple[Optional[pykube.objects.APIObject], Optional[str]]:
        """Find the entry point resource for the URL.

        Args:
            parsed_url: Parsed URL components

        Returns:
            Tuple of (resource, namespace) or (None, None)
        """
        hostname = parsed_url["hostname"]
        port = parsed_url["port"]

        # Check if hostname is a cluster-internal service DNS name
        # Format: <service-name>.<namespace>.svc.cluster.local
        if ".svc.cluster.local" in hostname:
            service, namespace = self._find_service_by_dns(hostname)
            if service:
                return service, namespace

            # Istio creates services with a "-istio" suffix for Gateway
            # resources (e.g. Gateway "my-gw" gets service "my-gw-istio").
            # When the service lookup fails, strip the suffix and try to
            # find the underlying Gateway resource directly.
            parts = hostname.split(".")
            if len(parts) >= 2:
                service_name = parts[0]
                namespace = parts[1]
                if service_name.endswith("-istio"):
                    gateway_name = service_name[: -len("-istio")]
                    logger.info(
                        "Service not found; trying Gateway %s/%s"
                        " (stripped Istio suffix)",
                        namespace,
                        gateway_name,
                    )
                    gateway = get_resource_by_name(
                        self.api, Gateway, gateway_name, namespace
                    )
                    if gateway:
                        logger.info(
                            "Found Gateway: %s/%s", namespace, gateway_name
                        )
                        return gateway, namespace

        # Check OpenShift Routes first (if on OpenShift)
        if self.is_openshift:
            route = self._find_route_by_host(hostname)
            if route:
                namespace = route.obj["metadata"]["namespace"]
                return route, namespace

        # Check Gateway API Gateways
        gateway = self._find_gateway_by_host(hostname)
        if gateway:
            namespace = gateway.obj["metadata"]["namespace"]
            return gateway, namespace

        # Check Services (NodePort/LoadBalancer)
        service = self._find_service_by_endpoint(hostname, port)
        if service:
            namespace = service.obj["metadata"]["namespace"]
            return service, namespace

        return None, None

    def _find_service_by_dns(
        self, hostname: str
    ) -> Tuple[Optional[pykube.Service], Optional[str]]:
        """Find Service by cluster-internal DNS name.

        Args:
            hostname: Service DNS name (e.g., svc.namespace.svc.cluster.local)

        Returns:
            Tuple of (Service, namespace) or (None, None)
        """
        try:
            # Parse DNS name: <service-name>.<namespace>.svc.cluster.local
            parts = hostname.split(".")
            if len(parts) >= 2:
                service_name = parts[0]
                namespace = parts[1]

                logger.info(
                    "Looking for service %s in namespace %s",
                    service_name,
                    namespace,
                )

                # Try to get the service directly
                service = get_resource_by_name(
                    self.api, pykube.Service, service_name, namespace
                )

                if service:
                    logger.info("Found service: %s/%s", namespace, service_name)

                    # Log service details
                    spec = service.obj.get("spec", {})
                    logger.info("  Service type: %s", spec.get("type", "ClusterIP"))
                    logger.info("  Service selector: %s", spec.get("selector", {}))
                    logger.info("  Service ports: %s", spec.get("ports", []))

                    return service, namespace
                logger.warning("Service not found: %s/%s", namespace, service_name)
            else:
                logger.warning("Invalid service DNS format: %s", hostname)

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error parsing service DNS name %s: %s", hostname, e)

        return None, None

    def _find_route_by_host(self, hostname: str) -> Optional[Route]:
        """Find OpenShift Route by hostname.

        Args:
            hostname: Hostname to search for

        Returns:
            Route object or None
        """
        try:
            # List all routes across namespaces
            routes = list_resources_by_selector(self.api, Route)

            for route in routes:
                spec = route.obj.get("spec", {})
                if spec.get("host") == hostname:
                    return route

            logger.info("No route found for hostname: %s", hostname)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error searching for routes: %s", e)

        return None

    def _find_gateway_by_host(self, hostname: str) -> Optional[Gateway]:
        """Find Gateway API Gateway by hostname.

        Args:
            hostname: Hostname to search for

        Returns:
            Gateway object or None
        """
        try:
            # List all gateways across namespaces
            gateways = list_resources_by_selector(self.api, Gateway)

            for gateway in gateways:
                spec = gateway.obj.get("spec", {})
                for listener in spec.get("listeners", []):
                    if listener.get("hostname") == hostname:
                        return gateway
                
                # Check status.addresses for IP-based gateway discovery
                status = gateway.obj.get("status", {})
                for address in status.get("addresses", []):
                    if address.get("value") == hostname:
                        return gateway

            logger.info("No gateway found for hostname: %s", hostname)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error searching for gateways: %s", e)

        return None

    def _find_service_by_endpoint(
        self, hostname: str, port: int
    ) -> Optional[pykube.Service]:
        """Find Service by endpoint hostname and port.

        Args:
            hostname: Hostname or IP
            port: Port number

        Returns:
            Service object or None
        """
        try:
            # List all services across namespaces
            services = list_resources_by_selector(self.api, pykube.Service)

            for service in services:
                spec = service.obj.get("spec", {})
                service_type = spec.get("type", "ClusterIP")

                # Check LoadBalancer services
                if service_type == "LoadBalancer":
                    status = service.obj.get("status", {})
                    lb_ingress = status.get("loadBalancer", {}).get("ingress", [])
                    for ingress in lb_ingress:
                        if (
                            ingress.get("hostname") == hostname
                            or ingress.get("ip") == hostname
                        ):
                            return service

                # Check NodePort services
                elif service_type == "NodePort":
                    # This is harder - would need to check node IPs
                    # For now, check if port matches a nodePort
                    for svc_port in spec.get("ports", []):
                        if svc_port.get("nodePort") == port:
                            return service

            logger.info("No service found for endpoint: %s:%s", hostname, port)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error searching for services: %s", e)

        return None

    def _trace_from_entry_point(
        self, entry_point: pykube.objects.APIObject, namespace: str
    ) -> List[Component]:
        """Trace through the stack from an entry point.

        Args:
            entry_point: Entry point resource
            namespace: Starting namespace

        Returns:
            List of discovered components
        """
        components = []
        visited = set()  # Track visited resources to avoid loops

        # Use BFS to trace through the stack
        queue = [(entry_point, namespace)]

        logger.info(
            "Starting BFS trace from %s/%s/%s",
            entry_point.kind,
            namespace,
            entry_point.name,
        )

        while queue:
            resource, ns = queue.pop(0)

            # Create unique key for visited tracking
            key = f"{resource.kind}/{ns}/{resource.name}"
            if key in visited:
                logger.debug("Skipping already visited: %s", key)
                continue
            visited.add(key)

            logger.info("Processing: %s", key)

            # Collect component based on type
            component = None

            if isinstance(resource, Route):
                component = self.gateway_collector.collect(resource)
                # Find backend service
                backend_service = self._get_route_backend_service(resource, ns)
                if backend_service:
                    queue.append((backend_service, ns))

            elif isinstance(resource, Gateway):
                component = self.gateway_collector.collect(resource)
                # Find HTTPRoutes
                httproutes = self._find_httproutes_for_gateway(resource, ns)
                for httproute in httproutes:
                    httproute_ns = httproute.obj.get("metadata", {}).get("namespace")
                    logger.debug(
                        "  Adding HTTPRoute to queue: %s/%s",
                        httproute_ns,
                        httproute.name,
                    )
                    queue.append((httproute, httproute_ns))

            elif isinstance(resource, HTTPRoute):
                component = self.gateway_collector.collect(resource)
                # Find backend services/InferencePools
                backends = self._get_httproute_backends(resource, ns)
                logger.info(
                    "Adding %d backends to queue from HTTPRoute %s/%s",
                    len(backends),
                    ns,
                    resource.name,
                )
                for backend, backend_ns in backends:
                    logger.debug(
                        "  Adding backend: %s/%s/%s",
                        backend.kind,
                        backend_ns,
                        backend.name,
                    )
                    queue.append((backend, backend_ns))

            elif resource.kind == "InferenceModel":
                component = self.generic_collector.collect(resource)
                # Follow poolRef to InferencePool
                spec = resource.obj.get("spec", {})
                pool_ref = spec.get("poolRef", {})
                pool_name = pool_ref.get("name")
                if pool_name:
                    pool = get_resource_by_name(
                        self.api, self.InferencePool, pool_name, ns
                    )
                    if pool:
                        logger.info(
                            "  InferenceModel %s/%s -> InferencePool %s/%s",
                            ns,
                            resource.name,
                            ns,
                            pool_name,
                        )
                        queue.append((pool, ns))
                    else:
                        logger.warning(
                            "  InferencePool %s/%s not found"
                            " (from InferenceModel poolRef)",
                            ns,
                            pool_name,
                        )

            elif resource.kind == "InferencePool":
                component = self.gaie_collector.collect(resource)
                # Find backend pods/services
                backends = self._get_inferencepool_backends(resource, ns)
                for backend in backends:
                    queue.append((backend, backend.namespace))

            elif isinstance(resource, pykube.Service):
                component = self.generic_collector.collect(resource)

                # Check if this is a Gateway service
                spec = resource.obj.get("spec", {})
                selector = spec.get("selector", {})

                # Look for Gateway API gateway service
                if "gateway.networking.k8s.io/gateway-name" in selector:
                    gateway_name = selector["gateway.networking.k8s.io/gateway-name"]
                    logger.info(
                        "Service is a Gateway API gateway service for: %s",
                        gateway_name,
                    )

                    # Try to find the Gateway resource in the same namespace
                    gateway = get_resource_by_name(self.api, Gateway, gateway_name, ns)
                    if gateway:
                        logger.info("  Found Gateway: %s/%s", ns, gateway_name)
                        queue.append((gateway, ns))
                    else:
                        logger.warning(
                            "  Gateway not found in namespace %s,"
                            " searching all namespaces...",
                            ns,
                        )

                        # Search all namespaces
                        all_gateways = list_resources_by_selector(self.api, Gateway)
                        for gw in all_gateways:
                            if gw.name == gateway_name:
                                gw_ns = gw.obj.get("metadata", {}).get("namespace")
                                logger.info(
                                    "  Found Gateway: %s/%s", gw_ns, gateway_name
                                )
                                queue.append((gw, gw_ns))
                                break

                    # Also look for HTTPRoutes that might reference this gateway or service
                    # This is a fallback in case Gateway isn't found
                    logger.info("  Searching for HTTPRoutes in namespace %s...", ns)
                    httproutes = list_resources_by_selector(
                        self.api, HTTPRoute, namespace=ns
                    )
                    for httproute in httproutes:
                        # Check if this HTTPRoute references our gateway
                        spec = httproute.obj.get("spec", {})
                        for parent_ref in spec.get("parentRefs", []):
                            if parent_ref.get("name") == gateway_name:
                                # Use the HTTPRoute's own namespace, not the service namespace
                                httproute_ns = httproute.obj.get("metadata", {}).get(
                                    "namespace", ns
                                )
                                logger.info(
                                    "    Found HTTPRoute: %s/%s",
                                    httproute_ns,
                                    httproute.name,
                                )
                                queue.append((httproute, httproute_ns))
                                break

                # Also find pods for this service
                pods = self._find_pods_for_service(resource, ns)
                logger.info(
                    "Found %d pods for service %s/%s", len(pods), ns, resource.name
                )
                for pod in pods:
                    logger.debug("  Adding pod to queue: %s/%s", ns, pod.name)
                    queue.append((pod, ns))

            elif isinstance(resource, pykube.Pod):
                logger.debug("Checking pod %s/%s", ns, resource.name)
                # Try vLLM collector first
                vllm_component = self.vllm_collector.collect(resource)
                if vllm_component:
                    logger.info("  -> Identified as vLLM pod")
                    component = vllm_component
                else:
                    # Try GAIE collector (handles GAIE controller pods)
                    gaie_component = self.gaie_collector.collect(resource)
                    if gaie_component:
                        logger.info("  -> Identified as GAIE pod")
                        component = gaie_component
                    else:
                        # Generic pod collection
                        logger.debug("  -> Treating as generic pod")
                        component = self.generic_collector.collect(resource)

            else:
                # Generic collection for other resources
                component = self.generic_collector.collect(resource)

            if component:
                components.append(component)

        return components

    def _get_route_backend_service(
        self, route: Route, namespace: str
    ) -> Optional[pykube.Service]:
        """Get the backend service for a Route.

        Args:
            route: OpenShift Route
            namespace: Route namespace

        Returns:
            Service object or None
        """
        spec = route.obj.get("spec", {})
        to = spec.get("to", {})

        if to.get("kind") == "Service":
            service_name = to.get("name")
            if service_name:
                return get_resource_by_name(
                    self.api, pykube.Service, service_name, namespace
                )

        return None

    def _find_httproutes_for_gateway(
        self, gateway: Gateway, namespace: str
    ) -> List[HTTPRoute]:
        """Find HTTPRoutes that reference a Gateway.

        Args:
            gateway: Gateway resource
            namespace: Gateway namespace

        Returns:
            List of HTTPRoute objects (preserves their original namespace)
        """
        httproutes = []

        try:
            # List all HTTPRoutes across all namespaces
            all_httproutes = list_resources_by_selector(self.api, HTTPRoute)

            logger.info(
                "Searching %d HTTPRoutes for references to Gateway %s/%s",
                len(all_httproutes),
                namespace,
                gateway.name,
            )

            for httproute in all_httproutes:
                spec = httproute.obj.get("spec", {})
                httproute_ns = httproute.obj.get("metadata", {}).get("namespace")

                for parent_ref in spec.get("parentRefs", []):
                    # Check if this parent ref points to our gateway
                    ref_name = parent_ref.get("name")
                    # The namespace in parentRef is the gateway's namespace
                    ref_namespace = parent_ref.get("namespace", httproute_ns)

                    if ref_name == gateway.name and ref_namespace == namespace:
                        logger.info(
                            "  Found HTTPRoute %s/%s referencing Gateway",
                            httproute_ns,
                            httproute.name,
                        )
                        httproutes.append(httproute)
                        break

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error finding HTTPRoutes: %s", e)

        return httproutes

    def _get_httproute_backends(
        self, httproute: HTTPRoute, namespace: str
    ) -> List[Tuple[pykube.objects.APIObject, str]]:
        """Get backend resources from an HTTPRoute.

        Args:
            httproute: HTTPRoute resource
            namespace: HTTPRoute namespace

        Returns:
            List of (resource, namespace) tuples
        """
        backends = []

        spec = httproute.obj.get("spec", {})
        rules = spec.get("rules", [])

        logger.info("  HTTPRoute has %d routing rules", len(rules))

        for rule_idx, rule in enumerate(rules):
            backend_refs = rule.get("backendRefs", [])
            logger.debug("    Rule %d: %d backend refs", rule_idx, len(backend_refs))

            for backend_ref in backend_refs:
                kind = backend_ref.get("kind", "Service")
                name = backend_ref.get("name")
                backend_ns = backend_ref.get("namespace", namespace)

                if not name:
                    logger.debug("      Skipping backend with no name")
                    continue

                logger.info("      Looking for %s %s/%s", kind, backend_ns, name)

                if kind == "Service":
                    service = get_resource_by_name(
                        self.api, pykube.Service, name, backend_ns
                    )
                    if service:
                        logger.info("        Found Service: %s/%s", backend_ns, name)
                        backends.append((service, backend_ns))
                    else:
                        logger.warning(
                            "        Service not found: %s/%s", backend_ns, name
                        )

                elif kind == "InferencePool":
                    # Resolve the right InferencePool class using the backendRef group
                    pool_class = self._get_inferencepool_class(
                        backend_ref.get("group", "")
                    )
                    pool = get_resource_by_name(self.api, pool_class, name, backend_ns)
                    if pool:
                        logger.info(
                            "        Found InferencePool: %s/%s", backend_ns, name
                        )
                        backends.append((pool, backend_ns))
                    else:
                        logger.warning(
                            "        InferencePool %s/%s not found"
                            " (API version: %s),"
                            " trying fallback pod discovery",
                            backend_ns,
                            name,
                            pool_class.version,
                        )
                        self._errors.append(
                            f"InferencePool {backend_ns}/{name} not found"
                            f" (API version: {pool_class.version})."
                            f" Falling back to direct pod discovery."
                        )
                        fallback_pods = list_resources_by_selector(
                            self.api,
                            pykube.Pod,
                            namespace=backend_ns,
                            selector={"llm-d.ai/inferenceServing": "true"},
                        )
                        if fallback_pods:
                            logger.info(
                                "        Fallback found %d pods in %s",
                                len(fallback_pods),
                                backend_ns,
                            )
                        else:
                            logger.warning(
                                "        Fallback found no pods in %s", backend_ns
                            )
                        for pod in fallback_pods:
                            backends.append((pod, backend_ns))

                elif kind == "InferenceModel":
                    model = get_resource_by_name(
                        self.api, self.InferenceModel, name, backend_ns
                    )
                    if model:
                        logger.info(
                            "        Found InferenceModel: %s/%s", backend_ns, name
                        )
                        backends.append((model, backend_ns))
                    else:
                        logger.warning(
                            "        InferenceModel not found: %s/%s", backend_ns, name
                        )

        logger.info("  Found %d total backends for HTTPRoute", len(backends))
        return backends

    def _get_inferencepool_class(self, group: str) -> type:
        """Return the InferencePool pykube class for the given API group.

        If *group* matches (or is absent from) the group already embedded in
        ``self.InferencePool.version``, the pre-built class is returned as-is.
        Otherwise the cluster is probed for the preferred version of *group*
        and a fresh dynamic class is built for it.

        Args:
            group: API group from an HTTPRoute backendRef (may be empty string)

        Returns:
            A pykube APIObject subclass for InferencePool
        """
        if not group:
            return self.InferencePool
        cur_group = (
            self.InferencePool.version.split("/")[0]
            if "/" in self.InferencePool.version
            else ""
        )
        if group == cur_group:
            return self.InferencePool
        # Group differs from the pre-built class - probe and build a new one.
        probed = detect_gaie_version_for_group(self.api, group)
        if probed:
            return make_inference_pool_class(probed)
        # Fall back: keep the version suffix, swap the group.
        ver_suffix = (
            self.InferencePool.version.split("/", 1)[1]
            if "/" in self.InferencePool.version
            else "v1alpha2"
        )
        return make_inference_pool_class(f"{group}/{ver_suffix}")

    def _get_inferencepool_backends(
        self, pool: pykube.objects.APIObject, namespace: str
    ) -> List[pykube.objects.APIObject]:
        """Get backend pods/services from an InferencePool.

        Args:
            pool: InferencePool resource
            namespace: Pool namespace

        Returns:
            List of backend resources (Pods or Services)
        """
        backends = []

        spec = pool.obj.get("spec", {})

        # Check for explicit backends
        for backend in spec.get("backends", []):
            service_ref = backend.get("service", {})
            service_name = service_ref.get("name")
            service_ns = service_ref.get("namespace", namespace)

            if service_name:
                service = get_resource_by_name(
                    self.api, pykube.Service, service_name, service_ns
                )
                if service:
                    backends.append(service)

        # Check for selector-based backends (pods selected directly)
        if "selector" in spec:
            selector = spec["selector"].get("matchLabels", {})
            selector_ns = spec["selector"].get("namespace", namespace)

            matched_pods = list_resources_by_selector(
                self.api, pykube.Pod, namespace=selector_ns, selector=selector
            )
            backends.extend(matched_pods)

        # Check for modelServers-based backends (pods selected directly)
        if "modelServers" in spec:
            model_servers = spec["modelServers"]
            match_labels = model_servers.get("matchLabels", {})
            if match_labels:
                matched_pods = list_resources_by_selector(
                    self.api, pykube.Pod, namespace=namespace, selector=match_labels
                )
                backends.extend(matched_pods)

        # Follow extensionRef or endpointPickerRef to EPP service
        for ref_key in ("extensionRef", "endpointPickerRef"):
            ref = spec.get(ref_key, {})
            ref_name = ref.get("name")
            ref_kind = ref.get("kind", "Service")
            ref_ns = ref.get("namespace", namespace)
            if ref_name and ref_kind == "Service":
                ref_service = get_resource_by_name(
                    self.api, pykube.Service, ref_name, ref_ns
                )
                if ref_service:
                    backends.append(ref_service)
                else:
                    logger.warning(
                        "%s Service %s/%s not found", ref_key, ref_ns, ref_name
                    )
                break

        return backends

    def _find_pods_for_service(
        self, service: pykube.Service, namespace: str
    ) -> List[pykube.Pod]:
        """Find pods that match a service selector.

        Args:
            service: Service resource
            namespace: Service namespace

        Returns:
            List of Pod objects
        """
        spec = service.obj.get("spec", {})
        selector = spec.get("selector", {})

        if not selector:
            logger.warning("Service %s/%s has no selector", namespace, service.name)
            return []

        logger.info(
            "Looking for pods matching selector %s in namespace %s",
            selector,
            namespace,
        )

        # Find pods matching the selector
        pods = list_resources_by_selector(
            self.api, pykube.Pod, namespace=namespace, selector=selector
        )

        logger.info(
            "Found %d pods for service %s/%s", len(pods), namespace, service.name
        )
        for pod in pods:
            logger.debug("  - Pod: %s", pod.name)

        return pods
