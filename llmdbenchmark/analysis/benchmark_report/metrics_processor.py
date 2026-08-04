"""
Process collected metrics and integrate into benchmark report.
"""

import json
import os
import re
from typing import Any


# ---------------------------------------------------------------------------
# Time-series embedding allow-list.
# Keys must be existing TimeSeriesResourceMetrics fields, and units must satisfy
# that field's validator. Hardware fields are v0.2; the engine and router fields
# below it are v0.2.1, so a report carrying them must declare that version.
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

# Fields above are v0.2 TimeSeriesResourceMetrics; the rest were introduced in
# v0.2.1. A report that embeds any of them must declare the later version, since
# v0.2 forbids extra keys on that model.
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
# Metrics that have corresponding graphs in metrics/graphs/
# Maps prometheus metric name -> (report key, units string, graph filename)
# ---------------------------------------------------------------------------
GRAPHED_METRICS: dict[str, tuple[str, str, str]] = {
    # Cache
    "vllm:kv_cache_usage_perc": (
        "vllm_kv_cache_usage_perc",
        "percent",
        "vllm_kv_cache_usage_perc.png",
    ),
    # Queue / scheduling
    "vllm:num_requests_running": (
        "vllm_num_requests_running",
        "count",
        "vllm_num_requests_running.png",
    ),
    "vllm:num_requests_waiting": (
        "vllm_num_requests_waiting",
        "count",
        "vllm_num_requests_waiting.png",
    ),
    "vllm:num_preemptions_total": (
        "vllm_num_preemptions_total",
        "count",
        "vllm_num_preemptions_total.png",
    ),
    # Prefix cache counters
    "vllm:prefix_cache_hits_total": (
        "vllm_prefix_cache_hits_total",
        "tokens",
        "vllm_prefix_cache_hits_total.png",
    ),
    "vllm:prefix_cache_queries_total": (
        "vllm_prefix_cache_queries_total",
        "tokens",
        "vllm_prefix_cache_queries_total.png",
    ),
    "vllm:external_prefix_cache_hits_total": (
        "vllm_external_prefix_cache_hits_total",
        "tokens",
        "vllm_external_prefix_cache_hits_total.png",
    ),
    "vllm:external_prefix_cache_queries_total": (
        "vllm_external_prefix_cache_queries_total",
        "tokens",
        "vllm_external_prefix_cache_queries_total.png",
    ),
    # Computed ratio metrics (produced by process_metrics.py)
    "vllm:prefix_cache_hit_rate": (
        "vllm_prefix_cache_hit_rate",
        "percent",
        "vllm_prefix_cache_hit_rate.png",
    ),
    "vllm:external_prefix_cache_hit_rate": (
        "vllm_external_prefix_cache_hit_rate",
        "percent",
        "vllm_external_prefix_cache_hit_rate.png",
    ),
    # NIXL KV transfer
    "vllm:nixl_xfer_time_seconds_sum": (
        "vllm_nixl_xfer_time_seconds_sum",
        "seconds",
        "vllm_nixl_xfer_time_seconds_sum.png",
    ),
    "vllm:nixl_xfer_time_seconds_count": (
        "vllm_nixl_xfer_time_seconds_count",
        "count",
        "vllm_nixl_xfer_time_seconds_count.png",
    ),
    "vllm:nixl_bytes_transferred_sum": (
        "vllm_nixl_bytes_transferred_sum",
        "bytes",
        "vllm_nixl_bytes_transferred_sum.png",
    ),
    "vllm:nixl_bytes_transferred_count": (
        "vllm_nixl_bytes_transferred_count",
        "count",
        "vllm_nixl_bytes_transferred_count.png",
    ),
    # EPP (inference scheduler) Prometheus metrics — pool-level gauges
    "inference_pool_average_kv_cache_utilization": (
        "epp_pool_avg_kv_cache_utilization",
        "percent",
        "epp_pool_avg_kv_cache_utilization.png",
    ),
    "inference_pool_average_queue_size": (
        "epp_pool_avg_queue_size",
        "count",
        "epp_pool_avg_queue_size.png",
    ),
    "inference_pool_average_running_requests": (
        "epp_pool_avg_running_requests",
        "count",
        "epp_pool_avg_running_requests.png",
    ),
    "inference_pool_ready_pods": (
        "epp_pool_ready_pods",
        "count",
        "epp_pool_ready_pods.png",
    ),
}

