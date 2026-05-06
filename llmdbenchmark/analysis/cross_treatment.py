"""Cross-treatment comparison analysis.

Reads benchmark report v0.2 YAML files from multiple result directories,
extracts key metrics, and produces:

1. A CSV summary table (one row per treatment)
2. Comparison bar charts (if matplotlib is available)

Usage from the CLI via ``--analyze`` (automatically invoked after
per-treatment analysis completes).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext

# Metrics to extract from benchmark report v0.2
# (dotted path into the YAML to column name, unit)
METRICS_OF_INTEREST = [
    ("results.request_performance.aggregate.latency.time_to_first_token.mean", "ttft_mean_s"),
    ("results.request_performance.aggregate.latency.time_to_first_token.p50", "ttft_p50_s"),
    ("results.request_performance.aggregate.latency.time_to_first_token.p99", "ttft_p99_s"),
    ("results.request_performance.aggregate.latency.time_per_output_token.mean", "tpot_mean_s"),
    ("results.request_performance.aggregate.latency.time_per_output_token.p99", "tpot_p99_s"),
    ("results.request_performance.aggregate.latency.inter_token_latency.mean", "itl_mean_s"),
    ("results.request_performance.aggregate.latency.inter_token_latency.p99", "itl_p99_s"),
    ("results.request_performance.aggregate.latency.request_latency.mean", "e2e_mean_s"),
    ("results.request_performance.aggregate.latency.request_latency.p99", "e2e_p99_s"),
    ("results.request_performance.aggregate.throughput.output_token_rate.mean", "output_tps"),
    ("results.request_performance.aggregate.throughput.request_rate.mean", "request_qps"),
    ("results.request_performance.aggregate.throughput.total_token_rate.mean", "total_tps"),
    ("results.request_performance.aggregate.requests.total", "total_requests"),
    ("results.request_performance.aggregate.requests.failures", "failures"),
]


def _deep_get(d: dict, dotted_key: str, default=None):
    """Traverse nested dict by dotted key path."""
    keys = dotted_key.split(".")
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
        if d is default:
            return default
    return d


def _shorten_treatment_label(name: str) -> str:
    """Extract a readable treatment label from a results directory name.

    Strips the harness prefix, experiment ID, and parallelism suffix to
    extract just the treatment-specific part.

    Examples:
        inference-perf-grp40-splen8k-1773947901-i5e39v_1 -> grp40-splen8k
        inference-perf-conc32-1773947901-abc123_1        -> conc32
        inference-perf-1773947901-xyz789_1               -> default
        qlen100-olen300                                  -> qlen100-olen300
    """
    import re

    # Strip trailing _N (parallelism index)
    name = re.sub(r"_\d+$", "", name)

    # Strip trailing random ID (e.g., -i5e39v or -abc123)
    name = re.sub(r"-[a-z0-9]{6,8}$", "", name)

    # Strip trailing timestamp/experiment ID (e.g., -1773947901)
    name = re.sub(r"-\d{10,}$", "", name)

    # Strip harness prefix (e.g., inference-perf-)
    for prefix in ("inference-perf-", "guidellm-", "vllm-benchmark-", "inferencemax-", "nop-"):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    return name if name else "default"


def generate_cross_treatment_summary(
    results_dir: Path,
    output_dir: Path | None = None,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate cross-treatment comparison from benchmark report v0.2 files.

    Args:
        results_dir: Parent directory containing per-treatment subdirs.
        output_dir: Where to write CSV and plots (default: results_dir/analysis/comparison).
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
                value = _deep_get(report, dotted_path)
                row[col_name] = value

            # Extract workload metadata
            row["input_len_mean"] = _deep_get(
                report, "results.request_performance.aggregate.requests.input_length.mean"
            )
            row["output_len_mean"] = _deep_get(
                report, "results.request_performance.aggregate.requests.output_length.mean"
            )
            row["tool"] = _deep_get(report, "scenario.load.standardized.tool", "")
            row["rate_qps"] = _deep_get(report, "scenario.load.standardized.rate_qps", "")

            rows.append(row)

    if not rows:
        _log(context, "No benchmark report v0.2 files found for comparison")
        return 0

    # Write CSV summary
    csv_path = output_dir / "treatment_comparison.csv"
    fieldnames = ["treatment", "source_file"] + [m[1] for m in METRICS_OF_INTEREST] + [
        "input_len_mean", "output_len_mean", "tool", "rate_qps",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    _log(context, f"Cross-treatment CSV: {csv_path} ({len(rows)} entries)")

    # Generate comparison plots (aggregate)
    plot_count = _generate_comparison_plots(rows, output_dir, context)

    # Generate overlaid per-request CDF plots across treatments
    plot_count += _generate_overlaid_cdf_plots(results_dir, output_dir, context)

    return len(rows)


def _generate_comparison_plots(
    rows: list[dict],
    output_dir: Path,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate bar charts comparing key metrics across treatments.

    Aggregates multiple stages per treatment into mean with min/max
    error bars, so each treatment gets one bar instead of one per stage.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        _log(context, "matplotlib not available -- skipping comparison plots")
        return 0

    if len(rows) < 2:
        _log(context, "Only 1 treatment -- skipping comparison plots")
        return 0

    # Metrics to plot (column_name, title, unit, higher_is_better)
    plot_specs = [
        ("ttft_mean_s", "Time to First Token (Mean)", "seconds", False),
        ("tpot_mean_s", "Time per Output Token (Mean)", "seconds", False),
        ("itl_mean_s", "Inter-Token Latency (Mean)", "seconds", False),
        ("e2e_mean_s", "End-to-End Latency (Mean)", "seconds", False),
        ("output_tps", "Output Token Throughput", "tokens/s", True),
        ("request_qps", "Request Throughput", "queries/s", True),
        ("ttft_p99_s", "TTFT P99", "seconds", False),
        ("tpot_p99_s", "TPOT P99", "seconds", False),
        ("failures", "Request Failures", "count", False),
    ]

    # Aggregate rows by treatment (average across stages)
    from collections import defaultdict
    treatment_values: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        label = _shorten_treatment_label(r["treatment"])
        treatment_values[label].append(r)

    treatment_labels = sorted(treatment_values.keys())
    if len(treatment_labels) < 2:
        return 0

    # Color palette
    bar_colors = [
        "#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f39c12",
        "#1abc9c", "#e67e22", "#34495e", "#16a085", "#c0392b",
    ]

    generated = 0

    for col_name, title, unit, higher_is_better in plot_specs:
        means = []
        mins = []
        maxs = []
        has_data = False

        for label in treatment_labels:
            vals = [
                float(r[col_name]) for r in treatment_values[label]
                if r.get(col_name) is not None
            ]
            if vals:
                has_data = True
                m = sum(vals) / len(vals)
                means.append(m)
                mins.append(m - min(vals))
                maxs.append(max(vals) - m)
            else:
                means.append(0.0)
                mins.append(0.0)
                maxs.append(0.0)

        if not has_data:
            continue

        fig, ax = plt.subplots(figsize=(max(8, len(treatment_labels) * 1.5), 5))
        colors = [bar_colors[i % len(bar_colors)] for i in range(len(treatment_labels))]

        # Highlight best treatment
        non_zero_means = [m for m in means if m > 0]
        if non_zero_means:
            if higher_is_better:
                best_idx = means.index(max(non_zero_means))
            else:
                best_idx = means.index(min(non_zero_means))
            colors[best_idx] = "#2ecc71"  # green for best

        x_pos = range(len(treatment_labels))
        bars = ax.bar(
            x_pos, means, color=colors, alpha=0.85,
            yerr=[mins, maxs], capsize=4, error_kw={"linewidth": 1.5},
        )

        # Add value labels on bars
        for bar, val in zip(bars, means):
            text = f"{val:.4f}" if val < 10 else f"{val:.1f}"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(maxs) * 0.02,
                text, ha="center", va="bottom", fontsize=8, fontweight="bold",
            )

        # Add stage count annotation
        for i, label in enumerate(treatment_labels):
            n_stages = len(treatment_values[label])
            ax.text(
                i, 0, f"n={n_stages}",
                ha="center", va="bottom", fontsize=7, color="gray",
            )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(treatment_labels, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel(unit)
        ax.set_title(f"{title}\n(mean across stages, error bars = min/max)")
        ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        plot_path = output_dir / f"compare_{col_name}.png"
        plt.savefig(str(plot_path), dpi=150)
        plt.close()

        generated += 1

    # --- Scatter / line plots: latency vs throughput curves ---
    generated += _generate_scatter_plots(rows, output_dir, context)

    if generated:
        _log(context, f"Generated {generated} comparison plot(s) in {output_dir}")

    return generated


def _generate_scatter_plots(
    rows: list[dict],
    output_dir: Path,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate scatter/line plots showing metric relationships across treatments.

    Produces latency-vs-throughput curves that show how performance
    degrades under load -- useful when treatments sweep concurrency
    or request rate.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return 0

    if len(rows) < 2:
        return 0

    # Try to extract a numeric sort key from treatment names
    # (e.g., "conc1", "conc8", "conc32" to sorted by number)
    import re

    def _sort_key(row):
        nums = re.findall(r"\d+", row.get("treatment", ""))
        return int(nums[-1]) if nums else 0

    sorted_rows = sorted(rows, key=_sort_key)

    # Scatter plot specs: (x_col, y_col, title, x_label, y_label)
    scatter_specs = [
        ("request_qps", "ttft_mean_s", "TTFT vs Request Rate", "Request Rate (QPS)", "TTFT Mean (s)"),
        ("request_qps", "tpot_mean_s", "TPOT vs Request Rate", "Request Rate (QPS)", "TPOT Mean (s)"),
        ("request_qps", "itl_mean_s", "ITL vs Request Rate", "Request Rate (QPS)", "ITL Mean (s)"),
        ("request_qps", "e2e_mean_s", "E2E Latency vs Request Rate", "Request Rate (QPS)", "E2E Mean (s)"),
        ("output_tps", "ttft_mean_s", "TTFT vs Throughput", "Output Throughput (tokens/s)", "TTFT Mean (s)"),
        ("output_tps", "tpot_mean_s", "TPOT vs Throughput", "Output Throughput (tokens/s)", "TPOT Mean (s)"),
        ("request_qps", "ttft_p99_s", "TTFT P99 vs Request Rate", "Request Rate (QPS)", "TTFT P99 (s)"),
        ("request_qps", "tpot_p99_s", "TPOT P99 vs Request Rate", "Request Rate (QPS)", "TPOT P99 (s)"),
    ]

    # Color palette
    colors = [
        "#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f39c12",
        "#1abc9c", "#e67e22", "#34495e", "#16a085", "#c0392b",
    ]
    markers = ["o", "s", "^", "D", "v", "P", "*", "X", "h", "p"]

    generated = 0

    for x_col, y_col, title, x_label, y_label in scatter_specs:
        # Build per-treatment data points
        treatment_points: dict[str, list[tuple[float, float]]] = {}
        for r in sorted_rows:
            x = r.get(x_col)
            y = r.get(y_col)
            if x is None or y is None:
                continue
            label = _shorten_treatment_label(r["treatment"])
            if label not in treatment_points:
                treatment_points[label] = []
            treatment_points[label].append((float(x), float(y)))

        if len(treatment_points) < 1:
            continue
        # Need at least 2 total data points across all treatments
        total_points = sum(len(pts) for pts in treatment_points.values())
        if total_points < 2:
            continue

        fig, ax = plt.subplots(figsize=(10, 6))

        for i, (label, points) in enumerate(sorted(treatment_points.items())):
            xs, ys = zip(*points)
            color = colors[i % len(colors)]
            marker = markers[i % len(markers)]
            ax.plot(xs, ys, f"{marker}-", color=color, markersize=8,
                    linewidth=2, alpha=0.8, label=label)

        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title)
        ax.legend(fontsize=8, loc="best")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = output_dir / f"scatter_{x_col}_vs_{y_col}.png"
        plt.savefig(str(plot_path), dpi=150)
        plt.close()

        generated += 1

    return generated


def _extract_per_request_metrics(pr_file: Path) -> dict[str, list[float]]:
    """Extract per-request TTFT, TPOT, ITL, E2E from a per_request JSON file.

    Returns dict with keys 'ttft', 'tpot', 'itl', 'e2e', each a list of floats.
    """
    import json

    with open(pr_file, encoding="utf-8") as f:
        raw = json.load(f)

    metrics: dict[str, list[float]] = {"ttft": [], "tpot": [], "itl": [], "e2e": []}

    for r in raw:
        info = r.get("info", {})
        start = r.get("start_time")
        end = r.get("end_time")
        token_times = info.get("output_token_times", [])

        if start is None or end is None or not token_times:
            continue

        metrics["e2e"].append(end - start)
        metrics["ttft"].append(token_times[0] - start)

        if len(token_times) > 1:
            decode_time = token_times[-1] - token_times[0]
            metrics["tpot"].append(decode_time / (len(token_times) - 1))

            for i in range(1, len(token_times)):
                metrics["itl"].append(token_times[i] - token_times[i - 1])

    return metrics


def _generate_overlaid_cdf_plots(
    results_dir: Path,
    output_dir: Path,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate overlaid CDF plots comparing per-request distributions across treatments.

    Each treatment gets its own curve on the same axes, making it easy
    to see how the full distribution shifts between configurations.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return 0

    # Collect per-request data from each treatment directory
    treatment_data: dict[str, dict[str, list[float]]] = {}

    for subdir in sorted(results_dir.iterdir()):
        if not subdir.is_dir():
            continue
        # Check both root and analysis/ subdirectory (inference-perf --analyze
        # moves the file into analysis/)
        pr_file = subdir / "per_request_lifecycle_metrics.json"
        if not pr_file.exists():
            pr_file = subdir / "analysis" / "per_request_lifecycle_metrics.json"
        if not pr_file.exists():
            continue
        try:
            metrics = _extract_per_request_metrics(pr_file)
            if any(len(v) > 0 for v in metrics.values()):
                treatment_data[subdir.name] = metrics
        except Exception:
            continue

    if len(treatment_data) < 2:
        return 0

    # Color palette for distinguishing treatments
    colors = [
        "#e74c3c", "#3498db", "#2ecc71", "#9b59b6", "#f39c12",
        "#1abc9c", "#e67e22", "#34495e", "#16a085", "#c0392b",
    ]

    metric_specs = [
        ("ttft", "TTFT CDF Comparison", "Time to First Token (s)"),
        ("tpot", "TPOT CDF Comparison", "Time per Output Token (s)"),
        ("e2e", "E2E Latency CDF Comparison", "End-to-End Latency (s)"),
        ("itl", "ITL CDF Comparison", "Inter-Token Latency (s)"),
    ]

    generated = 0
    treatments = list(treatment_data.keys())

    for metric_key, title, xlabel in metric_specs:
        # Check at least 2 treatments have data for this metric
        treatments_with_data = [
            t for t in treatments if len(treatment_data[t].get(metric_key, [])) > 0
        ]
        if len(treatments_with_data) < 2:
            continue

        fig, ax = plt.subplots(figsize=(10, 6))

        for i, treatment in enumerate(treatments_with_data):
            values = sorted(treatment_data[treatment][metric_key])
            n = len(values)
            cdf = [j / n for j in range(n)]

            label = _shorten_treatment_label(treatment)
            color = colors[i % len(colors)]

            ax.plot(values, cdf, linewidth=2, label=f"{label} (n={n})",
                    color=color, alpha=0.8)

        ax.axhline(0.5, color="gray", linestyle=":", alpha=0.4, linewidth=1)
        ax.axhline(0.99, color="gray", linestyle=":", alpha=0.4, linewidth=1)
        ax.text(ax.get_xlim()[1] * 0.98, 0.5, "P50", ha="right",
                va="bottom", fontsize=8, color="gray")
        ax.text(ax.get_xlim()[1] * 0.98, 0.99, "P99", ha="right",
                va="bottom", fontsize=8, color="gray")

        ax.set_xlabel(xlabel)
        ax.set_ylabel("Cumulative Probability")
        ax.set_title(title)
        ax.legend(fontsize=8, loc="lower right")
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(str(output_dir / f"cdf_overlay_{metric_key}.png"), dpi=150)
        plt.close()
        generated += 1

    if generated:
        _log(context, f"Generated {generated} overlaid CDF plot(s)")

    return generated


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
