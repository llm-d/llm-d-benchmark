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


def parse_vllm_log(file_path):
    """Parse vLLM logs to extract metrics."""
    metrics = defaultdict(list)
    timestamp = None
    pod_name = None
    namespace = None

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

            # Parse KV cache usage: "GPU KV cache usage: 45.2%" or "GPU KV cache usage: 0.0%"
            match = re.search(r'GPU KV cache usage:\s*([\d.]+)%', line)
            if match:
                metrics['kv_cache_usage_percent'].append(float(match.group(1)))

            # Parse KV cache hit rate: "Prefix cache hit rate: 51.0%"
            match = re.search(r'Prefix cache hit rate:\s*([\d.]+)%', line)
            if match:
                hit_rate = float(match.group(1))
                metrics['cache_hit_rate_percent'].append(hit_rate)

            # Parse cache hits and misses: "Cache hits: 1234, misses: 56"
            match = re.search(
                r'Cache hits:\s*(\d+)(?:,\s*misses:\s*(\d+))?', line)
            if match:
                hits = int(match.group(1))
                metrics['cache_hits'].append(hits)
                if match.group(2):
                    misses = int(match.group(2))
                    metrics['cache_misses'].append(misses)
                    # Calculate hit rate
                    total = hits + misses
                    if total > 0:
                        metrics['cache_hit_rate_percent'].append(
                            (hits / total) * 100)

            # Parse GPU memory: "GPU memory usage: 12.5 GB / 80.0 GB"
            match = re.search(
                r'GPU memory usage:\s*([\d.]+)\s*GB\s*/\s*([\d.]+)\s*GB', line)
            if match:
                used_gb = float(match.group(1))
                total_gb = float(match.group(2))
                metrics['gpu_memory_used_gb'].append(used_gb)
                metrics['gpu_memory_total_gb'].append(total_gb)
                metrics['gpu_memory_usage_percent'].append(
                    (used_gb / total_gb) * 100 if total_gb > 0 else 0)

            # Parse CPU memory: "CPU memory usage: 2.3 GB"
            match = re.search(r'CPU memory usage:\s*([\d.]+)\s*GB', line)
            if match:
                metrics['cpu_memory_used_gb'].append(float(match.group(1)))

            # Parse GPU utilization: "GPU utilization: 87%"
            match = re.search(r'GPU utilization:\s*([\d.]+)%', line)
            if match:
                metrics['gpu_utilization_percent'].append(
                    float(match.group(1)))

            # Parse requests: "Avg prompt throughput: 123.4 tokens/s, Avg generation throughput: 456.7 tokens/s"
            match = re.search(
                r'Avg prompt throughput:\s*([\d.]+)\s*tokens/s', line)
            if match:
                metrics['prompt_throughput_tokens_per_sec'].append(
                    float(match.group(1)))

            match = re.search(
                r'Avg generation throughput:\s*([\d.]+)\s*tokens/s', line)
            if match:
                metrics['generation_throughput_tokens_per_sec'].append(
                    float(match.group(1)))

            # Parse running requests: "Running: 5 reqs"
            match = re.search(r'Running:\s*(\d+)\s*reqs?', line)
            if match:
                metrics['running_requests'].append(int(match.group(1)))

            # Parse waiting requests: "Waiting: 12 reqs"
            match = re.search(r'Waiting:\s*(\d+)\s*reqs?', line)
            if match:
                metrics['waiting_requests'].append(int(match.group(1)))

            # Parse swapped requests: "Swapped: 3 reqs"
            match = re.search(r'Swapped:\s*(\d+)\s*reqs?', line)
            if match:
                metrics['swapped_requests'].append(int(match.group(1)))

            # Additional patterns for vLLM metrics logs
            # Parse: "KV cache usage: 45.2%" (alternative format)
            if 'kv_cache_usage_percent' not in metrics or not metrics['kv_cache_usage_percent']:
                match = re.search(
                    r'KV cache usage:\s*([\d.]+)%', line, re.IGNORECASE)
                if match:
                    metrics['kv_cache_usage_percent'].append(
                        float(match.group(1)))

            # Parse: "cache_hit_rate=0.512" or "cache_hit_rate: 51.2%"
            match = re.search(
                r'cache[_\s]hit[_\s]rate[=:]\s*([\d.]+)', line, re.IGNORECASE)
            if match:
                value = float(match.group(1))
                # If value is between 0 and 1, convert to percentage
                if value <= 1.0:
                    value = value * 100
                if 'cache_hit_rate_percent' not in metrics or value not in metrics['cache_hit_rate_percent']:
                    metrics['cache_hit_rate_percent'].append(value)

            # Parse power consumption: "Power: 285W" or "Power usage: 285 W"
            match = re.search(
                r'Power(?:\s+usage)?:\s*([\d.]+)\s*W', line, re.IGNORECASE)
            if match:
                metrics['power_consumption_watts'].append(
                    float(match.group(1)))

    return timestamp, pod_name, namespace, dict(metrics)


def aggregate_metrics():
    """Aggregate metrics from all collected files."""
    # Structure: {pod_name: {metric_name: [values]}}
    pod_metrics = defaultdict(lambda: defaultdict(list))
    pod_metadata = {}

    # Process all raw metric and log files
    metrics_files = glob.glob(os.path.join(raw_dir, '*_metrics.txt'))
    log_files = glob.glob(os.path.join(raw_dir, '*_logs.txt'))

    # Also support old format (*.log files)
    old_log_files = glob.glob(os.path.join(raw_dir, '*.log'))

    all_files = metrics_files + log_files + old_log_files

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
        # Determine file type and parse accordingly
        if file_path.endswith('_metrics.txt'):
            timestamp, pod_name, namespace, metrics = parse_prometheus_metrics(
                file_path)
        else:
            timestamp, pod_name, namespace, metrics = parse_vllm_log(file_path)

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
                results[pod_name]['metrics'][metric_name] = {
                    'mean': statistics.mean(values),
                    'stddev': statistics.stdev(values) if len(values) > 1 else 0,
                    'min': min(values),
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
        'prompt_throughput_tokens_per_sec': 'tokens/s',
        'generation_throughput_tokens_per_sec': 'tokens/s',
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
