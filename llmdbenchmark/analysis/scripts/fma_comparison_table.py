#!/usr/bin/env python3
"""Render the FMA autoscaling comparison table (baseline vs FMA warm/hot start).

Shared by the EPP+KEDA (``ci-benchmark-ocp-fma-keda``) and WVA
(``ci-benchmark-ocp-fma-wva``) nightly workflows

Per-pass artifacts consumed:
  * ``summary_lifecycle_metrics.json``              -- harness latency/throughput
  * ``metrics/processed/replica_status.json``       -- aggregate_ready_replicas
                                                       (avg/max replicas)
  * ``metrics/processed/metrics_summary.json``      -- _aggregated.metrics EPP
                                                       KV-cache / queue-depth means
  * ``metrics/processed/pod_startup_times.json``    -- per-pod creation->Ready
                                                       (Avg pod startup: baseline
                                                       decode / FMA runtime requester)
  (the metrics/* files are present only when monitoring.metricsScrapeEnabled).
"""

import argparse
import glob
import json
import os


# ---------------------------------------------------------------------------
# Artifact location + loading
# ---------------------------------------------------------------------------
def newest_run_dir(root):
    """Return the newest run directory under ``root`` (or "").

    Runs are ``runner-<UTC YYYYMMDD-HHMMSS>-<id>`` dirs holding a ``results/``,
    so the newest is simply the latest-sorting ``runner-*`` match. (Lexical, not
    mtime: these are GCS-downloaded, so mtimes are the download time.)"""
    runs = glob.glob(os.path.join(root, "**", "runner-*", "results"), recursive=True)
    return os.path.dirname(sorted(runs)[-1]) if runs else ""


def _find_one(run_root, filename):
    """Newest file named ``filename`` anywhere under ``run_root``, or ""."""
    if not run_root:
        return ""
    matches = sorted(glob.glob(os.path.join(run_root, "**", filename), recursive=True))
    return matches[-1] if matches else ""


def load_json(p):
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get(d, *keys, scale=1.0):
    if d is None:
        return None
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur * scale if isinstance(cur, (int, float)) else cur


def fmt(v, unit="", precision=1):
    if v is None:
        return "_n/a_"
    if isinstance(v, float):
        return f"{v:.{precision}f}{unit}"
    return f"{v}{unit}"


# ---------------------------------------------------------------------------

KV_CACHE_METRIC = "inference_pool_average_kv_cache_utilization"
QUEUE_SIZE_METRIC = "inference_pool_average_queue_size"


def replica_stats(rdir):
    """(avg, max) ready replicas, read from process_metrics' pre-aggregated
    ``replica_status.json`` (``aggregate_ready_replicas``)."""
    data = load_json(_find_one(rdir, "replica_status.json"))
    agg = (data or {}).get("aggregate_ready_replicas") or {}
    return (agg.get("mean"), agg.get("max"))


def epp_gauge_mean(rdir, metric):
    """Mean of an EPP pool gauge, read from process_metrics' pre-aggregated
    ``metrics_summary.json`` (``_aggregated.metrics.<metric>.mean``)."""
    data = load_json(_find_one(rdir, "metrics_summary.json"))
    metrics = (((data or {}).get("_aggregated") or {}).get("metrics")) or {}
    return (metrics.get(metric) or {}).get("mean")


def pod_startup_mean(rdir):
    """Avg pod startup = pod creation → Ready, read from process_metrics'
    pre-aggregated ``pod_startup_times.json``"""
    data = load_json(_find_one(rdir, "pod_startup_times.json")) or {}
    agg = data.get("requester_runtime_aggregate") or data.get("aggregate") or {}
    return agg.get("mean")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
ROWS = [
    ("Duration (s)", "benchmark_time_seconds", None, 0, ""),
    ("Total requests", "load_summary", "count", None, 0, ""),
    ("Successes", "successes", "count", None, 0, ""),
    ("Failures", "failures", "count", None, 0, ""),
    ("Avg prompt len (tokens)", "successes", "prompt_len", "mean", None, 1, ""),
    ("Avg output len (tokens)", "successes", "output_len", "mean", None, 1, ""),
    ("Throughput (req/s)", "successes", "throughput", "requests_per_sec", None, 1, ""),
    (
        "Throughput input (tok/s)",
        "successes",
        "throughput",
        "input_tokens_per_sec",
        None,
        0,
        "",
    ),
    (
        "Throughput output (tok/s)",
        "successes",
        "throughput",
        "output_tokens_per_sec",
        None,
        0,
        "",
    ),
    ("Latency mean (ms)", "successes", "latency", "request_latency", "mean", 1000, ""),
    ("Latency p99 (ms)", "successes", "latency", "request_latency", "p99", 1000, ""),
    ("TTFT mean (ms)", "successes", "latency", "time_to_first_token", "mean", 1000, ""),
    ("TTFT p99 (ms)", "successes", "latency", "time_to_first_token", "p99", 1000, ""),
    (
        "TPOT mean (ms)",
        "successes",
        "latency",
        "time_per_output_token",
        "mean",
        1000,
        "",
    ),
    ("TPOT p99 (ms)", "successes", "latency", "time_per_output_token", "p99", 1000, ""),
    ("ITL mean (ms)", "successes", "latency", "inter_token_latency", "mean", 1000, ""),
    ("ITL p99 (ms)", "successes", "latency", "inter_token_latency", "p99", 1000, ""),
]

