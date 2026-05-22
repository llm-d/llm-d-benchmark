# Analysis Pipeline

This document describes the full analysis pipeline for `llm-d-benchmark`, covering both in-container and local analysis, the available plot types, and when to use each mode.

## Overview

Analysis happens at two stages:

1. **In-container analysis** -- Runs automatically inside the harness pod after the benchmark completes. Always happens.
2. **Local analysis** -- Runs on the experimenter's workstation after results are collected. Requires `--analyze`.

Both stages produce output in the results directory. Local analysis augments (does not replace) the in-container analysis with additional plots that require matplotlib.

## In-Container Analysis

The harness entrypoint script (`llm-d-benchmark.sh` by default) orchestrates in-container analysis after the load generator finishes. The flow is:

1. **Harness execution** -- The load generator (inference-perf, guidellm, vllm-benchmark) runs the workload and writes raw results.
2. **Metrics processing** -- `process_metrics.py` aggregates raw Prometheus scrapes into summary statistics.
3. **Harness-native analysis** -- For `inference-perf`, runs `inference-perf --analyze` to produce its native report format.
4. **Benchmark report generation** -- Converts harness-native results into v0.2 benchmark report YAML/JSON.

The in-container analysis produces:
- Benchmark report v0.1 and v0.2 (YAML + JSON) — one report per stage file
- For `inference-perf` multi-turn workloads: additional benchmark reports from `*_session_lifecycle_metrics.json` files, with `results.session_performance` populated
- Processed metrics summaries (`metrics/processed/`)
- Harness-native analysis output (varies by harness)
- `per_request_lifecycle_metrics.json` (per-request raw data, if supported by the harness)

## Local Analysis (`--analyze`)

When `--analyze` is passed to `llmdbenchmark run`, step 11 (`analyze_results`) runs additional analysis on the local machine after results have been collected from the PVC.

```bash
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml --analyze
```

The local analysis pipeline runs the following in sequence:

### 1. Harness-Native Local Analysis

For `inference-perf`, invokes `inference-perf --analyze <results_dir>` if the CLI is available locally. This regenerates the inference-perf analysis output using the local installation, which may be newer than the version in the container image.

### 2. Per-Request Distribution Plots

**Module:** `llmdbenchmark/analysis/per_request_plots.py`

Reads `per_request_lifecycle_metrics.json` and generates distribution plots for individual request metrics. This provides visibility into the full distribution of latencies, not just aggregate statistics.

**Generated plots** (saved to `<results_dir>/analysis/distributions/`):

| Plot | Description |
|------|-------------|
| `ttft_histogram.png` | Histogram of time to first token with mean/P50/P99 vertical lines |
| `tpot_histogram.png` | Histogram of time per output token with mean/P50/P99 lines |
| `itl_histogram.png` | Histogram of inter-token latency with mean/P50/P99 lines |
| `e2e_histogram.png` | Histogram of end-to-end request latency with mean/P50/P99 lines |
| `ttft_cdf.png` | Cumulative distribution function of TTFT |
| `tpot_cdf.png` | CDF of time per output token |
| `itl_cdf.png` | CDF of inter-token latency |
| `e2e_cdf.png` | CDF of end-to-end latency |
| `ttft_vs_input_length.png` | Scatter: TTFT vs input token count |
| `e2e_vs_output_length.png` | Scatter: E2E latency vs output token count |
| `itl_distribution.png` | Distribution of ITL values across all tokens in all requests |

Each histogram includes vertical lines marking the mean, P50 (median), and P99 values for quick reference.

### 3. Session Lifecycle Plots (inference-perf)

**Module:** `llmdbenchmark/analysis/__init__.py` (`_run_session_plots`)

For `inference-perf` runs, reads all `benchmark_report_v0.2,_*_session_lifecycle_metrics.json.yaml` files in the results directory and generates per-stage bar charts. This is skipped automatically for harnesses other than `inference-perf`.

**Generated plots** (saved to `<results_dir>/analysis/session/`):

