"""GAIE (Gateway API Inference Extension) collector."""

import logging
from typing import Any, Dict, Optional, List

import pykube

from .base import BaseCollector
from ...models.components import Component
from ..utils import get_configmap_data

logger = logging.getLogger(__name__)


class GAIECollector(BaseCollector):
    """Collector for GAIE and InferencePool components."""

    def collect(self, resource: pykube.objects.APIObject) -> Optional[Component]:
        """Collect GAIE configuration from an InferencePool or GAIE Pod.

        Args:
            resource: InferencePool resource or Pod resource

        Returns:
            Component object or None if collection failed
        """
        # Handle GAIE controller pods
        if isinstance(resource, pykube.Pod) and self._is_gaie_pod(resource):
            return self.collect_gaie_pod(resource)

        # Handle InferencePool resources
        if not (hasattr(resource, "kind") and resource.kind == "InferencePool"):
            logger.warning(
                "Resource is not an InferencePool or GAIE Pod: %s", type(resource)
            )
            return None

        try:
            spec = resource.obj.get("spec", {})

            # Extract GAIE configuration
            gaie_config = self._extract_gaie_config(spec)

            # Extract backend information
            backends = self._extract_backends(spec)

            # Extract routing configuration
            routing = self._extract_routing_config(spec)

            # Build native configuration
            native = {
                "inference_pool": resource.obj,
                "inference_pool_name": resource.name,
                "backends": backends,
                "routing": routing,
                "gaie_config": gaie_config,
            }

            # Get GAIE version from status or annotations
            gaie_version = self._get_gaie_version(resource)

            return self.create_component(
                resource=resource,
                tool="gaie",
                tool_version=gaie_version,
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error("Failed to collect GAIE config from %s: %s", resource.name, e)
            return None

    def _extract_gaie_config(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Extract GAIE-specific configuration.

        Args:
            spec: InferencePool spec

        Returns:
            Dict with GAIE configuration
        """
        config = {}

        # Extract plugin configuration
        if "plugin" in spec:
            plugin = spec["plugin"]
            config["plugin_name"] = plugin.get("name", "unknown")
            config["plugin_version"] = plugin.get("version", "unknown")
            config["plugin_config"] = plugin.get("config", {})

        # Extract inference settings
        if "inference" in spec:
            inference = spec["inference"]
            config["inference_engine"] = inference.get("engine", "vllm")
            config["inference_config"] = inference.get("config", {})

        # Extract scheduling settings
        if "scheduling" in spec:
            scheduling = spec["scheduling"]
            config["scheduling_policy"] = scheduling.get("policy", "default")
            config["scheduling_config"] = scheduling.get("config", {})

        return config

    def _extract_backends(self, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract backend service information.

        Args:
            spec: InferencePool spec

        Returns:
            List of backend configurations
        """
        backends = []

        # Check for backend services
        if "backends" in spec:
            for backend in spec["backends"]:
                backend_info = {
                    "name": backend.get("name"),
                    "service": backend.get("service", {}).get("name"),
                    "namespace": backend.get("service", {}).get("namespace"),
                    "port": backend.get("service", {}).get("port"),
                    "weight": backend.get("weight", 1),
                    "labels": backend.get("labels", {}),
                }
                backends.append(backend_info)

        # Check for selector-based backends
        elif "selector" in spec:
            selector = spec["selector"]
            backend_info = {
                "type": "selector",
                "selector": selector.get("matchLabels", {}),
                "namespace": selector.get("namespace"),
            }
            backends.append(backend_info)

        return backends

    def _extract_routing_config(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Extract routing configuration.

        Args:
            spec: InferencePool spec

        Returns:
            Dict with routing configuration
        """
        routing = {}

        # Extract routing rules
        if "routing" in spec:
            routing_spec = spec["routing"]
            routing["type"] = routing_spec.get("type", "round-robin")
            routing["rules"] = routing_spec.get("rules", [])

        # Extract profile-based routing
        if "profiles" in spec:
            routing["profiles"] = []
            for profile in spec["profiles"]:
                profile_info = {
                    "name": profile.get("name"),
                    "match": profile.get("match", {}),
                    "backend": profile.get("backend"),
                    "config": profile.get("config", {}),
                }
                routing["profiles"].append(profile_info)

        return routing

    def _get_gaie_version(self, resource: pykube.objects.APIObject) -> str:
        """Get GAIE version from resource.

        Args:
            resource: InferencePool resource

        Returns:
            GAIE version string
        """
        # Check annotations
        annotations = resource.obj.get("metadata", {}).get("annotations", {})
        if "gaie.llm-d-toolkit.io/version" in annotations:
            return annotations["gaie.llm-d-toolkit.io/version"]

        # Check status
        status = resource.obj.get("status", {})
        if "version" in status:
            return status["version"]

        return "unknown"

    def collect_gaie_pod(self, pod: pykube.Pod) -> Optional[Component]:
        """Collect GAIE controller pod configuration.

        Args:
            pod: GAIE controller pod

        Returns:
            Component object or None
        """
        if not self._is_gaie_pod(pod):
            return None

        try:
            self.get_metadata(pod)
            pod_info = self.extract_pod_info(pod)

            # Extract controller configuration
            controller_config = self._extract_controller_config(pod_info, pod)

            native = {
                "pod": pod.obj,
                "component_type": "gaie-controller",
                "controller_config": controller_config,
                "command": pod_info.get("command"),
                "args": pod_info.get("args"),
                "environment": pod_info.get("env"),
                "resources": pod_info.get("resources"),
            }

            return self.create_component(
                resource=pod,
                tool="gaie-controller",
                tool_version=self._get_controller_version(pod_info),
                native=native,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.error(
                "Failed to collect GAIE controller config from %s: %s", pod.name, e
            )
            return None

    def _is_gaie_pod(self, pod: pykube.Pod) -> bool:
        """Check if a pod is a GAIE controller or EPP.

        Args:
            pod: Kubernetes pod

        Returns:
            True if this is a GAIE/EPP pod
        """
        labels = pod.obj.get("metadata", {}).get("labels", {})

        # Check for GAIE labels
        if labels.get("app.kubernetes.io/name") == "gaie":
            return True

        # Check for EPP component label
        if labels.get("app.kubernetes.io/component") in ("epp", "endpoint-picker"):
            return True

        # Check for inferencepool label ending with -epp
        inferencepool_label = labels.get("inferencepool", "")
        if inferencepool_label.endswith("-epp"):
            return True

        # Check container image
        containers = pod.obj.get("spec", {}).get("containers", [])
        for container in containers:
            image = container.get("image", "").lower()
            if "gaie" in image or "epp" in image or "inference-scheduler" in image:
                return True

        return False

    def _extract_controller_config(
        self, pod_info: Dict[str, Any], pod: pykube.Pod
    ) -> Dict[str, Any]:
        """Extract GAIE controller configuration.

        Args:
            pod_info: Pod information
            pod: Pod object

        Returns:
            Dict with controller configuration
        """
        config = {}

        # Parse command arguments
        parsed_args = self.parse_command_args(
            pod_info.get("command", []), pod_info.get("args", [])
        )

        flags = parsed_args.get("flags", {})

        # Extract common controller flags
        config["leader_elect"] = flags.get("leader-elect", True)
        config["metrics_addr"] = flags.get("metrics-bind-address", ":8080")
        config["health_addr"] = flags.get("health-probe-bind-address", ":8081")

        # Check environment variables
        for env in pod_info.get("env", []):
            name = env.get("name", "")
            value = env.get("value", "")

            if name == "GAIE_NAMESPACE":
                config["watch_namespace"] = value
            elif name == "GAIE_RECONCILE_INTERVAL":
                config["reconcile_interval"] = value

        # Get ConfigMap references and fetch their data
        configmaps = self.get_configmap_refs(pod)
        if configmaps:
            config["config_maps"] = configmaps
            namespace = pod.namespace
            config_map_data = {}
            for cm_name in configmaps:
                data = get_configmap_data(self.api, cm_name, namespace)
                if data:
                    config_map_data[cm_name] = data
            if config_map_data:
                config["config_map_data"] = config_map_data

        return config

    def _get_controller_version(self, pod_info: Dict[str, Any]) -> str:
        """Get GAIE controller version.

        Args:
            pod_info: Pod information

        Returns:
            Version string
        """
        # Check image tag
        image = pod_info.get("image", "")
        if ":" in image:
            tag = image.split(":")[-1]
            if tag != "latest":
                return tag

        # Check environment variables
        for env in pod_info.get("env", []):
            if env.get("name") == "GAIE_VERSION":
                return env.get("value", "unknown")

        return "unknown"