# Rows that are additive across inference-perf workers (each worker's summary
# reports only its own share), so they are multiplied by the worker count.
_PER_WORKER_ROWS = {
    "Total requests",
    "Successes",
    "Failures",
    "Throughput (req/s)",
    "Throughput input (tok/s)",
    "Throughput output (tok/s)",
}


def _num_workers(rdir, workload):
    prof = _find_one(rdir, os.path.basename(workload)) if workload else ""
    if prof:
        try:
            with open(prof, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("num_workers:"):
                        return int(s.split(":", 1)[1].strip())
        except (OSError, ValueError):
            pass
    return 1


def render_run_info(args, summaries, workers):
    """Emit a Run Info block above the table."""
    duration = next((get(s, "benchmark_time_seconds") for s in summaries if s), None)
    # Total requests summed across the inference-perf workers (per-worker count
    # x worker count), matching the per-arm Total requests row.
    total = next(
        (
            get(s, "load_summary", "count") * w
            for s, w in zip(summaries, workers)
            if s and get(s, "load_summary", "count") is not None
        ),
        None,
    )
    lines = ["### Run Info", ""]
    if args.model:
        lines.append(f"- **Model:** {args.model}")
    if args.harness:
        lines.append(f"- **Harness:** {args.harness}")
    if args.workload:
        lines.append(f"- **Workload:** {args.workload}")
    if duration is not None:
        lines.append(f"- **Duration:** {fmt(duration, 's', 0)}")
    if total is not None:
        lines.append(f"- **Total requests:** {fmt(total, '', 0)}")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline-dir", required=True)
    ap.add_argument("--warmstart-dir", required=True)
    ap.add_argument("--hotstart-dir", required=True)
    ap.add_argument("--col-baseline", required=True, help="Baseline column header")
    ap.add_argument("--col-warmstart", required=True, help="Warm-start column header")
    ap.add_argument("--col-hotstart", required=True, help="Hot-start column header")
    ap.add_argument("--model", default="", help="Run Info: served model")
    ap.add_argument("--harness", default="", help="Run Info: harness name")
    ap.add_argument("--workload", default="", help="Run Info: workload profile")
    ap.add_argument(
        "--gpu-hourly-cost",
        type=float,
        default=1.0,
        help="Cost row multiplier: cost = avg replicas × this (default 1.0, so "
        "Cost == avg replicas, matching the WVA benchmark doc).",
    )
    args = ap.parse_args()

    dirs = (args.baseline_dir, args.warmstart_dir, args.hotstart_dir)
    rdirs = [newest_run_dir(d) for d in dirs]
    sums = [_find_one(r, "summary_lifecycle_metrics.json") for r in rdirs]

    bl, fw_ws, fw_hs = (load_json(p) for p in sums)
    summaries = [bl, fw_ws, fw_hs]

    workers = [_num_workers(r, args.workload) for r in rdirs]
    out = [render_run_info(args, summaries, workers)]

    out.append(
        f"| Metric | {args.col_baseline} | {args.col_warmstart} | {args.col_hotstart} |"
    )
    out.append("|---|---:|---:|---:|")
    for row in ROWS:
        label = row[0]
        keys = [k for k in row[1:-2] if k is not None]
        scale = row[-2] or 1.0
        unit = row[-1]
        prec = (
            0
            if any(
                s in label
                for s in ("Total", "Successes", "Failures", "Duration", "tok/s")
            )
            else 1
        )
        vals = [get(s, *keys, scale=scale) for s in summaries]
        if label in _PER_WORKER_ROWS:
            vals = [v * w if v is not None else None for v, w in zip(vals, workers)]
        out.append(
            f"| {label} | " + " | ".join(fmt(v, unit, prec) for v in vals) + " |"
        )

    repl = [replica_stats(r) for r in rdirs]
    avg_repl = [r[0] for r in repl]
    max_repl = [r[1] for r in repl]
    startup = [pod_startup_mean(r) for r in rdirs]
    kv = [epp_gauge_mean(r, KV_CACHE_METRIC) for r in rdirs]
    qd = [epp_gauge_mean(r, QUEUE_SIZE_METRIC) for r in rdirs]
    kv_pct = [(v * 100 if v is not None and v <= 1.0 else v) for v in kv]
    cost = [(a * args.gpu_hourly_cost if a is not None else None) for a in avg_repl]

    out.append("| Avg replicas | " + " | ".join(fmt(v, "", 2) for v in avg_repl) + " |")
    out.append("| Max replicas | " + " | ".join(fmt(v, "", 0) for v in max_repl) + " |")
    out.append(
        "| Avg KV cache utilization | "
        + " | ".join(fmt(v, "%", 1) for v in kv_pct)
        + " |"
    )
    out.append(
        "| Avg queue depth (EPP) | " + " | ".join(fmt(v, "", 1) for v in qd) + " |"
    )
    out.append(
        "| Avg pod startup (s) | " + " | ".join(fmt(v, "", 0) for v in startup) + " |"
    )
    out.append(
        "| Cost (avg replicas × GPU/hr) | "
        + " | ".join(fmt(v, "", 2) for v in cost)
        + " |"
    )

    if bl is None:
        out.append("\n_No baseline results found._")
    if fw_ws is None:
        out.append("\n_No warm-start results found._")
    if fw_hs is None:
        out.append("\n_No hot-start results found._")

    print("\n".join(out))


if __name__ == "__main__":
    main()
