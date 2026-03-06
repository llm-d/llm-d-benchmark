"""Utilities for parsing Prometheus metrics and extracting vLLM data."""

import gzip
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Any


def parse_prometheus_text(
    text: str,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, str],
    dict[str, str],
]:
    """Parse Prometheus text exposition format into structured data.

    Returns (samples, types, helps) where samples maps metric names
    to lists of {"labels": {…}, "value": float}.
    """
    samples: dict[str, list[dict[str, Any]]] = {}
    types: dict[str, str] = {}
    helps: dict[str, str] = {}

    line_re = re.compile(
        r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
        r"(?:\{(?P<labels>[^}]*)\})?"
        r"\s+(?P<value>\S+)"
    )
    label_re = re.compile(r'(\w+)="([^"]*)"')

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("# TYPE "):
            parts = stripped[len("# TYPE ") :].split(None, 1)
            if len(parts) == 2:
                types[parts[0]] = parts[1]
            continue
        if stripped.startswith("# HELP "):
            parts = stripped[len("# HELP ") :].split(None, 1)
            if len(parts) == 2:
                helps[parts[0]] = parts[1]
            continue
        if stripped.startswith("#"):
            continue

        m = line_re.match(stripped)
        if not m:
            continue
        name = m.group("name")
        value_str = m.group("value")
        try:
            value = float(value_str)
        except ValueError:
            continue

        labels: dict[str, str] = {}
        if m.group("labels"):
            labels = dict(label_re.findall(m.group("labels")))

        samples.setdefault(name, []).append({"labels": labels, "value": value})

    return samples, types, helps


def _role_to_component_id(role: str) -> str:
    """Map a pod role label to a component identifier."""
    mapping = {
        "prefill": "prefill-engine",
        "decode": "decode-engine",
        "standalone": "vllm-engine",
    }
    return mapping.get(role, f"{role}-engine")


def _metric_short_name(metric_name: str) -> str:
    """Derive a short series name by stripping the 'vllm:' prefix."""
    name = metric_name.replace(":", "_")
    if name.startswith("vllm_"):
        name = name[len("vllm_"):]
    return name


def _metric_unit(metric_name: str) -> str:
    """Best-effort unit inference from the metric name."""
    if "byte" in metric_name:
        return "byte"
    if "seconds" in metric_name:
        return "second"
    if "requests" in metric_name:
        return "request"
    if "tokens" in metric_name:
        return "token"
    if "percent" in metric_name or "ratio" in metric_name or "perc" in metric_name:
        return "percent"
    if "blocks" in metric_name:
        return "block"
    return ""


