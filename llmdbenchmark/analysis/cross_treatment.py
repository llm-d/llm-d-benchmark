"""Cross-treatment comparison analysis.

Reads benchmark report v0.2 YAML files from multiple result directories,
extracts key metrics, and writes a CSV summary table (one row per treatment).

Usage from the CLI via ``--analyze`` (automatically invoked after
per-treatment analysis completes).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

from llmdbenchmark.analysis.session_metrics import (
    SESSION_METRICS_OF_INTEREST,
    deep_get,
)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext

# Metrics to extract from benchmark report v0.2
# (dotted path into the YAML to column name, unit)
METRICS_OF_INTEREST = [
    (
        "results.request_performance.aggregate.latency.time_to_first_token.mean",
        "ttft_mean_s",
    ),
    (
        "results.request_performance.aggregate.latency.time_to_first_token.p50",
        "ttft_p50_s",
    ),
    (
        "results.request_performance.aggregate.latency.time_to_first_token.p99",
        "ttft_p99_s",
    ),
    (
        "results.request_performance.aggregate.latency.time_per_output_token.mean",
        "tpot_mean_s",
    ),
    (
        "results.request_performance.aggregate.latency.time_per_output_token.p99",
        "tpot_p99_s",
    ),
    (
        "results.request_performance.aggregate.latency.inter_token_latency.mean",
        "itl_mean_s",
    ),
    (
        "results.request_performance.aggregate.latency.inter_token_latency.p99",
        "itl_p99_s",
    ),
    (
        "results.request_performance.aggregate.latency.request_latency.mean",
        "e2e_mean_s",
    ),
    ("results.request_performance.aggregate.latency.request_latency.p99", "e2e_p99_s"),
    (
        "results.request_performance.aggregate.throughput.output_token_rate.mean",
        "output_tps",
    ),
    (
        "results.request_performance.aggregate.throughput.request_rate.mean",
        "request_qps",
    ),
    (
        "results.request_performance.aggregate.throughput.total_token_rate.mean",
        "total_tps",
    ),
    ("results.request_performance.aggregate.requests.total", "total_requests"),
    ("results.request_performance.aggregate.requests.failures", "failures"),
]

# Seconds per unit for the "*_s" columns. Reports keep each statistic in its
# harness's own unit: vLLM and InferenceMAX use ms, aiperf and inference-perf s.
_SECONDS_PER_UNIT = {"s": 1.0, "ms": 0.001, "s/token": 1.0, "ms/token": 0.001}


def _column_value(report: dict, dotted_path: str, col_name: str):
    """Read a statistic for a column, converting "*_s" columns to seconds."""
    value = deep_get(report, dotted_path)
    if value is None or not col_name.endswith("_s"):
        return value
    units = deep_get(report, dotted_path.rsplit(".", 1)[0] + ".units")
    factor = _SECONDS_PER_UNIT.get(units)
    return value * factor if factor is not None else None


def generate_cross_treatment_summary(
    results_dir: Path,
    output_dir: Path | None = None,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate cross-treatment comparison from benchmark report v0.2 files.

    Args:
        results_dir: Parent directory containing per-treatment subdirs.
        output_dir: Where to write the CSV (default: results_dir/cross-treatment-comparison).
        context: Optional execution context for logging.

    Returns:
        Number of treatments compared.
    """
    if yaml is None:
        _log(context, "PyYAML not available -- skipping cross-treatment analysis")
        return 0

    if output_dir is None:
        output_dir = results_dir / "cross-treatment-comparison"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect all benchmark report v0.2 files across treatment subdirs
    rows: list[dict] = []

    for subdir in sorted(results_dir.iterdir()):
        if not subdir.is_dir():
            continue

        # Find benchmark report v0.2 files
        br_files = sorted(subdir.glob("benchmark_report_v0.2*yaml"))
        if not br_files:
            continue

        for br_file in br_files:
            try:
                with open(br_file, encoding="utf-8") as f:
                    report = yaml.safe_load(f)
                if not report:
                    continue
            except Exception:
                continue

            row: dict = {"treatment": subdir.name, "source_file": br_file.name}

            for dotted_path, col_name in METRICS_OF_INTEREST:
                row[col_name] = _column_value(report, dotted_path, col_name)

            for dotted_path, col_name in SESSION_METRICS_OF_INTEREST:
                row[col_name] = _column_value(report, dotted_path, col_name)

            # Extract workload metadata
            row["input_len_mean"] = deep_get(
                report,
                "results.request_performance.aggregate.requests.input_length.mean",
            )
            row["output_len_mean"] = deep_get(
                report,
                "results.request_performance.aggregate.requests.output_length.mean",
            )
            row["tool"] = deep_get(report, "scenario.load.standardized.tool", "")
            row["rate_qps"] = (
                deep_get(report, "scenario.load.standardized.rate_qps", "") or ""
            )

            rows.append(row)

    if not rows:
        _log(context, "No benchmark report v0.2 files found for comparison")
        return 0

    # Write CSV summary
    csv_path = output_dir / "treatment_comparison.csv"
    fieldnames = (
        ["treatment", "source_file"]
        + [m[1] for m in METRICS_OF_INTEREST]
        + [m[1] for m in SESSION_METRICS_OF_INTEREST]
        + ["input_len_mean", "output_len_mean", "tool", "rate_qps"]
    )
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    _log(context, f"Cross-treatment CSV: {csv_path} ({len(rows)} entries)")

    return len(rows)


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
