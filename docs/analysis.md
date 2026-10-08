# Analysis Pipeline

This document describes the full analysis pipeline for `llm-d-benchmark`, covering both in-container and local analysis, and when to use each mode.

To visualize results, use [llm-d-prism](https://github.com/llm-d/llm-d-prism), which reads the benchmark reports this pipeline produces. Standup can deploy it in-cluster.

## Overview

Analysis happens at two stages:

1. **In-container analysis** -- Runs automatically inside the harness pod after the benchmark completes. Always happens.
2. **Local analysis** -- Runs on the experimenter's workstation after results are collected. Requires `--analyze`.

Both stages produce output in the results directory. Local analysis augments (does not replace) the in-container analysis.

## In-Container Analysis

The harness entrypoint script (`llm-d-benchmark.sh` by default) orchestrates in-container analysis after the load generator finishes. The flow is:

1. **Harness execution** -- The load generator (inference-perf, guidellm, vllm-benchmark) runs the workload and writes raw results.
2. **Metrics processing** -- `process_metrics.py` aggregates raw Prometheus scrapes into summary statistics.
3. **Benchmark report generation** -- Converts harness-native results into v0.2 benchmark report YAML/JSON.

The in-container analysis produces:
- Benchmark report v0.1 and v0.2 (YAML + JSON) — one report per stage file
- For `inference-perf` multi-turn workloads: additional benchmark reports from `*_session_lifecycle_metrics.json` files, with `results.session_performance` populated
- Processed metrics summaries (`metrics/processed/`)
- `per_request_lifecycle_metrics.json` (per-request raw data, if supported by the harness)

## Local Analysis (`--analyze`)

When `--analyze` is passed to `llmdbenchmark run`, step 11 (`analyze_results`) runs additional analysis on the local machine after results have been collected from the PVC.

```bash
llmdbenchmark --spec gpu run -l inference-perf -w sanity_random.yaml --analyze
```

### Cross-Treatment Comparison

**Module:** `llmdbenchmark/analysis/cross_treatment.py`

When multiple treatments were executed (via `--experiments`), this module reads the v0.2 benchmark report from each treatment's result directory and produces **`treatment_comparison.csv`** -- one row per treatment with key metrics (TTFT, TPOT, ITL, E2E latency stats, throughput, request counts, and session metrics when available). See [Benchmark Report](benchmark_report.md#cross-treatment-comparison-csv) for the full column list.

Output is saved to `<results_dir>/cross-treatment-comparison/`.

## When to Use `--analyze` vs Not

| Scenario | Recommendation |
|----------|----------------|
| Quick sanity check | Skip `--analyze` -- in-container analysis provides benchmark reports and summary stats |
| Comparing multiple treatments | Use `--analyze` -- cross-treatment comparison generates the summary CSV |
| CI/CD pipeline | Skip `--analyze` -- rely on benchmark report YAML/JSON for programmatic consumption |
| Charts and dashboards | Use [llm-d-prism](https://github.com/llm-d/llm-d-prism) on the benchmark reports |

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
    <treatment_2>/
        ...
    cross-treatment-comparison/
        treatment_comparison.csv
```