def _labels_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Hashable key for a label dict."""
    return tuple(sorted(labels.items()))


def _extract_histogram_snapshots(
    base_name: str,
    samples: dict[str, list[dict[str, Any]]],
    ts: str,
) -> list[dict[str, Any]]:
    """Extract sum/count snapshots per label-set for a histogram metric."""
    count_samples = samples.get(f"{base_name}_count", [])
    sum_samples = samples.get(f"{base_name}_sum", [])

    results = []
    for cs in count_samples:
        key = _labels_key(cs["labels"])
        sum_val = next(
            (s["value"] for s in sum_samples if _labels_key(s["labels"]) == key),
            None,
        )
        results.append({
            "labels": cs["labels"],
            "snapshot": {"ts": ts, "sum": sum_val, "count": cs["value"]},
        })
    return results


# Histogram/summary sub-metric suffixes (not emitted as standalone entries).
_HISTOGRAM_SUFFIXES = ("_bucket", "_count", "_sum")
_SUMMARY_SUFFIXES = ("_count", "_sum")
_CREATED_SUFFIX = "_created"

# Categories for report output (unlisted metrics like http, python_gc are excluded).
_METRIC_CATEGORIES: dict[str, set[str]] = {
    "latency": {
        # e2e, TTFT, ITL, TPOT are already reported in request_performance;
        # keep only the server-side breakdown metrics unique to observability.
        "vllm:request_queue_time_seconds",
        "vllm:request_prefill_time_seconds",
        "vllm:request_decode_time_seconds",
        "vllm:request_inference_time_seconds",
    },
    "throughput": {
        "vllm:prompt_tokens_total",
        "vllm:generation_tokens_total",
        "vllm:iteration_tokens_total",
    },
    "scheduling": {
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:num_preemptions_total",
    },
    "resources": {
        "vllm:kv_cache_usage_perc",
        "process_resident_memory_bytes",
    },
    "cache": {
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
        "vllm:external_prefix_cache_hits_total",
        "vllm:external_prefix_cache_queries_total",
        "derived:prefix_cache_hit_rate",
        "derived:external_prefix_cache_hit_rate",
    },
}
_METRIC_TO_CATEGORY = {
    mid: cat for cat, ids in _METRIC_CATEGORIES.items() for mid in ids
}

# --- LTTB downsampling ---
#
# Long benchmark runs (e.g. 2 hours at 30s intervals with multiple pods) can
# produce thousands of data points per metric.  Plotting all of them makes
# PNGs large and slow to render, and the dense lines obscure trends rather
# than revealing them.
#
# LTTB (Largest Triangle Three Buckets) reduces the point count while
# preserving visually significant peaks, valleys, and slopes — unlike naive
# decimation (every Nth point) which can miss spikes entirely.  The threshold
# below caps each series at 200 points, which keeps plots crisp and file
# sizes small without losing meaningful detail.

_LTTB_THRESHOLD = 200


def _lttb_downsample(
    x: list[float], y: list[float], target_n: int,
) -> tuple[list[float], list[float]]:
    """Largest Triangle Three Buckets downsampling (pure Python).

    Returns (x_out, y_out) with target_n points (or fewer if input is shorter).
    """
    n = len(x)
    if n <= target_n or target_n < 2:
        return list(x), list(y)

    # target_n == 2: just first and last
    if target_n == 2:
        return [x[0], x[-1]], [y[0], y[-1]]

    out_x: list[float] = [x[0]]
    out_y: list[float] = [y[0]]

    bucket_size = (n - 2) / (target_n - 2)
    prev_idx = 0

    for i in range(1, target_n - 1):
        # Current bucket range
        bucket_start = int(math.floor(1 + (i - 1) * bucket_size))
        bucket_end = int(math.floor(1 + i * bucket_size))
        bucket_end = min(bucket_end, n - 1)

        # Next bucket average
        next_start = int(math.floor(1 + i * bucket_size))
        next_end = int(math.floor(1 + (i + 1) * bucket_size))
        next_end = min(next_end, n)
        if next_start >= n:
            next_start = n - 1
        span = max(1, next_end - next_start)
        avg_x = sum(x[j] for j in range(next_start, next_end)) / span
        avg_y = sum(y[j] for j in range(next_start, next_end)) / span

        # Pick point in current bucket maximising triangle area
        best_area = -1.0
        best_idx = bucket_start
        px, py = x[prev_idx], y[prev_idx]
        for j in range(bucket_start, bucket_end):
            area = abs(
                (px - avg_x) * (y[j] - py) - (px - x[j]) * (avg_y - py)
            )
            if area > best_area:
                best_area = area
                best_idx = j

        out_x.append(x[best_idx])
        out_y.append(y[best_idx])
        prev_idx = best_idx

    out_x.append(x[-1])
    out_y.append(y[-1])
    return out_x, out_y


# --- Flat-series detection ---


def _is_flat_series(values: list[float]) -> bool:
    """Return True if all values are identical (e.g. all zeros)."""
    if not values:
        return True
    first = values[0]
    return all(v == first for v in values)


# --- Timestamp helpers ---


def _parse_timestamps_to_labels(timestamps: list[str]) -> list[str]:
    """Convert ISO timestamp strings to HH:MM:SS display labels."""
    from datetime import datetime

    labels: list[str] = []
    for ts in timestamps:
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                labels.append(dt.strftime("%H:%M:%S"))
            except (ValueError, TypeError):
                labels.append(ts[:19] if len(ts) > 19 else ts)
        else:
            labels.append(f"t{len(labels)}")
    return labels


# --- Plot collector ---

_OVERLAY_COLORS = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]


@dataclass
class _PlotSeries:
    """A single time series to be plotted."""
    timestamps: list[str]
    values: list[float]
    pod_name: str
    label: str


class _PlotCollector:
    """Accumulates series data for overlay plots and dashboard."""

    def __init__(self) -> None:
        self._series: dict[str, list[_PlotSeries]] = {}
        self._meta: dict[str, tuple[str, str]] = {}   # plot_key -> (title, ylabel)
        self._metric_map: dict[str, list[str]] = {}    # metric_id -> [plot_key]

    def add(
        self,
        plot_key: str,
        metric_id: str,
        timestamps: list[str],
        values: list[float],
        pod_name: str,
        title: str,
        ylabel: str,
    ) -> None:
        series = _PlotSeries(
            timestamps=list(timestamps),
            values=list(values),
            pod_name=pod_name,
            label=pod_name,
        )
        self._series.setdefault(plot_key, []).append(series)
        if plot_key not in self._meta:
            self._meta[plot_key] = (title, ylabel)
        self._metric_map.setdefault(metric_id, [])
        if plot_key not in self._metric_map[metric_id]:
            self._metric_map[metric_id].append(plot_key)

    def generate_plots(self, plots_dir: str) -> dict[str, str]:
        """Generate overlay plots. Returns plot_key -> filename."""
        result: dict[str, str] = {}
        for plot_key, series_list in self._series.items():
            if all(_is_flat_series(s.values) for s in series_list):
                continue
            title, ylabel = self._meta[plot_key]
            filename = _safe_plot_filename(plot_key)
            filepath = os.path.join(plots_dir, filename)
            if _plot_overlay_time_series(series_list, title, ylabel, filepath):
                result[plot_key] = filename
        return result

    def series_for_metric(self, metric_id: str) -> list[_PlotSeries]:
        """Return all series across plot keys for a given metric."""
        out: list[_PlotSeries] = []
        for pk in self._metric_map.get(metric_id, []):
            out.extend(self._series.get(pk, []))
        return out


# --- Phase ordering ---


def _ordered_phases(phases: dict[str, Any]) -> list[str]:
    """Return phase keys in chronological order: pre, during_0, ..., during_N, post."""
    during = sorted(
        (k for k in phases if k.startswith("during_")),
        key=lambda k: int(k.split("_", 1)[1]),
    )
    result = []
    if "pre" in phases:
        result.append("pre")
    result.extend(during)
    if "post" in phases:
        result.append("post")
    return result


# --- Restructuring: flat list -> hierarchical dict ---


def _restructure_metrics(flat_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Transform flat metric entries into a hierarchical structure.

    Groups by category with per-pod components. Only _METRIC_TO_CATEGORY entries included.
    """
    result: dict[str, dict[str, Any]] = {}

    for entry in flat_entries:
        metric_id = entry.get("metric_ref", {}).get("id", "")
        category = _METRIC_TO_CATEGORY.get(metric_id)
        if category is None:
            continue

        short_name = _metric_short_name(metric_id)

        if category not in result:
            result[category] = {}

        if short_name not in result[category]:
            result[category][short_name] = {
                "metric_id": metric_id,
                "description": entry.get("description", ""),
                "units": entry.get("unit", ""),
                "components": [],
            }

        node = result[category][short_name]

        # Attach plot at the metric level (shared across pods)
        if "plot" in entry and "plot" not in node:
            node["plot"] = entry["plot"]

        labels = entry.get("labels", {})
        component: dict[str, Any] = {
            "component_id": entry.get("component_id", ""),
            "pod": labels.get("pod", ""),
            "role": labels.get("role", ""),
            "statistics": entry.get("statistics"),
        }

        extra_labels = {k: v for k, v in labels.items() if k not in ("pod", "role")}
        if extra_labels:
            component["labels"] = extra_labels

        node["components"].append(component)

    return result


