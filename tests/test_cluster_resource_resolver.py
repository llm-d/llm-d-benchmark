"""Tests for ``ClusterResourceResolver._resolve_accelerator_type_labels``.

Pins the contract that:

- CPU-only sections (``accelerator.count == 0`` or ``parallelism.tensor == 0``)
  are skipped silently when their ``acceleratorType.labelValue`` is the
  inherited ``"auto"`` default — they don't need a GPU node selector at all,
  and the template gates ``acceleratorTypes`` rendering on the same count.
- Disabled sections (``enabled: False``) are skipped silently regardless of
  whether ``labelValue`` was set to ``"auto"``.
- GPU-requesting sections still fail loudly when ``"auto"`` is requested
  but the cluster has no GPU labels — the scenario asked for accelerators
  the cluster cannot supply.
- ``effective_accelerator_count`` is shared between the resolver (here) and
  the validator (``step_03_workload_monitoring``) so both skip paths use
  identical semantics.

These tests guard against the regression where the H200 fix (flipping the
default from a hardcoded H100 SKU to ``auto``) caused every CPU-only / kind
scenario to fail at the render stage because they inherited ``auto`` they
couldn't satisfy.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.parser.cluster_resource_resolver import (
    ClusterResourceResolver,
    NodeResources,
    effective_accelerator_count,
)


@pytest.fixture
def resolver():
    """Build a resolver wired to ignore connectivity (we drive _node_resources)."""
    r = ClusterResourceResolver(logger=MagicMock(), dry_run=False)
    return r


class TestEffectiveAcceleratorCount:
    def test_explicit_count_wins(self):
        assert effective_accelerator_count({"accelerator": {"count": 4}}) == (
            4,
            "accelerator.count (explicit)",
        )

    def test_falls_back_to_tensor_parallelism(self):
        assert effective_accelerator_count({"parallelism": {"tensor": 2}}) == (
            2,
            "parallelism.tensor (fallback)",
        )

    def test_explicit_count_of_zero_treated_as_cpu(self):
        assert effective_accelerator_count(
            {"accelerator": {"count": 0}, "parallelism": {"tensor": 2}}
        ) == (0, "accelerator.count (explicit)")

    def test_unset_returns_zero(self):
        assert effective_accelerator_count({}) == (0, "unset")

    def test_unparseable_returns_zero_parse_error(self):
        assert effective_accelerator_count(
            {"accelerator": {"count": "not-a-number"}}
        ) == (0, "parse-error")


class TestResolveAcceleratorTypeLabelsSkipping:
    """The new skip paths added for the H200/kind regression."""

    def _gpu_resources(self):
        return NodeResources(
            accelerator_resources=["nvidia.com/gpu"],
            gpu_labels={"gpu.nvidia.com/class": ["H200"]},
        )

    def _no_gpu_resources(self):
        return NodeResources()

    def test_kind_shape_cpu_only_section_skipped_silently(self, resolver):
        """The exact ``cicd/kind`` failure mode: decode/prefill inherit auto
        but request 0 GPUs. Resolver must not flag them as unresolved."""
        resolver._node_resources = self._no_gpu_resources()
        values = {
            "decode": {
                "accelerator": {"count": 0},
                "parallelism": {"tensor": 0},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
            "prefill": {
                "accelerator": {"count": 0},
                "parallelism": {"tensor": 0},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        # The literal "auto" survives -- harmless because 13_ms-values.yaml.j2
        # gates `acceleratorTypes` rendering on `*_accel_count > 0`.
        assert values["decode"]["acceleratorType"]["labelValue"] == "auto"
        assert values["prefill"]["acceleratorType"]["labelValue"] == "auto"

    def test_disabled_section_skipped_silently(self, resolver):
        resolver._node_resources = self._no_gpu_resources()
        values = {
            "standalone": {
                "enabled": False,
                "parallelism": {"tensor": 2},  # would normally request GPUs
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []

    def test_gpu_requesting_section_with_no_cluster_gpus_still_errors(self, resolver):
        """A real GPU scenario on a GPU-less cluster must still fail fast."""
        resolver._node_resources = self._no_gpu_resources()
        values = {
            "decode": {
                "parallelism": {"tensor": 2},  # asks for 2 GPUs per pod
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == ["decode.acceleratorType.labelValue"]

    def test_gpu_section_with_discovery_resolves_both_key_and_value(self, resolver):
        resolver._node_resources = self._gpu_resources()
        values = {
            "decode": {
                "parallelism": {"tensor": 2},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",  # will be overwritten
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        assert values["decode"]["acceleratorType"]["labelKey"] == "gpu.nvidia.com/class"
        assert values["decode"]["acceleratorType"]["labelValue"] == "H200"

    def test_explicit_non_auto_value_left_alone(self, resolver):
        """Scenarios pinning a concrete SKU must not be touched by the resolver."""
        resolver._node_resources = self._gpu_resources()
        values = {
            "decode": {
                "parallelism": {"tensor": 2},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-A100-SXM4-80GB",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        # Pin survived discovery -- the validator will catch any mismatch later.
        assert (
            values["decode"]["acceleratorType"]["labelValue"] == "NVIDIA-A100-SXM4-80GB"
        )


class TestAmdRocmGracefulDegradation:
    """Cluster has GPU resources but no GPU SKU labels we know about (the
    AMD ROCM nightly failure mode). Resolver must drop the acceleratorType
    constraint so the template falls through to a resource-only pod spec,
    rather than raising and blocking the entire standup.
    """

    @pytest.fixture
    def amd_resources_no_labels(self):
        return NodeResources(
            accelerator_resources=["amd.com/gpu"],  # capacity says yes
            gpu_labels={},  # but no SKU label recognised
        )

    @pytest.fixture
    def amd_resources_with_label(self):
        return NodeResources(
            accelerator_resources=["amd.com/gpu"],
            gpu_labels={"amd.com/gpu.product": ["AMD-Instinct-MI300X"]},
        )

    def test_amd_no_labels_drops_constraint_and_does_not_error(
        self, resolver, amd_resources_no_labels
    ):
        resolver._node_resources = amd_resources_no_labels
        values = {
            "decode": {
                "parallelism": {"tensor": 2},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        # Both label fields must be GONE (not None, not "auto") so the
        # template's `is defined` gate falls through.
        accel = values["decode"]["acceleratorType"]
        assert "labelKey" not in accel
        assert "labelValue" not in accel

    def test_amd_with_known_label_is_resolved_via_extended_allow_list(
        self, resolver, amd_resources_with_label
    ):
        """`amd.com/gpu.product` was added to KNOWN_GPU_LABEL_KEYS."""
        resolver._node_resources = amd_resources_with_label
        values = {
            "decode": {
                "parallelism": {"tensor": 2},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        assert values["decode"]["acceleratorType"]["labelKey"] == "amd.com/gpu.product"
        assert (
            values["decode"]["acceleratorType"]["labelValue"] == "AMD-Instinct-MI300X"
        )

    def test_no_gpus_at_all_still_errors_when_section_requests_them(self, resolver):
        """The degradation only kicks in when the cluster HAS GPU resources.
        Empty cluster + GPU-requesting section must still raise."""
        resolver._node_resources = NodeResources()
        values = {
            "decode": {
                "parallelism": {"tensor": 2},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == ["decode.acceleratorType.labelValue"]


class TestGpuSkuLabelHeuristic:
    """The vendor-prefix + SKU-suffix heuristic that lets the resolver match
    GPU SKU labels from any vendor without per-vendor maintenance.

    Adding a new vendor (Intel, Habana, anything that follows the
    ``{vendor}/gpu.{suffix}`` convention) is now zero-config: as long as
    the vendor's accelerator resource is on the cluster, its labels match."""

    @pytest.fixture
    def looks_like(self):
        return ClusterResourceResolver._looks_like_gpu_sku_label

    def test_vendor_prefixes_extracted_from_resource_keys(self):
        prefixes = ClusterResourceResolver._vendor_prefixes(
            {"nvidia.com/gpu", "amd.com/gpu", "gpu.intel.com/i915", "habana.ai/gaudi"}
        )
        assert prefixes == {
            "nvidia.com/",
            "amd.com/",
            "gpu.intel.com/",
            "habana.ai/",
        }

    def test_vendor_prefixes_skips_keys_without_slash(self):
        # Defensive: malformed resource keys shouldn't produce empty prefixes
        # that would match every label.
        assert ClusterResourceResolver._vendor_prefixes({"malformed"}) == set()

    # --- vendor-prefix + SKU-suffix matches ---

    def test_nvidia_product_matches_via_heuristic(self, looks_like):
        assert looks_like("nvidia.com/gpu.product", {"nvidia.com/"})

    def test_amd_product_matches_via_heuristic(self, looks_like):
        assert looks_like("amd.com/gpu.product", {"amd.com/"})

    def test_amd_family_matches_via_heuristic(self, looks_like):
        assert looks_like("amd.com/gpu.family", {"amd.com/"})

    def test_intel_family_matches_via_heuristic(self, looks_like):
        """Intel GPU Operator labels work out of the box now."""
        assert looks_like("gpu.intel.com/family", {"gpu.intel.com/"})

    def test_habana_gaudi_matches_via_heuristic(self, looks_like):
        assert looks_like("habana.ai/gaudi.product", {"habana.ai/"})

    # --- explicit allow-list matches (cross-namespace) ---

    def test_nvidia_class_label_matches_via_allow_list(self, looks_like):
        """`gpu.nvidia.com/class` doesn't share a prefix with `nvidia.com/gpu`,
        so the allow-list is the only path."""
        assert looks_like("gpu.nvidia.com/class", {"nvidia.com/"})

    def test_amd_product_name_matches_via_allow_list(self, looks_like):
        """`kubernetes.amd.com/gpu.product-name` doesn't share a prefix with
        `amd.com/gpu`, so the allow-list catches it."""
        assert looks_like("kubernetes.amd.com/gpu.product-name", {"amd.com/"})

    # --- cloud-managed labels ---

    def test_gke_accelerator_label_matches_via_cloud_list(self, looks_like):
        # No vendor prefix needed -- cloud-provider lists carry these.
        assert looks_like("cloud.google.com/gke-accelerator", set())

    # --- rejection paths ---

    def test_unrelated_label_with_sku_suffix_rejected_without_vendor(self, looks_like):
        """A label that happens to end in `.family` but lives in an unrelated
        namespace shouldn't match when no GPU vendor is on the cluster."""
        assert not looks_like("topology.node.kubernetes.io/family", set())

    def test_unrelated_label_with_sku_suffix_rejected_with_unrelated_vendor(
        self, looks_like
    ):
        """Same label, but cluster has only AMD GPUs -- still no match."""
        assert not looks_like("topology.node.kubernetes.io/family", {"amd.com/"})

    def test_nvidia_count_label_rejected_no_sku_suffix(self, looks_like):
        """nvidia.com/gpu.count is GPU-related but it's a count not a SKU."""
        assert not looks_like("nvidia.com/gpu.count", {"nvidia.com/"})


class TestScanIntegratesHeuristicForIntel:
    """End-to-end: an Intel cluster (resource = gpu.intel.com/i915) with
    `gpu.intel.com/family: dg2` labels resolves cleanly through the resolver."""

    def test_intel_cluster_resolves_via_heuristic(self):
        resolver = ClusterResourceResolver(logger=MagicMock(), dry_run=False)
        resolver._node_resources = NodeResources(
            accelerator_resources=["gpu.intel.com/i915"],
            # Mimics what _scan_nodes would have collected via the heuristic.
            gpu_labels={"gpu.intel.com/family": ["dg2"]},
        )
        values = {
            "decode": {
                "parallelism": {"tensor": 1},
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "auto",
                },
            },
        }
        unresolved: list[str] = []
        resolver._resolve_accelerator_type_labels(values, unresolved)

        assert unresolved == []
        assert values["decode"]["acceleratorType"]["labelKey"] == "gpu.intel.com/family"
        assert values["decode"]["acceleratorType"]["labelValue"] == "dg2"
