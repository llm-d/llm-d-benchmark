"""
Process collected metrics and integrate into benchmark report.
"""

import json
import os
import re
import statistics
from typing import Any


# ---------------------------------------------------------------------------
# Time-series embedding allow-list.
# Keys must be existing TimeSeriesResourceMetrics fields, and units must satisfy
# that field's validator. Hardware fields date from 0.2; the engine and router
# fields below them were added in 0.2.1 (see _V0_2_TIME_SERIES_FIELDS).
# Spec is either {"metric": <prometheus name>} or {"ratio": (num, den)}.
# ---------------------------------------------------------------------------
_EMBED_TIME_SERIES: dict[str, dict[str, Any]] = {
    "kv_cache_usage": {
        "metric": "vllm:kv_cache_usage_perc",
        "units": "fraction",
    },
    "gpu_cache_usage": {
        "metric": "vllm:gpu_cache_usage_perc",
        "units": "fraction",
    },
    "cpu_cache_usage": {
        "metric": "vllm:cpu_cache_usage_perc",
        "units": "fraction",
    },
    "gpu_memory_usage": {
        "metric": "vllm:gpu_memory_usage_bytes",
        "units": "bytes",
    },
    "cpu_memory_usage": {
        "metric": "vllm:cpu_memory_usage_bytes",
        "units": "bytes",
    },
    "gpu_utilization": {
        "metric": "DCGM_FI_DEV_GPU_UTIL",
        "units": "percent",
    },
    "power_consumption": {
        "metric": "DCGM_FI_DEV_POWER_USAGE",
        "units": "Watts",
    },
    # Engine scheduling / queue depth
    "num_requests_running": {
        "metric": "vllm:num_requests_running",
        "units": "count",
    },
    "num_requests_waiting": {
        "metric": "vllm:num_requests_waiting",
        "units": "count",
    },
    "num_preemptions": {
        "metric": "vllm:num_preemptions_total",
        "units": "count",
    },
    # Prefix cache effectiveness. vLLM v1 exposes only the counters, so the hit
    # rates are derived; compute_ratio_series emits percent, not fraction.
    "prefix_cache_queries": {
        "metric": "vllm:prefix_cache_queries_total",
        "units": "count",
    },
    "prefix_cache_hits": {
        "metric": "vllm:prefix_cache_hits_total",
        "units": "count",
    },
    "prefix_cache_hit_rate": {
        "ratio": ("vllm:prefix_cache_hits_total", "vllm:prefix_cache_queries_total"),
        "units": "percent",
    },
    "external_prefix_cache_queries": {
        "metric": "vllm:external_prefix_cache_queries_total",
        "units": "count",
    },
    "external_prefix_cache_hits": {
        "metric": "vllm:external_prefix_cache_hits_total",
        "units": "count",
    },
    "external_prefix_cache_hit_rate": {
        "ratio": (
            "vllm:external_prefix_cache_hits_total",
            "vllm:external_prefix_cache_queries_total",
        ),
        "units": "percent",
    },
    # KV offload transfer counters. One metric per direction, distinguished only
    # by transfer_type, so each needs a label selector
    "kv_offload_store_bytes": {
        "metric": "vllm:kv_offload_total_bytes_total",
        "labels": {"transfer_type": "GPU_to_CPU"},
        "units": "bytes",
    },
    "kv_offload_load_bytes": {
        "metric": "vllm:kv_offload_total_bytes_total",
        "labels": {"transfer_type": "CPU_to_GPU"},
        "units": "bytes",
    },
    "kv_offload_store_time": {
        "metric": "vllm:kv_offload_total_time_total",
        "labels": {"transfer_type": "GPU_to_CPU"},
        "units": "s",
    },
    "kv_offload_load_time": {
        "metric": "vllm:kv_offload_total_time_total",
        "labels": {"transfer_type": "CPU_to_GPU"},
        "units": "s",
    },
    # Token throughput counters
    "prompt_tokens": {
        "metric": "vllm:prompt_tokens_total",
        "units": "count",
    },
    "generation_tokens": {
        "metric": "vllm:generation_tokens_total",
        "units": "count",
    },
    # Router / endpoint-picker pool state
    "pool_avg_kv_cache_utilization": {
        "metric": "inference_pool_average_kv_cache_utilization",
        "units": "fraction",
    },
    "pool_avg_queue_size": {
        "metric": "inference_pool_average_queue_size",
        "units": "count",
    },
    "pool_avg_running_requests": {
        "metric": "inference_pool_average_running_requests",
        "units": "count",
    },
    "pool_ready_pods": {
        "metric": "inference_pool_ready_pods",
        "units": "count",
    },
}

