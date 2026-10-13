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

## eval-containers Run Summary (`aggregate_eval_containers.py`)

`eval-containers` runs one task per harness pod. This module rolls a treatment's tasks up into one run-level v0.2 report. It never runs on the driver: it ships in the harness-scripts ConfigMap and runs as a plain script in one extra summary pod per treatment (or one more container under nok8s), in the llm-d-benchmark image, so it must parse on that image's Python and read only plain files.

```
aggregate_eval_containers.py --expect N --deadline SECONDS <results_dir> <experiment_id>
```

It waits until N task dirs `<experiment_id>_<n>` hold the done marker (or the deadline passes, 0 means no deadline), then writes `<experiment_id>_summary/` with `benchmark_report_v0.2,_agentic_run.yaml` and `agentic_tasks.csv`. It always exits 0.

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

Harnesses analyze their own results in the pod (eval-containers through a report sidecar in each task pod). Run step 12 (`AnalyzeResultsStep`) always runs after result collection and calls `run_analysis()` for each result directory; it does nothing for a directory that already holds the pod's reports, so it only fills gaps. It has no converter for aiperf or lm-eval, and for inference-perf, vllm-benchmark or inferencemax it warns when the raw results are only inside a set's archive. It is skipped under `--data-collect skip`.

## Dependencies

- **Required**: `pydantic`, `PyYAML`, `numpy`
- **Optional**: `pandas` (only for `nop` harness analysis script)
