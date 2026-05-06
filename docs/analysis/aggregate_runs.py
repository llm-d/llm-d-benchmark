#!/usr/bin/env python3

# Copyright 2025 The llm-d Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Aggregate benchmark results across repeated runs.

Reads Benchmark Report v0.2 YAML files from multiple runs of the same
experiment and produces an aggregated summary with mean, std dev, min,
and max for key performance metrics.

Usage:
    python3 aggregate_runs.py \
        --results-prefix /requests \
        --harness inference-perf \
        --stack llm-d-7b-base \
        --run-ids 1742680000_workload1_run1 1742680000_workload1_run2 \
        --output /requests/1742680000_workload1_aggregated
"""

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def load_yaml(filepath):
    """Load a YAML file, falling back to a simple parser if PyYAML is unavailable."""
    if yaml is not None:
        with open(filepath) as f:
            return yaml.safe_load(f)
    # Fallback: try loading as JSON (benchmark reports may also be in JSON)
    with open(filepath) as f:
        return json.load(f)


def find_benchmark_reports(results_dir):
    """Find Benchmark Report v0.2 YAML/JSON files in a results directory."""
    reports = []
    for pattern in ["benchmark_report_v0.2*.yaml", "benchmark_report_v0.2*.json"]:
        reports.extend(glob.glob(os.path.join(results_dir, pattern)))
    return sorted(reports)


def extract_aggregate_metrics(report_data):
    """Extract the aggregate metrics section from a benchmark report.

    Returns a flat dict of metric_path -> value for all numeric values
    under results.request_performance.aggregate.
    """
    metrics = {}
    results = report_data.get("results", {})
    aggregate = results.get("request_performance", {}).get("aggregate", {})
    _flatten_dict(aggregate, "", metrics)
    return metrics


def _flatten_dict(d, prefix, out):
    """Recursively flatten a nested dict into dot-separated keys with numeric values."""
    if isinstance(d, dict):
        for key, value in d.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            _flatten_dict(value, new_prefix, out)
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out[prefix] = d


def compute_aggregated_stats(all_metrics):
    """Compute mean, std dev, min, max across runs for each metric.

    Args:
        all_metrics: list of dicts, each from extract_aggregate_metrics()

    Returns:
        dict of metric_path -> {mean, std, min, max, count, values}
    """
    # Collect values per metric across all runs
    combined = {}
    for run_metrics in all_metrics:
        for key, value in run_metrics.items():
            if key not in combined:
                combined[key] = []
            combined[key].append(value)

    stats = {}
    for key, values in combined.items():
        n = len(values)
        mean = sum(values) / n
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            std = math.sqrt(variance)
        else:
            std = 0.0
        stats[key] = {
            "mean": mean,
            "std": std,
            "min": min(values),
            "max": max(values),
            "count": n,
            "values": values,
        }
    return stats


def format_summary_text(stats, run_ids):
    """Format aggregated stats as a human-readable text table."""
    lines = []
    lines.append(f"Aggregated Benchmark Results ({len(run_ids)} runs)")
    lines.append(f"Run IDs: {', '.join(run_ids)}")
    lines.append("=" * 90)
    lines.append(
        f"{'Metric':<55} {'Mean':>10} {'Std':>10} {'Min':>10} {'Max':>10}"
    )
    lines.append("-" * 90)

    for key in sorted(stats.keys()):
        s = stats[key]
        # Skip non-leaf metrics (units, count fields that aren't performance data)
        if key.endswith(".units"):
            continue
        lines.append(
            f"{key:<55} {s['mean']:>10.4f} {s['std']:>10.4f} "
            f"{s['min']:>10.4f} {s['max']:>10.4f}"
        )

    lines.append("=" * 90)
    return "\n".join(lines)


def format_summary_json(stats, run_ids):
    """Format aggregated stats as a JSON-serializable dict."""
    summary = {
        "aggregation": {
            "run_count": len(run_ids),
            "run_ids": run_ids,
        },
        "metrics": {},
    }
    for key, s in sorted(stats.items()):
        if key.endswith(".units"):
            continue
        summary["metrics"][key] = {
            "mean": s["mean"],
            "std": s["std"],
            "min": s["min"],
            "max": s["max"],
            "count": s["count"],
        }
    return summary


def find_results_dir(results_prefix, harness, stack, run_id):
    """Find the actual results directory for a given run ID.

    The directory naming convention is:
        {results_prefix}/{harness}_{run_id}_{stack}[_N]
    where _N is the parallelism index.
    """
    base = os.path.join(results_prefix, f"{harness}_{run_id}_{stack}")
    # Try exact match first
    if os.path.isdir(base):
        return base
    # Try with parallelism suffix (_1, _2, etc.)
    for suffix_dir in sorted(glob.glob(f"{base}_*")):
        if os.path.isdir(suffix_dir):
            return suffix_dir
    # Try without stack name (run_only.sh may not include it)
    base_no_stack = os.path.join(results_prefix, run_id)
    if os.path.isdir(base_no_stack):
        return base_no_stack
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Aggregate benchmark results across repeated runs."
    )
    parser.add_argument(
        "--results-prefix",
        required=True,
        help="Root directory containing results (e.g., /requests)",
    )
    parser.add_argument(
        "--harness",
        required=True,
        help="Harness name (e.g., inference-perf)",
    )
    parser.add_argument(
        "--stack",
        required=True,
        help="Stack name (e.g., llm-d-7b-base)",
    )
    parser.add_argument(
        "--run-ids",
        nargs="+",
        required=True,
        help="List of run IDs to aggregate",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for aggregated results",
    )

    args = parser.parse_args()

    # Collect benchmark reports from all runs
    all_metrics = []
    found_run_ids = []

    for run_id in args.run_ids:
        results_dir = find_results_dir(
            args.results_prefix, args.harness, args.stack, run_id
        )
        if results_dir is None:
            print(f"Warning: no results directory found for run_id={run_id}, skipping")
            continue

        reports = find_benchmark_reports(results_dir)
        if not reports:
            print(
                f"Warning: no benchmark report v0.2 files found in {results_dir}, "
                "skipping"
            )
            continue

        # Use the first (or only) benchmark report
        report_path = reports[0]
        print(f"Loading {report_path}")
        report_data = load_yaml(report_path)
        metrics = extract_aggregate_metrics(report_data)

        if metrics:
            all_metrics.append(metrics)
            found_run_ids.append(run_id)
        else:
            print(f"Warning: no aggregate metrics found in {report_path}")

    if len(all_metrics) < 2:
        print(
            f"Error: need at least 2 runs to aggregate, found {len(all_metrics)}. "
            "Skipping aggregation."
        )
        return 1

    # Compute aggregated statistics
    stats = compute_aggregated_stats(all_metrics)

    # Write outputs
    os.makedirs(args.output, exist_ok=True)

    # Write text summary
    summary_text = format_summary_text(stats, found_run_ids)
    text_path = os.path.join(args.output, "aggregated_summary.txt")
    with open(text_path, "w") as f:
        f.write(summary_text)
    print(f"\nWritten: {text_path}")
    print(summary_text)

    # Write JSON summary
    summary_json = format_summary_json(stats, found_run_ids)
    json_path = os.path.join(args.output, "aggregated_summary.json")
    with open(json_path, "w") as f:
        json.dump(summary_json, f, indent=2)
    print(f"Written: {json_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
