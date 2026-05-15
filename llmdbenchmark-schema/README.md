# llmdbenchmark-schema

Pydantic types for the **Benchmark Report (BR)** format used by
[llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark).

Published as a standalone distribution so that **external benchmark
tooling** (workload generators, dashboards, analysis pipelines) can
depend on just the schema without pulling in the rest of `llmdbenchmark`
(which carries Kubernetes/transformers/HF deps).

## Install

```bash
pip install llmdbenchmark-schema
```

Runtime deps: `pydantic>=2.0`, `PyYAML`. That's it.

## Use

```python
from llmdbenchmark_schema import BenchmarkReportV02

with open("report.yaml") as f:
    import yaml
    BenchmarkReportV02.model_validate(yaml.safe_load(f))
```

The full `llmdbenchmark` package re-exports these types under
`llmdbenchmark.analysis.benchmark_report` for back-compat, so existing
code does not need to change.

## Versioning

The package version tracks the latest *schema* version it ships. Older
schema versions remain accessible (e.g. `BenchmarkReportV01`).

## Source

This package's source lives inside the
[llm-d-benchmark](https://github.com/llm-d/llm-d-benchmark) repository,
under `llmdbenchmark-schema/`. Changes are made there.
