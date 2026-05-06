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


# Metrics that should include an aggregated mean line across pods
AGGREGATE_METRICS = {
    'vllm:kv_cache_usage_perc', 'vllm:num_requests_running',
    'vllm:num_requests_waiting', 'vllm:num_preemptions_total',
}

# Metrics to visualize: prometheus_name -> (title, ylabel)
METRICS_TO_PLOT = {
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
    'vllm:prefix_cache_hits_total': ('Prefix Cache Hits', 'Tokens'),
    'vllm:prefix_cache_queries_total': ('Prefix Cache Queries', 'Tokens'),
    'vllm:external_prefix_cache_hits_total': ('External Prefix Cache Hits (Cross-Instance)', 'Tokens'),
    'vllm:external_prefix_cache_queries_total': ('External Prefix Cache Queries (Cross-Instance)', 'Tokens'),
    'vllm:nixl_xfer_time_seconds_sum': ('NIXL KV Transfer Time', 'Time (s)'),
    'vllm:nixl_xfer_time_seconds_count': ('NIXL KV Transfer Count', 'Count'),
    'vllm:nixl_bytes_transferred_sum': ('NIXL Bytes Transferred', 'Bytes'),
    'vllm:nixl_bytes_transferred_count': ('NIXL Transfers Count', 'Count'),
    'vllm:num_preemptions_total': ('Request Preemptions', 'Count'),
    # EPP (inference scheduler) pool-level metrics
    'inference_pool_average_kv_cache_utilization': ('EPP Pool Avg KV Cache Utilization', 'Utilization (%)'),
    'inference_pool_average_queue_size': ('EPP Pool Avg Queue Size', 'Count'),
    'inference_pool_average_running_requests': ('EPP Pool Avg Running Requests', 'Count'),
    'inference_pool_ready_pods': ('EPP Pool Ready Pods', 'Count'),
}

# Computed ratio metrics: (numerator, denominator, title, ylabel, output_name)
RATIO_METRICS = [
    ('vllm:prefix_cache_hits_total', 'vllm:prefix_cache_queries_total',
     'Prefix Cache Hit Rate', 'Hit Rate (%)', 'vllm_prefix_cache_hit_rate'),
    ('vllm:external_prefix_cache_hits_total', 'vllm:external_prefix_cache_queries_total',
     'External Prefix Cache Hit Rate (Cross-Instance)', 'Hit Rate (%)', 'vllm_external_prefix_cache_hit_rate'),
]


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def parse_prometheus_metrics_with_timestamp(file_path):
    """Parse Prometheus metrics from a file with timestamps.

    Returns:
        Tuple of (timestamp_str, pod_name, metrics_dict)
        where metrics_dict maps metric_name -> [(datetime, value), ...]
    """
    metrics = {}
    timestamp_str = None
    timestamp_dt = None
    pod_name = None

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            if line.startswith('# Timestamp:'):
                timestamp_str = line.split(':', 1)[1].strip()
                try:
                    timestamp_dt = datetime.fromisoformat(
                        timestamp_str.replace('Z', '+00:00'))
                except ValueError:
                    pass
            elif line.startswith('# Pod:'):
                pod_name = line.split(':', 1)[1].strip()

            if line.startswith('#') or not line:
                continue

            match = re.match(
                r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)', line)
            if match and timestamp_dt:
                base_name = match.group(1).split('{')[0]
                value = float(match.group(2))
                metrics.setdefault(base_name, []).append((timestamp_dt, value))

    return timestamp_str, pod_name, metrics


