"""Kubernetes utilities for stack discovery.

Reuses patterns from setup/functions.py for Kubernetes interactions.
"""

import os
import sys
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import pykube
from pykube.exceptions import PyKubeError, ObjectDoesNotExist
from kubernetes import client as k8s_client, config as k8s_config

logger = logging.getLogger(__name__)


def kube_connect(
    config_path: Optional[str] = None, context: Optional[str] = None
) -> tuple[pykube.HTTPClient, Any]:
    """Connect to Kubernetes cluster.

    Args:
        config_path: Path to kubeconfig file (defaults to ~/.kube/config)
        context: Kubernetes context to use (optional)

    Returns:
        Tuple of (pykube HTTPClient, kubernetes client)
    """
    if config_path is None:
        config_path = os.path.expanduser("~/.kube/config")

    try:
        # Load pykube config
        kube_config = pykube.KubeConfig.from_file(config_path)
        if context:
            kube_config.set_current_context(context)
        api = pykube.HTTPClient(kube_config, timeout=120)

        # Load kubernetes-python config
        k8s_config.load_kube_config(config_path, context=context)

        return api, k8s_client
    except FileNotFoundError:
        logger.error("Kubeconfig file not found at %s", config_path)
        sys.exit(1)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to connect to Kubernetes: %s", e)
        sys.exit(1)


class SecurityContextConstraints(pykube.objects.APIObject):
    """OpenShift SecurityContextConstraints resource."""

    version = "security.openshift.io/v1"
    endpoint = "securitycontextconstraints"
    kind = "SecurityContextConstraints"


class Route(pykube.objects.APIObject):
    """OpenShift Route resource."""

    version = "route.openshift.io/v1"
    endpoint = "routes"
    kind = "Route"
    namespaced = True


class Gateway(pykube.objects.APIObject):
    """Gateway API Gateway resource."""

    version = "gateway.networking.k8s.io/v1"
    endpoint = "gateways"
    kind = "Gateway"
    namespaced = True


class HTTPRoute(pykube.objects.APIObject):
    """Gateway API HTTPRoute resource."""

    version = "gateway.networking.k8s.io/v1"
    endpoint = "httproutes"
    kind = "HTTPRoute"
    namespaced = True


class InferencePool(pykube.objects.APIObject):
    """GAIE InferencePool resource (default v1alpha2)."""

    version = "inference.networking.x-k8s.io/v1alpha2"
    endpoint = "inferencepools"
    kind = "InferencePool"
    namespaced = True


class InferenceModel(pykube.objects.APIObject):
    """GAIE InferenceModel resource (default v1alpha2)."""

    version = "inference.networking.x-k8s.io/v1alpha2"
    endpoint = "inferencemodels"
    kind = "InferenceModel"
    namespaced = True


GAIE_API_GROUP = "inference.networking.x-k8s.io"
# Stable (non-experimental) group name used since GAIE v0.5+
GAIE_API_GROUP_STABLE = "inference.networking.k8s.io"


def detect_gaie_version_for_group(api: pykube.HTTPClient, group: str) -> Optional[str]:
    """Return the preferred API version for one specific GAIE API group.

    Args:
        api: Pykube HTTP client
        group: API group name to probe (e.g. "inference.networking.k8s.io")

    Returns:
        Full group/version string or None if the group is not available.
    """
    try:
        response = api.session.get(
            url=f"{api.url}/apis/{group}",
        )
        if response.ok:
            data = response.json()
            preferred = data.get("preferredVersion", {}).get("groupVersion")
            if preferred:
                return preferred
            versions = data.get("versions", [])
            if versions:
                return versions[0].get("groupVersion")
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("Could not probe GAIE API group: %s", group)
    return None


