# llmdbenchmark.analysis

Post-benchmark result processing and visualization. Converts raw harness output into standardized benchmark report formats (v0.1 and v0.2 YAML) and generates plots for latency, throughput, and resource metrics.

## Analysis Pipeline

The entry point is `run_analysis()` in `__init__.py`, which performs these stages:

1. **Benchmark report conversion** -- Convert harness-native JSON results into standardized YAML reports (v0.1 and v0.2) using the bundled `benchmark_report` library. Falls back to the `benchmark-report` CLI if the Python API is unavailable.
2. **Summary extraction** -- Extract the tail of `stdout.log` from a harness-specific marker into `analysis/summary.txt`.
3. **Harness-specific post-processing** -- For `inference-perf`, run `inference-perf --analyze` if available.
4. **Metric visualization** -- Generate time-series PNG plots from collected Prometheus metrics (requires `matplotlib`).
5. **Per-request distribution plots** -- Generate histograms, CDFs, and scatter plots from per-request lifecycle data.

Supported harnesses: `inference-perf`, `guidellm`, `vllm-benchmark`, `inferencemax`, `nop`.

## Files

| File | Description |
|------|-------------|
| `__init__.py` | `run_analysis()` entry point, harness dispatch, conversion pipeline |
| `cross_treatment.py` | Cross-treatment comparison: CSV summary tables, bar charts, scatter plots, overlaid CDF plots |
| `per_request_plots.py` | Per-request distribution plots: TTFT/TPOT/ITL/E2E histograms, CDFs, scatter vs token length |
| `visualize_metrics.py` | Prometheus metric time-series plots (KV cache, GPU utilization, memory, power, queue depth) |

### benchmark_report/ subdirectory

Bundled library for standardized benchmark reporting with Pydantic-validated schemas.

| File | Description |
|------|-------------|
| `__init__.py` | Public API re-exports (`BenchmarkReport`, `BenchmarkReportV01`, `BenchmarkReportV02`, utility functions) |
| `base.py` | `BenchmarkReport` base class (Pydantic), `WorkloadGenerator` enum, `Units` enum with unit categories |
| `cli.py` | CLI for converting native output files to benchmark report format |
| `core.py` | Core utilities: YAML/CSV import, nested dict access, schema auto-detection, JSON schema generation |
| `metrics_processor.py` | Prometheus metrics parsing and `ComponentObservability` construction for v0.2 reports (not yet integrated) |
| `native_to_br0_1.py` | Converters from native harness output to benchmark report v0.1 schema |
| `native_to_br0_2.py` | Converters from native harness output to benchmark report v0.2 schema |
| `schema_v0_1.py` | Pydantic models for benchmark report v0.1 (Scenario, Metrics, Latency, Throughput, etc.) |
| `schema_v0_2.py` | Pydantic models for benchmark report v0.2 (Component stack, Load, RequestPerformance, Observability, etc.) |
| `schema_v0_2_components.py` | Standardized component classes for v0.2 (Generic, InferenceEngine) with discriminated unions |

### scripts/ subdirectory

| File | Description |
|------|-------------|
| `nop-analyze_results.py` | Legacy analysis script for the `nop` harness (model load timing). Uses pandas and the benchmark_report library directly. |

## Dependencies

- **Required**: `pydantic`, `PyYAML`, `numpy`
- **Optional**: `matplotlib` (for all plot generation; gracefully skipped if absent)
- **Optional**: `pandas` (only for `nop` harness analysis script)
