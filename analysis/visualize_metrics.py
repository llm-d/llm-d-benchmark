#!/usr/bin/env python3
"""
Generate visualizations from collected metrics.

This script creates time series graphs for metrics collected during benchmarking.
"""

import argparse
import json
import os
import sys
import glob
import re
from datetime import datetime
from pathlib import Path

try:
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Warning: matplotlib not available. Install with: pip install matplotlib")


def parse_prometheus_metrics_with_timestamp(file_path: str) -> tuple[str | None, str | None, dict[str, list[tuple[datetime, float]]]]:
    """Parse Prometheus metrics from a file with timestamps.

    Args:
        file_path: Path to metrics file

    Returns:
        Tuple of (timestamp, pod_name, metrics_dict with timestamps)
    """
    metrics: dict[str, list[tuple[datetime, float]]] = {}
    timestamp_str = None
    timestamp_dt = None
    pod_name = None

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Extract timestamp
            if line.startswith('# Timestamp:'):
                timestamp_str = line.split(':', 1)[1].strip()
                try:
                    timestamp_dt = datetime.fromisoformat(
                        timestamp_str.replace('Z', '+00:00'))
                except ValueError:
                    pass

            # Extract pod name
            if line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()

            # Skip comments and empty lines
            if line.startswith('#') or not line:
                continue

            # Parse metric line
            match = re.match(
                r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)', line)
            if match and timestamp_dt:
                metric_name = match.group(1)
                value = float(match.group(2))

                # Extract base metric name (without labels)
                base_name = metric_name.split('{')[0]

                if base_name not in metrics:
                    metrics[base_name] = []
                metrics[base_name].append((timestamp_dt, value))

    return timestamp_str, pod_name, metrics


def collect_time_series_data(metrics_dir: str) -> dict[str, dict[str, list[tuple[datetime, float]]]]:
    """Collect time series data from all metric files.

    Args:
        metrics_dir: Directory containing metrics

    Returns:
        Dictionary mapping pod names to their time series data
    """
    raw_dir = os.path.join(metrics_dir, 'raw')
    pod_data: dict[str, dict[str, list[tuple[datetime, float]]]] = {}

    for file_path in glob.glob(os.path.join(raw_dir, '*.txt')):
        _, pod_name, metrics = parse_prometheus_metrics_with_timestamp(
            file_path)

        if pod_name:
            if pod_name not in pod_data:
                pod_data[pod_name] = {}

            for metric_name, data_points in metrics.items():
                if metric_name not in pod_data[pod_name]:
                    pod_data[pod_name][metric_name] = []
                pod_data[pod_name][metric_name].extend(data_points)

    # Sort time series data by timestamp
    for pod_name in pod_data:
        for metric_name in pod_data[pod_name]:
            pod_data[pod_name][metric_name].sort(key=lambda x: x[0])

    return pod_data


def plot_metric_time_series(
    pod_data: dict[str, dict[str, list[tuple[datetime, float]]]],
    metric_name: str,
    output_path: str,
    title: str | None = None,
    ylabel: str | None = None
):
    """Plot time series for a specific metric across all pods.

    Args:
        pod_data: Time series data for all pods
        metric_name: Name of metric to plot
        output_path: Path to save the plot
        title: Plot title (optional)
        ylabel: Y-axis label (optional)
    """
    if not MATPLOTLIB_AVAILABLE:
        print(f"Skipping plot for {metric_name}: matplotlib not available")
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    for pod_name, metrics in pod_data.items():
        if metric_name in metrics:
            timestamps, values = zip(*metrics[metric_name])
            ax.plot(timestamps, values, label=pod_name,
                    marker='o', markersize=3)

    ax.set_xlabel('Time')
    ax.set_ylabel(ylabel or metric_name)
    ax.set_title(title or f'{metric_name} Over Time')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Format x-axis
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.xticks(rotation=45)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()

    print(f"Saved plot: {output_path}")


def generate_all_visualizations(metrics_dir: str, output_dir: str | None = None):
    """Generate visualizations for all collected metrics.

    Args:
        metrics_dir: Directory containing collected metrics
        output_dir: Directory to save visualizations (default: metrics_dir/graphs)
    """
    if not MATPLOTLIB_AVAILABLE:
        print("Error: matplotlib is required for visualization")
        print("Install with: pip install matplotlib")
        return

    if output_dir is None:
        output_dir = os.path.join(metrics_dir, 'graphs')

    os.makedirs(output_dir, exist_ok=True)

    # Collect time series data
    print("Collecting time series data...")
    pod_data = collect_time_series_data(metrics_dir)

    if not pod_data:
        print("No metrics data found")
        return

    # Define metrics to visualize
    metrics_to_plot = {
        'vllm:kv_cache_usage_perc': ('KV Cache Usage', 'Usage (%)'),
        'vllm:gpu_cache_usage_perc': ('GPU Cache Usage', 'Usage (%)'),
        'vllm:cpu_cache_usage_perc': ('CPU Cache Usage', 'Usage (%)'),
        'vllm:gpu_memory_usage_bytes': ('GPU Memory Usage', 'Memory (bytes)'),
        'vllm:cpu_memory_usage_bytes': ('CPU Memory Usage', 'Memory (bytes)'),
        'container_memory_usage_bytes': ('Container Memory Usage', 'Memory (bytes)'),
        'DCGM_FI_DEV_GPU_UTIL': ('GPU Utilization', 'Utilization (%)'),
        'DCGM_FI_DEV_POWER_USAGE': ('GPU Power Usage', 'Power (W)'),
        'vllm:num_requests_running': ('Running Requests', 'Count'),
        'vllm:num_requests_waiting': ('Waiting Requests', 'Count'),
    }

    # Generate plots
    for metric_name, (title, ylabel) in metrics_to_plot.items():
        # Check if any pod has this metric
        has_metric = any(
            metric_name in metrics for metrics in pod_data.values())

        if has_metric:
            output_path = os.path.join(
                output_dir, f'{metric_name.replace(":", "_")}.png')
            plot_metric_time_series(
                pod_data, metric_name, output_path, title, ylabel)

    print(f"\nAll visualizations saved to: {output_dir}")


def main():
    """Main entry point for visualization."""
    parser = argparse.ArgumentParser(
        description="Generate visualizations from collected metrics"
    )
    parser.add_argument(
        "metrics_dir",
        help="Directory containing collected metrics",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory for graphs (default: metrics_dir/graphs)",
    )

    args = parser.parse_args()

    if not os.path.exists(args.metrics_dir):
        sys.stderr.write(
            f"Error: Metrics directory not found: {args.metrics_dir}\n")
        sys.exit(1)

    generate_all_visualizations(args.metrics_dir, args.output_dir)


if __name__ == "__main__":
    main()

# Made with Bob
