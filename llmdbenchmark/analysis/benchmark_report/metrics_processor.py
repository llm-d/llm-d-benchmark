"""
Process collected metrics and integrate into benchmark report.
"""

import json
import os
from typing import Any


# ---------------------------------------------------------------------------
# Metrics that have corresponding graphs in metrics/graphs/
# Maps prometheus metric name -> (report key, units string, graph filename)
# ---------------------------------------------------------------------------
GRAPHED_METRICS: dict[str, tuple[str, str, str]] = {
    # Cache
    'vllm:kv_cache_usage_perc': (
        'vllm_kv_cache_usage_perc', 'percent',
        'vllm_kv_cache_usage_perc.png'),
    # Queue / scheduling
    'vllm:num_requests_running': (
        'vllm_num_requests_running', 'count',
        'vllm_num_requests_running.png'),
    'vllm:num_requests_waiting': (
        'vllm_num_requests_waiting', 'count',
        'vllm_num_requests_waiting.png'),
    'vllm:num_preemptions_total': (
        'vllm_num_preemptions_total', 'count',
        'vllm_num_preemptions_total.png'),
    # Prefix cache counters
    'vllm:prefix_cache_hits_total': (
        'vllm_prefix_cache_hits_total', 'tokens',
        'vllm_prefix_cache_hits_total.png'),
    'vllm:prefix_cache_queries_total': (
        'vllm_prefix_cache_queries_total', 'tokens',
        'vllm_prefix_cache_queries_total.png'),
    'vllm:external_prefix_cache_hits_total': (
        'vllm_external_prefix_cache_hits_total', 'tokens',
        'vllm_external_prefix_cache_hits_total.png'),
    'vllm:external_prefix_cache_queries_total': (
        'vllm_external_prefix_cache_queries_total', 'tokens',
        'vllm_external_prefix_cache_queries_total.png'),
    # Computed ratio metrics (produced by process_metrics.py)
    'vllm:prefix_cache_hit_rate': (
        'vllm_prefix_cache_hit_rate', 'percent',
        'vllm_prefix_cache_hit_rate.png'),
    'vllm:external_prefix_cache_hit_rate': (
        'vllm_external_prefix_cache_hit_rate', 'percent',
        'vllm_external_prefix_cache_hit_rate.png'),
    # NIXL KV transfer
    'vllm:nixl_xfer_time_seconds_sum': (
        'vllm_nixl_xfer_time_seconds_sum', 'seconds',
        'vllm_nixl_xfer_time_seconds_sum.png'),
    'vllm:nixl_xfer_time_seconds_count': (
        'vllm_nixl_xfer_time_seconds_count', 'count',
        'vllm_nixl_xfer_time_seconds_count.png'),
    'vllm:nixl_bytes_transferred_sum': (
        'vllm_nixl_bytes_transferred_sum', 'bytes',
        'vllm_nixl_bytes_transferred_sum.png'),
    'vllm:nixl_bytes_transferred_count': (
        'vllm_nixl_bytes_transferred_count', 'count',
        'vllm_nixl_bytes_transferred_count.png'),
    # EPP (inference scheduler) Prometheus metrics — pool-level gauges
    'inference_pool_average_kv_cache_utilization': (
        'epp_pool_avg_kv_cache_utilization', 'percent',
        'epp_pool_avg_kv_cache_utilization.png'),
    'inference_pool_average_queue_size': (
        'epp_pool_avg_queue_size', 'count',
        'epp_pool_avg_queue_size.png'),
    'inference_pool_average_running_requests': (
        'epp_pool_avg_running_requests', 'count',
        'epp_pool_avg_running_requests.png'),
    'inference_pool_ready_pods': (
        'epp_pool_ready_pods', 'count',
        'epp_pool_ready_pods.png'),
}

