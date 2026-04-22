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

    # Compute ratio metrics per-file before aggregation
    # Structure: {pod_name: {ratio_metric_name: [per-file ratio values]}}
    ratio_definitions = [
        ('vllm:prefix_cache_hit_rate', 'vllm:prefix_cache_hits_total',
         'vllm:prefix_cache_queries_total'),
        ('vllm:external_prefix_cache_hit_rate',
         'vllm:external_prefix_cache_hits_total',
         'vllm:external_prefix_cache_queries_total'),
    ]
    for pod_name, metrics in pod_metrics.items():
        for ratio_name, num_metric, den_metric in ratio_definitions:
            if num_metric in metrics and den_metric in metrics:
                num_vals = metrics[num_metric]
                den_vals = metrics[den_metric]
                # Values are collected in file order; pair by index
                ratio_vals = []
                for i in range(min(len(num_vals), len(den_vals))):
                    if den_vals[i] > 0:
                        ratio_vals.append(
                            num_vals[i] / den_vals[i] * 100)
                    else:
                        ratio_vals.append(0.0)
                if ratio_vals:
                    metrics[ratio_name] = ratio_vals

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

    # Aggregate selected metrics across all pods
    AGGREGATE_METRICS = {
        'vllm:kv_cache_usage_perc', 'vllm:num_requests_running',
        'vllm:num_requests_waiting', 'vllm:num_preemptions_total',
        'vllm:prefix_cache_hit_rate', 'vllm:external_prefix_cache_hit_rate',
        # EPP pool-level gauges (already aggregated by EPP across endpoints)
        'inference_pool_average_kv_cache_utilization',
        'inference_pool_average_queue_size',
        'inference_pool_average_running_requests',
        'inference_pool_ready_pods',
    }
    aggregated_values = defaultdict(list)
    for pod_name, metrics in pod_metrics.items():
        for metric_name, values in metrics.items():
            if metric_name in AGGREGATE_METRICS and values:
                aggregated_values[metric_name].extend(values)

    if aggregated_values:
        agg_metrics = {}
        for metric_name, values in aggregated_values.items():
            sorted_vals = sorted(values)
            agg_metrics[metric_name] = {
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
                'unit': get_metric_unit(metric_name),
            }
        results['_aggregated'] = {'metrics': agg_metrics}
        print(f"Aggregated {len(agg_metrics)} metrics across all pods")

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
        'vllm:gpu_cache_usage_perc': '%',
        'vllm:cpu_cache_usage_perc': '%',
        'vllm:prefix_cache_hits_total': 'tokens',
        'vllm:prefix_cache_queries_total': 'tokens',
        'vllm:external_prefix_cache_hits_total': 'tokens',
        'vllm:external_prefix_cache_queries_total': 'tokens',
        # Memory metrics
        'vllm:gpu_memory_usage_bytes': 'bytes',
        'DCGM_FI_DEV_FB_USED': 'bytes',
        'vllm:cpu_memory_usage_bytes': 'bytes',
        'container_memory_usage_bytes': 'bytes',
        # Compute metrics
        'DCGM_FI_DEV_GPU_UTIL': '%',
        'container_cpu_usage_seconds_total': 'seconds',
        # Performance metrics
        'DCGM_FI_DEV_POWER_USAGE': 'watts',
        # NIXL KV transfer metrics
        'vllm:nixl_xfer_time_seconds_sum': 'seconds',
        'vllm:nixl_xfer_time_seconds_count': 'count',
        'vllm:nixl_bytes_transferred_sum': 'bytes',
        'vllm:nixl_bytes_transferred_count': 'count',
        # Preemption metrics
        'vllm:num_preemptions_total': 'count',
        # Queue metrics
        'vllm:num_requests_running': 'count',
        'vllm:num_requests_waiting': 'count',
        'vllm:num_requests_swapped': 'count',
        # Computed ratio metrics
        'vllm:prefix_cache_hit_rate': '%',
        'vllm:external_prefix_cache_hit_rate': '%',
        # EPP (inference scheduler) Prometheus metrics
        'inference_pool_average_kv_cache_utilization': '%',
        'inference_pool_average_queue_size': 'count',
        'inference_pool_average_running_requests': 'count',
        'inference_pool_ready_pods': 'count',
        'inference_extension_scheduler_e2e_duration_seconds_bucket': 'seconds',
        'inference_extension_scheduler_e2e_duration_seconds_sum': 'seconds',
        'inference_extension_scheduler_e2e_duration_seconds_count': 'count',
        'inference_extension_plugin_duration_seconds_bucket': 'seconds',
        'inference_extension_plugin_duration_seconds_sum': 'seconds',
        'inference_extension_plugin_duration_seconds_count': 'count',
        'inference_extension_request_duration_seconds_bucket': 'seconds',
        'inference_extension_request_duration_seconds_sum': 'seconds',
        'inference_extension_request_duration_seconds_count': 'count',
        'inference_extension_request_ttft_duration_seconds_bucket': 'seconds',
        'inference_extension_request_ttft_duration_seconds_sum': 'seconds',
        'inference_extension_request_ttft_duration_seconds_count': 'count',
        'inference_extension_input_tokens_bucket': 'tokens',
        'inference_extension_output_tokens_bucket': 'tokens',
        'inference_extension_normalized_time_per_output_token_bucket': 'seconds',
        'inference_extension_prefix_indexer_hit_ratio_bucket': 'ratio',
        'inference_extension_prefix_indexer_size': 'count',
        'llm_d_inference_scheduler_pd_decision_total': 'count',
        'llm_d_inference_scheduler_disagg_decision_total': 'count',
    }
    return units.get(metric_name, '')


