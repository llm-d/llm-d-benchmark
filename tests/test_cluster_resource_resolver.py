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
