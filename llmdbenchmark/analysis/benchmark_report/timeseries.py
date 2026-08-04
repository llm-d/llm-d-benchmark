"""Reconstruct per-pod metric time series from raw Prometheus scrapes.

Duplicates the parsing in visualize_metrics.py rather than importing it: the image
copies that module to /usr/local/bin, outside this package.
"""

import glob
import os
import re
from datetime import datetime
from typing import Any

_METRIC_RE = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*(?:\{[^}]*\})?) ([\d.eE+-]+)")


def _parse_scrape(file_path: str) -> tuple[str | None, dict[str, list]]:
    """Parse one raw scrape file into (pod_name, {metric: [(datetime, value), ...]})."""
    metrics: dict[str, list] = {}
    timestamp_dt = None
    pod_name = None
    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# Timestamp:"):
                ts = line.split(":", 1)[1].strip()
                try:
                    timestamp_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass
                continue
            if line.startswith("# Pod:"):
                pod_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("#") or not line:
                continue
            match = _METRIC_RE.match(line)
            if match and timestamp_dt:
                base_name = match.group(1).split("{")[0]
                metrics.setdefault(base_name, []).append(
                    (timestamp_dt, float(match.group(2)))
                )
    return pod_name, metrics


def collect_time_series_data(metrics_dir: str) -> dict[str, dict[str, list]]:
    """Return {pod_name: {metric_name: [(datetime, value), ...]}} sorted by time."""
    raw_dir = os.path.join(metrics_dir, "raw")
    pod_data: dict[str, dict[str, list]] = {}
    for file_path in glob.glob(os.path.join(raw_dir, "*.log")):
        pod_name, metrics = _parse_scrape(file_path)
        if not pod_name:
            continue
        pod = pod_data.setdefault(pod_name, {})
        for metric_name, points in metrics.items():
            pod.setdefault(metric_name, []).extend(points)
    for pod in pod_data.values():
        for metric_name in pod:
            pod[metric_name].sort(key=lambda x: x[0])
    return pod_data


def compute_ratio_series(
    pod_metrics: dict[str, list], numerator: str, denominator: str
) -> list[tuple[datetime, float]]:
    """Per-pod ratio (numerator/denominator*100) over shared timestamps.

    No default allow-list entry uses this; it serves "ratio" specs supplied via
    METRICS_EMBED_TIME_SERIES_SPEC.
    """
    if numerator not in pod_metrics or denominator not in pod_metrics:
        return []
    num_by_ts = {ts: val for ts, val in pod_metrics[numerator]}
    den_by_ts = {ts: val for ts, val in pod_metrics[denominator]}
    common_ts = sorted(set(num_by_ts) & set(den_by_ts))
    return [
        (ts, (num_by_ts[ts] / den_by_ts[ts] * 100) if den_by_ts[ts] > 0 else 0.0)
        for ts in common_ts
    ]


def downsample(points: list, max_points: int) -> list:
    """Uniform-stride decimation to at most max_points, keeping first and last."""
    n = len(points)
    if max_points <= 0 or n <= max_points:
        return points
    stride = (n - 1) / (max_points - 1)
    idx = sorted({round(i * stride) for i in range(max_points)} | {0, n - 1})
    return [points[i] for i in idx]


def series_points(points: list, max_points: int) -> list[dict[str, Any]]:
    """Convert [(datetime, value), ...] to [{"ts": iso8601, "value": float}, ...]."""
    return [
        {"ts": ts.isoformat(), "value": float(val)}
        for ts, val in downsample(points, max_points)
    ]