def collect_time_series_data(metrics_dir):
    """Collect time series data from all metric files.

    Returns:
        Dictionary mapping pod names to their time series data:
        {pod_name: {metric_name: [(datetime, value), ...]}}
    """
    raw_dir = os.path.join(metrics_dir, 'raw')
    pod_data = {}

    for file_path in glob.glob(os.path.join(raw_dir, '*.log')):
        _, pod_name, metrics = parse_prometheus_metrics_with_timestamp(file_path)

        if pod_name:
            pod = pod_data.setdefault(pod_name, {})
            for metric_name, data_points in metrics.items():
                pod.setdefault(metric_name, []).extend(data_points)

    # Sort by timestamp
    for pod in pod_data.values():
        for metric_name in pod:
            pod[metric_name].sort(key=lambda x: x[0])

    return pod_data


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def _save_plot(fig, output_path):
    """Format axes and save a plot to disk."""
    for ax in fig.axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"Saved plot: {output_path}")


def _parse_ts(ts_str):
    """Parse an ISO timestamp string to datetime, or None."""
    try:
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Plot functions
# ---------------------------------------------------------------------------

def plot_metric_time_series(pod_data, metric_name, output_path,
                            title=None, ylabel=None, show_aggregate=False):
    """Plot time series for a specific metric across all pods.

    Args:
        pod_data: Time series data for all pods
        metric_name: Name of metric to plot
        output_path: Path to save the plot
        title: Plot title (optional)
        ylabel: Y-axis label (optional)
        show_aggregate: If True, add a dashed mean line across all pods
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    ts_values = {} if show_aggregate else None

    for pod_name, metrics in pod_data.items():
        if metric_name not in metrics:
            continue
        timestamps, values = zip(*metrics[metric_name])
        alpha = 0.5 if show_aggregate else 1.0
        ax.plot(timestamps, values, label=pod_name,
                marker='o', markersize=3, alpha=alpha)
        if show_aggregate:
            for ts, val in metrics[metric_name]:
                ts_values.setdefault(ts, []).append(val)

    if show_aggregate and ts_values:
        sorted_ts = sorted(ts_values.keys())
        agg_means = [sum(ts_values[ts]) / len(ts_values[ts]) for ts in sorted_ts]
        ax.plot(sorted_ts, agg_means, label='Aggregated (mean)',
                color='black', linewidth=2, linestyle='--', marker='s',
                markersize=4)

    ax.set_xlabel('Time')
    ax.set_ylabel(ylabel or metric_name)
    ax.set_title(title or f'{metric_name} Over Time')
    ax.legend()
    ax.grid(True, alpha=0.3)
    _save_plot(fig, output_path)


def plot_pod_startup_times(metrics_dir, output_path):
    """Scatter plot of pod startup times.

    X-axis: ready_timestamp (when the pod became ready)
    Y-axis: startup_seconds (how long it took)
    """
    if not MATPLOTLIB_AVAILABLE:
        return

    startup_file = os.path.join(metrics_dir, 'processed', 'pod_startup_times.json')
    if not os.path.exists(startup_file):
        return

    with open(startup_file) as f:
        data = json.load(f)

    pods = data.get('pods', [])
    if not pods:
        return

    points = []
    for pod in pods:
        ts = _parse_ts(pod.get('ready_timestamp', ''))
        secs = pod.get('startup_seconds')
        if ts and secs is not None:
            points.append((ts, secs))

    if not points:
        return

    timestamps, startup_secs = zip(*points)

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.scatter(timestamps, startup_secs, s=80, c='steelblue',
               edgecolors='black', linewidths=0.5, zorder=5)

    agg = data.get('aggregate', {})
    if agg.get('mean'):
        ax.axhline(y=agg['mean'], color='orange', linestyle='--',
                   linewidth=1.5, label=f"Mean: {agg['mean']:.1f}s")
    if agg.get('p99'):
        ax.axhline(y=agg['p99'], color='red', linestyle='--',
                   linewidth=1, label=f"P99: {agg['p99']:.1f}s")
    if agg.get('mean') or agg.get('p99'):
        ax.legend()

    ax.set_xlabel('Time (pod became Ready)')
    ax.set_ylabel('Startup Time (seconds)')
    ax.set_title('Pod Startup Times')
    ax.grid(True, alpha=0.3)
    _save_plot(fig, output_path)


def plot_replica_status(metrics_dir, output_path):
    """Line plot of replica counts over time, one line per role."""
    if not MATPLOTLIB_AVAILABLE:
        return

    ts_file = os.path.join(metrics_dir, 'processed', 'replica_status_timeseries.json')
    if not os.path.exists(ts_file):
        return

    with open(ts_file) as f:
        ts_data = json.load(f)

    snapshots = ts_data.get('snapshots', [])
    if len(snapshots) < 2:
        return

    # Build per-role time series
    role_series = {}
    for snap in snapshots:
        ts = _parse_ts(snap.get('timestamp', ''))
        if not ts:
            continue
        role_counts = {}
        for ctrl in snap.get('controllers', []):
            role = ctrl.get('role', 'unknown')
            role_counts[role] = role_counts.get(role, 0) + ctrl.get('ready_replicas', 0)
        for role, count in role_counts.items():
            role_series.setdefault(role, []).append((ts, count))

    if not role_series:
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    for role, points in sorted(role_series.items()):
        points.sort(key=lambda x: x[0])
        timestamps, counts = zip(*points)
        ax.plot(timestamps, counts, label=role, marker='o', markersize=3)

    ax.set_xlabel('Time')
    ax.set_ylabel('Ready Replicas')
    ax.set_title('Replica Count Over Time')
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.yaxis.get_major_locator().set_params(integer=True)
    _save_plot(fig, output_path)


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def generate_all_visualizations(metrics_dir, output_dir=None, context=None):
    """Generate visualizations for all collected metrics.

    Returns the number of plots generated (0 when matplotlib is missing or
    no data is found).
    """
    if not MATPLOTLIB_AVAILABLE:
        print("Error: matplotlib is required for visualization")
        return 0

    if output_dir is None:
        output_dir = os.path.join(metrics_dir, 'graphs')
    os.makedirs(output_dir, exist_ok=True)

    print("Collecting time series data...")
    pod_data = collect_time_series_data(metrics_dir)

    if not pod_data:
        print("No metrics data found")
        return 0

    plot_count = 0

    # Ratio metrics (computed from pairs of counters)
    for numerator, denominator, title, ylabel, output_name in RATIO_METRICS:
        ratio_data = {}
        for pod_name, metrics in pod_data.items():
            if numerator in metrics and denominator in metrics:
                hits_by_ts = {ts: val for ts, val in metrics[numerator]}
                queries_by_ts = {ts: val for ts, val in metrics[denominator]}
                common_ts = sorted(set(hits_by_ts) & set(queries_by_ts))
                ratio_points = [
                    (ts, (hits_by_ts[ts] / queries_by_ts[ts] * 100)
                     if queries_by_ts[ts] > 0 else 0.0)
                    for ts in common_ts
                ]
                if ratio_points:
                    ratio_data[pod_name] = {output_name: ratio_points}
        if ratio_data:
            plot_metric_time_series(
                ratio_data, output_name,
                os.path.join(output_dir, f'{output_name}.png'),
                title, ylabel)
            plot_count += 1

    # Standard time series plots
    for metric_name, (title, ylabel) in METRICS_TO_PLOT.items():
        has_metric = any(metric_name in m for m in pod_data.values())
        if has_metric:
            safe_name = metric_name.replace(':', '_')
            plot_metric_time_series(
                pod_data, metric_name,
                os.path.join(output_dir, f'{safe_name}.png'),
                title, ylabel,
                show_aggregate=(metric_name in AGGREGATE_METRICS))
            plot_count += 1

    # Infrastructure plots
    plot_pod_startup_times(
        metrics_dir, os.path.join(output_dir, 'pod_startup_times.png'))
    plot_count += 1
    plot_replica_status(
        metrics_dir, os.path.join(output_dir, 'replica_status.png'))
    plot_count += 1

    print(f"\nAll visualizations saved to: {output_dir}")
    return plot_count


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