# --- Main entry point ---


def parse_vllm_metrics_dir(metrics_dir: str) -> dict[str, Any] | None:
    """Read scraped vLLM metric files and return observability data.

    Parses all metrics from Prometheus text files, emitting each in the
    appropriate format. Returns dict for results.observability, or None.
    """
    # Locate the raw data directory: prefer raw_data/ subdirectory, fall back
    # to metrics_dir itself for backward compatibility with older scrapes.
    raw_dir = os.path.join(metrics_dir, "raw_data")
    if not os.path.isdir(raw_dir):
        raw_dir = metrics_dir

    # Load metadata for timestamps (pre, post, during_N)
    timestamps: dict[str, str] = {}
    meta_re = re.compile(r"^(pre|post|during_\d+)_metadata\.json$")
    for fname in sorted(os.listdir(raw_dir)):
        mm = meta_re.match(fname)
        if not mm:
            continue
        meta_path = os.path.join(raw_dir, fname)
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="UTF-8") as fh:
                meta = json.load(fh)
            timestamps[mm.group(1)] = meta.get("timestamp", "")

    # Compute interval_seconds from the first two during-run timestamps
    interval_seconds: int | None = None
    during_phases = sorted(
        (k for k in timestamps if k.startswith("during_")),
        key=lambda k: int(k.split("_", 1)[1]),
    )
    if len(during_phases) >= 2:
        from datetime import datetime

        def _parse_ts(ts_str: str) -> datetime | None:
            try:
                return datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                return None

        t0 = _parse_ts(timestamps[during_phases[0]])
        t1 = _parse_ts(timestamps[during_phases[1]])
        if t0 is not None and t1 is not None:
            interval_seconds = int(abs((t1 - t0).total_seconds()))

    # Discover per-pod log files: {phase}_{pod_name}.log or .log.gz
    log_re = re.compile(r"^(pre|post|during_\d+)_(.+)\.log(?:\.gz)?$")
    pod_data: dict[
        str,
        dict[
            str,
            tuple[
                dict[str, list[dict[str, Any]]],
                dict[str, str],
                dict[str, str],
            ],
        ],
    ] = {}
    pod_roles: dict[str, str] = {}
    errors: list[str] = []

    for fname in sorted(os.listdir(raw_dir)):
        m = log_re.match(fname)
        if not m:
            continue
        phase = m.group(1)
        pod_name = m.group(2)

        fpath = os.path.join(raw_dir, fname)
        try:
            opener = gzip.open if fname.endswith(".gz") else open
            with opener(fpath, "rt", encoding="UTF-8") as fh:
                text = fh.read()
        except OSError as exc:
            errors.append(f"Failed to read {fname}: {exc}")
            continue

        if not text.strip():
            errors.append(f"Empty metrics file: {fname}")
            continue

        parsed = parse_prometheus_text(text)
        pod_data.setdefault(pod_name, {})[phase] = parsed

        if pod_name not in pod_roles:
            if "prefill" in pod_name:
                pod_roles[pod_name] = "prefill"
            elif "decode" in pod_name:
                pod_roles[pod_name] = "decode"
            else:
                pod_roles[pod_name] = "standalone"

    if not pod_data:
        return None

    # Collect global type/help metadata
    global_types: dict[str, str] = {}
    global_helps: dict[str, str] = {}
    for phases in pod_data.values():
        for _, types, helps in phases.values():
            for name, t in types.items():
                global_types.setdefault(name, t)
            for name, h in helps.items():
                global_helps.setdefault(name, h)

    # Base metric names (TYPE-declared); sub-metrics handled as part of parent.
    base_metric_names: set[str] = set(global_types.keys())

    # Also detect undeclared metrics (no TYPE line) that are not sub-metrics
    all_sample_names: set[str] = set()
    for phases in pod_data.values():
        for samples, _, _ in phases.values():
            all_sample_names.update(samples.keys())

    owned_suffixes: set[str] = set()
    for base in base_metric_names:
        mtype = global_types.get(base, "")
        if mtype == "histogram":
            for sfx in _HISTOGRAM_SUFFIXES:
                owned_suffixes.add(f"{base}{sfx}")
        elif mtype == "summary":
            for sfx in _SUMMARY_SUFFIXES:
                owned_suffixes.add(f"{base}{sfx}")
        # _created companion
        owned_suffixes.add(f"{base}{_CREATED_SUFFIX}")

    # Add standalone sample names that are not sub-metrics
    for name in all_sample_names:
        if name not in owned_suffixes and name not in base_metric_names:
            base_metric_names.add(name)

    # Create plots subdirectory inside metrics_dir
    plots_dir = os.path.join(metrics_dir, "plots")
    try:
        os.makedirs(plots_dir, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"Failed to create plots directory {plots_dir}: {e}\n")
        plots_dir = None

    # Pass 1: build entries and collect plot series
    plot_collector = _PlotCollector() if plots_dir else None
    metric_entries: list[dict[str, Any]] = []

    for pod_name in sorted(pod_data):
        phases = pod_data[pod_name]
        role = pod_roles.get(pod_name, "standalone")

        for metric_name in sorted(base_metric_names):
            if metric_name not in _METRIC_TO_CATEGORY:
                continue
            mtype = global_types.get(metric_name, "gauge")
            description = global_helps.get(metric_name, "")
            short_name = _metric_short_name(metric_name)
            unit = _metric_unit(metric_name)

            if mtype == "histogram":
                _emit_histogram_entries(
                    metric_entries, metric_name, short_name, mtype,
                    unit, description, pod_name, role, phases, timestamps,
                    plot_collector=plot_collector,
                )
            else:
                _emit_gauge_counter_entries(
                    metric_entries, metric_name, short_name, mtype,
                    unit, description, pod_name, role, phases, timestamps,
                    plot_collector=plot_collector,
                )

        # Derived metrics
        _emit_derived_cache_hit_rate(
            metric_entries, pod_name, role, phases, timestamps,
            hits_metric="vllm:prefix_cache_hits_total",
            queries_metric="vllm:prefix_cache_queries_total",
            derived_id="derived:prefix_cache_hit_rate",
            short_name="prefix_cache_hit_rate",
            description="Prefix cache hit rate (hits / queries).",
            plot_collector=plot_collector,
        )
        _emit_derived_cache_hit_rate(
            metric_entries, pod_name, role, phases, timestamps,
            hits_metric="vllm:external_prefix_cache_hits_total",
            queries_metric="vllm:external_prefix_cache_queries_total",
            derived_id="derived:external_prefix_cache_hit_rate",
            short_name="external_prefix_cache_hit_rate",
            description="External prefix cache hit rate (hits / queries).",
            plot_collector=plot_collector,
        )

    if not metric_entries:
        return None

    # Relative path prefix for plot references in the report output.
    # Plots live at vllm_metrics/plots/ relative to the experiment results dir.
    plots_reldir = "vllm_metrics/plots"

    # Pass 2: generate overlay plots and back-annotate entries
    if plot_collector and plots_dir:
        plot_map = plot_collector.generate_plots(plots_dir)
        for entry in metric_entries:
            pk = entry.pop("_plot_key", None)
            if pk and pk in plot_map:
                entry["plot"] = f"{plots_reldir}/{plot_map[pk]}"

    # Pass 3: restructure into hierarchical output
    structured_metrics = _restructure_metrics(metric_entries)

    # Dashboard
    result: dict[str, Any] = {
        "scrape_info": {
            "interval_seconds": interval_seconds,
            "method": "pull",
            "source": "direct-scrape",
            "errors": errors,
        },
        "metrics": structured_metrics,
    }

    if plot_collector and plots_dir:
        dashboard_path = os.path.join(plots_dir, "dashboard.png")
        if _generate_dashboard(plot_collector, dashboard_path):
            result["dashboard"] = f"{plots_reldir}/dashboard.png"

    return result


