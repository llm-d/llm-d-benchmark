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
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile

# Reuse the dual-pods-controller log parser (workload/harnesses) so hit-rate
# classification matches the authoritative FMA actuation logic. The script runs
# from the repo root ($GITHUB_WORKSPACE) in CI, so add that to sys.path.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
try:
    from workload.harnesses.dpc_log_parser import _parse_rfc3339_nano, parse_dpc_log
except ImportError:
    parse_dpc_log = None
    _parse_rfc3339_nano = None


# ---------------------------------------------------------------------------
# Artifact location + loading
# ---------------------------------------------------------------------------
def newest_run_dir(root):
    """Return the newest run directory under ``root`` (or "").

    A run dir is any directory holding a ``results/`` subdir -- CI names them
    ``runner-<UTC>-<id>`` and local runs ``<user>-<UTC>-<id>``, so match any
    ``*/results`` rather than only ``runner-*``. ``root`` may itself be a run dir
    (its own ``results/`` matches). Newest = latest-sorting name (lexical, since
    GCS-downloaded mtimes are download time). Timestamped names sort chronologically."""
    runs = glob.glob(os.path.join(root, "**", "results"), recursive=True)
    runs = [r for r in runs if os.path.isdir(r)]
    return os.path.dirname(sorted(runs)[-1]) if runs else ""


def _find_one(run_root, filename):
    """Newest file named ``filename`` anywhere under ``run_root``, or ""."""
    if not run_root:
        return ""
    matches = sorted(glob.glob(os.path.join(run_root, "**", filename), recursive=True))
    return matches[-1] if matches else ""


def _archives(run_root):
    """Every result archive under ``run_root``, newest-sorting last."""
    if not run_root:
        return []
    found = glob.glob(os.path.join(run_root, "**/*.tar.zst"), recursive=True)
    return sorted(f for f in found if os.path.isfile(f))


def _read_from_archives(run_root, filename):
    """Return ``filename``'s bytes from any archive under ``run_root``, or None.

    A compressed run keeps the small artifacts plain, so this is the fallback for
    a run whose keep-plain list predates them -- or for anything that legitimately
    travels inside the archive. Matched on basename at any depth, mirroring
    ``_find_one``. Self-contained on purpose: this script runs in CI from a
    GCS download, with no llmdbenchmark package importable.
    """
    want = os.path.basename(filename)
    for archive in reversed(_archives(run_root)):
        try:
            zstd = shutil.which("zstd")
            if not zstd:
                continue
            proc = subprocess.Popen([zstd, "-dc", archive], stdout=subprocess.PIPE)
            try:
                with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                    for member in tar:
                        if member.isfile() and os.path.basename(member.name) == want:
                            handle = tar.extractfile(member)
                            if handle is not None:
                                return handle.read()
            finally:
                if proc.stdout is not None:
                    proc.stdout.close()
                proc.wait()
        except (tarfile.TarError, OSError, subprocess.SubprocessError):
            continue
    return None


def load_text(p, run_root="", filename=""):
    """Text of ``p``, falling back to an archive under ``run_root``. None if absent."""
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    if not (run_root and filename):
        return None
    payload = _read_from_archives(run_root, filename)
    if payload is None:
        return None
    return payload.decode("utf-8", errors="replace")


def load_json(p, run_root="", filename=""):
    """Load JSON from ``p``, falling back to an archive under ``run_root``."""
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    if not (run_root and filename):
        return None
    payload = _read_from_archives(run_root, filename)
    if payload is None:
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
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
# EPP flow-control metrics that drive the KEDA scale triggers: pool saturation
# for the saturation trigger, queue size for the queue trigger.
POOL_SATURATION_METRIC = "llm_d_epp_flow_control_pool_saturation"
FLOW_CONTROL_QUEUE_METRIC = "llm_d_epp_flow_control_queue_size"
RUNNING_REQUESTS_METRIC = "llm_d_epp_request_running"


def replica_stats(rdir):
    """(avg, max) ready replicas, read from process_metrics' pre-aggregated
    ``replica_status.json`` (``aggregate_ready_replicas``)."""
    data = load_json(
        _find_one(rdir, "replica_status.json"), rdir, "replica_status.json"
    )
    agg = (data or {}).get("aggregate_ready_replicas") or {}
    return (agg.get("mean"), agg.get("max"))


def epp_gauge_mean(rdir, metric):
    """Mean of an EPP pool gauge, read from process_metrics' pre-aggregated
    ``metrics_summary.json`` (``_aggregated.metrics.<metric>.mean``)."""
    data = load_json(
        _find_one(rdir, "metrics_summary.json"), rdir, "metrics_summary.json"
    )
    metrics = (((data or {}).get("_aggregated") or {}).get("metrics")) or {}
    return (metrics.get(metric) or {}).get("mean")


def pod_startup_mean(rdir):
    """Avg pod startup = pod creation → Ready, read from process_metrics'
    pre-aggregated ``pod_startup_times.json``"""
    data = (
        load_json(
            _find_one(rdir, "pod_startup_times.json"), rdir, "pod_startup_times.json"
        )
        or {}
    )
    agg = data.get("requester_runtime_aggregate") or data.get("aggregate") or {}
    return agg.get("mean")


