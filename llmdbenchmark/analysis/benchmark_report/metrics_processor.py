"""
Process collected metrics and integrate into benchmark report.
"""

import json
import os
from typing import Any

from .base import Units
from .schema_v0_2 import (
    Statistics,
    ResourceMetrics,
    ComponentObservability,
)


def load_metrics_summary(metrics_dir: str) -> dict[str, Any]:
    """Load the processed metrics summary JSON file.

    Args:
        metrics_dir: Directory containing metrics

    Returns:
        Dictionary with metrics summary
    """
    summary_file = os.path.join(
        metrics_dir, 'processed', 'metrics_summary.json')

    if not os.path.exists(summary_file):
        return {}

    with open(summary_file, 'r') as f:
        return json.load(f)


def create_component_observability(
    component_label: str,
    pod_name: str,
    metrics_summary: dict[str, Any],
    metrics_dir: str
) -> ComponentObservability:
    """Create ComponentObservability from metrics summary.

    Args:
        component_label: Label of the component
        pod_name: Name of the pod/replica
        metrics_summary: Metrics summary for the pod
        metrics_dir: Directory containing metrics files

    Returns:
        ComponentObservability object
    """
    aggregate = ResourceMetrics()

    # Map metric names to ResourceMetrics fields
    metric_mapping = {
        # Cache metrics
        'vllm:kv_cache_usage_perc': ('kv_cache_usage', Units.PERCENT),
        'vllm:gpu_cache_usage_perc': ('gpu_cache_usage', Units.PERCENT),
        'vllm:cpu_cache_usage_perc': ('cpu_cache_usage', Units.PERCENT),
        # Memory metrics
        'vllm:gpu_memory_usage_bytes': ('gpu_memory_usage', Units.GIB),
        'DCGM_FI_DEV_FB_USED': ('gpu_memory_usage', Units.GIB),
        'vllm:cpu_memory_usage_bytes': ('cpu_memory_usage', Units.GIB),
        'container_memory_usage_bytes': ('cpu_memory_usage', Units.GIB),
        # Compute metrics
        'DCGM_FI_DEV_GPU_UTIL': ('gpu_utilization', Units.PERCENT),
        'container_cpu_usage_seconds_total': ('cpu_utilization', Units.PERCENT),
        # Performance metrics
        'DCGM_FI_DEV_POWER_USAGE': ('power_consumption', Units.WATTS),
        # Queue metrics
        'vllm:num_requests_running': ('running_requests', Units.COUNT),
        'vllm:num_requests_waiting': ('waiting_requests', Units.COUNT),
        'vllm:num_requests_swapped': ('swapped_requests', Units.COUNT),
        # Preemption metrics
        'vllm:num_preemptions_total': ('preemptions', Units.COUNT),
    }

    for metric_name, metric_data in metrics_summary.items():
        if metric_name in metric_mapping:
            field_name, units = metric_mapping[metric_name]

            # Get values from metric_data
            mean_value = metric_data.get('mean', 0.0)
            stddev_value = metric_data.get('stddev', 0.0)
            min_value = metric_data.get('min', 0.0)
            max_value = metric_data.get('max', 0.0)
            p25_value = metric_data.get('p25')
            p50_value = metric_data.get('p50')
            p75_value = metric_data.get('p75')
            p90_value = metric_data.get('p90')
            p95_value = metric_data.get('p95')
            p99_value = metric_data.get('p99')

            # Scaling factor for unit conversion (applied uniformly to all stat values)
            scale = 1.0
            if 'bytes' in metric_name.lower() and units == Units.GIB:
                scale = 1.0 / (1024 ** 3)
            elif metric_name.endswith('_gb') and units == Units.GIB:
                # GB to GiB
                scale = (1000 ** 3) / (1024 ** 3)

            def _scale(v):
                return v * scale if v is not None else None

            stats = Statistics(
                units=units,
                mean=mean_value * scale,
                stddev=stddev_value * scale,
                min=_scale(min_value),
                p25=_scale(p25_value),
                p50=_scale(p50_value),
                p75=_scale(p75_value),
                p90=_scale(p90_value),
                p95=_scale(p95_value),
                p99=_scale(p99_value),
                max=_scale(max_value),
            )

            setattr(aggregate, field_name, stats)

    return ComponentObservability(
        component_label=component_label,
        replica_id=pod_name,
        aggregate=aggregate,
        raw_data_path=f"{metrics_dir}/raw/{pod_name}_*.log",
    )


def process_metrics_for_benchmark_report(
    metrics_dir: str,
    component_label: str = "vllm-service"
) -> list[ComponentObservability]:
    """Process collected metrics and create ComponentObservability objects.

    Args:
        metrics_dir: Directory containing collected metrics
        component_label: Label for the component

    Returns:
        List of ComponentObservability objects
    """
    metrics_summary = load_metrics_summary(metrics_dir)

    if not metrics_summary:
        return []

    observability_list = []

    for pod_name, pod_metrics in metrics_summary.items():
        comp_obs = create_component_observability(
            component_label=component_label,
            pod_name=pod_name,
            metrics_summary=pod_metrics,
            metrics_dir=metrics_dir
        )
        observability_list.append(comp_obs)

    return observability_list


def add_metrics_to_benchmark_report(
    br_dict: dict[str, Any],
    metrics_dir: str,
    component_label: str = "vllm-service"
) -> dict[str, Any]:
    """Add metrics to an existing benchmark report dictionary.

    Args:
        br_dict: Benchmark report as dictionary
        metrics_dir: Directory containing collected metrics
        component_label: Label for the component

    Returns:
        Updated benchmark report dictionary
    """
    # Ensure results.observability exists
    if 'results' not in br_dict:
        br_dict['results'] = {}

    if 'observability' not in br_dict['results']:
        br_dict['results']['observability'] = {}

    # Process metrics and add to observability
    component_obs_list = process_metrics_for_benchmark_report(
        metrics_dir, component_label)

    if component_obs_list:
        br_dict['results']['observability']['components'] = [
            comp.model_dump(mode='json', exclude_none=True, by_alias=True)
            for comp in component_obs_list
        ]

    return br_dict

# Made with Bob
