#!/usr/bin/env python3

# Copyright 2025 The llm-d Authors.
# Licensed under the Apache License, Version 2.0 (the "License");

"""
Metrics processing script for llm-d-benchmark
Parses and aggregates Prometheus metrics and vLLM logs
"""

import os
import re
import json
import glob
from collections import defaultdict
import statistics

metrics_dir = os.environ.get('METRICS_DIR', 'metrics')
raw_dir = os.path.join(metrics_dir, 'raw')
processed_dir = os.path.join(metrics_dir, 'processed')


def parse_prometheus_metrics(file_path):
    """Parse Prometheus metrics from a file."""
    metrics = defaultdict(list)
    timestamp = None
    pod_name = None
    namespace = None
    source = None

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Extract metadata
            if line.startswith('# Timestamp:'):
                timestamp = line.split(':', 1)[1].strip()
            elif line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()
            elif line.startswith('# Namespace:'):
                namespace = line.split(':', 1)[1].strip()
            elif line.startswith('# Source:'):
                source = line.split(':', 1)[1].strip()

            # Skip other comments and empty lines
            if line.startswith('#') or not line:
                continue

            # Parse Prometheus metric line: metric_name{labels} value timestamp
            # Example: vllm:kv_cache_usage_perc 45.2
            # Or cluster metrics: container_memory_usage_bytes{...} 1234567890 1234567890
            match = re.match(
                r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)(?:\s+\d+)?', line)
            if match:
                metric_name = match.group(1)
                value = float(match.group(2))

                # Extract base metric name (without labels)
                base_name = metric_name.split('{')[0]

                # For cluster metrics, convert bytes to GB for readability
                if base_name in ['container_memory_usage_bytes', 'container_memory_working_set_bytes']:
                    value = value / (1024**3)  # Convert to GB
                    base_name = base_name.replace('_bytes', '_gb')
                elif base_name in ['container_network_receive_bytes_total', 'container_network_transmit_bytes_total']:
                    value = value / (1024**2)  # Convert to MB
                    base_name = base_name.replace('_bytes_total', '_mb_total')

                metrics[base_name].append(value)

    return timestamp, pod_name, namespace, dict(metrics)


def percentile(sorted_values, p):
    """Calculate the p-th percentile from a sorted list using linear interpolation.

    Args:
        sorted_values: Sorted list of numeric values
        p: Percentile (0-100)

    Returns:
        Interpolated percentile value
    """
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[f]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def aggregate_metrics():
    """Aggregate metrics from all collected files."""
    # Structure: {pod_name: {metric_name: [values]}}
    pod_metrics = defaultdict(lambda: defaultdict(list))
    pod_metadata = {}

    # Process all raw metric files
    all_files = glob.glob(os.path.join(raw_dir, '*_metrics.log'))

    if not all_files:
        print("Warning: No raw files found to process")
        print(f"Checked directory: {raw_dir}")
        # Create an informative empty result
        results = {
            '_info': {
                'status': 'no_data',
                'message': 'No metrics collected - no raw files found',
                'raw_dir': raw_dir,
                'possible_reasons': [
                    'Metrics collection could not find any pods',
                    'kubectl/oc command may not have access to the cluster',
                    'Label selector may not match any pods',
                    'Namespace may be incorrect'
                ]
            }
        }
        output_file = os.path.join(processed_dir, 'metrics_summary.json')
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Empty metrics summary saved to: {output_file}")
        return results

    print(f"Processing {len(all_files)} files...")

    for file_path in all_files:
        timestamp, pod_name, namespace, metrics = parse_prometheus_metrics(
            file_path)

        if pod_name:
            if pod_name not in pod_metadata:
                pod_metadata[pod_name] = {'namespace': namespace, 'files': []}
            pod_metadata[pod_name]['files'].append(os.path.basename(file_path))

            for metric_name, values in metrics.items():
                pod_metrics[pod_name][metric_name].extend(values)

    # Calculate statistics for each metric
    results = {}
    for pod_name, metrics in pod_metrics.items():
        results[pod_name] = {
            'metadata': pod_metadata.get(pod_name, {}),
            'metrics': {}
        }

        for metric_name, values in metrics.items():
            if values:
                sorted_vals = sorted(values)
                results[pod_name]['metrics'][metric_name] = {
                    'mean': statistics.mean(values),
                    'stddev': statistics.stdev(values) if len(values) > 1 else 0,
                    'min': min(values),
                    'p25': percentile(sorted_vals, 25),
                    'p50': percentile(sorted_vals, 50),
                    'p75': percentile(sorted_vals, 75),
                    'p90': percentile(sorted_vals, 90),
                    'p95': percentile(sorted_vals, 95),
                    'p99': percentile(sorted_vals, 99),
                    'max': max(values),
                    'count': len(values),
                    'unit': get_metric_unit(metric_name)
                }

    # Save aggregated results
    output_file = os.path.join(processed_dir, 'metrics_summary.json')
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"Metrics summary saved to: {output_file}")
    print(f"Processed metrics from {len(results)} pods")
    return results


def get_metric_unit(metric_name):
    """Get the unit for a metric."""
    units = {
        # Cache metrics
        'vllm:kv_cache_usage_perc': '%',
        'kv_cache_usage_percent': '%',
        'cache_hit_rate_percent': '%',
        'vllm:gpu_cache_usage_perc': '%',
        'vllm:cpu_cache_usage_perc': '%',
        'cache_hits': 'count',
        'cache_misses': 'count',
        # Memory metrics
        'vllm:gpu_memory_usage_bytes': 'bytes',
        'DCGM_FI_DEV_FB_USED': 'bytes',
        'vllm:cpu_memory_usage_bytes': 'bytes',
        'container_memory_usage_bytes': 'bytes',
        'gpu_memory_used_gb': 'GB',
        'gpu_memory_total_gb': 'GB',
        'gpu_memory_usage_percent': '%',
        'cpu_memory_used_gb': 'GB',
        # Compute metrics
        'DCGM_FI_DEV_GPU_UTIL': '%',
        'container_cpu_usage_seconds_total': 'seconds',
        'gpu_utilization_percent': '%',
        # Performance metrics
        'DCGM_FI_DEV_POWER_USAGE': 'watts',
        'power_consumption_watts': 'watts',
        'prompt_throughput_tokens_per_sec': 'tokens/s',
        'generation_throughput_tokens_per_sec': 'tokens/s',
        # Preemption metrics
        'vllm:num_preemptions_total': 'count',
        # Queue metrics
        'vllm:num_requests_running': 'count',
        'vllm:num_requests_waiting': 'count',
        'vllm:num_requests_swapped': 'count',
        'running_requests': 'count',
        'waiting_requests': 'count',
        'swapped_requests': 'count',
    }
    return units.get(metric_name, '')


if __name__ == '__main__':
    aggregate_metrics()
