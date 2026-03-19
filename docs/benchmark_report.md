# Benchmark Report

A "benchmark report" is a standardized format for aggregate benchmark results that describes the inference platform environment, workload, and performance metrics. Details on the benchmark report format are provided in [this document](../llmdbenchmark/analysis/benchmark_report/README.md)

**Why is this needed**:
A consistent data format which unambiguously describes a benchmarking experiment and results has multiple benefits:
- Having all relevant parameters describing benchmarking inputs and the specific environment they were executed in will make it clear to anyone examining the data exactly what was measured.
- Experiments can be easily repeated by others; anyone repeating an experiment should get the same result, within a reasonable margin.
- Tools utilizing benchmarking data will have a stable format that can be relied on, and be trusted to contain all the data needed to draw some result or conclusion (note there is a tradeoff between requiring certain data while maintaining flexibility of the report to support a wide array of use cases).
- Combining benchmarking results from multiple sources to perform analysis will be just as easy as analyzing data from a single source.
- With all available useful data consistently captured, there is reduced need to repeat experiments in order to acquire some piece of information that was not previously recorded.

A benchmark report is primarily meant to capture performance statistics for a particular combination of workload and environment, rather than detailed traces for individual requests. For benchmarking experiments that require capture of information that is not part of the standard benchmark report schema, a `metadata` field may be placed almost anywhere to supplement with arbitrary data.

## v0.2 Schema Structure

The v0.2 benchmark report is the current recommended format. Its top-level structure is:

```yaml
version: "0.2"
run:
  uid: <unique-run-id>
  start_time: <ISO-8601>
  end_time: <ISO-8601>
  duration: <seconds>
  user: <experimenter>
  experiment_id: <experiment-group-id>
  cluster_id: <cluster-name>
  pod_id: <harness-pod-name>
scenario:
  stack:
    - component_type: inference_engine
      metadata: { ... }
      standardized: { model, accelerator, parallelism, ... }
      native: { vllm_args, env_vars, ... }
    - component_type: router
      ...
  load:
    metadata: { ... }
    standardized: { request_rate, duration, ... }
    native: { harness_args, ... }
results:
  request_performance:
    aggregate:
      latency:
        time_to_first_token: { mean, p50, p90, p95, p99, min, max }
        time_per_output_token: { mean, p50, p90, p95, p99, min, max }
        inter_token_latency: { mean, p50, p90, p95, p99, min, max }
        request_latency: { mean, p50, p90, p95, p99, min, max }
      throughput:
        output_token_rate: { mean }
        input_token_rate: { mean }
        request_rate: { mean }
        total_token_rate: { mean }
      requests:
        total: <int>
        failures: <int>
    per_stage: [ ... ]   # optional, per-load-stage breakdowns
```

See [`llmdbenchmark/analysis/benchmark_report/br_v0_2_example.yaml`](../llmdbenchmark/analysis/benchmark_report/br_v0_2_example.yaml) for a complete example and [`br_v0_2_json_schema.json`](../llmdbenchmark/analysis/benchmark_report/br_v0_2_json_schema.json) for the formal JSON Schema.

## Cross-Treatment Comparison CSV

When multiple treatments are executed (via `--experiments` or `experiment`), the analysis pipeline generates a `treatment_comparison.csv` file that extracts key metrics from each treatment's v0.2 benchmark report into a single summary table.

### CSV Columns

Each row represents one treatment. Columns include:

| Column | Source Path in v0.2 Report |
|--------|---------------------------|
| `treatment` | Directory name |
| `ttft_mean_s` | `results.request_performance.aggregate.latency.time_to_first_token.mean` |
| `ttft_p50_s` | `results.request_performance.aggregate.latency.time_to_first_token.p50` |
| `ttft_p99_s` | `results.request_performance.aggregate.latency.time_to_first_token.p99` |
| `tpot_mean_s` | `results.request_performance.aggregate.latency.time_per_output_token.mean` |
| `tpot_p99_s` | `results.request_performance.aggregate.latency.time_per_output_token.p99` |
| `itl_mean_s` | `results.request_performance.aggregate.latency.inter_token_latency.mean` |
| `itl_p99_s` | `results.request_performance.aggregate.latency.inter_token_latency.p99` |
| `e2e_mean_s` | `results.request_performance.aggregate.latency.request_latency.mean` |
| `e2e_p99_s` | `results.request_performance.aggregate.latency.request_latency.p99` |
| `output_tps` | `results.request_performance.aggregate.throughput.output_token_rate.mean` |
| `request_qps` | `results.request_performance.aggregate.throughput.request_rate.mean` |
| `total_tps` | `results.request_performance.aggregate.throughput.total_token_rate.mean` |
| `total_requests` | `results.request_performance.aggregate.requests.total` |
| `failures` | `results.request_performance.aggregate.requests.failures` |

The CSV is written to `<results_dir>/cross-treatment-comparison/treatment_comparison.csv`.

## Generated Plot Types

The analysis pipeline produces several categories of plots. All plots are PNG files saved under `<results_dir>/analysis/` or `<results_dir>/cross-treatment-comparison/`.

### Per-Request Distribution Plots

Generated from `per_request_lifecycle_metrics.json` (requires `--analyze`):

| Plot | Description |
|------|-------------|
| TTFT histogram | Distribution of time to first token with mean/P50/P99 vertical lines |
| TPOT histogram | Distribution of time per output token with mean/P50/P99 lines |
| ITL histogram | Distribution of inter-token latency with mean/P50/P99 lines |
| E2E histogram | Distribution of end-to-end request latency with mean/P50/P99 lines |
| TTFT CDF | Cumulative distribution function of TTFT |
| TPOT CDF | Cumulative distribution function of TPOT |
| ITL CDF | Cumulative distribution function of ITL |
| E2E CDF | Cumulative distribution function of E2E |
| TTFT vs input length | Scatter plot showing correlation between input length and TTFT |
| E2E vs output length | Scatter plot showing correlation between output length and E2E latency |
| ITL distribution | Distribution of inter-token latencies across all tokens in all requests |

### Cross-Treatment Comparison Plots

Generated when multiple treatments are available:

| Plot | Description |
|------|-------------|
| Metric bar charts | Side-by-side bars comparing each aggregate metric across treatments |
| Latency vs throughput | Scatter/line plot showing the latency-throughput trade-off curve |
| Overlaid CDFs | CDF plots from multiple treatments overlaid for direct comparison |

### Prometheus Metric Plots

Generated from raw metric scrapes (see [Metrics Collection](metrics_collection.md#metric-visualization-visualize_metricspy)):

| Plot | Description |
|------|-------------|
| Time series | Per-pod time series for KV cache, GPU utilization, request counts, throughput, and power |