def detect_gaie_version(api: pykube.HTTPClient) -> Optional[str]:
    """Detect the installed GAIE API version by querying the API group.

    Primary strategy: query /apis to get all API groups at once, then look for
    either GAIE group (stable k8s.io preferred over experimental x-k8s.io).
    Fallback: probe each group's individual /apis/<group> endpoint.

    Args:
        api: Pykube HTTP client

    Returns:
        Full group/version string (e.g. "inference.networking.k8s.io/v1alpha2")
        or None if not installed.
    """
    # Primary: single /apis request lists every group with its preferred version
    try:
        response = api.session.get(url=f"{api.url}/apis")
        if response.ok:
            found: dict = {}
            for group_info in response.json().get("groups", []):
                name = group_info.get("name", "")
                if name in (GAIE_API_GROUP_STABLE, GAIE_API_GROUP):
                    preferred = group_info.get("preferredVersion", {}).get(
                        "groupVersion"
                    )
                    if preferred:
                        found[name] = preferred
                    else:
                        versions = group_info.get("versions", [])
                        if versions:
                            found[name] = versions[0].get("groupVersion")
            # Prefer stable group over experimental
            for grp in [GAIE_API_GROUP_STABLE, GAIE_API_GROUP]:
                if grp in found:
                    return found[grp]
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("Could not probe /apis endpoint for GAIE group discovery")

    # Fallback: probe individual groups (stable first, then experimental)
    for group in [GAIE_API_GROUP_STABLE, GAIE_API_GROUP]:
        version = detect_gaie_version_for_group(api, group)
        if version:
            return version
    return None


def make_inference_pool_class(api_version: str) -> type:
    """Create an InferencePool pykube class for the given API version.

    Args:
        api_version: Full API version (e.g. "inference.networking.x-k8s.io/v1alpha3")

    Returns:
        A pykube APIObject subclass for InferencePool.
    """
    return type(
        "InferencePool",
        (pykube.objects.APIObject,),
        {
            "version": api_version,
            "endpoint": "inferencepools",
            "kind": "InferencePool",
            "namespaced": True,
        },
    )


def make_inference_model_class(api_version: str) -> type:
    """Create an InferenceModel pykube class for the given API version.

    Args:
        api_version: Full API version (e.g. "inference.networking.x-k8s.io/v1alpha3")

    Returns:
        A pykube APIObject subclass for InferenceModel.
    """
    return type(
        "InferenceModel",
        (pykube.objects.APIObject,),
        {
            "version": api_version,
            "endpoint": "inferencemodels",
            "kind": "InferenceModel",
            "namespaced": True,
        },
    )


class ClusterVersion(pykube.objects.APIObject):
    """OpenShift ClusterVersion resource."""

    version = "config.openshift.io/v1"
    endpoint = "clusterversions"
    kind = "ClusterVersion"


def is_openshift(api: pykube.HTTPClient) -> bool:
    """Check if connected to an OpenShift cluster."""
    try:
        # Check for privileged SCC which is standard in OpenShift
        SecurityContextConstraints.objects(api).get(name="privileged")
        logger.info("OpenShift cluster detected")
        return True
    except ObjectDoesNotExist:
        logger.info("'privileged' SCC not found (not OpenShift)")
        return False
    except PyKubeError as e:
        if getattr(e, "code", None) == 404:
            logger.info("Standard Kubernetes cluster detected (not OpenShift)")
            return False
        logger.warning("Could not query SCCs: %s. Assuming not OpenShift", e)
        return False
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Unexpected error checking for OpenShift: %s", e)
        return False


def get_resource_by_name(
    api: pykube.HTTPClient,
    resource_class: type[pykube.objects.APIObject],
    name: str,
    namespace: Optional[str] = None,
) -> Optional[pykube.objects.APIObject]:
    """Get a Kubernetes resource by name.

    Args:
        api: Pykube HTTP client
        resource_class: Resource class (e.g., pykube.Service)
        name: Resource name
        namespace: Namespace (required for namespaced resources)

    Returns:
        Resource object or None if not found
    """
    try:
        if hasattr(resource_class, "namespaced") and resource_class.namespaced:
            if not namespace:
                logger.error("Namespace required for %s", resource_class.kind)
                return None
            return resource_class.objects(api, namespace=namespace).get(name=name)
        return resource_class.objects(api).get(name=name)
    except ObjectDoesNotExist:
        return None
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting %s/%s: %s", resource_class.kind, name, e)
        return None


