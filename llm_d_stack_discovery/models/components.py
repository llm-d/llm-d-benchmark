"""Component models for stack discovery.

Simple data models without external dependencies.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class ComponentMetadata:
    """Metadata about a discovered component."""

    namespace: str
    """Kubernetes namespace containing the component."""
    name: str
    """Component name."""
    kind: str
    """Kubernetes resource kind (e.g., Pod, Service, Route)."""
    labels: Dict[str, str] = field(default_factory=dict)
    """Kubernetes labels."""
    annotations: Dict[str, str] = field(default_factory=dict)
    """Kubernetes annotations."""


@dataclass
class Component:
    """A discovered component with its configuration."""

    metadata: ComponentMetadata
    """Component metadata."""
    tool: Optional[str] = None
    """Tool name (e.g., vllm, gaie, etc)."""
    tool_version: Optional[str] = None
    """Tool version."""
    native: Dict[str, Any] = field(default_factory=dict)
    """Raw native configuration."""


@dataclass
class DiscoveryResult:
    """Result of stack discovery."""

    url: str
    """Original URL used for discovery."""
    timestamp: str
    """Timestamp of discovery."""
    cluster_info: Dict[str, str] = field(default_factory=dict)
    """Basic cluster information."""
    components: List[Component] = field(default_factory=list)
    """Discovered components."""
    errors: List[str] = field(default_factory=list)
    """Non-fatal errors encountered during discovery."""
