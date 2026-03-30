"""Base collector class for component configuration collection."""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

import pykube

from ..utils import get_pod_containers
from ...models.components import Component, ComponentMetadata

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    """Abstract base class for component collectors."""

    def __init__(self, api: pykube.HTTPClient):
        """Initialize collector with Kubernetes API client.

        Args:
            api: Pykube HTTP client
        """
        self.api = api

    @abstractmethod
    def collect(self, resource: pykube.objects.APIObject) -> Optional[Component]:
        """Collect configuration from a resource.

        Args:
            resource: Kubernetes resource to collect from

        Returns:
            Component object or None if collection failed
        """

    def get_metadata(self, resource: pykube.objects.APIObject) -> ComponentMetadata:
        """Extract metadata from a Kubernetes resource.

        Args:
            resource: Kubernetes resource

        Returns:
            ComponentMetadata object
        """
        meta = resource.obj.get("metadata", {})

        return ComponentMetadata(
            namespace=meta.get("namespace", "default"),
            name=meta.get("name", ""),
            kind=resource.kind,
            labels=meta.get("labels", {}),
            annotations=meta.get("annotations", {}),
        )

    def extract_pod_info(self, pod: pykube.Pod) -> Dict[str, Any]:
        """Extract configuration information from a pod.

        Args:
            pod: Pykube Pod object

        Returns:
            Dict with pod configuration info
        """
        containers = get_pod_containers(pod)

        # Extract main container info (usually the first one)
        main_container = containers[0] if containers else {}

        # Parse command and args
        command = main_container.get("command", [])
        args = main_container.get("args", [])

        # Extract environment variables (filter sensitive ones)
        env_vars = self._filter_env_vars(main_container.get("env", []))
        env_from = main_container.get("env_from", [])

        # Extract resource requests/limits
        resources = main_container.get("resources", {})

        return {
            "image": main_container.get("image"),
            "command": command,
            "args": args,
            "env": env_vars,
            "env_from": env_from,
            "resources": resources,
            "node_name": pod.obj.get("spec", {}).get("nodeName"),
            "containers": containers,
        }

    def _filter_env_vars(self, env_vars: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Filter environment variables to remove sensitive data.

        Args:
            env_vars: List of environment variable definitions

        Returns:
            Filtered list of env vars
        """
        sensitive_patterns = [
            "TOKEN",
            "KEY",
            "SECRET",
            "PASSWORD",
            "CREDENTIAL",
            "PRIVATE",
        ]

        filtered = []
        for env in env_vars:
            name = env.get("name", "")

            # Check if this is a sensitive variable
            is_sensitive = any(
                pattern in name.upper() for pattern in sensitive_patterns
            )

            if is_sensitive:
                # For sensitive vars, only include the name
                filtered.append({"name": name, "value": "<REDACTED>"})
            else:
                filtered.append(env)

        return filtered

    def get_configmap_refs(self, pod: pykube.Pod) -> List[str]:
        """Get ConfigMap references from a pod.

        Args:
            pod: Pykube Pod object

        Returns:
            List of ConfigMap names referenced by the pod
        """
        configmaps = set()
        containers = get_pod_containers(pod)

        for container in containers:
            # Check envFrom references
            for env_from in container.get("env_from", []):
                if "configMapRef" in env_from:
                    configmaps.add(env_from["configMapRef"]["name"])

            # Check volume mounts
            for volume in pod.obj.get("spec", {}).get("volumes", []):
                if "configMap" in volume:
                    configmaps.add(volume["configMap"]["name"])

        return list(configmaps)

    def get_secret_refs(self, pod: pykube.Pod) -> List[str]:
        """Get Secret references from a pod (names only, not values).

        Args:
            pod: Pykube Pod object

        Returns:
            List of Secret names referenced by the pod
        """
        secrets = set()
        containers = get_pod_containers(pod)

        for container in containers:
            # Check envFrom references
            for env_from in container.get("env_from", []):
                if "secretRef" in env_from:
                    secrets.add(env_from["secretRef"]["name"])

            # Check volume mounts
            for volume in pod.obj.get("spec", {}).get("volumes", []):
                if "secret" in volume:
                    secrets.add(volume["secret"]["secretName"])

        return list(secrets)

    def parse_command_args(self, command: List[str], args: List[str]) -> Dict[str, Any]:
        """Parse command and arguments into structured format.

        Args:
            command: Command list
            args: Arguments list

        Returns:
            Dict with parsed command info
        """
        # Combine command and args
        full_command = command + args

        # Parse into a dict of flags and values
        parsed = {
            "command": command[0] if command else None,
            "flags": {},
            "positional": [],
        }

        i = 0
        while i < len(full_command):
            arg = full_command[i]

            # Skip the command itself
            if i < len(command):
                i += 1
                continue

            # Check if it's a flag
            if arg.startswith("--"):
                flag = arg[2:]
                # Check if next item is a value or another flag
                if i + 1 < len(full_command) and not full_command[i + 1].startswith(
                    "-"
                ):
                    parsed["flags"][flag] = full_command[i + 1]
                    i += 2
                else:
                    parsed["flags"][flag] = True
                    i += 1
            elif arg.startswith("-"):
                flag = arg[1:]
                if i + 1 < len(full_command) and not full_command[i + 1].startswith(
                    "-"
                ):
                    parsed["flags"][flag] = full_command[i + 1]
                    i += 2
                else:
                    parsed["flags"][flag] = True
                    i += 1
            else:
                parsed["positional"].append(arg)
                i += 1

        return parsed

    def create_component(
        self,
        resource: pykube.objects.APIObject,
        tool: Optional[str] = None,
        tool_version: Optional[str] = None,
        native: Optional[Dict[str, Any]] = None,
    ) -> Component:
        """Create a component from a resource.

        Args:
            resource: Kubernetes resource
            tool: Tool name
            tool_version: Tool version
            native: Native configuration data

        Returns:
            Component object
        """
        metadata = self.get_metadata(resource)

        # Use resource object as native if not provided
        if native is None:
            native = resource.obj

        return Component(
            metadata=metadata,
            tool=tool,
            tool_version=tool_version,
            native=native,
        )