def aggregate_pod_startup_stats():
    """Compute aggregate statistics for pod startup times."""
    startup_file = os.path.join(processed_dir, 'pod_startup_times.json')
    if not os.path.exists(startup_file):
        return

    with open(startup_file) as f:
        data = json.load(f)

    values = [
        p['startup_seconds'] for p in data.get('pods', [])
        if isinstance(p.get('startup_seconds'), (int, float))
    ]
    if not values:
        return

    sorted_vals = sorted(values)
    data['aggregate'] = {
        'units': 's',
        'mean': statistics.mean(values),
        'stddev': statistics.stdev(values) if len(values) > 1 else 0,
        'min': min(values),
        'p50': percentile(sorted_vals, 50),
        'p90': percentile(sorted_vals, 90),
        'p99': percentile(sorted_vals, 99),
        'max': max(values),
    }

    with open(startup_file, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Pod startup stats: {len(values)} pods, "
          f"mean={data['aggregate']['mean']:.1f}s")


def aggregate_replica_stats():
    """Compute aggregate statistics from replica status time series."""
    ts_file = os.path.join(processed_dir, 'replica_status_timeseries.json')
    status_file = os.path.join(processed_dir, 'replica_status.json')
    if not os.path.exists(ts_file):
        return

    with open(ts_file) as f:
        ts_data = json.load(f)

    snapshots = ts_data.get('snapshots', [])
    if not snapshots:
        return

    # Compute total ready replicas per snapshot
    ready_counts = []
    for snap in snapshots:
        total_ready = sum(
            c.get('ready_replicas', 0) for c in snap.get('controllers', [])
        )
        ready_counts.append(total_ready)

    sorted_vals = sorted(ready_counts)
    aggregate = {
        'units': 'count',
        'mean': statistics.mean(ready_counts),
        'stddev': statistics.stdev(ready_counts) if len(ready_counts) > 1 else 0,
        'min': min(ready_counts),
        'p50': percentile(sorted_vals, 50),
        'p90': percentile(sorted_vals, 90),
        'p99': percentile(sorted_vals, 99),
        'max': max(ready_counts),
    }

    # Write aggregate into the latest snapshot file for report integration
    if os.path.exists(status_file):
        with open(status_file) as f:
            status_data = json.load(f)
        status_data['aggregate_ready_replicas'] = aggregate
        with open(status_file, 'w') as f:
            json.dump(status_data, f, indent=2)

    print(f"Replica stats: {len(snapshots)} snapshots, "
          f"ready replicas min={aggregate['min']} max={aggregate['max']} "
          f"mean={aggregate['mean']:.1f}")


if __name__ == '__main__':
    aggregate_metrics()
    aggregate_pod_startup_stats()
    aggregate_replica_stats()
