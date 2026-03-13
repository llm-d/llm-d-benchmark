"""Resolve ``"auto"`` cluster resources (accelerator, network, affinity) by scanning nodes.

Fails early when ``"auto"`` is requested but the cluster is unreachable.
No-op when no ``"auto"`` values are present.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeResources:
    """Discovered node resources from cluster scanning."""

    # Resource keys found in node status.capacity (e.g. ["nvidia.com/gpu"])
    accelerator_resources: list[str] = field(default_factory=list)

    # Network resource keys found in node status.capacity
    network_resources: list[str] = field(default_factory=list)

    # GPU product labels found on nodes.
    # Maps label_key -> sorted list of label_values.
    # e.g. {"nvidia.com/gpu.product": ["NVIDIA-H100-80GB-HBM3"]}
    gpu_labels: dict[str, list[str]] = field(default_factory=dict)


class ClusterResourceResolver:
    """Resolve ``"auto"`` cluster resource values by querying node capacities and labels.

    Connects lazily via ``kube_connect()`` on first call. Node scan results are cached.
    Raises ``RuntimeError`` when ``"auto"`` values exist but the cluster is unreachable.
    """

    # Known accelerator resource keys (checked in node status.capacity)
    KNOWN_ACCELERATOR_RESOURCES = [
        "nvidia.com/gpu",
        "amd.com/gpu",
        "habana.ai/gaudi",
        "google.com/tpu",
        "intel.com/gpu",
        "gpu.intel.com/i915",
        "gpu.intel.com/xe",
    ]

    # Known GPU label keys (checked in node metadata.labels)
    KNOWN_GPU_LABEL_KEYS = [
        "nvidia.com/gpu.product",
        "gpu.nvidia.com/class",
        "cloud.google.com/gke-accelerator",
    ]

    # Known network resource keys (checked in node status.capacity)
    KNOWN_NETWORK_RESOURCES = [
        "rdma/rdma_shared_device_a",
        "rdma/hca_shared_devices_a",
        "nvidia.com/hostdev",
        "rdma/roce_gdr",
        "rdma/ib",
    ]

    def __init__(self, logger: Any, dry_run: bool = False) -> None:
        self.logger = logger
        self.dry_run = dry_run
        self._node_resources: NodeResources | None = None
        self._api_client: Any = None
        self._connected = False

    def resolve_all(self, values: dict) -> dict:
        """Resolve all ``"auto"`` cluster resource values. Returns a new dict."""
        result = deepcopy(values)

        auto_fields = self.has_unresolved(result)
        if not auto_fields:
            self._propagate_network_to_methods(result)
            return result

        self.logger.log_info(
            f"Auto-detection requested for: {', '.join(auto_fields)}"
        )

        self._connect(required_fields=auto_fields)
        self._scan_nodes(required_fields=auto_fields)

        unresolved: list[str] = []
        self._resolve_accelerator_resource(result, unresolved)
        self._resolve_network_resource(result, unresolved)
        self._resolve_affinity_node_selector(result, unresolved)
        self._resolve_accelerator_type_labels(result, unresolved)
        self._propagate_network_to_methods(result)

        if unresolved:
            msg = (
                f"Could not auto-detect the following cluster resources: "
                f"{', '.join(unresolved)}. "
                "Either provide explicit values in your scenario spec or "
                "ensure the cluster has the expected resources."
            )
            if self.dry_run:
                # In dry-run the plan won't be applied — warn, don't fail.
                self.logger.log_warning(f"[DRY RUN] {msg}")
            else:
                raise RuntimeError(msg)

        return result

    def has_unresolved(self, values: dict) -> list[str]:
        """Return a list of field paths that still contain ``"auto"``."""
        unresolved: list[str] = []

        if values.get("accelerator", {}).get("resource") == "auto":
            unresolved.append("accelerator.resource")

        vllm = values.get("vllmCommon", {})
        if vllm.get("networkResource") == "auto":
            unresolved.append("vllmCommon.networkResource")
        if vllm.get("networkNr") == "auto":
            unresolved.append("vllmCommon.networkNr")

        if values.get("affinity", {}).get("nodeSelector") == "auto":
            unresolved.append("affinity.nodeSelector")

        for section in ("decode", "prefill", "standalone"):
            accel_type = values.get(section, {}).get("acceleratorType", {})
            if isinstance(accel_type, dict) and accel_type.get("labelValue") == "auto":
                unresolved.append(f"{section}.acceleratorType.labelValue")

        return unresolved

    def _connect(self, required_fields: list[str] | None = None) -> bool:
        """Lazy cluster connection. Raises RuntimeError if auto fields need it but it fails."""
        if self._connected:
            return True

        if self.dry_run:
            self.logger.log_info(
                "[DRY RUN] Skipping cluster connection for resource "
                "auto-detection — defaults will be used"
            )
            return False

        try:
            from llmdbenchmark.utilities.cluster import (  # noqa: WPS433
                kube_connect,
                _KUBE_AVAILABLE,
            )

            if not _KUBE_AVAILABLE:
                raise RuntimeError(
                    "kubernetes Python package is not installed. "
                    "Cannot auto-detect cluster resources for: "
                    f"{', '.join(required_fields or ['unknown'])}. "
                    "Install with: pip install kubernetes"
                )

            self._api_client = kube_connect()
            self._connected = True
            self.logger.log_info(
                "Connected to cluster for resource auto-detection"
            )
            return True

        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Cannot connect to cluster for resource auto-detection. "
                f"Fields requiring cluster access: "
                f"{', '.join(required_fields or ['unknown'])}. "
                f"Error: {exc}"
            ) from exc

    def _scan_nodes(
        self, required_fields: list[str] | None = None,
    ) -> NodeResources:
        """Scan node capacities and labels via list_node(). Cached after first call."""
        if self._node_resources is not None:
            return self._node_resources

        resources = NodeResources()

        if not self._connected or self._api_client is None:
            self._node_resources = resources
            return resources

        try:
            from kubernetes import client as k8s_client

            v1 = k8s_client.CoreV1Api(self._api_client)
            nodes = v1.list_node()

            accel_set: set[str] = set()
            net_set: set[str] = set()
            gpu_labels: dict[str, set[str]] = {}

            for node in nodes.items:
                capacity = node.status.capacity or {}
                for key, count in capacity.items():
                    if key in self.KNOWN_ACCELERATOR_RESOURCES:
                        if str(count) not in ("0", ""):
                            accel_set.add(key)
                    if key in self.KNOWN_NETWORK_RESOURCES:
                        if str(count) not in ("0", ""):
                            net_set.add(key)

                labels = node.metadata.labels or {}
                for label_key in self.KNOWN_GPU_LABEL_KEYS:
                    if label_key in labels and labels[label_key]:
                        gpu_labels.setdefault(label_key, set()).add(
                            labels[label_key]
                        )

            resources.accelerator_resources = sorted(accel_set)
            resources.network_resources = sorted(net_set)
            resources.gpu_labels = {k: sorted(v) for k, v in gpu_labels.items()}

            if resources.accelerator_resources:
                self.logger.log_info(
                    f"Discovered accelerator resources: "
                    f"{', '.join(resources.accelerator_resources)}"
                )
            if resources.network_resources:
                self.logger.log_info(
                    f"Discovered network resources: "
                    f"{', '.join(resources.network_resources)}"
                )
            for label_key, label_vals in resources.gpu_labels.items():
                self.logger.log_info(
                    f"Discovered GPU labels: "
                    f"{label_key} = {', '.join(label_vals)}"
                )

        except Exception as exc:
            raise RuntimeError(
                f"Failed to scan cluster nodes for resource auto-detection. "
                f"Fields requiring cluster access: "
                f"{', '.join(required_fields or ['unknown'])}. "
                f"Error: {exc}"
            ) from exc

        self._node_resources = resources
        return resources

    def _resolve_accelerator_resource(
        self, values: dict, unresolved: list[str],
    ) -> None:
        """``accelerator.resource: "auto"`` → detected GPU resource key."""
        accel = values.get("accelerator", {})
        if accel.get("resource") != "auto":
            return

        resources = self._node_resources or NodeResources()

        if resources.accelerator_resources:
            resolved = resources.accelerator_resources[0]
            accel["resource"] = resolved
            self.logger.log_info(
                f"Resolved accelerator.resource: {resolved}"
            )
        else:
            unresolved.append("accelerator.resource")

    def _resolve_network_resource(
        self, values: dict, unresolved: list[str],
    ) -> None:
        """``vllmCommon.networkResource: "auto"`` → detected RDMA resource.

        Also sets ``networkNr`` to ``"1"`` when a network resource is found.
        Network resources are optional — if none are found on the cluster,
        the fields are cleared (templates will skip the network section).
        """
        vllm_common = values.get("vllmCommon", {})
        net_resource = vllm_common.get("networkResource", "")
        net_nr = vllm_common.get("networkNr", "")

        if net_resource != "auto" and net_nr != "auto":
            return

        resources = self._node_resources or NodeResources()

        if net_resource == "auto":
            if resources.network_resources:
                resolved_resource = resources.network_resources[0]
                vllm_common["networkResource"] = resolved_resource
                self.logger.log_info(
                    f"Resolved vllmCommon.networkResource: {resolved_resource}"
                )
                if net_nr == "auto" or not net_nr:
                    vllm_common["networkNr"] = "1"
                    self.logger.log_info(
                        "Resolved vllmCommon.networkNr: 1"
                    )
            else:
                # Network resources are optional — no RDMA/IB is fine
                vllm_common["networkResource"] = ""
                self.logger.log_info(
                    "No RDMA/IB network resource found on cluster — "
                    "network resource disabled"
                )
                if net_nr == "auto":
                    vllm_common["networkNr"] = ""

        elif net_nr == "auto":
            if net_resource:
                vllm_common["networkNr"] = "1"
                self.logger.log_info(
                    f"Resolved vllmCommon.networkNr: 1 "
                    f"(networkResource={net_resource})"
                )
            else:
                vllm_common["networkNr"] = ""

    def _resolve_affinity_node_selector(
        self, values: dict, unresolved: list[str],
    ) -> None:
        """``affinity.nodeSelector: "auto"`` → dict from GPU product labels.

        When ``nodeSelector`` is the string ``"auto"``, scan node labels for
        GPU product labels and build a dict, e.g.
        ``{"nvidia.com/gpu.product": "NVIDIA-H100-80GB-HBM3"}``.
        Also sets ``affinity.enabled`` to ``True``.
        """
        affinity = values.get("affinity", {})
        node_selector = affinity.get("nodeSelector", {})

        if node_selector != "auto":
            return

        resources = self._node_resources or NodeResources()

        if resources.gpu_labels:
            label_key = next(iter(resources.gpu_labels))
            label_value = resources.gpu_labels[label_key][0]

            affinity["nodeSelector"] = {label_key: label_value}
            affinity["enabled"] = True
            self.logger.log_info(
                f"Resolved affinity.nodeSelector: "
                f"{{{label_key}: {label_value}}}, enabled=True"
            )
        else:
            unresolved.append("affinity.nodeSelector")

        values["affinity"] = affinity

    def _resolve_accelerator_type_labels(
        self, values: dict, unresolved: list[str],
    ) -> None:
        """``*.acceleratorType.labelValue: "auto"`` for decode/prefill/standalone.

        When ``labelValue`` is ``"auto"``, detect the GPU product label from
        the cluster and set both ``labelKey`` and ``labelValue``.
        """
        resources = self._node_resources or NodeResources()

        for section in ("decode", "prefill", "standalone"):
            section_dict = values.get(section, {})
            if not isinstance(section_dict, dict):
                continue

            accel_type = section_dict.get("acceleratorType", {})
            if not isinstance(accel_type, dict):
                continue

            if accel_type.get("labelValue") != "auto":
                continue

            if resources.gpu_labels:
                label_key = next(iter(resources.gpu_labels))
                label_value = resources.gpu_labels[label_key][0]

                accel_type["labelKey"] = label_key
                accel_type["labelValue"] = label_value
                self.logger.log_info(
                    f"Resolved {section}.acceleratorType: "
                    f"labelKey={label_key}, labelValue={label_value}"
                )
            else:
                unresolved.append(f"{section}.acceleratorType.labelValue")

    def _propagate_network_to_methods(self, values: dict) -> None:
        """Propagate ``vllmCommon`` network settings to per-method sections.

        When the per-method ``networkResource`` is ``"auto"`` or empty,
        inherit from ``vllmCommon``.  Mirrors the bash
        ``propagate_common_to_standup_methods()`` function.
        """
        vllm_common = values.get("vllmCommon", {})
        common_net_resource = vllm_common.get("networkResource", "")
        common_net_nr = vllm_common.get("networkNr", "")

        for section in ("decode", "prefill", "standalone"):
            section_dict = values.get(section, {})
            if not isinstance(section_dict, dict):
                continue

            sec_net = section_dict.get("networkResource")
            sec_nr = section_dict.get("networkNr")

            if sec_net is not None and (sec_net == "auto" or not sec_net):
                section_dict["networkResource"] = common_net_resource
            if sec_nr is not None and (sec_nr == "auto" or not sec_nr):
                section_dict["networkNr"] = common_net_nr
