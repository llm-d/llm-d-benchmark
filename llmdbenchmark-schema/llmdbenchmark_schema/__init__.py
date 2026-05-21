"""llmdbenchmark-schema: BR (Benchmark Report) schema types.

This is the schema-only subset of ``llmdbenchmark``, published as a
standalone distribution so external benchmark tooling can depend on just
the report types without pulling in the rest of ``llmdbenchmark``
(kubernetes client, transformers, etc.).

The full ``llmdbenchmark`` package re-exports these types under
``llmdbenchmark.analysis.benchmark_report`` for back-compat.
"""

from .base import BenchmarkReport, Units, WorkloadGenerator
from .schema_v0_1 import BenchmarkReportV01
from .schema_v0_2 import BenchmarkReportV02

__all__ = [
    "BenchmarkReport",
    "BenchmarkReportV01",
    "BenchmarkReportV02",
    "Units",
    "WorkloadGenerator",
]
