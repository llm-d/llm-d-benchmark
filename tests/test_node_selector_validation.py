"""Tests for node-selector validation hints in step_03 workload_monitoring.

Pins the contract that:

- ``_collect_cluster_gpu_labels`` surfaces GPU-related labels (anything whose
  key contains ``gpu``/``accelerator``/``nvidia``/``amd``/``habana``) as
  ``{key: [sorted unique values]}``.
- When a node selector from ``*.acceleratorType`` doesn't match the cluster,
  the appended error mentions the discovered GPU labels AND tells the user
  to set ``<source>.labelValue: auto`` so the resolver can substitute.
- Non-acceleratorType sources (e.g. ``affinity.nodeSelector``) get the
  plain error message without the auto-detect hint.

These guards directly answer the H100-vs-H200 papercut where discovery
populated the right labels but the scenario's hard-coded H100 default
masked them; we now nudge the user to opt in to substitution.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from llmdbenchmark.standup.steps.step_03_workload_monitoring import (
    WorkloadMonitoringStep,
)


@pytest.fixture
def step():
    """A step instance with mocked logger -- enough to call validators."""
    s = WorkloadMonitoringStep()
    s.logger = MagicMock()
    return s


@pytest.fixture
def context():
    ctx = MagicMock()
    ctx.dry_run = False
    ctx.logger = MagicMock()
    return ctx


class TestCollectClusterGpuLabels:
    def test_dedupes_and_sorts_values_per_key(self):
        node_labels = [
            {"nvidia.com/gpu.product": "H200", "kubernetes.io/hostname": "n1"},
            {
                "nvidia.com/gpu.product": "H200",
                "topology.kubernetes.io/zone": "us-east",
            },
            {"nvidia.com/gpu.product": "A100", "kubernetes.io/hostname": "n3"},
        ]
        out = WorkloadMonitoringStep._collect_cluster_gpu_labels(node_labels)
        assert out == {"nvidia.com/gpu.product": ["A100", "H200"]}

    def test_recognizes_multiple_gpu_label_conventions(self):
        node_labels = [
            {
                "gpu.nvidia.com/class": "H200",
                "habana.ai/gaudi": "v2",
                "amd.com/gpu": "MI300",
                "kubernetes.io/os": "linux",
            }
        ]
        out = WorkloadMonitoringStep._collect_cluster_gpu_labels(node_labels)
        assert "gpu.nvidia.com/class" in out
        assert "habana.ai/gaudi" in out
        assert "amd.com/gpu" in out
        assert "kubernetes.io/os" not in out

    def test_returns_empty_when_no_gpu_labels(self):
        node_labels = [{"kubernetes.io/hostname": "n1", "kubernetes.io/os": "linux"}]
        assert WorkloadMonitoringStep._collect_cluster_gpu_labels(node_labels) == {}


class TestNodeSelectorValidationHint:
    """End-to-end: the validation error for an acceleratorType mismatch
    must include both the discovered cluster GPU labels and the `auto`
    opt-in suggestion."""

    def _node_labels_payload(self, node_labels: list[dict[str, str]]) -> dict:
        return {
            "items": [
                {
                    "metadata": {"labels": labels},
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
                for labels in node_labels
            ]
        }

    def _stub_kubectl(self, step, payload_json: str):
        cmd = MagicMock()
        result = MagicMock()
        result.success = True
        result.stdout = payload_json
        cmd.kube.return_value = result
        return cmd

    def test_acceleratortype_mismatch_includes_auto_hint_and_discovered_labels(
        self, step, context
    ):
        import json

        # Cluster has H200, scenario asks for H100 -- the exact pd-disaggregation
        # papercut.
        node_labels = [
            {"gpu.nvidia.com/class": "H200", "nvidia.com/gpu.present": "true"}
        ]
        cmd = self._stub_kubectl(
            step, json.dumps(self._node_labels_payload(node_labels))
        )

        plan_config = {
            "decode": {
                "acceleratorType": {
                    "labelKey": "nvidia.com/gpu.product",
                    "labelValue": "NVIDIA-H100-80GB-HBM3",
                },
                "parallelism": {"tensor": 2},
            },
        }
        errors: list[str] = []
        step._validate_node_selectors(cmd, context, plan_config, errors)

        assert len(errors) == 1
        msg = errors[0]
        # The scenario's broken selector is named.
        assert "nvidia.com/gpu.product=NVIDIA-H100-80GB-HBM3" in msg
        assert "from decode.acceleratorType" in msg
        # The discovered cluster labels are surfaced.
        assert "gpu.nvidia.com/class=H200" in msg
        # The auto-detect remediation is spelled out.
        assert "decode.acceleratorType.labelValue: auto" in msg

    def test_affinity_nodeselector_mismatch_does_NOT_include_auto_hint(
        self, step, context
    ):
        """Non-acceleratorType sources stay terse -- the auto knob doesn't apply."""
        import json

        node_labels = [{"topology.kubernetes.io/zone": "us-east-1a"}]
        cmd = self._stub_kubectl(
            step, json.dumps(self._node_labels_payload(node_labels))
        )

        plan_config = {
            "affinity": {
                "enabled": True,
                "nodeSelector": {"topology.kubernetes.io/zone": "us-west-2c"},
            },
        }
        errors: list[str] = []
        step._validate_node_selectors(cmd, context, plan_config, errors)

        assert len(errors) == 1
        msg = errors[0]
        assert "topology.kubernetes.io/zone=us-west-2c" in msg
        assert "from affinity.nodeSelector" in msg
        assert "labelValue: auto" not in msg
