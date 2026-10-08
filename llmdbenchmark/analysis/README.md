# llmdbenchmark.analysis

Post-benchmark result processing. Converts raw harness output into standardized benchmark report formats (v0.1 and v0.2 YAML). To visualize results, use [llm-d-prism](https://github.com/llm-d/llm-d-prism).

## Analysis Pipeline

The entry point is `run_analysis()` in `__init__.py`, which performs these stages:

1. **Benchmark report conversion** -- Convert harness-native JSON results into standardized YAML reports (v0.1 and v0.2) using the bundled `benchmark_report` library. Falls back to the `benchmark-report` CLI if the Python API is unavailable.
2. **Summary extraction** -- Extract the tail of `stdout.log` from a harness-specific marker into `analysis/summary.txt`. Markers are defined per harness (e.g. `"Setup complete, starting benchmarks"` for guidellm, `"Result =="` for vllm-benchmark).
3. **Metric embedding** -- Merge the collected Prometheus metrics into the v0.2 reports, clipped per stage.

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

Reads benchmark report v0.2 YAML files from multiple result directories and produces a comparison table.

### `generate_cross_treatment_summary(results_dir, output_dir=None, context=None) -> int`

Returns the number of treatments compared. Output goes to `results_dir/cross-treatment-comparison/` by default.

It writes `treatment_comparison.csv`: one row per treatment with columns for TTFT, TPOT, ITL, E2E latency (mean and P99), output/request/total throughput, total requests, failures, input/output lengths, tool, and rate.

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
- **Optional**: `pandas` (only for `nop` harness analysis script)
