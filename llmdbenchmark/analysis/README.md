# llmdbenchmark.analysis

Post-benchmark result processing and visualization. Converts raw harness output into standardized benchmark report formats (v0.1 and v0.2 YAML) and generates plots for latency, throughput, and resource metrics.

## Analysis Pipeline

The entry point is `run_analysis()` in `__init__.py`, which performs these stages:

1. **Benchmark report conversion** -- Convert harness-native JSON results into standardized YAML reports (v0.1 and v0.2) using the bundled `benchmark_report` library. Falls back to the `benchmark-report` CLI if the Python API is unavailable.
2. **Summary extraction** -- Extract the tail of `stdout.log` from a harness-specific marker into `analysis/summary.txt`. Markers are defined per harness (e.g. `"Setup complete, starting benchmarks"` for guidellm, `"Result =="` for vllm-benchmark).
3. **Harness-specific post-processing** -- For `inference-perf`, runs `inference-perf --analyze` if available on `$PATH`. For `nop`, delegates to the `nop-analyze_results.py` script.
4. **Metric visualization** -- Generate time-series PNG plots from collected Prometheus metrics in `metrics/raw/*.log` (requires `matplotlib`). Output to `analysis/graphs/`.
5. **Per-request distribution plots** -- Generate histograms, CDFs, and scatter plots from `per_request_lifecycle_metrics.json`. Output to `analysis/distributions/`.

### `run_analysis(harness_name, results_dir, context=None) -> str | None`

Run analysis for a single results directory. Returns `None` on success, or an error string describing conversion failures.

Supported harnesses: `inference-perf`, `guidellm`, `vllm-benchmark`, `inferencemax`, `nop`.

Result file patterns per harness:

| Harness | Pattern |
|---------|---------|
| `inference-perf` | `stage_*.json` |
| `guidellm` | `results.json` |
| `vllm-benchmark` | `openai*.json` |
| `inferencemax` | `*.json` |

### Conversion Pipeline

Each result file is converted to both v0.1 and v0.2 benchmark report formats. The conversion tries the Python API first (faster, no subprocess), then falls back to the `benchmark-report` CLI.

Output files:
- `benchmark_report,_<filename>.yaml` -- v0.1 format
- `benchmark_report_v0.2,_<filename>.yaml` -- v0.2 format

## Cross-Treatment Comparison (`cross_treatment.py`)

Reads benchmark report v0.2 YAML files from multiple result directories and produces comparison artifacts.

### `generate_cross_treatment_summary(results_dir, output_dir=None, context=None) -> int`

Returns the number of treatments compared. Output goes to `results_dir/cross-treatment-comparison/` by default.

Artifacts produced:

1. **CSV summary table** (`treatment_comparison.csv`) -- One row per treatment with columns for TTFT, TPOT, ITL, E2E latency (mean and P99), output/request/total throughput, total requests, failures, input/output lengths, tool, and rate.

2. **Bar charts** -- For each metric, a bar chart comparing treatments. Multi-stage treatments are aggregated (mean with min/max error bars). The best treatment is highlighted in green. Metrics plotted:
   - TTFT mean, TPOT mean, ITL mean, E2E mean (lower is better)
   - Output throughput, request throughput (higher is better)
   - TTFT P99, TPOT P99, request failures (lower is better)

3. **Scatter/line plots** -- Latency vs throughput curves showing performance degradation under load:
   - TTFT/TPOT/ITL/E2E mean vs request rate (QPS)
   - TTFT/TPOT mean vs output throughput
   - TTFT/TPOT P99 vs request rate

4. **Overlaid CDF plots** -- Per-request distribution CDFs across treatments on the same axes (TTFT, TPOT, ITL, E2E). Each treatment gets its own curve. Reference lines at P50 and P99. Requires `per_request_lifecycle_metrics.json` in at least 2 treatment directories.

Treatment labels are shortened by stripping harness prefixes, experiment IDs, timestamp suffixes, and parallelism indices.

## Metric Visualization (`visualize_metrics.py`)

Generates time-series PNG plots from Prometheus metrics collected during benchmark runs.

### `generate_all_visualizations(metrics_dir, output_dir, context=None) -> int`

Reads `metrics/raw/*.log` files containing Prometheus metrics with timestamps and pod names. Produces PNG plots in the output directory.

Parses metric files with `# Timestamp:` and `# Pod:` comment headers, then standard Prometheus text format lines. Collects time-series data across multiple scrape files.

Requires `matplotlib` (optional dependency; gracefully skipped if absent).

## Per-Request Plots (`per_request_plots.py`)

Generates distribution plots from `per_request_lifecycle_metrics.json`.

### `generate_per_request_plots(results_dir, output_dir=None, context=None) -> int`

Reads the per-request lifecycle data and generates:
- Histograms of TTFT, TPOT, ITL, E2E latency distributions
- CDF plots for each metric
- Scatter plots: TTFT vs input token length, TPOT vs output token length

Output to `analysis/distributions/` by default. Requires `matplotlib`.

## benchmark_report/ Subdirectory

Bundled library for standardized benchmark reporting with Pydantic-validated schemas.

```
benchmark_report/
├── __init__.py                  -- Public API re-exports
├── base.py                      -- BenchmarkReport base class, WorkloadGenerator enum, Units enum
├── cli.py                       -- CLI for converting native output to benchmark report format
├── core.py                      -- YAML/CSV import, nested dict access, schema auto-detection
├── metrics_processor.py         -- Prometheus metrics parsing for v0.2 ComponentObservability
├── native_to_br0_1.py           -- Native to v0.1 converters (per-harness)
├── native_to_br0_2.py           -- Native to v0.2 converters (per-harness)
├── schema_v0_1.py               -- Pydantic models for v0.1 (Scenario, Metrics, Latency, Throughput)
├── schema_v0_2.py               -- Pydantic models for v0.2 (Component stack, Load, RequestPerformance)
└── schema_v0_2_components.py    -- Standardized component classes for v0.2
```

### scripts/ Subdirectory

| File | Description |
|------|-------------|
| `nop-analyze_results.py` | Analysis script for the `nop` harness (model load timing). Uses pandas and the benchmark_report library directly. |

## Integration into the Run Phase

Analysis is invoked by run step 11 (`AnalyzeResultsStep`) after result collection. The step calls `run_analysis()` for each result directory, then `generate_cross_treatment_summary()` if multiple treatments were collected. Analysis is also triggered when `--analyze` is passed to the `run` command.

## Dependencies

- **Required**: `pydantic`, `PyYAML`, `numpy`
- **Optional**: `matplotlib` (for all plot generation; gracefully skipped if absent)
- **Optional**: `pandas` (only for `nop` harness analysis script)
