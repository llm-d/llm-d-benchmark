"""Output formatters for discovery results."""

# pylint: disable=duplicate-code

import json
import logging
from typing import Any, Dict, List, Optional, TextIO

import yaml

from ..models.components import Component, DiscoveryResult
from .benchmark_report import discovery_to_stack_components

logger = logging.getLogger(__name__)


class OutputFormatter:
    """Formats discovery results for output."""

    def format(
        self,
        result: DiscoveryResult,
        format_type: str = "json",
        output_file: Optional[TextIO] = None,
        filter_type: Optional[str] = None,
    ) -> str:
        """Format discovery result for output.

        Args:
            result: Discovery result to format
            format_type: Output format (json, yaml, summary, native)
            output_file: Optional file to write output to
            filter_type: Optional component type filter

        Returns:
            Formatted string output
        """
        # Filter components if requested
        components = result.components
        if filter_type:
            components = self._filter_components(components, filter_type)

        # Format based on type
        if format_type == "json":
            output = self._format_json(result, components)
        elif format_type == "yaml":
            output = self._format_yaml(result, components)
        elif format_type == "summary":
            output = self._format_summary(result, components)
        elif format_type == "native":
            output = self._format_native(result, components)
        elif format_type == "native-yaml":
            output = self._format_native_yaml(result, components)
        elif format_type == "benchmark-report":
            output = self._format_benchmark_report(result, components)
        else:
            raise ValueError(f"Unknown format type: {format_type}")

        # Write to file if provided
        if output_file:
            output_file.write(output)
            output_file.write("\n")

        return output

    def _filter_components(
        self, components: List[Component], filter_type: str
    ) -> List[Component]:
        """Filter components by type.

        Args:
            components: List of components
            filter_type: Type to filter by

        Returns:
            Filtered list of components
        """
        filtered = []

        for component in components:
            # Check metadata kind
            if component.metadata.kind.lower() == filter_type.lower():
                filtered.append(component)
                continue

            # Check tool name
            if component.tool and component.tool == filter_type.lower():
                filtered.append(component)
                continue

        return filtered

    def _format_json(self, result: DiscoveryResult, components: List[Component]) -> str:
        """Format as JSON.

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            JSON string
        """
        # Convert to dict for JSON serialization
        data = {
            "url": result.url,
            "timestamp": result.timestamp,
            "cluster_info": result.cluster_info,
            "components": [self._component_to_dict(c) for c in components],
            "errors": result.errors,
        }

        return json.dumps(data, indent=2, default=str)

    def _format_yaml(self, result: DiscoveryResult, components: List[Component]) -> str:
        """Format as YAML.

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            YAML string
        """
        # Convert to dict for YAML serialization
        data = {
            "url": result.url,
            "timestamp": result.timestamp,
            "cluster_info": result.cluster_info,
            "components": [self._component_to_dict(c) for c in components],
            "errors": result.errors,
        }

        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def _format_native(
        self, result: DiscoveryResult, components: List[Component]
    ) -> str:
        """Format as native configuration (JSON).

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            JSON string with native config
        """
        data = self._extract_native_config(result, components)
        return json.dumps(data, indent=2, default=str)

    def _format_native_yaml(
        self, result: DiscoveryResult, components: List[Component]
    ) -> str:
        """Format as native configuration (YAML).

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            YAML string with native config
        """
        data = self._extract_native_config(result, components)
        return yaml.dump(data, default_flow_style=False, sort_keys=False)

    def _format_benchmark_report(
        self, result: DiscoveryResult, components: List[Component]
    ) -> str:
        """Format as benchmark-report-compatible JSON.

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            JSON string with benchmark report stack components
        """
        # Build a filtered result with only the selected components
        filtered_result = DiscoveryResult(
            url=result.url,
            timestamp=result.timestamp,
            cluster_info=result.cluster_info,
            components=components,
            errors=result.errors,
        )
        component_dicts = discovery_to_stack_components(filtered_result)
        return json.dumps(component_dicts, indent=2, default=str)

    def _extract_native_config(
        self, result: DiscoveryResult, components: List[Component]
    ) -> Dict[str, Any]:
        """Extract native configuration from components.

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            Dict with native config organized by component name
        """
        config = {
            "discovery_metadata": {
                "url": result.url,
                "timestamp": result.timestamp,
                "cluster": result.cluster_info,
            },
            "components": {},
        }

        for component in components:
            # Create a unique key for this component
            component_key = (
                f"{component.metadata.kind}"
                f"/{component.metadata.namespace}"
                f"/{component.metadata.name}"
            )

            # Extract relevant native configuration
            native_config = {
                "metadata": {
                    "kind": component.metadata.kind,
                    "namespace": component.metadata.namespace,
                    "name": component.metadata.name,
                    "labels": component.metadata.labels,
                    "annotations": component.metadata.annotations,
                },
            }

            # Add tool information if available
            if component.tool:
                native_config["tool"] = {
                    "name": component.tool,
                    "version": component.tool_version,
                }

            # Extract pod-specific configuration
            if component.metadata.kind == "Pod":
                pod_config = self._extract_pod_config(component.native)
                native_config.update(pod_config)

            # Extract service-specific configuration
            elif component.metadata.kind == "Service":
                service_config = self._extract_service_config(component.native)
                native_config.update(service_config)

            # For other resource types, include relevant parts of native config
            else:
                # Include spec and status if present
                if isinstance(component.native, dict):
                    if "spec" in component.native:
                        native_config["spec"] = component.native["spec"]
                    if "status" in component.native:
                        native_config["status"] = component.native["status"]

            config["components"][component_key] = native_config

        if result.errors:
            config["errors"] = result.errors

        return config

    def _extract_pod_config(self, native: Dict[str, Any]) -> Dict[str, Any]:
        """Extract configuration from a pod's native data.

        Args:
            native: Native pod configuration

        Returns:
            Dict with extracted pod config
        """
        config = {}

        # Handle both pod object and vllm_config format
        if "pod" in native:
            pod_obj = native["pod"]
        else:
            pod_obj = native

        spec = pod_obj.get("spec", {})
        containers = spec.get("containers", [])

        if containers:
            main_container = containers[0]

            # Extract command and args
            if "command" in native:
                config["command"] = native["command"]
            elif "command" in main_container:
                config["command"] = main_container["command"]

            if "args" in native:
                config["args"] = native["args"]
            elif "args" in main_container:
                config["args"] = main_container["args"]

            # Extract environment variables
            if "environment" in native:
                config["environment"] = native["environment"]
            elif "env" in main_container:
                config["environment"] = main_container["env"]

            # Extract resources
            if "resources" in native:
                config["resources"] = native["resources"]
            elif "resources" in main_container:
                config["resources"] = main_container["resources"]

            # Extract image
            if "image" in main_container:
                config["image"] = main_container["image"]

        # Include vllm_config if present
        if "vllm_config" in native:
            config["vllm_config"] = native["vllm_config"]

        # Include node assignment
        if "nodeName" in spec:
            config["node"] = spec["nodeName"]

        return config

    def _extract_service_config(self, native: Dict[str, Any]) -> Dict[str, Any]:
        """Extract configuration from a service's native data.

        Args:
            native: Native service configuration

        Returns:
            Dict with extracted service config
        """
        spec = native.get("spec", {})

        return {
            "service_type": spec.get("type", "ClusterIP"),
            "cluster_ip": spec.get("clusterIP"),
            "ports": spec.get("ports", []),
            "selector": spec.get("selector", {}),
            "session_affinity": spec.get("sessionAffinity"),
        }

    def _format_summary(
        self, result: DiscoveryResult, components: List[Component]
    ) -> str:
        """Format as human-readable summary.

        Args:
            result: Discovery result
            components: Filtered components

        Returns:
            Summary string
        """
        lines = []

        lines.append("=== LLM-D Stack Discovery Summary ===")
        lines.append(f"\nURL: {result.url}")
        lines.append(f"Timestamp: {result.timestamp}")
        platform = result.cluster_info.get("platform", "unknown")
        version = result.cluster_info.get("version", "")
        lines.append(f"Cluster: {platform} {version}")

        # Group components by type
        by_type = {}
        for component in components:
            comp_type = self._get_component_type_label(component)
            if comp_type not in by_type:
                by_type[comp_type] = []
            by_type[comp_type].append(component)

        lines.append(f"\nDiscovered {len(components)} components:")

        for comp_type, comps in sorted(by_type.items()):
            lines.append(f"\n{comp_type} ({len(comps)}):")

            for comp in comps:
                lines.append(self._format_component_summary(comp))

        # Show errors if any
        if result.errors:
            lines.append("\nErrors encountered:")
            for error in result.errors:
                lines.append(f"  - {error}")

        return "\n".join(lines)

    def _get_component_type_label(self, component: Component) -> str:
        """Get a human-readable type label for a component.

        Args:
            component: Component to label

        Returns:
            Type label string
        """
        # Check tool name
        if component.tool:
            if component.tool == "vllm":
                return "Inference Engine (vLLM)"
            return component.tool.replace("-", " ").title()

        # Fallback to metadata kind
        return component.metadata.kind

    def _format_component_summary(self, component: Component) -> str:
        """Format a single component summary line.

        Args:
            component: Component to summarize

        Returns:
            Summary string
        """
        parts = [f"  - {component.metadata.namespace}/{component.metadata.name}"]

        # Add specific details based on type
        if component.tool == "vllm":
            # Show vLLM details
            vllm_config = component.native.get("vllm_config", {})
            role = component.native.get("role", "replica")
            gpu = component.native.get("gpu", {})

            model = vllm_config.get("model", "unknown")
            if model and "/" in model:
                model = model.split("/")[-1]
            parts.append(f"[{model}]")
            parts.append(f"role:{role}")

            gpu_count = gpu.get("count", 0)
            gpu_model = gpu.get("model", "unknown")
            if gpu_count > 0:
                parts.append(f"GPUs:{gpu_count}x{gpu_model}")

            # Show parallelism
            tp = vllm_config.get("tensor_parallel_size", 1)
            pp = vllm_config.get("pipeline_parallel_size", 1)
            if tp > 1 or pp > 1:
                parts.append(f"TP={tp},PP={pp}")

        elif component.tool == "openshift-route":
            # Show route host
            native = component.native
            route_obj = native.get("route", {})
            host = route_obj.get("spec", {}).get("host", "")
            if not host:
                host = native.get("route_config", {}).get("host", "")
            if host:
                parts.append(f"-> {host}")

        elif component.tool == "gateway-api":
            # Show gateway listeners
            native = component.native
            listeners = native.get("listeners", [])
            if listeners:
                ports = [str(listener.get("port", "?")) for listener in listeners]
                parts.append(f"ports: {','.join(ports)}")

        return " ".join(parts)

    def _component_to_dict(self, component: Component) -> Dict[str, Any]:
        """Convert component to dict for serialization.

        Args:
            component: Component to convert

        Returns:
            Dict representation
        """
        return {
            "metadata": {
                "namespace": component.metadata.namespace,
                "name": component.metadata.name,
                "kind": component.metadata.kind,
                "labels": component.metadata.labels,
                "annotations": component.metadata.annotations,
            },
            "tool": component.tool,
            "tool_version": component.tool_version,
            "native": component.native,
        }