| Plot | Description |
|------|-------------|
| `session_session_rate_qps.png` | Session completion rate per stage (sessions/s) |
| `session_session_duration_mean_s.png` | Mean session wall-clock duration per stage |
| `session_session_duration_p99_s.png` | P99 session duration per stage |
| `session_events_per_session_mean.png` | Mean event (request) count per session per stage |
| `session_events_cancelled_per_session_mean.png` | Mean cancelled event count per session per stage |
| `session_output_tokens_per_session_mean.png` | Mean output tokens per session per stage |
| `session_failed_sessions.png` | Failed session count per stage |

### 4. Cross-Treatment Comparison

**Module:** `llmdbenchmark/analysis/cross_treatment.py`

When multiple treatments were executed (via `--experiments`), this module reads the v0.2 benchmark report from each treatment's result directory and produces:

- **`treatment_comparison.csv`** -- One row per treatment with key metrics (TTFT, TPOT, ITL, E2E latency stats, throughput, request counts, and session metrics when available). See [Benchmark Report](benchmark_report.md#cross-treatment-comparison-csv) for the full column list.
- **Bar charts** -- Side-by-side bars comparing aggregate metrics across treatments.
- **Session comparison bar charts** -- Side-by-side bars comparing session lifecycle metrics across treatments, with error bars showing min/max across stages. Generated only when at least two treatments have session data.
- **Session scatter plots** -- Line/scatter plots showing relationships between session rate and session duration, events per session, and output tokens per session across treatments.
- **Latency vs throughput curves** -- Scatter/line plots showing the latency-throughput trade-off.
- **Overlaid CDFs** -- CDF plots from per-request data overlaid across treatments for direct comparison.

Output is saved to `<results_dir>/cross-treatment-comparison/`.

### 5. Prometheus Metric Visualization

**Module:** `llmdbenchmark/analysis/visualize_metrics.py`

Generates time series plots from raw Prometheus metric files collected by `collect_metrics.sh`. See [Metrics Collection](metrics_collection.md#metric-visualization-visualize_metricspy) for the full list of generated plots.

Output is saved to `<results_dir>/analysis/metrics/`.

## When to Use `--analyze` vs Not

| Scenario | Recommendation |
|----------|----------------|
| Quick sanity check | Skip `--analyze` -- in-container analysis provides benchmark reports and summary stats |
| Investigating latency distributions | Use `--analyze` -- per-request plots show the full distribution, not just aggregates |
| Comparing multiple treatments | Use `--analyze` -- cross-treatment comparison generates the summary CSV and comparison charts |
| CI/CD pipeline | Skip `--analyze` -- rely on benchmark report YAML/JSON for programmatic consumption |
| Presentation-quality plots | Use `--analyze` -- generates publication-ready PNG plots with matplotlib |
| Container image lacks matplotlib | Use `--analyze` -- local analysis uses the experimenter's Python environment |

## Jupyter Notebook Analysis

For interactive exploration beyond what `--analyze` provides, use the Jupyter notebook at `docs/analysis/analysis.ipynb`. This notebook imports benchmark report files into a Pandas DataFrame for custom plotting and analysis. See [`docs/analysis/README.md`](analysis/README.md) for setup instructions.

## Output Directory Structure

After a full analysis run, the results directory contains:

```text
<results_dir>/
    <treatment_1>/
        benchmark_report,_stage_<N>_lifecycle_metrics.json.yaml         # per-stage request report (v0.1)
        benchmark_report_v0.2,_stage_<N>_lifecycle_metrics.json.yaml    # per-stage request report (v0.2)
        benchmark_report_v0.2,_stage_<N>_session_lifecycle_metrics.json.yaml  # session report (inference-perf multi-turn)
        per_request_lifecycle_metrics.json
        metrics/
            raw/          # Timestamped Prometheus scrapes
            processed/    # Aggregated metric summaries
        analysis/
            distributions/   # Per-request histograms and CDFs
            metrics/         # Prometheus time series plots
            session/         # Per-stage session lifecycle bar charts (inference-perf only)
    <treatment_2>/
        ...
    cross-treatment-comparison/
        treatment_comparison.csv
        <comparison_plots>.png          # Aggregate metric comparisons
        session_compare_<metric>.png    # Session metric comparisons (when session data present)
        session_scatter_<x>_vs_<y>.png  # Session metric scatter plots
```
