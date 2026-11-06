"""Unit tests for piecewise Allocated GPU-Hours integration in process_metrics.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROCESS_METRICS_PATH = PROJECT_ROOT / "workload" / "harnesses" / "process_metrics.py"


def _load_process_metrics():
    spec = importlib.util.spec_from_file_location(
        "llmdbench_process_metrics", PROCESS_METRICS_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = _load_process_metrics()


def _snap(
    epoch: float,
    scheduled: int,
    ready: int,
    terminating: int = 0,
    gpus_per_pod: int = 1,
    accelerator_mode: str = "physical_gpu",
) -> dict:
    alloc_gpus = float(scheduled * gpus_per_pod) if gpus_per_pod > 0 else 0.0
    ready_gpus = float(ready * gpus_per_pod) if gpus_per_pod > 0 else 0.0
    eff_alloc = alloc_gpus if gpus_per_pod > 0 else float(scheduled)
    eff_ready = ready_gpus if gpus_per_pod > 0 else float(ready)
    return {
        "timestamp": f"epoch-{int(epoch)}",
        "epoch_seconds": epoch,
        "controllers": [
            {
                "kind": "Deployment",
                "name": "qwen3-decode",
                "role": "decode",
                "desired_replicas": scheduled,
                "ready_replicas": ready,
                "available_replicas": ready,
                "gpus_per_pod": gpus_per_pod,
            }
        ],
        "pod_allocation": {
            "scheduled_replicas": scheduled,
            "ready_replicas": ready,
            "terminating_replicas": terminating,
            "pending_unscheduled_replicas": 0,
            "gpus_per_pod_max": gpus_per_pod,
            "allocated_gpus": alloc_gpus,
            "ready_gpus": ready_gpus,
            "effective_allocated_gpus": eff_alloc,
            "effective_ready_gpus": eff_ready,
            "virtual_gpus_per_pod": 1,
            "accelerator_mode": accelerator_mode,
        },
    }


def test_scale_to_zero_lifecycle_captures_cold_start_and_termination_tax():
    """Verify 0 -> cold-start -> Ready -> scale-out -> terminating -> 0 integral."""
    snapshots = [
        # t=0..30 (30s): idle at 0 replicas -> 0 GPU-s
        _snap(1000.0, scheduled=0, ready=0),
        # t=30..90 (60s): 1 pod scheduled (cold start), 0 Ready -> 60 alloc GPU-s, 0 ready GPU-s
        _snap(1030.0, scheduled=1, ready=0),
        # t=90..210 (120s): 2 pods scheduled & Ready -> 240 alloc GPU-s, 240 ready GPU-s
        _snap(1090.0, scheduled=2, ready=2),
        # t=210..270 (60s): 1 pod terminating (still scheduled on GPU node), 0 Ready -> 60 alloc GPU-s, 0 ready GPU-s
        _snap(1210.0, scheduled=1, ready=0, terminating=1),
        # t=270: back at 0 replicas
        _snap(1270.0, scheduled=0, ready=0),
    ]

    res = pm.compute_resource_integral(snapshots)
    # Total window = 270s
    assert res["window_seconds"] == pytest.approx(270.0)
    # Allocated GPU-s = 0 + 60*1 + 120*2 + 60*1 = 360 GPU-s = 0.1 GPU-hr
    assert res["allocated_gpu_seconds"] == pytest.approx(360.0)
    assert res["allocated_gpu_hours"] == pytest.approx(0.1)
    # Ready GPU-s = 120*2 = 240 GPU-s (excludes 60s cold start + 60s cooldown/termination)
    assert res["ready_gpu_seconds"] == pytest.approx(240.0)
    assert res["ready_gpu_hours"] == pytest.approx(240.0 / 3600.0, abs=1e-6)
    # Overhead GPU fraction = 1 - 240/360 = 1/3
    assert res["overhead_gpu_fraction"] == pytest.approx(1.0 / 3.0, abs=1e-5)
    assert res["accelerator_mode"] == "physical_gpu"
    assert res["snapshot_count"] == 5


def test_multi_gpu_pods_multiply_by_gpus_per_pod():
    """2 replicas of TP=2 (2 GPUs/pod) for 900s = 3600 GPU-s = 1.0 GPU-hr."""
    snapshots = [
        _snap(2000.0, scheduled=2, ready=2, gpus_per_pod=2),
        _snap(2900.0, scheduled=2, ready=2, gpus_per_pod=2),
    ]
    res = pm.compute_resource_integral(snapshots)
    assert res["allocated_gpu_seconds"] == pytest.approx(3600.0)
    assert res["allocated_gpu_hours"] == pytest.approx(1.0)
    assert res["allocated_pod_seconds"] == pytest.approx(1800.0)
    assert res["allocated_pod_hours"] == pytest.approx(0.5)


def test_cpu_simulator_fallback_uses_virtual_gpus_per_pod():
    """CPU simulator pods (gpus_per_pod=0) integrate virtual_gpus_per_pod cleanly."""
    s0 = _snap(
        3000.0,
        scheduled=2,
        ready=2,
        gpus_per_pod=0,
        accelerator_mode="simulated_pod_units",
    )
    s1 = _snap(
        3600.0,
        scheduled=0,
        ready=0,
        gpus_per_pod=0,
        accelerator_mode="simulated_pod_units",
    )

    res = pm.compute_resource_integral([s0, s1], virtual_gpus_per_pod=2.0)
    # 600s * 2 pods * 2 virtual GPUs/pod = 2400 virtual GPU-s
    assert res["accelerator_mode"] == "simulated_pod_units"
    assert res["allocated_gpu_seconds"] == pytest.approx(2400.0)
    assert res["allocated_gpu_hours"] == pytest.approx(2400.0 / 3600.0, abs=1e-6)


def test_window_clipping_and_duty_cycle_tail_hold():
    """Explicit [window_start, window_end] clips early samples and holds final state across duty cycle."""
    snapshots = [
        _snap(
            100.0, scheduled=2, ready=2
        ),  # before window_start=150 -> held at 2 from 150..200 (50s * 2 = 100)
        _snap(
            200.0, scheduled=0, ready=0
        ),  # scaled down to 0 at t=200; held at 0 through window_end=510 (310s * 0 = 0)
    ]
    res = pm.compute_resource_integral(snapshots, window_start=150.0, window_end=510.0)
    assert res["window_seconds"] == pytest.approx(360.0)
    assert res["allocated_gpu_seconds"] == pytest.approx(100.0)
    assert res["allocated_gpu_hours"] == pytest.approx(100.0 / 3600.0, abs=1e-6)


def test_llm_d_async_4layer_metrics_registered():
    """Ensure 4-layer llm-d-async backlog and gate metrics are registered."""
    assert "llm_d_async_async_broker_backlog" in pm.LEGACY_AGGREGATE_METRICS
    assert "llm_d_async_async_gate_waiting_requests" in pm.LEGACY_AGGREGATE_METRICS
    assert pm.METRIC_UNITS["llm_d_async_async_broker_backlog"] == "count"