def list_resources_by_selector(
    api: pykube.HTTPClient,
    resource_class: type[pykube.objects.APIObject],
    namespace: Optional[str] = None,
    selector: Optional[Dict[str, str]] = None,
) -> List[pykube.objects.APIObject]:
    """List resources matching a label selector.

    Args:
        api: Pykube HTTP client
        resource_class: Resource class
        namespace: Namespace (optional, for all namespaces if not specified)
        selector: Label selector dict

    Returns:
        List of matching resources
    """
    try:
        query_params = {}
        if (
            namespace
            and hasattr(resource_class, "namespaced")
            and resource_class.namespaced
        ):
            query_params["namespace"] = namespace

        if selector:
            # Convert selector dict to label selector string
            label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
            resources = resource_class.objects(api, **query_params).filter(
                selector=label_selector
            )
        else:
            resources = resource_class.objects(api, **query_params)

        return list(resources)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error listing %s: %s", resource_class.kind, e)
        return []


def parse_endpoint_url(url: str) -> Dict[str, Any]:
    """Parse an OpenAI-style endpoint URL.

    Args:
        url: URL to parse (e.g., https://model.example.com/v1)

    Returns:
        Dict with parsed components
    """
    parsed = urlparse(url)

    # Extract port or use default
    port = parsed.port
    if not port:
        port = 443 if parsed.scheme == "https" else 80

    return {
        "scheme": parsed.scheme,
        "hostname": parsed.hostname,
        "port": port,
        "path": parsed.path.rstrip("/"),
        "full_url": url,
    }


def get_pod_containers(pod: pykube.Pod) -> List[Dict[str, Any]]:
    """Extract container information from a pod.

    Args:
        pod: Pykube Pod object

    Returns:
        List of container info dicts
    """
    containers = []
    pod_spec = pod.obj.get("spec", {})

    for container in pod_spec.get("containers", []):
        info = {
            "name": container.get("name"),
            "image": container.get("image"),
            "command": container.get("command", []),
            "args": container.get("args", []),
            "env": container.get("env", []),
            "env_from": container.get("envFrom", []),
            "resources": container.get("resources", {}),
            "ports": container.get("ports", []),
        }
        containers.append(info)

    return containers


def get_node_info(api: pykube.HTTPClient, node_name: str) -> Optional[Dict[str, Any]]:
    """Get information about a node.

    Args:
        api: Pykube HTTP client
        node_name: Node name

    Returns:
        Node info dict or None
    """
    try:
        node = pykube.Node.objects(api).get(name=node_name)

        labels = node.obj.get("metadata", {}).get("labels", {})

        # Extract GPU info from labels
        gpu_info = {}
        if "nvidia.com/gpu.product" in labels:
            gpu_info["product"] = labels["nvidia.com/gpu.product"]
        if "nvidia.com/gpu.count" in labels:
            gpu_info["count"] = labels["nvidia.com/gpu.count"]
        if "nvidia.com/gpu.memory" in labels:
            gpu_info["memory"] = labels["nvidia.com/gpu.memory"]

        return {
            "name": node_name,
            "labels": labels,
            "gpu": gpu_info if gpu_info else None,
            "capacity": node.obj.get("status", {}).get("capacity", {}),
            "allocatable": node.obj.get("status", {}).get("allocatable", {}),
        }
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting node info for %s: %s", node_name, e)
        return None


def get_configmap_data(
    api: pykube.HTTPClient, name: str, namespace: str
) -> Optional[Dict[str, str]]:
    """Get data from a ConfigMap.

    Args:
        api: Pykube HTTP client
        name: ConfigMap name
        namespace: Namespace

    Returns:
        ConfigMap data dict or None
    """
    try:
        cm = pykube.ConfigMap.objects(api, namespace=namespace).get(name=name)
        return cm.obj.get("data", {})
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error getting ConfigMap %s/%s: %s", namespace, name, e)
        return None


def get_service_endpoints(service: pykube.Service) -> List[Dict[str, Any]]:
    """Extract endpoint information from a service.

    Args:
        service: Pykube Service object

    Returns:
        List of endpoint info dicts
    """
    spec = service.obj.get("spec", {})
    endpoints = []

    # Get service type and ports
    service_type = spec.get("type", "ClusterIP")
    ports = spec.get("ports", [])

    for port in ports:
        endpoint = {
            "name": port.get("name"),
            "port": port.get("port"),
            "target_port": port.get("targetPort"),
            "protocol": port.get("protocol", "TCP"),
            "node_port": port.get("nodePort") if service_type == "NodePort" else None,
        }
        endpoints.append(endpoint)

    return endpoints
