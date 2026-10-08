#!/usr/bin/env python3

"""
EPP (Endpoint Picker Plugin) log parser for llm-d-benchmark.
Parses structured JSON logs from EPP pods and extracts scheduling metrics.

Usage:
    python3 process_epp_logs.py <results_dir>
    python3 process_epp_logs.py <results_dir> -o <output_dir>    # custom output location
"""

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EppLogEntry:
    pod_name: str
    container: str
    timestamp: Optional[datetime]
    level: str
    msg: str
    raw: Dict[str, Any]


@dataclass
class RequestTrace:
    request_id: str
    assembled_time: Optional[datetime] = None
    picker_complete_time: Optional[datetime] = None
    handled_time: Optional[datetime] = None
    response_complete_time: Optional[datetime] = None
    picked_endpoint: Optional[str] = None
    picked_endpoint_score: Optional[float] = None
    filter_plugin_timings: Dict[str, Tuple[Optional[datetime], Optional[datetime]]] = (
        field(default_factory=dict)
    )
    scorer_plugin_timings: Dict[str, Tuple[Optional[datetime], Optional[datetime]]] = (
        field(default_factory=dict)
    )


@dataclass
class ScoringSnapshot:
    timestamp: datetime
    request_id: str
    endpoint_address: str
    score: float


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Matches "[pod/<pod_name>/<container>] " prefix from kubectl --prefix=true logs
PREFIX_RE = re.compile(r"^\[pod/([^/]+)/([^\]]+)\]\s*")