# EPP log-derived metrics: summary_key -> (report_key, default_units, graph_file, per_component)
_EPP_METRICS: dict[str, tuple[str, str, str, bool]] = {
    "dispatch_latency": (
        "epp_dispatch_latency",
        "seconds",
        "epp_dispatch_latency.png",
        False,
    ),
    "endpoint_scores": (
        "epp_endpoint_scores",
        "score",
        "epp_endpoint_scores.png",
        True,
    ),
    "request_distribution": (
        "epp_request_distribution",
        "count",
        "epp_request_distribution.png",
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


def _make_stats_dict(
    metric_data: dict[str, Any], units: str, graph_path: str | None = None
) -> dict[str, Any]:
    """Build a statistics dict from a metric_data entry."""
    stats: dict[str, Any] = {
        "mean": metric_data.get("mean", 0.0),
        "p50": metric_data.get("p50", 0.0),
        "p99": metric_data.get("p99", 0.0),
        "stddev": metric_data.get("stddev", 0.0),
        "units": units,
    }
    if graph_path:
        stats["graph_path"] = graph_path
    return stats


def _graph_path(graph_file: str) -> str:
    """Return the relative graph path for a graph filename."""
    return f"metrics/graphs/{graph_file}"


def _load_time_series_metrics(metrics_dir: str) -> list[str]:
    """Load configured metric names, with legacy defaults for older results."""
    value = _load_json(
        os.path.join(metrics_dir, "processed", "time_series_metrics.json")
    )
    if not isinstance(value, list):
        return list(GRAPHED_METRICS)
    return [name for name in value if isinstance(name, str) and name]


def _metric_metadata(
    prom_name: str, metrics_summary: dict[str, Any]
) -> tuple[str, str, str]:
    """Return report metadata, deriving sensible values for custom metrics."""
    if prom_name in GRAPHED_METRICS:
        return GRAPHED_METRICS[prom_name]

    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", prom_name).strip("_")
    units = ""
    for pod_name, pod_data in metrics_summary.items():
        if pod_name.startswith("_"):
            continue
        metric_data = pod_data.get("metrics", {}).get(prom_name, {})
        if metric_data:
            units = metric_data.get("unit", "")
            break
    return safe_name, units, f"{safe_name}.png"


def _build_embedded_time_series(
    obs: dict[str, Any], metrics_dir: str, max_points: int
) -> set[str]:
    """Populate `observability.components[].time_series`, one entry per pod.

    Returns the set of field names actually embedded.
    """
    from .timeseries import (
        collect_time_series_data,
        compute_ratio_series,
        series_points,
    )

    pod_data = collect_time_series_data(metrics_dir)
    if not pod_data:
        return set()

    specs = _embed_time_series_specs()
    components = obs.setdefault("components", [])
    by_replica = {c.get("replica_id"): c for c in components}
    embedded: set[str] = set()

    for pod_name in sorted(pod_data):
        pod_metrics = pod_data[pod_name]
        series_by_field: dict[str, Any] = {}

        for field, spec in specs.items():
            ratio = spec.get("ratio")
            if ratio:
                points = compute_ratio_series(pod_metrics, ratio[0], ratio[1])
            else:
                points = pod_metrics.get(spec.get("metric", ""), [])
            if not points:
                continue
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

    return embedded


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
            report_key, units, graph_file = _metric_metadata(prom_name, metrics_summary)

            component_entry = {
                "component_id": comp_id,
                "pod": pod_name,
                "role": role,
                "statistics": _make_stats_dict(
                    metrics[prom_name], units, _graph_path(graph_file)
                ),
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
        report_key, units, _ = _metric_metadata(prom_name, metrics_summary)
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
        graph_file,
        per_component,
    ) in _EPP_METRICS.items():
        data = epp_summary.get(summary_key)
        if not data:
            continue

        gpath = _graph_path(graph_file)

        if per_component and isinstance(data, dict):
            components = [
                {
                    "component_id": comp_id,
                    "statistics": _make_stats_dict(
                        comp_data, comp_data.get("unit", default_units), gpath
                    ),
                }
                for comp_id, comp_data in data.items()
                if isinstance(comp_data, dict)
            ]
            if components:
                entries[report_key] = {"components": components}
        elif isinstance(data, dict):
            entries[report_key] = {
                "statistics": _make_stats_dict(
                    data, data.get("unit", default_units), gpath
                ),
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
    br_dict: dict[str, Any], metrics_dir: str, component_label: str = "vllm-service"
) -> dict[str, Any]:
    """Add metrics to an existing benchmark report dictionary.

    Populates per-metric entries (e.g. results.observability.vllm_kv_cache_usage_perc)
    with per-component statistics, role, graph paths, and EPP metrics.
    """
    obs = br_dict.setdefault("results", {}).setdefault("observability", {})

    # Remove legacy components/aggregate structure if present
    obs.pop("components", None)

    # Per-metric entries from vLLM and EPP Prometheus scrapes
    metrics_summary = _load_json(
        os.path.join(metrics_dir, "processed", "metrics_summary.json")
    )
    if metrics_summary:
        metric_names = _load_time_series_metrics(metrics_dir)
        obs.update(_build_per_metric_entries(metrics_summary, metric_names))
        _build_aggregated_entries(metrics_summary, obs, metric_names)
        if _embed_time_series_enabled():
            embedded = _build_embedded_time_series(
                obs, metrics_dir, _embed_time_series_max_points()
            )
            if embedded - _V0_2_TIME_SERIES_FIELDS and br_dict.get("version") == "0.2":
                br_dict["version"] = "0.2.1"

    # EPP log-derived metrics
    epp_summary = _load_json(os.path.join(metrics_dir, "epp_metrics_summary.json"))
    if epp_summary:
        obs.update(_build_epp_entries(epp_summary))

    # Replica status
    replica_status = _load_json(
        os.path.join(metrics_dir, "processed", "replica_status.json")
    )
    if replica_status.get("controllers"):
        # Full time series stays in replica_status_timeseries.json;
        # only include summary + graph_path in the report.
        replica_status["graph_path"] = _graph_path("replica_status.png")
        obs["replica_status"] = replica_status

    # Pod startup times
    startup_times = _load_json(
        os.path.join(metrics_dir, "processed", "pod_startup_times.json")
    )
    if startup_times.get("pods"):
        startup_times["graph_path"] = _graph_path("pod_startup_times.png")
        obs["pod_startup_times"] = startup_times

    return br_dict