def fma_hit_rates(rdir):
    """(hot_rate, warm_rate, cold_rate) of the run's FMA scale-up actuations, or
    (None, None, None) when this isn't an FMA pass / no DPC log was captured.

    Classifies each requester the dual-pods-controller acted on during the run
    from ``dual-pods-controller.log`` (captured by step_09a): a ``wake`` anchor
    is a HOT actuation (woke a sleeping vLLM), ``create_instance`` is WARM
    (existing launcher, new vLLM), and a launcher-create is COLD. Rates are each
    category over the total classified actuations.

    Only RUN-phase actuations count: those whose anchor time is at/after the run
    start (first replica-status snapshot). Standup/warmup actuations are excluded
    -- matching aggregate_requester_startup_stats' "runtime requester" filter in
    process_metrics.py."""
    if parse_dpc_log is None:
        return (None, None, None)
    text = load_text(
        _find_one(rdir, "dual-pods-controller.log"),
        rdir,
        "dual-pods-controller.log",
    )
    if text is None:
        return (None, None, None)
    records = parse_dpc_log(io.StringIO(text))

    # Run start = first replica-status snapshot timestamp, as an epoch float to
    # compare against the DPC anchor times (also epoch). None => no filtering.
    run_start = None
    ts_data = load_json(
        _find_one(rdir, "replica_status_timeseries.json"),
        rdir,
        "replica_status_timeseries.json",
    )
    snaps = (ts_data or {}).get("snapshots", [])
    if snaps and _parse_rfc3339_nano is not None:
        run_start = _parse_rfc3339_nano(snaps[0].get("timestamp"))

    hot = warm = cold = 0
    for rec in records.values():
        # Precedence mirrors fma_functions' classification: a wake anchor means
        # the actuation woke a sleeping vLLM (hot); else an instance-create is
        # warm; else a launcher was created cold. The chosen anchor's start time
        # is the actuation time used for the run-phase filter.
        if rec.wake_start_time is not None:
            category, anchor = "hot", rec.wake_start_time
        elif rec.instance_create_start_time is not None:
            category, anchor = "warm", rec.instance_create_start_time
        elif rec.launcher_create_start_time is not None:
            category, anchor = "cold", rec.launcher_create_start_time
        else:
            continue
        # Skip standup/warmup actuations (before the run started).
        if run_start is not None and anchor is not None and anchor < run_start:
            continue
        if category == "hot":
            hot += 1
        elif category == "warm":
            warm += 1
        else:
            cold += 1
    total = hot + warm + cold
    if total == 0:
        return (None, None, None)
    return (hot / total, warm / total, cold / total)


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
    if not workload:
        return 1
    name = os.path.basename(workload)
    text = load_text(_find_one(rdir, name), rdir, name)
    if text is None:
        return 1
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("num_workers:"):
            try:
                return int(s.split(":", 1)[1].strip())
            except ValueError:
                return 1
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
    _sum_name = "summary_lifecycle_metrics.json"
    sums = [_find_one(r, _sum_name) for r in rdirs]

    bl, fw_ws, fw_hs = (load_json(p, r, _sum_name) for p, r in zip(sums, rdirs))
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
    sat = [epp_gauge_mean(r, POOL_SATURATION_METRIC) for r in rdirs]
    fcq = [epp_gauge_mean(r, FLOW_CONTROL_QUEUE_METRIC) for r in rdirs]
    rr = [epp_gauge_mean(r, RUNNING_REQUESTS_METRIC) for r in rdirs]
    kv_pct = [(v * 100 if v is not None and v <= 1.0 else v) for v in kv]
    cost = [(a * args.gpu_hourly_cost if a is not None else None) for a in avg_repl]
    hit = [fma_hit_rates(r) for r in rdirs]
    hot_pct = [(h[0] * 100 if h[0] is not None else None) for h in hit]
    warm_pct = [(h[1] * 100 if h[1] is not None else None) for h in hit]

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
        "| Avg flow-control pool saturation (EPP) | "
        + " | ".join(fmt(v, "", 2) for v in sat)
        + " |"
    )
    out.append(
        "| Avg flow-control queue (EPP) | "
        + " | ".join(fmt(v, "", 1) for v in fcq)
        + " |"
    )
    out.append(
        "| Avg running requests (EPP) | " + " | ".join(fmt(v, "", 1) for v in rr) + " |"
    )
    out.append(
        "| Avg pod startup (s) | " + " | ".join(fmt(v, "", 0) for v in startup) + " |"
    )
    out.append(
        "| Cost (avg replicas × GPU/hr) | "
        + " | ".join(fmt(v, "", 2) for v in cost)
        + " |"
    )
    out.append("| Hot hit rate | " + " | ".join(fmt(v, "%", 1) for v in hot_pct) + " |")
    out.append(
        "| Warm hit rate | " + " | ".join(fmt(v, "%", 1) for v in warm_pct) + " |"
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