def parse_timestamp(ts_str: Any) -> Optional[datetime]:
    """Parse an ISO 8601 string or float/int epoch, truncating ns to µs.

    The EPP emits ``ts`` as a float, so string-only handling raises TypeError.
    """
    if not ts_str:
        return None
    if isinstance(ts_str, (int, float)) and not isinstance(ts_str, bool):
        try:
            return datetime.fromtimestamp(ts_str, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(ts_str, str):
        return None
    # Handle nanosecond timestamps by truncating to 6 decimal places
    ts_str = re.sub(r"(\.\d{6})\d+", r"\1", ts_str)
    # Remove trailing Z and parse
    ts_str = ts_str.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            continue
    return None


def parse_log_file(log_path: str) -> List[EppLogEntry]:
    """Parse an EPP pod log file into structured entries."""
    entries = []
    parse_errors = 0

    with open(log_path, "r") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue

            # Extract pod prefix
            m = PREFIX_RE.match(line)
            if not m:
                continue
            pod_name = m.group(1)
            container = m.group(2)
            remainder = line[m.end() :]

            # Try to parse as JSON
            if not remainder.startswith("{"):
                continue
            try:
                data = json.loads(remainder)
            except json.JSONDecodeError:
                parse_errors += 1
                continue

            ts = parse_timestamp(data.get("ts", ""))
            level = data.get("level", "")
            msg = data.get("msg", "")

            entries.append(
                EppLogEntry(
                    pod_name=pod_name,
                    container=container,
                    timestamp=ts,
                    level=level,
                    msg=msg,
                    raw=data,
                )
            )

    return entries


def extract_saturation_config(entries: List[EppLogEntry]) -> Dict[str, Any]:
    """Extract SaturationDetector config from startup logs."""
    for entry in entries:
        if entry.msg == "Creating new SaturationDetector":
            return {
                "queueDepthThreshold": entry.raw.get("queueDepthThreshold"),
                "kvCacheUtilThreshold": entry.raw.get("kvCacheUtilThreshold"),
                "metricsStalenessThreshold": entry.raw.get("metricsStalenessThreshold"),
            }
    return {}


def correlate_requests(entries: List[EppLogEntry]) -> Dict[str, RequestTrace]:
    """Group entries by x-request-id and extract lifecycle timings."""
    traces: Dict[str, RequestTrace] = {}

    for entry in entries:
        rid = entry.raw.get("x-request-id")
        if not rid:
            continue
        if rid not in traces:
            traces[rid] = RequestTrace(request_id=rid)
        trace = traces[rid]

        msg = entry.msg
        ts = entry.timestamp

        if msg == "LLM request assembled":
            trace.assembled_time = ts

        elif msg == "Running filter plugin":
            plugin = entry.raw.get("plugin", "")
            if plugin and plugin not in trace.filter_plugin_timings:
                trace.filter_plugin_timings[plugin] = (ts, None)

        elif msg == "Completed running filter plugin successfully":
            plugin = entry.raw.get("plugin", "")
            if plugin and plugin in trace.filter_plugin_timings:
                start, _ = trace.filter_plugin_timings[plugin]
                trace.filter_plugin_timings[plugin] = (start, ts)

        elif msg == "Running scorer plugin":
            plugin = entry.raw.get("plugin", "")
            if plugin and plugin not in trace.scorer_plugin_timings:
                trace.scorer_plugin_timings[plugin] = (ts, None)

        elif msg == "Completed running scorer plugin successfully":
            plugin = entry.raw.get("plugin", "")
            if plugin and plugin in trace.scorer_plugin_timings:
                start, _ = trace.scorer_plugin_timings[plugin]
                trace.scorer_plugin_timings[plugin] = (start, ts)

        elif msg == "Completed running picker plugin successfully":
            trace.picker_complete_time = ts
            result = entry.raw.get("result", {})
            targets = result.get("TargetEndpoints", [])
            if targets:
                ep = targets[0].get("Endpoint", {})
                trace.picked_endpoint = ep.get("Address", "")
                if ep.get("Port"):
                    trace.picked_endpoint = f"{trace.picked_endpoint}:{ep['Port']}"
                trace.picked_endpoint_score = targets[0].get("Score")

        elif msg == "Request handled":
            trace.handled_time = ts
            if not trace.picked_endpoint:
                trace.picked_endpoint = entry.raw.get("endpoint", "")

        elif msg == "Exiting HandleResponseBodyComplete":
            # Take the latest response body complete as the response finish time
            trace.response_complete_time = ts

    return traces


def extract_scoring_data(
    entries: List[EppLogEntry],
) -> Dict[str, List[ScoringSnapshot]]:
    """Extract per-endpoint scoring data from Candidate pods and Calculated score messages."""
    scoring: Dict[str, List[ScoringSnapshot]] = defaultdict(list)

    for entry in entries:
        ts = entry.timestamp
        if not ts:
            continue
        rid = entry.raw.get("x-request-id", "")

        if entry.msg == "Calculated score":
            ep_info = entry.raw.get("endpoint", {})
            score = entry.raw.get("score")
            if score is not None and isinstance(ep_info, dict):
                name = ep_info.get("name", "")
                ns = ep_info.get("namespace", "")
                key = f"{ns}/{name}" if ns else name
                scoring[key].append(
                    ScoringSnapshot(
                        timestamp=ts,
                        request_id=rid,
                        endpoint_address=key,
                        score=score,
                    )
                )

        elif entry.msg == "Candidate pods for picking":
            for scored in entry.raw.get("endpoints-weighted-score", []):
                ep = scored.get("Endpoint", {})
                addr = ep.get("Address", "")
                port = ep.get("Port", "")
                full_addr = f"{addr}:{port}" if port else addr
                score = scored.get("Score")
                if score is not None:
                    scoring[full_addr].append(
                        ScoringSnapshot(
                            timestamp=ts,
                            request_id=rid,
                            endpoint_address=full_addr,
                            score=score,
                        )
                    )

    return dict(scoring)


def count_errors(entries: List[EppLogEntry]) -> Dict[str, int]:
    """Count error messages."""
    counts: Dict[str, int] = defaultdict(int)
    for entry in entries:
        if entry.level == "error":
            counts[entry.msg] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def compute_stats(values: List[float], unit: str = "") -> Dict[str, Any]:
    """Compute mean/stddev/min/max/p50/p95/p99 for a list of values."""
    if not values:
        return {
            "mean": 0,
            "stddev": 0,
            "min": 0,
            "max": 0,
            "p50": 0,
            "p95": 0,
            "p99": 0,
            "count": 0,
            "unit": unit,
        }
    n = len(values)
    sorted_vals = sorted(values)
    mean = sum(values) / n
    if n > 1:
        variance = sum((v - mean) ** 2 for v in values) / (n - 1)
        stddev = math.sqrt(variance)
    else:
        stddev = 0.0

    def percentile(pct):
        k = (pct / 100.0) * (n - 1)
        f = math.floor(k)
        c = min(f + 1, n - 1)
        if f == c:
            return sorted_vals[int(k)]
        d = k - f
        return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])

    return {
        "mean": mean,
        "stddev": stddev,
        "min": sorted_vals[0],
        "max": sorted_vals[-1],
        "p50": percentile(50),
        "p95": percentile(95),
        "p99": percentile(99),
        "count": n,
        "unit": unit,
    }


# ---------------------------------------------------------------------------
# Aggregation and output
# ---------------------------------------------------------------------------


