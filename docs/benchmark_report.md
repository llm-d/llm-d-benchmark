# Benchmark Report

A "benchmark report" is a standardized format for aggregate benchmark results that describes the inference platform environment, workload, and performance metrics. Details on the benchmark report format are provided in [this document](../workload/report/README.md)

**Why is this needed**:
A consistent data format which unambiguously describes a benchmarking experiment and results has multiple benefits:
- Having all relevant parameters describing benchmarking inputs and the specific environment they were executed in will make it clear to anyone examining the data exactly what was measured.
- Experiments can be easily repeated by others; anyone repeating an experiment should get the same result, within a reasonable margin.
- Tools utilizing benchmarking data will have a stable format that can be relied on, and be trusted to contain all the data needed to draw some result or conclusion (note there is a tradeoff between requiring certain data while maintaining flexibility of the report to support a wide array of use cases).
- Combining benchmarking results from multiple sources to perform analysis will be just as easy as analyzing data from a single source.
- With all available useful data consistently captured, there is reduced need to repeat experiments in order to acquire some piece of information that was not previously recorded.

A benchmark report is primarily meant to capture performance statistics for a particular combination of workload and environment, rather than detailed traces for individual requests. For benchmarking experiments that require capture of information that is not part of the standard benchmark report schema, a `metadata` field may be placed almost anywhere to supplement with arbitrary data.