# --- Naming / statistics helpers ---


def _label_suffix(labels: dict[str, str]) -> str:
    """Build a dotted suffix string from label values for series naming."""
    if not labels:
        return ""
    return "." + ".".join(v for v in labels.values() if v)


def _compute_statistics(values: list[float], unit: str) -> dict[str, Any] | None:
    """Compute summary statistics (mean, p50, p99, stddev). Returns None if empty.

    Values are returned at full float precision to match the request_performance
    section produced by the benchmark tool.
    """
    if not values:
        return None
    n = len(values)
    mean = sum(values) / n
    sorted_vals = sorted(values)
    p50 = sorted_vals[int(n * 0.50)]
    p99 = sorted_vals[min(int(n * 0.99), n - 1)]
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0
    return {
        "mean": mean,
        "p50": p50,
        "p99": p99,
        "stddev": stddev,
        "units": unit,
    }


def _safe_plot_filename(name: str) -> str:
    """Sanitize a metric/series name into a safe filename."""
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return safe[:200] + ".png"


# --- Plotting ---

def _downsample_series(values: list[float]) -> tuple[list[float], list[float]]:
    """Apply LTTB downsampling to a series, returning (x, y) coordinates."""
    n = len(values)
    x = [float(i) for i in range(n)]
    if n > _LTTB_THRESHOLD:
        return _lttb_downsample(x, values, _LTTB_THRESHOLD)
    return x, list(values)