# EPP log-derived metrics: summary_key -> (report_key, default_units, graph_file, per_component)
_EPP_METRICS: dict[str, tuple[str, str, str, bool]] = {
    'dispatch_latency': (
        'epp_dispatch_latency', 'seconds',
        'epp_dispatch_latency.png', False),
    'endpoint_scores': (
        'epp_endpoint_scores', 'score',
        'epp_endpoint_scores.png', True),
    'request_distribution': (
        'epp_request_distribution', 'count',
        'epp_request_distribution.png', True),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_role(pod_name: str) -> str:
    """Detect component role from pod name."""
    lower = pod_name.lower()
    if 'prefill' in lower:
        return 'prefill'
    if 'decode' in lower:
        return 'decode'
    return 'replica'


def _component_id(role: str) -> str:
    """Return a component_id string from role."""
    if role in ('prefill', 'decode'):
        return f'{role}-engine'
    return 'inference-engine'


def _load_json(filepath: str) -> dict[str, Any]:
    """Load a JSON file, returning {} if it doesn't exist."""
    if not os.path.exists(filepath):
        return {}
    with open(filepath, 'r') as f:
        return json.load(f)


def _make_stats_dict(metric_data: dict[str, Any], units: str,
                     graph_path: str | None = None) -> dict[str, Any]:
    """Build a statistics dict from a metric_data entry."""
    stats: dict[str, Any] = {
        'mean': metric_data.get('mean', 0.0),
        'p50': metric_data.get('p50', 0.0),
        'p99': metric_data.get('p99', 0.0),
        'stddev': metric_data.get('stddev', 0.0),
        'units': units,
    }
    if graph_path:
        stats['graph_path'] = graph_path
    return stats


def _graph_path(graph_file: str) -> str:
    """Return the relative graph path for a graph filename."""
    return f'metrics/graphs/{graph_file}'


# ---------------------------------------------------------------------------
# Build observability entries
# ---------------------------------------------------------------------------

def _build_per_metric_entries(
    metrics_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build per-metric observability entries with per-component statistics.

    Returns a dict keyed by report metric name (e.g. 'vllm_prefix_cache_hit_rate')
    with 'components' lists underneath.
    """
    entries: dict[str, dict] = {}

    for pod_name, pod_data in metrics_summary.items():
        if pod_name.startswith('_'):
            continue
        metrics = pod_data.get('metrics', {})
        role = _detect_role(pod_name)
        comp_id = _component_id(role)

        for prom_name, (report_key, units, graph_file) in GRAPHED_METRICS.items():
            if prom_name not in metrics:
                continue

            component_entry = {
                'component_id': comp_id,
                'pod': pod_name,
                'role': role,
                'statistics': _make_stats_dict(
                    metrics[prom_name], units, _graph_path(graph_file)),
            }

            if report_key not in entries:
                entries[report_key] = {'components': []}
            entries[report_key]['components'].append(component_entry)

    return entries


def _build_aggregated_entries(
    metrics_summary: dict[str, Any],
    obs: dict[str, Any],
) -> None:
    """Add cluster-wide aggregated stats to existing observability entries."""
    aggregated = metrics_summary.get('_aggregated', {}).get('metrics', {})
    for prom_name, (report_key, units, _) in GRAPHED_METRICS.items():
        if prom_name not in aggregated:
            continue
        entry = obs.setdefault(report_key, {})
        entry['aggregated'] = _make_stats_dict(aggregated[prom_name], units)


def _build_epp_entries(
    epp_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build EPP log-derived metric entries for observability section."""
    entries: dict[str, Any] = {}

    for summary_key, (report_key, default_units, graph_file, per_component) in _EPP_METRICS.items():
        data = epp_summary.get(summary_key)
        if not data:
            continue

        gpath = _graph_path(graph_file)

        if per_component and isinstance(data, dict):
            components = [
                {
                    'component_id': comp_id,
                    'statistics': _make_stats_dict(
                        comp_data, comp_data.get('unit', default_units), gpath),
                }
                for comp_id, comp_data in data.items()
                if isinstance(comp_data, dict)
            ]
            if components:
                entries[report_key] = {'components': components}
        elif isinstance(data, dict):
            entries[report_key] = {
                'statistics': _make_stats_dict(
                    data, data.get('unit', default_units), gpath),
            }

    # Plugin latencies (dynamic keys)
    for plugin_type, plugins in epp_summary.get('plugin_latencies', {}).items():
        for plugin_name, latency_data in plugins.items():
            key = f'epp_plugin_{plugin_type}_{plugin_name}'.replace(
                '/', '_').replace('-', '_')
            entries[key] = {
                'statistics': _make_stats_dict(
                    latency_data, latency_data.get('unit', 'seconds')),
            }

    return entries


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_metrics_to_benchmark_report(
    br_dict: dict[str, Any],
    metrics_dir: str,
    component_label: str = "vllm-service"
) -> dict[str, Any]:
    """Add metrics to an existing benchmark report dictionary.

    Populates per-metric entries (e.g. results.observability.vllm_kv_cache_usage_perc)
    with per-component statistics, role, graph paths, and EPP metrics.
    """
    obs = br_dict.setdefault('results', {}).setdefault('observability', {})

    # Remove legacy components/aggregate structure if present
    obs.pop('components', None)

    # Per-metric entries from vLLM and EPP Prometheus scrapes
    metrics_summary = _load_json(
        os.path.join(metrics_dir, 'processed', 'metrics_summary.json'))
    if metrics_summary:
        obs.update(_build_per_metric_entries(metrics_summary))
        _build_aggregated_entries(metrics_summary, obs)

    # EPP log-derived metrics
    epp_summary = _load_json(
        os.path.join(metrics_dir, 'epp_metrics_summary.json'))
    if epp_summary:
        obs.update(_build_epp_entries(epp_summary))

    # Replica status
    replica_status = _load_json(
        os.path.join(metrics_dir, 'processed', 'replica_status.json'))
    if replica_status.get('controllers'):
        # Merge time series if available
        ts_data = _load_json(
            os.path.join(metrics_dir, 'processed', 'replica_status_timeseries.json'))
        snapshots = ts_data.get('snapshots', [])
        if snapshots:
            replica_status['time_series'] = snapshots
        replica_status['graph_path'] = _graph_path('replica_status.png')
        obs['replica_status'] = replica_status

    # Pod startup times
    startup_times = _load_json(
        os.path.join(metrics_dir, 'processed', 'pod_startup_times.json'))
    if startup_times.get('pods'):
        startup_times['graph_path'] = _graph_path('pod_startup_times.png')
        obs['pod_startup_times'] = startup_times

    return br_dict