def aggregate_and_output(
    entries: List[EppLogEntry], output_dir: str, log_path: str
) -> dict:
    """Aggregate all extracted data and write JSON output files."""
    os.makedirs(output_dir, exist_ok=True)

    sat_config = extract_saturation_config(entries)
    traces = correlate_requests(entries)
    scoring_data = extract_scoring_data(entries)
    errors = count_errors(entries)

    # Count JSON vs non-JSON lines for metadata
    total_lines = 0
    parsed_entries = len(entries)
    try:
        with open(log_path, "r") as f:
            total_lines = sum(1 for _ in f)
    except OSError:
        pass

    # --- Dispatch latency ---
    dispatch_latencies = []
    for trace in traces.values():
        if trace.assembled_time and trace.picker_complete_time:
            dt = (trace.picker_complete_time - trace.assembled_time).total_seconds()
            if dt >= 0:
                dispatch_latencies.append(dt)

    # --- Plugin latencies ---
    filter_latencies: Dict[str, List[float]] = defaultdict(list)
    scorer_latencies: Dict[str, List[float]] = defaultdict(list)
    for trace in traces.values():
        for plugin, (start, end) in trace.filter_plugin_timings.items():
            if start and end:
                dt = (end - start).total_seconds()
                if dt >= 0:
                    filter_latencies[plugin].append(dt)
        for plugin, (start, end) in trace.scorer_plugin_timings.items():
            if start and end:
                dt = (end - start).total_seconds()
                if dt >= 0:
                    scorer_latencies[plugin].append(dt)

    # --- Request distribution ---
    request_dist: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"count": 0})
    for trace in traces.values():
        if trace.picked_endpoint:
            request_dist[trace.picked_endpoint]["count"] += 1

    # --- Endpoint scores ---
    endpoint_scores: Dict[str, Dict[str, Any]] = {}
    for addr, snapshots in scoring_data.items():
        endpoint_scores[addr] = compute_stats([s.score for s in snapshots], "score")

    # --- Build summary ---
    summary = {
        "_metadata": {
            "source_file": os.path.basename(log_path),
            "total_log_lines": total_lines,
            "parsed_entries": parsed_entries,
            "total_requests": len(traces),
            "parse_errors": total_lines - parsed_entries,
        },
        "saturation_config": sat_config,
        "dispatch_latency": compute_stats(dispatch_latencies, "seconds"),
        "plugin_latencies": {
            "filter": {
                p: compute_stats(v, "seconds") for p, v in filter_latencies.items()
            },
            "scorer": {
                p: compute_stats(v, "seconds") for p, v in scorer_latencies.items()
            },
        },
        "request_distribution": dict(request_dist),
        "endpoint_scores": endpoint_scores,
        "error_counts": errors,
    }

    # --- Build timeseries ---
    ts_data: Dict[str, Any] = {
        "scoring_timeseries": {},
        "dispatch_latency_timeseries": {},
    }

    for addr, snapshots in scoring_data.items():
        ts_data["scoring_timeseries"][addr] = {
            "timestamps": [s.timestamp.isoformat() for s in snapshots],
            "scores": [s.score for s in snapshots],
            "request_ids": [s.request_id for s in snapshots],
        }

    # Dispatch latency timeseries (sorted by time)
    latency_points = []
    for trace in traces.values():
        if trace.assembled_time and trace.picker_complete_time:
            dt = (trace.picker_complete_time - trace.assembled_time).total_seconds()
            if dt >= 0:
                latency_points.append((trace.assembled_time, dt, trace.request_id))
    latency_points.sort(key=lambda x: x[0])
    ts_data["dispatch_latency_timeseries"] = {
        "timestamps": [p[0].isoformat() for p in latency_points],
        "latencies_seconds": [p[1] for p in latency_points],
        "request_ids": [p[2] for p in latency_points],
    }

    # --- Write output files ---
    summary_path = os.path.join(output_dir, "epp_metrics_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Summary written to {summary_path}")

    timeseries_path = os.path.join(output_dir, "epp_timeseries.json")
    with open(timeseries_path, "w") as f:
        json.dump(ts_data, f, indent=2, default=str)
    print(f"  Timeseries written to {timeseries_path}")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Parse EPP pod logs and extract scheduling metrics"
    )
    parser.add_argument(
        "results_dir", help="Results directory containing logs/epp_pods.log"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Custom output directory (default: <results_dir>/epp_metrics)",
    )
    args = parser.parse_args()

    results_dir = args.results_dir
    log_path = os.path.join(results_dir, "logs", "epp_pods.log")

    if not os.path.isfile(log_path):
        # Also check if the log is directly in results_dir (standalone usage)
        alt_path = os.path.join(results_dir, "epp_pods.log")
        if os.path.isfile(alt_path):
            log_path = alt_path
        else:
            print(f"EPP log file not found: {log_path}")
            print("No EPP logs to process.")
            sys.exit(0)

    if os.path.getsize(log_path) == 0:
        print(f"EPP log file is empty: {log_path}")
        print("No EPP logs to process.")
        sys.exit(0)

    output_dir = args.output_dir or os.path.join(results_dir, "metrics")

    print(f"Parsing EPP logs from {log_path} ...")
    entries = parse_log_file(log_path)
    print(f"  Parsed {len(entries)} structured log entries")

    if not entries:
        print("  No structured entries found. Nothing to aggregate.")
        sys.exit(0)

    print("Aggregating metrics ...")
    aggregate_and_output(entries, output_dir, log_path)

    print("Done.")


if __name__ == "__main__":
    main()