_MAX_TICK_LABELS = 15  # Maximum x-axis tick labels shown on plots


def _plot_overlay_time_series(
    series_list: list["_PlotSeries"],
    title: str,
    ylabel: str,
    filepath: str,
) -> bool:
    """Plot overlaid time series with LTTB downsampling. Returns True on success."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(10, 4))

        multi_pod = len({s.pod_name for s in series_list}) > 1

        # Use the longest series for x-axis tick labels
        longest = max(series_list, key=lambda s: len(s.values))
        all_x_labels = _parse_timestamps_to_labels(longest.timestamps)
        all_n = len(longest.values)

        for idx, series in enumerate(series_list):
            ds_x, ds_y = _downsample_series(series.values)
            color = _OVERLAY_COLORS[idx % len(_OVERLAY_COLORS)]
            marker = "o" if len(ds_x) <= 60 else None
            markersize = 4 if len(ds_x) <= 60 else 0
            label = series.label if multi_pod else None
            ax.plot(
                ds_x, ds_y, marker=marker, linewidth=1.5,
                markersize=markersize, color=color, label=label,
            )

        # Thin tick labels
        if all_x_labels:
            step = max(1, all_n // _MAX_TICK_LABELS)
            tick_indices = list(range(0, all_n, step))
            if tick_indices and tick_indices[-1] != all_n - 1:
                tick_indices.append(all_n - 1)
            ax.set_xticks(tick_indices)
            ax.set_xticklabels(
                [all_x_labels[i] for i in tick_indices],
                rotation=45, ha="right", fontsize=7,
            )

        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Time")
        ax.grid(True, alpha=0.3)

        if multi_pod:
            ax.legend(fontsize=8, loc="best")

        fig.tight_layout()
        fig.savefig(filepath, dpi=100)
        plt.close(fig)
        return True
    except Exception as e:
        sys.stderr.write(f"Failed to generate overlay plot {filepath}: {e}\n")
        return False


# --- Dashboard ---

_DASHBOARD_METRICS = [
    ("vllm:request_queue_time_seconds", "Queue Time (s)"),
    ("vllm:request_prefill_time_seconds", "Prefill Time (s)"),
    ("vllm:request_decode_time_seconds", "Decode Time (s)"),
    ("vllm:kv_cache_usage_perc", "KV Cache %"),
    ("vllm:num_requests_running", "Requests Running"),
    ("vllm:num_requests_waiting", "Requests Waiting"),
    ("vllm:prompt_tokens_total", "Prompt Tokens"),
    ("vllm:generation_tokens_total", "Gen Tokens"),
]


def _generate_dashboard(
    collector: "_PlotCollector",
    filepath: str,
) -> bool:
    """Generate a dashboard PNG with key metrics as subplots in a 3-column grid."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # Filter to metrics that have non-flat data
        panels: list[tuple[str, list[_PlotSeries]]] = []
        for metric_id, panel_title in _DASHBOARD_METRICS:
            series = collector.series_for_metric(metric_id)
            if series and not all(_is_flat_series(s.values) for s in series):
                panels.append((panel_title, series))

        if not panels:
            return False

        ncols = 3
        nrows = math.ceil(len(panels) / ncols)
        fig, axes = plt.subplots(
            nrows, ncols, figsize=(5 * ncols, 3.5 * nrows), squeeze=False,
        )

        for idx, (panel_title, series_list) in enumerate(panels):
            row, col = divmod(idx, ncols)
            ax = axes[row][col]
            multi_pod = len({s.pod_name for s in series_list}) > 1

            for sidx, series in enumerate(series_list):
                ds_x, ds_y = _downsample_series(series.values)
                color = _OVERLAY_COLORS[sidx % len(_OVERLAY_COLORS)]
                label = series.label if multi_pod else None
                ax.plot(ds_x, ds_y, linewidth=1, color=color, label=label)

            ax.set_title(panel_title, fontsize=9)
            ax.grid(True, alpha=0.3)
            if multi_pod:
                ax.legend(fontsize=6, loc="best")

        # Hide unused subplots
        for idx in range(len(panels), nrows * ncols):
            row, col = divmod(idx, ncols)
            axes[row][col].set_visible(False)

        fig.suptitle("vLLM Metrics Dashboard", fontsize=12, y=1.0)
        fig.tight_layout()
        fig.savefig(filepath, dpi=100)
        plt.close(fig)
        return True
    except Exception as e:
        sys.stderr.write(f"Failed to generate dashboard {filepath}: {e}\n")
        return False


