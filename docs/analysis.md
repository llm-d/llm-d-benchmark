# Analysis Pipeline

This document describes the analysis pipeline for `llm-d-benchmark`: where the benchmark reports are built and what the driver adds.

To visualize results, use [llm-d-prism](https://github.com/llm-d/llm-d-prism), which reads the benchmark reports this pipeline produces. `llmdbenchmark run` deploys it in-cluster (`--no-prism` to opt out), where it reads them straight from the workload PVC.

## Overview

Analysis always runs; there is no flag to turn it on.

1. **In-container analysis** -- Runs inside the harness pod after the benchmark completes. It builds the benchmark reports, so they land on the workload PVC next to the raw results. For eval-containers, whose task image cannot build reports, an llm-d-benchmark sidecar in each task pod (a separate container with nok8s) builds them once the task ends.
2. **Run summary** (eval-containers only) -- One extra pod per treatment rolls all its tasks up into one run-level report. See [Run summary](#run-summary-eval-containers).
3. **Driver fallback** -- Step 12 (`analyze_results`) rebuilds the reports of a result set the pod did not analyze (for example an older image, or a pod killed after the load finished). A set that already holds the pod's reports is left as it is. It has no converter for aiperf or lm-eval, and for inference-perf, vllm-benchmark or inferencemax it cannot convert raw results that are only inside a set's archive; it warns in that case. It is skipped under `--data-collect skip`, which leaves nothing local.

`--analyze` (and `LLMDBENCH_RUN_EXPERIMENT_ANALYZE_LOCALLY`) is still accepted for old scripts, but it only logs a warning.

## In-Container Analysis

The harness entrypoint script (`llm-d-benchmark.sh` by default) orchestrates in-container analysis after the load generator finishes. The flow is:

1. **Harness execution** -- The load generator (inference-perf, guidellm, vllm-benchmark, aiperf, ...) runs the workload and writes raw results.
2. **Metrics processing** -- `process_metrics.py` aggregates raw Prometheus scrapes into summary statistics.
3. **Benchmark report generation** -- Converts harness-native results into v0.2 benchmark report YAML/JSON.
4. **Metrics embedding** -- When metrics were scraped, `embed_metrics.py` merges them, with their time series, into the v0.2 reports. Each stage report gets only its own stage's time window.

The in-container analysis produces:
- Benchmark report v0.1 and v0.2 (YAML + JSON) — one report per stage file
- For `inference-perf` multi-turn workloads: additional benchmark reports from `*_session_lifecycle_metrics.json` files, with `results.session_performance` populated
- Processed metrics summaries (`metrics/processed/`)
- `per_request_lifecycle_metrics.json` (per-request raw data, if supported by the harness)

## Run Summary (eval-containers)

`eval-containers` runs one agent task per harness pod, so each per-task report covers one task only. For every treatment, step 07 also starts one small summary pod next to the tasks. It waits until every task's report sidecar has written its done marker, then writes `<experiment-id>_summary/` on the PVC:

- `benchmark_report_v0.2,_agentic_run.yaml` -- the run-level report: pass rate, pooled LLM-call latency, token totals
- `agentic_tasks.csv` -- one row per task

The set is compressed and collected like any other, and prism lists its report. With nok8s the summary runs as one more container on the same host. On Kubernetes, `--no-pvc` runs have no run summary, because each task keeps its results in its own emptyDir. See [agentic_eval.md](agentic_eval.md#run-level-aggregate).

## Output Directory Structure

After a run, the results directory contains:

```text
<results_dir>/
    <experiment_id>_1/
        benchmark_report,_stage_<N>_lifecycle_metrics.json.yaml         # per-stage request report (v0.1)
        benchmark_report_v0.2,_stage_<N>_lifecycle_metrics.json.yaml    # per-stage request report (v0.2)
        benchmark_report_v0.2,_stage_<N>_session_lifecycle_metrics.json.yaml  # session report (inference-perf multi-turn)
        per_request_lifecycle_metrics.json
        metrics/
            raw/          # Timestamped Prometheus scrapes
            processed/    # Aggregated metric summaries
    <experiment_id>_2/
        ...
    <experiment_id>_summary/                                            # eval-containers only
        benchmark_report_v0.2,_agentic_run.yaml
        agentic_tasks.csv
```

With compression on (the default), everything except the benchmark reports and `run_metadata.yaml` sits inside each set's `workspace.tar.zst`; see [Compressed output](../README.md#compressed-output).