_DEFAULT_TS_MAX_POINTS = 256

# Fields above date from 0.2; the rest were added in 0.2.1. The converters
# declare 0.2.1, but a report converted before that revision declares 0.2 and
# is bumped when it embeds any of the later fields, so the version it declares
# stays accurate for readers that predate 0.2.1.
_V0_2_TIME_SERIES_FIELDS = frozenset(
    (
        "kv_cache_usage",
        "gpu_cache_usage",
        "cpu_cache_usage",
        "gpu_memory_usage",
        "cpu_memory_usage",
        "storage_usage",
        "gpu_utilization",
        "cpu_utilization",
        "power_consumption",
    )
)


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"LLMDBENCH_{name}", os.environ.get(name, default))


def _embed_time_series_enabled() -> bool:
    return (_env("METRICS_EMBED_TIME_SERIES", "true") or "true").lower() != "false"


def _embed_time_series_max_points() -> int:
    try:
        return int(_env("METRICS_TS_MAX_POINTS", str(_DEFAULT_TS_MAX_POINTS)))
    except (TypeError, ValueError):
        return _DEFAULT_TS_MAX_POINTS


def _embed_time_series_specs() -> dict[str, dict[str, Any]]:
    override = _env("METRICS_EMBED_TIME_SERIES_SPEC")
    if override:
        try:
            parsed = json.loads(override)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return _EMBED_TIME_SERIES


# ---------------------------------------------------------------------------
# Known metrics
# Maps prometheus metric name -> (report key, units string)
# ---------------------------------------------------------------------------
KNOWN_METRICS: dict[str, tuple[str, str]] = {
    # Cache
    "vllm:kv_cache_usage_perc": (
        "vllm_kv_cache_usage_perc",
        "fraction",
    ),
    # Queue / scheduling
    "vllm:num_requests_running": (
        "vllm_num_requests_running",
        "count",
    ),
    "vllm:num_requests_waiting": (
        "vllm_num_requests_waiting",
        "count",
    ),
    "vllm:num_preemptions_total": (
        "vllm_num_preemptions_total",
        "count",
    ),
    # Prefix cache counters
    "vllm:prefix_cache_hits_total": (
        "vllm_prefix_cache_hits_total",
        "tokens",
    ),
    "vllm:prefix_cache_queries_total": (
        "vllm_prefix_cache_queries_total",
        "tokens",
    ),
    "vllm:external_prefix_cache_hits_total": (
        "vllm_external_prefix_cache_hits_total",
        "tokens",
    ),
    "vllm:external_prefix_cache_queries_total": (
        "vllm_external_prefix_cache_queries_total",
        "tokens",
    ),
    # Computed ratio metrics (produced by process_metrics.py)
    "vllm:prefix_cache_hit_rate": (
        "vllm_prefix_cache_hit_rate",
        "percent",
    ),
    "vllm:external_prefix_cache_hit_rate": (
        "vllm_external_prefix_cache_hit_rate",
        "percent",
    ),
    # NIXL KV transfer
    "vllm:nixl_xfer_time_seconds_sum": (
        "vllm_nixl_xfer_time_seconds_sum",
        "seconds",
    ),
    "vllm:nixl_xfer_time_seconds_count": (
        "vllm_nixl_xfer_time_seconds_count",
        "count",
    ),
    "vllm:nixl_bytes_transferred_sum": (
        "vllm_nixl_bytes_transferred_sum",
        "bytes",
    ),
    "vllm:nixl_bytes_transferred_count": (
        "vllm_nixl_bytes_transferred_count",
        "count",
    ),
    # EPP (inference scheduler) Prometheus metrics — pool-level gauges
    "inference_pool_average_kv_cache_utilization": (
        "epp_pool_avg_kv_cache_utilization",
        "fraction",
    ),
    "inference_pool_average_queue_size": (
        "epp_pool_avg_queue_size",
        "count",
    ),
    "inference_pool_average_running_requests": (
        "epp_pool_avg_running_requests",
        "count",
    ),
    "inference_pool_ready_pods": (
        "epp_pool_ready_pods",
        "count",
    ),
}