# --- Metric emitters ---
#
# Each emitter below handles one class of Prometheus metric for a single pod.
# They walk the scrape phases in chronological order (pre -> during_0 ... -> post),
# build a time-series of values, compute summary statistics, and optionally
# register the series with the plot collector for PNG generation.
#
# The three emitters correspond to different Prometheus metric types:
#   _emit_gauge_counter_entries  – gauges and counters (direct sample values)
#   _emit_histogram_entries      – histograms (per-interval mean from _sum/_count deltas)
#   _emit_derived_cache_hit_rate – synthetic ratio computed from two counter metrics


def _emit_derived_cache_hit_rate(
    entries: list[dict[str, Any]],
    pod_name: str,
    role: str,
    phases: dict[str, tuple],
    timestamps: dict[str, str],
    hits_metric: str,
    queries_metric: str,
    derived_id: str,
    short_name: str,
    description: str,
    plot_collector: "_PlotCollector | None" = None,
) -> None:
    """Emit a derived cache hit-rate metric (hits / queries).

    This metric does not exist in the Prometheus output directly.  It is
    computed per phase by dividing the cumulative hits counter by the
    cumulative queries counter, producing a ratio in [0, 1] at each
    scrape point.  Used for both local prefix cache and external
    (cross-instance) prefix cache hit rates.
    """
    ordered = _ordered_phases(phases)

    rate_values: list[float] = []
    rate_ts: list[str] = []

    # For each scrape phase, compute hit_rate = total_hits / total_queries.
    # Both are cumulative counters so the ratio at each point reflects the
    # lifetime hit rate up to that scrape.
    for phase in ordered:
        if phase not in phases:
            continue
        phase_samples = phases[phase][0]
        hits_entries = phase_samples.get(hits_metric, [])
        queries_entries = phase_samples.get(queries_metric, [])
        if not hits_entries or not queries_entries:
            continue
        total_hits = sum(e["value"] for e in hits_entries)
        total_queries = sum(e["value"] for e in queries_entries)
        if total_queries > 0:
            rate_values.append(total_hits / total_queries)
            rate_ts.append(timestamps.get(phase, ""))

    if not rate_values:
        return

    unit = "ratio"
    series_name = f"series.{short_name}.{pod_name}"
    statistics = _compute_statistics(rate_values, unit)

    entry_dict: dict[str, Any] = {
        "name": series_name,
        "metric_ref": {"id": derived_id, "version": 1},
        "component_id": _role_to_component_id(role),
        "type": "derived",
        "unit": unit,
        "description": description,
        "labels": {"pod": pod_name, "role": role},
        "statistics": statistics,
    }

    if len(rate_values) >= 2 and plot_collector:
        plot_key = short_name
        plot_collector.add(
            plot_key, derived_id, rate_ts, rate_values,
            pod_name, short_name, unit,
        )
        entry_dict["_plot_key"] = plot_key

    entries.append(entry_dict)


