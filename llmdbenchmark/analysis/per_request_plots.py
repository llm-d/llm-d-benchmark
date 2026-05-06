"""Per-request distribution plots from per_request_lifecycle_metrics.json.

Reads the raw per-request data and generates:
- Histograms of TTFT, TPOT, ITL, E2E latency
- CDF plots
- Scatter: TTFT vs input length, TPOT vs output length

These give visibility into the full distribution of individual requests,
not just aggregate statistics.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llmdbenchmark.executor.context import ExecutionContext


def generate_per_request_plots(
    results_dir: Path,
    output_dir: Path | None = None,
    context: "ExecutionContext | None" = None,
) -> int:
    """Generate per-request distribution plots.

    Args:
        results_dir: Directory containing per_request_lifecycle_metrics.json.
        output_dir: Where to write PNGs (default: results_dir/analysis/distributions).
        context: Optional execution context for logging.

    Returns:
        Number of plots generated.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _log(context, "matplotlib not available -- skipping per-request plots")
        return 0

    pr_file = results_dir / "per_request_lifecycle_metrics.json"
    if not pr_file.exists():
        pr_file = results_dir / "analysis" / "per_request_lifecycle_metrics.json"
    if not pr_file.exists():
        return 0

    try:
        with open(pr_file, encoding="utf-8") as f:
            raw_requests = json.load(f)
    except (json.JSONDecodeError, Exception):
        return 0

    if not isinstance(raw_requests, list) or len(raw_requests) < 2:
        return 0

    if output_dir is None:
        output_dir = results_dir / "analysis" / "distributions"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract per-request metrics
    requests = []
    for r in raw_requests:
        info = r.get("info", {})
        start = r.get("start_time")
        end = r.get("end_time")
        token_times = info.get("output_token_times", [])
        input_tokens = info.get("input_tokens", 0)
        output_tokens = info.get("output_tokens", 0)

        if start is None or end is None or not token_times:
            continue

        e2e = end - start
        ttft = token_times[0] - start if token_times else None

        # TPOT: time per output token (excluding TTFT)
        if len(token_times) > 1:
            decode_time = token_times[-1] - token_times[0]
            tpot = decode_time / (len(token_times) - 1)
        else:
            tpot = None

        # ITL: inter-token latencies
        itls = []
        for i in range(1, len(token_times)):
            itls.append(token_times[i] - token_times[i - 1])

        requests.append({
            "e2e": e2e,
            "ttft": ttft,
            "tpot": tpot,
            "itl_mean": sum(itls) / len(itls) if itls else None,
            "itls": itls,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })

    if not requests:
        return 0

    generated = 0

    # --- Histograms ---
    hist_specs = [
        ("ttft", "TTFT Distribution", "Time to First Token (s)", "s"),
        ("tpot", "TPOT Distribution", "Time per Output Token (s)", "s"),
        ("e2e", "End-to-End Latency Distribution", "E2E Latency (s)", "s"),
        ("itl_mean", "Mean ITL Distribution", "Mean Inter-Token Latency (s)", "s"),
    ]

    for key, title, xlabel, unit in hist_specs:
        values = [r[key] for r in requests if r[key] is not None]
        if len(values) < 2:
            continue

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        # Histogram
        ax1.hist(values, bins=min(30, len(values)), color="#3498db",
                 alpha=0.7, edgecolor="black", linewidth=0.5)
        ax1.axvline(sum(values) / len(values), color="#e74c3c",
                    linestyle="--", label=f"Mean: {sum(values)/len(values):.4f}{unit}")
        sorted_v = sorted(values)
        p50 = sorted_v[len(sorted_v) // 2]
        p99_idx = min(int(len(sorted_v) * 0.99), len(sorted_v) - 1)
        ax1.axvline(p50, color="#2ecc71", linestyle="--",
                    label=f"P50: {p50:.4f}{unit}")
        ax1.axvline(sorted_v[p99_idx], color="#e67e22", linestyle="--",
                    label=f"P99: {sorted_v[p99_idx]:.4f}{unit}")
        ax1.set_xlabel(xlabel)
        ax1.set_ylabel("Count")
        ax1.set_title(f"{title} (n={len(values)})")
        ax1.legend(fontsize=8)
        ax1.grid(alpha=0.3)

        # CDF
        ax2.plot(sorted_v, [i / len(sorted_v) for i in range(len(sorted_v))],
                 color="#3498db", linewidth=2)
        ax2.axhline(0.5, color="#2ecc71", linestyle=":", alpha=0.5, label="P50")
        ax2.axhline(0.99, color="#e67e22", linestyle=":", alpha=0.5, label="P99")
        ax2.set_xlabel(xlabel)
        ax2.set_ylabel("Cumulative Probability")
        ax2.set_title(f"{title} CDF")
        ax2.legend(fontsize=8)
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        plt.savefig(str(output_dir / f"dist_{key}.png"), dpi=150)
        plt.close()
        generated += 1

    # --- Scatter: TTFT vs input length ---
    ttft_vals = [(r["input_tokens"], r["ttft"]) for r in requests
                 if r["ttft"] is not None and r["input_tokens"]]
    if len(ttft_vals) >= 2:
        xs, ys = zip(*ttft_vals)
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(xs, ys, alpha=0.6, s=30, color="#3498db")
        ax.set_xlabel("Input Tokens")
        ax.set_ylabel("TTFT (s)")
        ax.set_title("TTFT vs Input Length (per request)")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(str(output_dir / "scatter_ttft_vs_input.png"), dpi=150)
        plt.close()
        generated += 1

    # --- Scatter: E2E vs output length ---
    e2e_vals = [(r["output_tokens"], r["e2e"]) for r in requests
                if r["e2e"] is not None and r["output_tokens"]]
    if len(e2e_vals) >= 2:
        xs, ys = zip(*e2e_vals)
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.scatter(xs, ys, alpha=0.6, s=30, color="#e74c3c")
        ax.set_xlabel("Output Tokens")
        ax.set_ylabel("E2E Latency (s)")
        ax.set_title("E2E Latency vs Output Length (per request)")
        ax.grid(alpha=0.3)
        plt.tight_layout()
        plt.savefig(str(output_dir / "scatter_e2e_vs_output.png"), dpi=150)
        plt.close()
        generated += 1

    # --- ITL timeline (all tokens across all requests) ---
    all_itls = []
    for r in requests:
        all_itls.extend(r.get("itls", []))
    if len(all_itls) >= 10:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        # Histogram
        ax1.hist(all_itls, bins=50, color="#9b59b6", alpha=0.7,
                 edgecolor="black", linewidth=0.5)
        ax1.set_xlabel("Inter-Token Latency (s)")
        ax1.set_ylabel("Count")
        ax1.set_title(f"ITL Distribution (all tokens, n={len(all_itls)})")
        ax1.grid(alpha=0.3)

        # CDF
        sorted_itls = sorted(all_itls)
        ax2.plot(sorted_itls,
                 [i / len(sorted_itls) for i in range(len(sorted_itls))],
                 color="#9b59b6", linewidth=2)
        ax2.set_xlabel("Inter-Token Latency (s)")
        ax2.set_ylabel("Cumulative Probability")
        ax2.set_title("ITL CDF (all tokens)")
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        plt.savefig(str(output_dir / "dist_itl_all_tokens.png"), dpi=150)
        plt.close()
        generated += 1

    if generated:
        _log(context, f"Generated {generated} per-request distribution plot(s)")

    return generated


def _log(context, message, warning=False):
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
