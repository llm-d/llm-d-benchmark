"""Tests for the acceleratorType validation guards in step_03_workload_monitoring.

These guards mirror the Jinja template's ``*_accel_count > 0`` / ``enabled`` /
``replicas > 0`` conditions so that the standup validator does not flag
``nvidia.com/gpu.product=NVIDIA-H100-80GB-HBM3`` (inherited from defaults.yaml)
on pure-CPU scenarios, where the template never actually emits that selector.

Also covers the matching helper in step_09_deploy_modelservice that controls
whether WVA's ``va.accelerator`` is populated from ``decode.acceleratorType``.
"""

from __future__ import annotations

from llmdbenchmark.standup.steps.step_03_workload_monitoring import (
    WorkloadMonitoringStep,
)
from llmdbenchmark.standup.steps.step_09_deploy_modelservice import (
    DeployModelserviceStep,
)


# ---------------------------------------------------------------------------
# _method_renders_accelerator_type: standalone
# ---------------------------------------------------------------------------


def test_standalone_disabled_does_not_render():
    """standalone.enabled=false → acceleratorType is not rendered."""
    cfg = {
        "enabled": False,
        "parallelism": {"tensor": 2},
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "standalone", cfg
    ) is False


def test_standalone_enabled_with_tensor_parallelism_renders():
    """standalone.enabled=true and tensor>0 → acceleratorType is rendered."""
    cfg = {
        "enabled": True,
        "parallelism": {"tensor": 2},
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "standalone", cfg
    ) is True


def test_standalone_enabled_with_zero_tensor_does_not_render():
    """standalone.enabled=true but tensor=0 → no affinity block."""
    cfg = {
        "enabled": True,
        "parallelism": {"tensor": 0},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "standalone", cfg
    ) is False


# ---------------------------------------------------------------------------
# _method_renders_accelerator_type: decode / prefill create guards
# ---------------------------------------------------------------------------


def test_decode_not_created_when_enabled_false():
    """decode.enabled=false → no decode section, no acceleratorType."""
    cfg = {
        "enabled": False,
        "replicas": 2,
        "accelerator": {"count": 1},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is False


def test_decode_not_created_when_replicas_zero_and_enabled_unset():
    """No decode.enabled and decode.replicas=0 → decode section not created."""
    cfg = {
        "replicas": 0,
        "accelerator": {"count": 1},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is False


def test_decode_created_via_replicas_with_accelerator():
    """No decode.enabled but decode.replicas>0 and accelerator.count>0 → render."""
    cfg = {
        "replicas": 2,
        "accelerator": {"count": 1},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is True


def test_decode_cpu_scenario_with_accelerator_count_zero():
    """The CPU scenario case: decode exists but accelerator.count=0 → no render.

    This is the bug the guard is protecting against -- in the CPU example
    scenario, ``decode.acceleratorType`` is inherited from defaults.yaml (nvidia)
    but ``decode.accelerator.count`` is 0, so the template does not emit it.
    The validator must not flag it either.
    """
    cfg = {
        "replicas": 2,
        "accelerator": {"count": 0},
        "parallelism": {"tensor": 1},  # tensor>0 but accelerator.count wins
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is False


def test_decode_falls_back_to_parallelism_tensor_when_accelerator_missing():
    """If decode.accelerator is not set, the template uses parallelism.tensor."""
    cfg_with_tensor = {
        "replicas": 2,
        "parallelism": {"tensor": 2},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg_with_tensor
    ) is True

    cfg_tensor_zero = {
        "replicas": 2,
        "parallelism": {"tensor": 0},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg_tensor_zero
    ) is False


def test_prefill_disabled_scenario():
    """prefill.enabled=false → no prefill section, no acceleratorType."""
    cfg = {
        "enabled": False,
        "replicas": 0,
        "accelerator": {"count": 0},
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "prefill", cfg
    ) is False


def test_prefill_enabled_with_gpu():
    """prefill.enabled=true and accelerator.count>0 → render."""
    cfg = {
        "enabled": True,
        "replicas": 1,
        "accelerator": {"count": 1},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "prefill", cfg
    ) is True


def test_integer_coercion_handles_string_values():
    """YAML numeric fields sometimes arrive as strings; must still compare."""
    cfg = {
        "enabled": True,
        "replicas": "2",
        "accelerator": {"count": "0"},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is False


def test_integer_coercion_handles_garbage_values():
    """Non-numeric garbage in count falls back to 0 (i.e. no render)."""
    cfg = {
        "enabled": True,
        "replicas": "2",
        "accelerator": {"count": "banana"},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert WorkloadMonitoringStep._method_renders_accelerator_type(
        "decode", cfg
    ) is False


# ---------------------------------------------------------------------------
# _decode_uses_accelerator (step_09 WVA)
# ---------------------------------------------------------------------------


def test_wva_decode_uses_accelerator_count_zero_returns_false():
    """CPU scenario: decode.accelerator.count=0 → WVA sees no accelerator."""
    decode_cfg = {
        "replicas": 2,
        "accelerator": {"count": 0},
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert DeployModelserviceStep._decode_uses_accelerator(decode_cfg) is False


def test_wva_decode_uses_accelerator_count_positive_returns_true():
    """GPU scenario: decode.accelerator.count>0 → WVA picks up accelerator."""
    decode_cfg = {
        "replicas": 2,
        "accelerator": {"count": 1},
        "acceleratorType": {
            "labelKey": "nvidia.com/gpu.product",
            "labelValue": "NVIDIA-H100-80GB-HBM3",
        },
    }
    assert DeployModelserviceStep._decode_uses_accelerator(decode_cfg) is True


def test_wva_decode_falls_back_to_tensor_parallelism():
    """When decode.accelerator is unset, the template uses tensor parallelism."""
    decode_with_tensor = {
        "replicas": 2,
        "parallelism": {"tensor": 4},
        "acceleratorType": {"labelKey": "k", "labelValue": "v"},
    }
    assert (
        DeployModelserviceStep._decode_uses_accelerator(decode_with_tensor)
        is True
    )

    decode_tensor_zero = {
        "replicas": 2,
        "parallelism": {"tensor": 0},
    }
    assert (
        DeployModelserviceStep._decode_uses_accelerator(decode_tensor_zero)
        is False
    )


def test_wva_decode_empty_config_returns_false():
    assert DeployModelserviceStep._decode_uses_accelerator({}) is False
    assert DeployModelserviceStep._decode_uses_accelerator(None) is False