def _emit_gauge_counter_entries(
    entries: list[dict[str, Any]],
    metric_name: str,
    short_name: str,
    mtype: str,
    unit: str,
    description: str,
    pod_name: str,
    role: str,
    phases: dict[str, tuple],
    timestamps: dict[str, str],
    plot_collector: "_PlotCollector | None" = None,
) -> None:
    """Emit gauge/counter metric entries with statistics and optional plot.

    For gauges (e.g. num_requests_running, kv_cache_usage_perc) and counters
    (e.g. prompt_tokens_total), the raw sample value from each scrape phase
    is used directly.  A separate entry is emitted for each unique Prometheus
    label set (e.g. different engine IDs or model names on the same pod).
    """
    # Collect unique label sets across phases
    ordered = _ordered_phases(phases)
    label_sets: list[dict[str, str]] = []
    for phase in ordered:
        if phase not in phases:
            continue
        phase_samples = phases[phase][0]
        for entry in phase_samples.get(metric_name, []):
            if entry["labels"] not in label_sets:
                label_sets.append(entry["labels"])

    if not label_sets:
        return

    for label_set in label_sets:
        sample_values: list[float] = []
        sample_ts: list[str] = []
        for phase in ordered:
            if phase not in phases:
                continue
            phase_samples = phases[phase][0]
            for entry in phase_samples.get(metric_name, []):
                if entry["labels"] == label_set:
                    ts = timestamps.get(phase, "")
                    sample_values.append(entry["value"])
                    sample_ts.append(ts)
                    break

        if not sample_values:
            continue

        suffix = _label_suffix(label_set)
        series_name = f"series.{short_name}.{pod_name}{suffix}"
        entry_labels = {"pod": pod_name, "role": role}
        entry_labels.update(label_set)

        statistics = _compute_statistics(sample_values, unit)

        entry_dict: dict[str, Any] = {
            "name": series_name,
            "metric_ref": {"id": metric_name, "version": 1},
            "component_id": _role_to_component_id(role),
            "type": mtype,
            "unit": unit,
            "description": description,
            "labels": entry_labels,
            "statistics": statistics,
        }

        if len(sample_values) >= 2 and plot_collector:
            plot_key = f"{short_name}{suffix}"
            title = f"{short_name}{suffix}"
            plot_collector.add(
                plot_key, metric_name, sample_ts, sample_values,
                pod_name, title, unit,
            )
            entry_dict["_plot_key"] = plot_key

        entries.append(entry_dict)


