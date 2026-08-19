"""Session-lifecycle bar charts from benchmark report v0.2 files.

Reads all ``benchmark_report_v0.2,_*_session_lifecycle_metrics.json.yaml`` files
in a results directory and produces bar charts in ``analysis/session/``.
"""

from __future__ import annotations

from pathlib import Path

try:
    from llmdbenchmark.analysis.session_metrics import (
        SESSION_METRICS_OF_INTEREST,
        deep_get,
    )
except ImportError:  # in-pod: shipped flat, no llmdbenchmark package
    from session_metrics import SESSION_METRICS_OF_INTEREST, deep_get

# (column_name, title, unit)
PLOT_SPECS = [
    ("session_rate_qps", "Session Rate", "sessions/s"),
    ("session_duration_mean_s", "Session Duration (Mean)", "seconds"),
    ("session_duration_p99_s", "Session Duration P99", "seconds"),
    ("events_per_session_mean", "Events per Session (Mean)", "count"),
    (
        "events_cancelled_per_session_mean",
        "Cancelled Events per Session (Mean)",
        "count",
    ),
    ("output_tokens_per_session_mean", "Output Tokens per Session (Mean)", "tokens"),
    ("failed_sessions", "Failed Sessions", "count"),
]

BAR_COLOR = "#3498db"


def generate_session_plots(results_dir: Path, output_dir: Path | None = None) -> int:
    """Generate session lifecycle bar charts. Returns the number written."""
    try:
        import yaml
    except ImportError:
        return 0

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return 0

    results_dir = Path(results_dir)
    session_br_files = sorted(
        results_dir.glob("benchmark_report_v0.2,_*_session_lifecycle_metrics.json.yaml")
    )
    if not session_br_files:
        return 0

    rows: list[dict] = []
    for br_file in session_br_files:
        try:
            with open(br_file, encoding="utf-8") as handle:
                report = yaml.safe_load(handle)
            if not report:
                continue
        except Exception:  # pylint: disable=broad-exception-caught
            continue

        row: dict = {"stage_file": br_file.name}
        for dotted_path, col_name in SESSION_METRICS_OF_INTEREST:
            row[col_name] = deep_get(report, dotted_path)
        rows.append(row)

    if not rows:
        return 0

    if output_dir is None:
        output_dir = results_dir / "analysis" / "session"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stage_labels = [
        r["stage_file"]
        .replace("benchmark_report_v0.2,_", "")
        .replace("_session_lifecycle_metrics.json.yaml", "")
        for r in rows
    ]

    generated = 0
    for col_name, title, unit in PLOT_SPECS:
        values = [r.get(col_name) for r in rows]
        if all(v is None for v in values):
            continue
        values_plot = [float(v) if v is not None else float("nan") for v in values]

        fig, ax = plt.subplots(figsize=(max(6, len(rows) * 1.5), 5))
        x_pos = range(len(rows))
        bars = ax.bar(x_pos, values_plot, color=BAR_COLOR, alpha=0.85)

        for bar, val in zip(bars, values_plot):
            if np.isnan(val):
                continue
            text = f"{val:.4f}" if val < 10 else f"{val:.1f}"
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height(),
                text,
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
            )

        ax.set_xticks(x_pos)
        ax.set_xticklabels(stage_labels, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel(unit)
        ax.set_title(title)
        ax.grid(axis="y", alpha=0.3)

        plt.tight_layout()
        plt.savefig(str(output_dir / f"session_{col_name}.png"), dpi=150)
        plt.close()
        generated += 1

    return generated
