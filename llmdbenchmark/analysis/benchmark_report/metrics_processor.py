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


def load_metrics_summary(metrics_dir: str) -> dict[str, Any]:
    """Load the processed metrics summary JSON file."""
    summary_file = os.path.join(
        metrics_dir, 'processed', 'metrics_summary.json')

    if not os.path.exists(summary_file):
        return {}

    with open(summary_file, 'r') as f:
        return json.load(f)


def load_epp_metrics_summary(metrics_dir: str) -> dict[str, Any]:
    """Load the EPP metrics summary JSON file."""
    summary_file = os.path.join(metrics_dir, 'epp_metrics_summary.json')

    if not os.path.exists(summary_file):
        return {}

    with open(summary_file, 'r') as f:
        return json.load(f)


def load_replica_status(metrics_dir: str) -> dict[str, Any]:
    """Load the replica status JSON file.

    Merges the latest snapshot with time series data if available.
    """
    status_file = os.path.join(
        metrics_dir, 'processed', 'replica_status.json')
    ts_file = os.path.join(
        metrics_dir, 'processed', 'replica_status_timeseries.json')

    if not os.path.exists(status_file):
        return {}

    with open(status_file, 'r') as f:
        result = json.load(f)

    # Merge time series if available
    if os.path.exists(ts_file):
        with open(ts_file, 'r') as f:
            ts_data = json.load(f)
        snapshots = ts_data.get('snapshots', [])
        if snapshots:
            result['time_series'] = snapshots

    return result


def load_pod_startup_times(metrics_dir: str) -> dict[str, Any]:
    """Load the pod startup times JSON file."""
    status_file = os.path.join(
        metrics_dir, 'processed', 'pod_startup_times.json')

    if not os.path.exists(status_file):
        return {}

    with open(status_file, 'r') as f:
        return json.load(f)


def _make_stats_dict(metric_data: dict[str, Any], units: str,
                     graph_path: str | None = None) -> dict[str, Any]:
    """Build a statistics dict from a metric_data entry (from metrics_summary)."""
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


# ---------------------------------------------------------------------------
# Build per-metric observability entries
# ---------------------------------------------------------------------------

def _build_per_metric_entries(
    metrics_summary: dict[str, Any],
    graphs_dir: str,
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

            metric_data = metrics[prom_name]
            graph_path = f'metrics/graphs/{graph_file}'

            component_entry = {
                'component_id': comp_id,
                'pod': pod_name,
                'role': role,
                'statistics': _make_stats_dict(
                    metric_data, units, graph_path),
            }

            if report_key not in entries:
                entries[report_key] = {'components': []}
            entries[report_key]['components'].append(component_entry)

    return entries


def _build_epp_entries(
    epp_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build EPP metric entries for observability section."""
    entries: dict[str, Any] = {}

    # Dispatch latency
    if 'dispatch_latency' in epp_summary:
        dl = epp_summary['dispatch_latency']
        entries['epp_dispatch_latency'] = {
            'statistics': {
                'mean': dl.get('mean', 0.0),
                'p50': dl.get('p50', 0.0),
                'p99': dl.get('p99', 0.0),
                'stddev': dl.get('stddev', 0.0),
                'units': dl.get('unit', 'seconds'),
                'graph_path': 'metrics/graphs/epp_dispatch_latency.png',
            }
        }

    # Endpoint scores
    if 'endpoint_scores' in epp_summary:
        components = []
        for endpoint_id, score_data in epp_summary['endpoint_scores'].items():
            components.append({
                'component_id': endpoint_id,
                'statistics': {
                    'mean': score_data.get('mean', 0.0),
                    'p50': score_data.get('p50', 0.0),
                    'p99': score_data.get('p99', 0.0),
                    'stddev': score_data.get('stddev', 0.0),
                    'units': score_data.get('unit', 'score'),
                    'graph_path': 'metrics/graphs/epp_endpoint_scores.png',
                }
            })
        if components:
            entries['epp_endpoint_scores'] = {'components': components}

    # Request distribution
    if 'request_distribution' in epp_summary:
        components = []
        for endpoint_id, dist_data in epp_summary[
                'request_distribution'].items():
            components.append({
                'component_id': endpoint_id,
                'statistics': {
                    'count': dist_data.get('count', 0),
                    'units': 'count',
                    'graph_path': 'metrics/graphs/epp_request_distribution.png',
                }
            })
        if components:
            entries['epp_request_distribution'] = {'components': components}

    # Plugin latencies
    if 'plugin_latencies' in epp_summary:
        for plugin_type, plugins in epp_summary['plugin_latencies'].items():
            for plugin_name, latency_data in plugins.items():
                key = f'epp_plugin_{plugin_type}_{plugin_name}'.replace(
                    '/', '_').replace('-', '_')
                entries[key] = {
                    'statistics': {
                        'mean': latency_data.get('mean', 0.0),
                        'p50': latency_data.get('p50', 0.0),
                        'p99': latency_data.get('p99', 0.0),
                        'stddev': latency_data.get('stddev', 0.0),
                        'units': latency_data.get('unit', 'seconds'),
                    }
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
    if 'results' not in br_dict:
        br_dict['results'] = {}

    if 'observability' not in br_dict['results']:
        br_dict['results']['observability'] = {}

    obs = br_dict['results']['observability']

    # Remove legacy components/aggregate structure if present
    obs.pop('components', None)

    # Per-metric entries from vLLM metrics
    metrics_summary = load_metrics_summary(metrics_dir)
    if metrics_summary:
        graphs_dir = os.path.join(metrics_dir, 'graphs')
        per_metric = _build_per_metric_entries(metrics_summary, graphs_dir)
        obs.update(per_metric)

        # Cluster-wide aggregated metrics (from _aggregated key)
        aggregated = metrics_summary.get('_aggregated', {}).get('metrics', {})
        for prom_name, (report_key, units, _) in GRAPHED_METRICS.items():
            if prom_name in aggregated:
                agg_data = aggregated[prom_name]
                if report_key in obs:
                    obs[report_key]['aggregated'] = _make_stats_dict(
                        agg_data, units)
                else:
                    obs[report_key] = {
                        'aggregated': _make_stats_dict(agg_data, units)
                    }

    # EPP metrics
    epp_summary = load_epp_metrics_summary(metrics_dir)
    if epp_summary:
        epp_entries = _build_epp_entries(epp_summary)
        obs.update(epp_entries)

    # Replica status (desired vs ready vs available per controller/model)
    replica_status = load_replica_status(metrics_dir)
    if replica_status and replica_status.get('controllers'):
        replica_status['graph_path'] = 'metrics/graphs/replica_status.png'
        obs['replica_status'] = replica_status

    # Pod startup times (creation to Ready, per node per replica)
    startup_times = load_pod_startup_times(metrics_dir)
    if startup_times and startup_times.get('pods'):
        startup_times['graph_path'] = 'metrics/graphs/pod_startup_times.png'
        obs['pod_startup_times'] = startup_times

    return br_dict
