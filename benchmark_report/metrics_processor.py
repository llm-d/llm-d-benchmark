"""
Process collected metrics and integrate into benchmark report.
"""

import json
import os
import glob
import re
from typing import Any
from pathlib import Path

from .base import Units
from .schema_v0_2 import (
    Statistics,
    ResourceMetrics,
    TimeSeriesData,
    TimeSeriesPoint,
    TimeSeriesResourceMetrics,
    ComponentObservability,
)


def parse_prometheus_metrics(file_path: str) -> tuple[str | None, str | None, dict[str, list[float]]]:
    """Parse Prometheus metrics from a file.

    Args:
        file_path: Path to metrics file

    Returns:
        Tuple of (timestamp, pod_name, metrics_dict)
    """
    metrics: dict[str, list[float]] = {}
    timestamp = None
    pod_name = None

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Extract timestamp
            if line.startswith('# Timestamp:'):
                timestamp = line.split(':', 1)[1].strip()

            # Extract pod name
            if line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()

            # Skip comments and empty lines
            if line.startswith('#') or not line:
                continue

            # Parse metric line: metric_name{labels} value
            match = re.match(
                r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)', line)
            if match:
                metric_name = match.group(1)
                value = float(match.group(2))

                # Extract base metric name (without labels)
                base_name = metric_name.split('{')[0]

                if base_name not in metrics:
                    metrics[base_name] = []
                metrics[base_name].append(value)

    return timestamp, pod_name, metrics


def calculate_statistics(values: list[float], units: Units) -> Statistics:
    """Calculate statistics from a list of values.

    Args:
        values: List of numeric values
        units: Units for the values

    Returns:
        Statistics object
    """
    import statistics as stats
    import numpy as np

    if not values:
        return Statistics(units=units, mean=0.0, stddev=0.0)

    sorted_values = sorted(values)
    n = len(sorted_values)

    return Statistics(
        units=units,
        mean=stats.mean(values),
        stddev=stats.stdev(values) if n > 1 else 0.0,
        min=min(values),
        p25=np.percentile(sorted_values, 25),
        p50=np.percentile(sorted_values, 50),
        p75=np.percentile(sorted_values, 75),
        p90=np.percentile(sorted_values, 90),
        p95=np.percentile(sorted_values, 95),
        p99=np.percentile(sorted_values, 99),
        max=max(values),
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
        'vllm:kv_cache_usage_perc': ('kv_cache_usage', Units.PERCENT),
        'vllm:gpu_cache_usage_perc': ('gpu_cache_usage', Units.PERCENT),
        'vllm:cpu_cache_usage_perc': ('cpu_cache_usage', Units.PERCENT),
        'vllm:gpu_memory_usage_bytes': ('gpu_memory_usage', Units.GIB),
        'vllm:cpu_memory_usage_bytes': ('cpu_memory_usage', Units.GIB),
        'container_memory_usage_bytes': ('cpu_memory_usage', Units.GIB),
        'DCGM_FI_DEV_GPU_UTIL': ('gpu_utilization', Units.PERCENT),
        'container_cpu_usage_seconds_total': ('cpu_utilization', Units.PERCENT),
        'DCGM_FI_DEV_POWER_USAGE': ('power_consumption', Units.WATTS),
    }

    for metric_name, metric_data in metrics_summary.items():
        if metric_name in metric_mapping:
            field_name, units = metric_mapping[metric_name]

            # Convert bytes to GiB if needed
            mean_value = metric_data['mean']
            stddev_value = metric_data['stddev']
            min_value = metric_data['min']
            max_value = metric_data['max']

            if 'bytes' in metric_name and units == Units.GIB:
                mean_value = mean_value / (1024 ** 3)
                stddev_value = stddev_value / (1024 ** 3)
                min_value = min_value / (1024 ** 3)
                max_value = max_value / (1024 ** 3)

            stats = Statistics(
                units=units,
                mean=mean_value,
                stddev=stddev_value,
                min=min_value,
                max=max_value,
            )

            setattr(aggregate, field_name, stats)

    return ComponentObservability(
        component_label=component_label,
        replica_id=pod_name,
        aggregate=aggregate,
        raw_data_path=f"{metrics_dir}/raw/{pod_name}_*.txt",
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