def _emit_histogram_entries(
    entries: list[dict[str, Any]],
    metric_name: str,
    short_name: str,
    mtype: str,
    unit: str,
    description: str,
    pod_name: str,
    role: str,
    phases: dict[str, tuple],
    timestamps: dict[str, str],
    plot_collector: "_PlotCollector | None" = None,
) -> None:
    """Emit histogram entries with per-interval mean from consecutive snapshots.

    Prometheus histograms expose cumulative _sum and _count sub-metrics.
    To get a meaningful time-series we compute the per-interval mean:

        interval_mean = (sum[t] - sum[t-1]) / (count[t] - count[t-1])

    This gives the average value (e.g. average queue time in seconds) for
    requests that completed between two consecutive scrapes, rather than
    the lifetime average which would flatten over time.

    When only a single scrape is available (e.g. post-only), we fall back
    to the overall mean (sum / count) from that snapshot.
    """
    ordered = _ordered_phases(phases)

    # Collect histogram snapshots across phases, grouped by label-set
    label_snapshots: dict[tuple, list[dict[str, Any]]] = {}
    label_dicts: dict[tuple, dict[str, str]] = {}

    for phase in ordered:
        if phase not in phases:
            continue
        phase_samples = phases[phase][0]
        ts = timestamps.get(phase, "")
        for hr in _extract_histogram_snapshots(metric_name, phase_samples, ts):
            key = _labels_key(hr["labels"])
            label_snapshots.setdefault(key, []).append(hr["snapshot"])
            label_dicts[key] = hr["labels"]

    for key, snapshots in label_snapshots.items():
        base_labels = label_dicts[key]
        suffix = _label_suffix(base_labels)
        series_name = f"series.{short_name}.{pod_name}{suffix}"
        entry_labels = {"pod": pod_name, "role": role}
        entry_labels.update(base_labels)

        # Compute per-interval means from consecutive snapshots
        interval_means: list[float] = []
        interval_ts: list[str] = []
        for i in range(1, len(snapshots)):
            prev, curr = snapshots[i - 1], snapshots[i]
            delta_sum = (curr.get("sum") or 0) - (prev.get("sum") or 0)
            delta_count = (curr.get("count") or 0) - (prev.get("count") or 0)
            if delta_count > 0:
                interval_means.append(delta_sum / delta_count)
                interval_ts.append(curr.get("ts", ""))

        # Fallback: single snapshot → overall mean
        if not interval_means and snapshots:
            h = snapshots[-1]
            count = h.get("count") or 0
            total = h.get("sum") or 0
            if count > 0:
                interval_means.append(total / count)
                interval_ts.append(h.get("ts", ""))

        entry_dict: dict[str, Any] = {
            "name": series_name,
            "metric_ref": {"id": metric_name, "version": 1},
            "component_id": _role_to_component_id(role),
            "type": mtype,
            "unit": unit,
            "description": description,
            "labels": entry_labels,
            "statistics": _compute_statistics(interval_means, unit),
        }

        if len(interval_means) >= 2 and plot_collector:
            plot_key = f"{short_name}{suffix}"
            title = f"{short_name} mean{suffix}" if suffix else f"{short_name} mean"
            plot_collector.add(
                plot_key, metric_name, interval_ts, interval_means,
                pod_name, title, unit,
            )
            entry_dict["_plot_key"] = plot_key

        entries.append(entry_dict)


