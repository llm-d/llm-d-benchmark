#!/usr/bin/env python3
"""Generate visualizations from collected metrics.

This module creates time series graphs for Prometheus metrics collected
during benchmarking.  It can be called programmatically via
:func:`generate_all_visualizations` or as a standalone CLI.

Matplotlib is an optional dependency.  When it is not installed the
functions return early with a log message rather than raising an error.
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext

try:
    import matplotlib
    matplotlib.use("Agg")  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Prometheus metric parsing
# ---------------------------------------------------------------------------


def parse_prometheus_metrics_with_timestamp(
    file_path: str,
) -> tuple[str | None, str | None, dict[str, list[tuple[datetime, float]]]]:
    """Parse Prometheus metrics from a file with timestamps.

    Returns:
        Tuple of (timestamp_str, pod_name, metrics_dict).
    """
    metrics: dict[str, list[tuple[datetime, float]]] = {}
    timestamp_str = None
    timestamp_dt = None
    pod_name = None

    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()

            if line.startswith("# Timestamp:"):
                timestamp_str = line.split(":", 1)[1].strip()
                try:
                    timestamp_dt = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            if line.startswith("# Pod:"):
                pod_name = line.split(":", 1)[1].strip()

            if line.startswith("#") or not line:
                continue

            match = re.match(
                r"([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)", line
            )
            if match and timestamp_dt:
                metric_name = match.group(1)
                value = float(match.group(2))
                base_name = metric_name.split("{")[0]

                if base_name not in metrics:
                    metrics[base_name] = []
                metrics[base_name].append((timestamp_dt, value))

    return timestamp_str, pod_name, metrics


def collect_time_series_data(
    metrics_dir: str,
) -> dict[str, dict[str, list[tuple[datetime, float]]]]:
    """Collect time series data from all metric files in *metrics_dir*/raw/."""
    raw_dir = os.path.join(metrics_dir, "raw")
    pod_data: dict[str, dict[str, list[tuple[datetime, float]]]] = {}

    for file_path in glob.glob(os.path.join(raw_dir, "*.txt")):
        _, pod_name, metrics = parse_prometheus_metrics_with_timestamp(file_path)

        if pod_name:
            if pod_name not in pod_data:
                pod_data[pod_name] = {}

            for metric_name, data_points in metrics.items():
                if metric_name not in pod_data[pod_name]:
                    pod_data[pod_name][metric_name] = []
                pod_data[pod_name][metric_name].extend(data_points)

    # Sort by timestamp
    for pod_name in pod_data:
        for metric_name in pod_data[pod_name]:
            pod_data[pod_name][metric_name].sort(key=lambda x: x[0])

    return pod_data


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

# Default metrics to plot with (title, y-axis label)
DEFAULT_METRICS: dict[str, tuple[str, str]] = {
    "vllm:kv_cache_usage_perc": ("KV Cache Usage", "Usage (%)"),
    "vllm:gpu_cache_usage_perc": ("GPU Cache Usage", "Usage (%)"),
    "vllm:cpu_cache_usage_perc": ("CPU Cache Usage", "Usage (%)"),
    "vllm:gpu_memory_usage_bytes": ("GPU Memory Usage", "Memory (bytes)"),
    "vllm:cpu_memory_usage_bytes": ("CPU Memory Usage", "Memory (bytes)"),
    "container_memory_usage_bytes": ("Container Memory Usage", "Memory (bytes)"),
    "DCGM_FI_DEV_GPU_UTIL": ("GPU Utilization", "Utilization (%)"),
    "DCGM_FI_DEV_POWER_USAGE": ("GPU Power Usage", "Power (W)"),
    "vllm:num_requests_running": ("Running Requests", "Count"),
    "vllm:num_requests_waiting": ("Waiting Requests", "Count"),
}


def plot_metric_time_series(
    pod_data: dict[str, dict[str, list[tuple[datetime, float]]]],
    metric_name: str,
    output_path: str,
    title: str | None = None,
    ylabel: str | None = None,
) -> None:
    """Plot time series for *metric_name* across all pods."""
    if not MATPLOTLIB_AVAILABLE:
        return

    fig, ax = plt.subplots(figsize=(12, 6))

    for pod_name, metrics in pod_data.items():
        if metric_name in metrics:
            timestamps, values = zip(*metrics[metric_name])
            ax.plot(timestamps, values, label=pod_name, marker="o", markersize=3)

    ax.set_xlabel("Time")
    ax.set_ylabel(ylabel or metric_name)
    ax.set_title(title or f"{metric_name} Over Time")
    ax.legend()
    ax.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
    plt.xticks(rotation=45)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def generate_all_visualizations(
    metrics_dir: str,
    output_dir: str | None = None,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate PNG plots for all available metrics.

    Args:
        metrics_dir: Directory containing ``raw/*.txt`` metric files.
        output_dir: Where to write the PNGs (default: *metrics_dir*/graphs).
        context: Optional execution context for logging.

    Returns:
        Number of plots generated.
    """
    if not MATPLOTLIB_AVAILABLE:
        _log(context, "matplotlib not available -- skipping metric plots")
        return 0

    if output_dir is None:
        output_dir = os.path.join(metrics_dir, "graphs")

    os.makedirs(output_dir, exist_ok=True)

    pod_data = collect_time_series_data(metrics_dir)
    if not pod_data:
        _log(context, "No metrics data found for plotting")
        return 0

    generated = 0
    for metric_name, (title, ylabel) in DEFAULT_METRICS.items():
        has_metric = any(metric_name in m for m in pod_data.values())
        if has_metric:
            out_path = os.path.join(
                output_dir, f'{metric_name.replace(":", "_")}.png'
            )
            plot_metric_time_series(pod_data, metric_name, out_path, title, ylabel)
            _log(context, f"Plot saved: {Path(out_path).name}")
            generated += 1

    _log(context, f"Generated {generated} metric plot(s) in {output_dir}")
    return generated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(
    context: "ExecutionContext | None",
    message: str,
    warning: bool = False,
) -> None:
    if context:
        if warning:
            context.logger.log_warning(message)
        else:
            context.logger.log_info(message)
    else:
        import logging

        logger = logging.getLogger(__name__)
        if warning:
            logger.warning(message)
        else:
            logger.info(message)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Standalone CLI for generating metric plots."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Generate visualizations from collected metrics"
    )
    parser.add_argument("metrics_dir", help="Directory containing collected metrics")
    parser.add_argument(
        "-o",
        "--output-dir",
        help="Output directory for graphs (default: metrics_dir/graphs)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.metrics_dir):
        sys.stderr.write(f"Error: Metrics directory not found: {args.metrics_dir}\n")
        sys.exit(1)

    generate_all_visualizations(args.metrics_dir, args.output_dir)


if __name__ == "__main__":
    main()
