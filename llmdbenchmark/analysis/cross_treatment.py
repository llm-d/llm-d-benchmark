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
# (dotted path into the YAML → column name, unit)
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
    """Generate bar charts comparing key metrics across treatments."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
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
    ]

    labels = [r["treatment"] for r in rows]
    generated = 0

    for col_name, title, unit, higher_is_better in plot_specs:
        values = [r.get(col_name) for r in rows]
        # Skip if all None
        if all(v is None for v in values):
            continue

        values = [float(v) if v is not None else 0.0 for v in values]

        fig, ax = plt.subplots(figsize=(max(8, len(labels) * 1.2), 5))
        colors = ["#2ecc71" if higher_is_better else "#e74c3c"] * len(values)

        # Highlight best
        if higher_is_better:
            best_idx = values.index(max(values))
        else:
            non_zero = [v for v in values if v > 0]
            best_idx = values.index(min(non_zero)) if non_zero else 0
        colors[best_idx] = "#3498db"

        bars = ax.bar(range(len(labels)), values, color=colors, alpha=0.8)

        # Add value labels on bars
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                f"{val:.4f}" if val < 10 else f"{val:.1f}",
                ha="center", va="bottom", fontsize=8,
            )

        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_ylabel(unit)
        ax.set_title(title)
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
    degrades under load — useful when treatments sweep concurrency
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
    # (e.g., "conc1", "conc8", "conc32" → sorted by number)
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

    generated = 0

    for x_col, y_col, title, x_label, y_label in scatter_specs:
        x_vals = [r.get(x_col) for r in sorted_rows]
        y_vals = [r.get(y_col) for r in sorted_rows]
        labels = [r["treatment"] for r in sorted_rows]

        # Skip if insufficient data
        valid_pairs = [(x, y, l) for x, y, l in zip(x_vals, y_vals, labels)
                       if x is not None and y is not None]
        if len(valid_pairs) < 2:
            continue

        xs, ys, lbls = zip(*valid_pairs)
        xs = [float(x) for x in xs]
        ys = [float(y) for y in ys]

        fig, ax = plt.subplots(figsize=(10, 6))

        # Line + scatter
        ax.plot(xs, ys, "o-", color="#3498db", markersize=8, linewidth=2, alpha=0.8)

        # Label each point with treatment name
        for x, y, lbl in zip(xs, ys, lbls):
            # Shorten label for readability
            short = lbl.split("_")[-1] if "_" in lbl else lbl[-12:]
            ax.annotate(
                short, (x, y),
                textcoords="offset points", xytext=(5, 8),
                fontsize=7, alpha=0.7,
            )

        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title)
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
        pr_file = subdir / "per_request_lifecycle_metrics.json"
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

            # Shorten label for legend
            label = treatment.split("_")[-1] if "_" in treatment else treatment[-20:]
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
