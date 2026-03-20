"""Generic collector for other Kubernetes resources."""

import logging
from typing import Optional

import pykube

from .base import BaseCollector
from ...models.components import Component

logger = logging.getLogger(__name__)


class GenericCollector(BaseCollector):
    """Generic collector for Kubernetes resources without specific collectors."""

    def collect(self, resource: pykube.objects.APIObject) -> Optional[Component]:
        """Collect generic configuration from a resource.

        Args:
            resource: Kubernetes resource

        Returns:
            Component object
        """
        try:
            # Determine tool name based on resource type
            tool = self._determine_tool(resource)

            # Extract basic information based on resource type
            extra_info = {}

            if isinstance(resource, pykube.Service):
                extra_info = self._collect_service_info(resource)
            elif isinstance(resource, pykube.ConfigMap):
                extra_info = self._collect_configmap_info(resource)
            elif isinstance(resource, pykube.Deployment):
                extra_info = self._collect_deployment_info(resource)
            elif isinstance(resource, pykube.StatefulSet):
                extra_info = self._collect_statefulset_info(resource)

            # Build native config with extra info
            native = {**resource.obj}
            if extra_info:
                native["extracted_info"] = extra_info

            return self.create_component(
                resource=resource,
                tool=tool,
                tool_version="unknown",
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to collect generic config from %s: %s", resource.name, e
            )
            return None

    def _determine_tool(self, resource: pykube.objects.APIObject) -> str:
        """Determine tool name for a resource.

        Args:
            resource: Kubernetes resource

        Returns:
            Tool name string
        """
        # Check labels for tool hints
        labels = resource.obj.get("metadata", {}).get("labels", {})

        if "app.kubernetes.io/name" in labels:
            return labels["app.kubernetes.io/name"]
        if "app" in labels:
            return labels["app"]
        if "component" in labels:
            return labels["component"]

        # Default to resource kind
        return resource.kind.lower()

    def _collect_service_info(self, service: pykube.Service) -> dict:
        """Collect Service-specific information.

        Args:
            service: Kubernetes Service

        Returns:
            Dict with service information
        """
        spec = service.obj.get("spec", {})

        return {
            "service_type": spec.get("type", "ClusterIP"),
            "cluster_ip": spec.get("clusterIP"),
            "ports": spec.get("ports", []),
            "selector": spec.get("selector", {}),
            "session_affinity": spec.get("sessionAffinity", "None"),
            "external_name": spec.get("externalName"),
            "load_balancer_ip": spec.get("loadBalancerIP"),
            "load_balancer_class": spec.get("loadBalancerClass"),
        }

    def _collect_configmap_info(self, configmap: pykube.ConfigMap) -> dict:
        """Collect ConfigMap information (keys only, not values).

        Args:
            configmap: Kubernetes ConfigMap

        Returns:
            Dict with configmap information
        """
        data = configmap.obj.get("data", {})
        binary_data = configmap.obj.get("binaryData", {})

        return {
            "data_keys": list(data.keys()),
            "binary_data_keys": list(binary_data.keys()),
            "immutable": configmap.obj.get("immutable", False),
        }

    def _collect_deployment_info(self, deployment: pykube.Deployment) -> dict:
        """Collect Deployment information.

        Args:
            deployment: Kubernetes Deployment

        Returns:
            Dict with deployment information
        """
        spec = deployment.obj.get("spec", {})
        status = deployment.obj.get("status", {})

        # Extract container images
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        images = [c.get("image") for c in containers if c.get("image")]

        return {
            "replicas": spec.get("replicas", 1),
            "selector": spec.get("selector", {}),
            "strategy": spec.get("strategy", {}),
            "images": images,
            "ready_replicas": status.get("readyReplicas", 0),
            "available_replicas": status.get("availableReplicas", 0),
            "updated_replicas": status.get("updatedReplicas", 0),
        }

    def _collect_statefulset_info(self, statefulset: pykube.StatefulSet) -> dict:
        """Collect StatefulSet information.

        Args:
            statefulset: Kubernetes StatefulSet

        Returns:
            Dict with statefulset information
        """
        spec = statefulset.obj.get("spec", {})
        status = statefulset.obj.get("status", {})

        # Extract container images
        containers = spec.get("template", {}).get("spec", {}).get("containers", [])
        images = [c.get("image") for c in containers if c.get("image")]

        # Extract volume claim templates
        volume_claims = []
        for vct in spec.get("volumeClaimTemplates", []):
            volume_claims.append(
                {
                    "name": vct.get("metadata", {}).get("name"),
                    "storage_class": vct.get("spec", {}).get("storageClassName"),
                    "access_modes": vct.get("spec", {}).get("accessModes", []),
                    "storage": vct.get("spec", {})
                    .get("resources", {})
                    .get("requests", {})
                    .get("storage"),
                }
            )

        return {
            "replicas": spec.get("replicas", 1),
            "selector": spec.get("selector", {}),
            "service_name": spec.get("serviceName"),
            "pod_management_policy": spec.get("podManagementPolicy", "OrderedReady"),
            "update_strategy": spec.get("updateStrategy", {}),
            "images": images,
            "volume_claim_templates": volume_claims,
            "ready_replicas": status.get("readyReplicas", 0),
            "current_replicas": status.get("currentReplicas", 0),
            "updated_replicas": status.get("updatedReplicas", 0),
        }