# EPP log-derived metrics: summary_key -> (report_key, default_units, per_component)
_EPP_METRICS: dict[str, tuple[str, str, bool]] = {
    "dispatch_latency": (
        "epp_dispatch_latency",
        "seconds",
        False,
    ),
    "endpoint_scores": (
        "epp_endpoint_scores",
        "score",
        True,
    ),
    "request_distribution": (
        "epp_request_distribution",
        "count",
        True,
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_role(pod_name: str) -> str:
    """Detect component role from pod name."""
    lower = pod_name.lower()
    if "prefill" in lower:
        return "prefill"
    if "decode" in lower:
        return "decode"
    return "replica"


def _component_id(role: str) -> str:
    """Return a component_id string from role."""
    if role in ("prefill", "decode"):
        return f"{role}-engine"
    return "inference-engine"


def _load_json(filepath: str) -> dict[str, Any]:
    """Load a JSON file, returning {} if it doesn't exist."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, "r") as f:
        return json.load(f)


def _make_stats_dict(metric_data: dict[str, Any], units: str) -> dict[str, Any]:
    """Build a statistics dict from a metric_data entry."""
    return {
        "mean": metric_data.get("mean", 0.0),
        "p50": metric_data.get("p50", 0.0),
        "p99": metric_data.get("p99", 0.0),
        "stddev": metric_data.get("stddev", 0.0),
        "units": units,
    }


def _load_time_series_metrics(metrics_dir: str) -> list[str]:
    """Load configured metric names, with legacy defaults for older results."""
    value = _load_json(
        os.path.join(metrics_dir, "processed", "time_series_metrics.json")
    )
    if not isinstance(value, list):
        return list(KNOWN_METRICS)
    return [name for name in value if isinstance(name, str) and name]


def _metric_metadata(
    prom_name: str, metrics_summary: dict[str, Any]
) -> tuple[str, str]:
    """Return report metadata, deriving sensible values for custom metrics."""
    if prom_name in KNOWN_METRICS:
        return KNOWN_METRICS[prom_name]

    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", prom_name).strip("_")
    units = ""
    for pod_name, pod_data in metrics_summary.items():
        if pod_name.startswith("_"):
            continue
        metric_data = pod_data.get("metrics", {}).get(prom_name, {})
        if metric_data:
            units = metric_data.get("unit", "")
            break
    return safe_name, units


# process_metrics.py converts these to GB/MB and renames them; the raw scrapes
# carry the original name in bytes.
_CONVERTED_METRICS: dict[str, tuple[str, int]] = {
    "container_memory_usage_gb": ("container_memory_usage_bytes", 1024**3),
    "container_memory_working_set_gb": ("container_memory_working_set_bytes", 1024**3),
    "container_network_receive_mb_total": (
        "container_network_receive_bytes_total",
        1024**2,
    ),
    "container_network_transmit_mb_total": (
        "container_network_transmit_bytes_total",
        1024**2,
    ),
}

# Rates derived from a pair of counters: metric -> (numerator, denominator)
_RATIO_METRICS: dict[str, tuple[str, str]] = {
    "vllm:prefix_cache_hit_rate": (
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
    ),
    "vllm:external_prefix_cache_hit_rate": (
        "vllm:external_prefix_cache_hits_total",
        "vllm:external_prefix_cache_queries_total",
    ),
}


def _percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile, as process_metrics.py computes it."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, n - 1)
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def _stats_from_values(
    values: list[float], units: str, mean: float | None = None
) -> dict[str, Any]:
    """Statistics over *values*; *mean* overrides the plain average."""
    sorted_values = sorted(values)
    return {
        "mean": statistics.mean(values) if mean is None else mean,
        "p50": _percentile(sorted_values, 50),
        "p99": _percentile(sorted_values, 99),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "units": units,
    }


def _build_statistics_from_scrapes(
    metrics_summary: dict[str, Any],
    metric_names: list[str],
    metrics_dir: str,
    window: tuple[Any, Any] | None,
) -> dict[str, Any] | None:
    """Per-metric statistics from the raw scrapes, clipped to *window*.

    The same points the embedded time series are built from, so the series and
    the statistics in one report cover the same interval. A hit rate's mean is
    the hits gained over the queries gained across the interval, so busy
    intervals weigh more than quiet ones; its percentiles are over the
    per-scrape-interval rates. Returns None when there are no raw scrapes.
    """
    from .timeseries import (
        clip_to_window,
        collect_time_series_data,
        compute_ratio_series,
        ratio_totals,
    )

    pod_data = collect_time_series_data(metrics_dir)
    if not pod_data:
        return None

    aggregate_names = set(metrics_summary.get("_aggregated", {}).get("metrics", {}))
    entries: dict[str, dict] = {}
    pooled: dict[str, tuple[list[float], list[float]]] = {}

    for pod_name in sorted(pod_data):
        pod_metrics = pod_data[pod_name]
        role = _detect_role(pod_name)
        for prom_name in metric_names:
            ratio = _RATIO_METRICS.get(prom_name)
            totals = None
            if ratio:
                # clip first, or the first delta includes the stage before
                clipped = {
                    name: clip_to_window(pod_metrics.get(name, []), window)
                    for name in ratio
                }
                values = [v for _, v in compute_ratio_series(clipped, *ratio)]
                totals = ratio_totals(clipped, *ratio)
            else:
                raw_name, divisor = _CONVERTED_METRICS.get(prom_name, (prom_name, 1))
                values = [
                    v / divisor
                    for _, v in clip_to_window(pod_metrics.get(raw_name, []), window)
                ]
            if not values:
                continue

            report_key, units = _metric_metadata(prom_name, metrics_summary)
            mean = totals[0] / totals[1] * 100 if totals and totals[1] > 0 else None
            entries.setdefault(report_key, {"components": []})["components"].append(
                {
                    "component_id": _component_id(role),
                    "pod": pod_name,
                    "role": role,
                    "statistics": _stats_from_values(values, units, mean),
                }
            )
            if prom_name in aggregate_names:
                pool_values, pool_totals = pooled.setdefault(
                    prom_name, ([], [0.0, 0.0])
                )
                pool_values.extend(values)
                if totals:
                    pool_totals[0] += totals[0]
                    pool_totals[1] += totals[1]

    for prom_name, (values, (num, den)) in pooled.items():
        report_key, units = _metric_metadata(prom_name, metrics_summary)
        mean = num / den * 100 if prom_name in _RATIO_METRICS and den > 0 else None
        entries[report_key]["aggregated"] = _stats_from_values(values, units, mean)

    return entries


def _build_embedded_time_series(
    obs: dict[str, Any],
    metrics_dir: str,
    max_points: int,
    window: tuple[Any, Any] | None = None,
) -> tuple[set[str], dict[str, Any]]:
    """Populate `observability.components[].time_series`, one entry per pod.

    Returns the field names to declare a version against, plus the clip outcome:
    how many points survived, how many were available before clipping, and the
    span the scrapes actually cover. An empty clip is otherwise indistinguishable
    from a stage that genuinely had no metrics.
    """
    from .timeseries import (
        clip_to_window,
        collect_time_series_data,
        compute_ratio_series,
        series_key,
        series_points,
    )

    specs = _embed_time_series_specs()
    label_names = frozenset(
        name for spec in specs.values() for name in (spec.get("labels") or {})
    )
    pod_data = collect_time_series_data(metrics_dir, label_names)
    if not pod_data:
        return set(), {"datapoints": 0, "datapoints_available": 0}

    components = obs.setdefault("components", [])
    by_replica = {c.get("replica_id"): c for c in components}
    embedded: set[str] = set()
    # Pre-clip field set: keying the version bump off the clipped one would make
    # sibling reports in one sweep declare different versions.
    available: set[str] = set()
    kept = total = 0
    scraped: list[Any] = []

    for pod_name in sorted(pod_data):
        pod_metrics = pod_data[pod_name]
        series_by_field: dict[str, Any] = {}

        for field, spec in specs.items():
            ratio = spec.get("ratio")
            if ratio:
                # clip first, or the first delta includes the stage before
                clipped = {
                    name: clip_to_window(pod_metrics.get(name, []), window)
                    for name in (ratio[0], ratio[1])
                }
                points = compute_ratio_series(clipped, ratio[0], ratio[1])
            else:
                key = spec.get("metric", "")
                labels = spec.get("labels")
                if labels:
                    key = series_key(key, labels)
                points = pod_metrics.get(key, [])
            if points:
                available.add(field)
                total += len(points)
                scraped += [points[0][0], points[-1][0]]
            points = clip_to_window(points, window)
            if not points:
                continue
            kept += len(points)
            series_by_field[field] = {
                "units": spec["units"],
                "series": series_points(points, max_points),
            }

        if not series_by_field:
            continue

        role = _detect_role(pod_name)
        component = by_replica.get(pod_name)
        if component is None:
            component = {
                "component_label": _component_id(role),
                "replica_id": pod_name,
            }
            components.append(component)
            by_replica[pod_name] = component
        component.setdefault("time_series", {}).update(series_by_field)
        embedded.update(series_by_field)

    if not components:
        obs.pop("components", None)

    outcome: dict[str, Any] = {"datapoints": kept, "datapoints_available": total}
    if scraped:
        outcome["scraped_from"] = min(scraped).isoformat()
        outcome["scraped_to"] = max(scraped).isoformat()
    return (available if window else embedded), outcome


# ---------------------------------------------------------------------------
# Build observability entries
# ---------------------------------------------------------------------------


def _build_per_metric_entries(
    metrics_summary: dict[str, Any],
    metric_names: list[str],
) -> dict[str, Any]:
    """Build per-metric observability entries with per-component statistics.

    Returns a dict keyed by report metric name (e.g. 'vllm_prefix_cache_hit_rate')
    with 'components' lists underneath.
    """
    entries: dict[str, dict] = {}

    for pod_name, pod_data in metrics_summary.items():
        if pod_name.startswith("_"):
            continue
        metrics = pod_data.get("metrics", {})
        role = _detect_role(pod_name)
        comp_id = _component_id(role)

        for prom_name in metric_names:
            if prom_name not in metrics:
                continue
            report_key, units = _metric_metadata(prom_name, metrics_summary)

            component_entry = {
                "component_id": comp_id,
                "pod": pod_name,
                "role": role,
                "statistics": _make_stats_dict(metrics[prom_name], units),
            }

            if report_key not in entries:
                entries[report_key] = {"components": []}
            entries[report_key]["components"].append(component_entry)

    return entries


def _build_aggregated_entries(
    metrics_summary: dict[str, Any],
    obs: dict[str, Any],
    metric_names: list[str],
) -> None:
    """Add cluster-wide aggregated stats to existing observability entries."""
    aggregated = metrics_summary.get("_aggregated", {}).get("metrics", {})
    for prom_name in metric_names:
        if prom_name not in aggregated:
            continue
        report_key, units = _metric_metadata(prom_name, metrics_summary)
        entry = obs.setdefault(report_key, {})
        entry["aggregated"] = _make_stats_dict(aggregated[prom_name], units)


def _build_epp_entries(
    epp_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build EPP log-derived metric entries for observability section."""
    entries: dict[str, Any] = {}

    for summary_key, (
        report_key,
        default_units,
        per_component,
    ) in _EPP_METRICS.items():
        data = epp_summary.get(summary_key)
        if not data:
            continue

        if per_component and isinstance(data, dict):
            components = [
                {
                    "component_id": comp_id,
                    "statistics": _make_stats_dict(
                        comp_data, comp_data.get("unit", default_units)
                    ),
                }
                for comp_id, comp_data in data.items()
                if isinstance(comp_data, dict)
            ]
            if components:
                entries[report_key] = {"components": components}
        elif isinstance(data, dict):
            entries[report_key] = {
                "statistics": _make_stats_dict(data, data.get("unit", default_units)),
            }

    # Plugin latencies (dynamic keys)
    for plugin_type, plugins in epp_summary.get("plugin_latencies", {}).items():
        for plugin_name, latency_data in plugins.items():
            key = f"epp_plugin_{plugin_type}_{plugin_name}".replace("/", "_").replace(
                "-", "_"
            )
            entries[key] = {
                "statistics": _make_stats_dict(
                    latency_data, latency_data.get("unit", "seconds")
                ),
            }

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def add_metrics_to_benchmark_report(
    br_dict: dict[str, Any],
    metrics_dir: str,
    component_label: str = "vllm-service",
    time_series_window: tuple[Any, Any] | None = None,
) -> dict[str, Any]:
    """Add metrics to an existing benchmark report dictionary.

    Populates per-metric entries (e.g. results.observability.vllm_kv_cache_usage_perc)
    with per-component statistics, role, and EPP metrics.

    ``time_series_window`` restricts the embedded series and the scalar
    statistics to one stage's interval; both are computed from the same raw
    scrapes. Without raw scrapes the statistics fall back to the run-level
    ``metrics_summary.json``. ``observability.time_series_interval`` records the
    interval, the statistics' scope, and how many points survived the clip so
    an empty series is never mistaken for a quiet stage.
    """
    obs = br_dict.setdefault("results", {}).setdefault("observability", {})

    # Remove legacy components/aggregate structure if present
    obs.pop("components", None)

    # Per-metric entries from vLLM and EPP Prometheus scrapes
    metrics_summary = _load_json(
        os.path.join(metrics_dir, "processed", "metrics_summary.json")
    )
    # Recorded on every path: absence would otherwise be ambiguous between
    # embedding disabled, no metrics, and a report predating the field.
    start, end = time_series_window or (None, None)
    interval: dict[str, Any] = {
        "scope": "stage" if time_series_window else "run",
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "statistics_scope": "run",
    }
    obs["time_series_interval"] = interval

    if metrics_summary:
        metric_names = _load_time_series_metrics(metrics_dir)
        scraped = _build_statistics_from_scrapes(
            metrics_summary, metric_names, metrics_dir, time_series_window
        )
        if scraped is not None:
            obs.update(scraped)
            if time_series_window:
                interval["statistics_scope"] = "stage"
        else:
            # No raw scrapes to clip: fall back to the run-level summary.
            obs.update(_build_per_metric_entries(metrics_summary, metric_names))
            _build_aggregated_entries(metrics_summary, obs, metric_names)
        if _embed_time_series_enabled():
            embedded, outcome = _build_embedded_time_series(
                obs, metrics_dir, _embed_time_series_max_points(), time_series_window
            )
            if embedded - _V0_2_TIME_SERIES_FIELDS and br_dict.get("version") == "0.2":
                br_dict["version"] = "0.2.1"
            interval.update(outcome)
        else:
            interval["scope"] = "disabled"
    else:
        interval["scope"] = "unavailable"

    # EPP log-derived metrics
    epp_summary = _load_json(os.path.join(metrics_dir, "epp_metrics_summary.json"))
    if epp_summary:
        obs.update(_build_epp_entries(epp_summary))

    # Replica status
    replica_status = _load_json(
        os.path.join(metrics_dir, "processed", "replica_status.json")
    )
    if replica_status.get("controllers"):
        # Full time series stays in replica_status_timeseries.json, to keep the
        # report small.
        obs["replica_status"] = replica_status

    # Pod startup times
    startup_times = _load_json(
        os.path.join(metrics_dir, "processed", "pod_startup_times.json")
    )
    if startup_times.get("pods"):
        obs["pod_startup_times"] = startup_times

    return br_dict
