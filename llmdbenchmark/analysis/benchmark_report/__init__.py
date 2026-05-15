"""
Benchmark Report standardized reporting format.

Schema types now live in the standalone ``llmdbenchmark-schema`` package
(distributed independently so external benchmark tooling can depend on
just the schema without pulling in the rest of ``llmdbenchmark``).

This module re-exports the schema types and bundles them with the
analysis utilities (``core``, ``cli``, ``native_to_*``) so existing
``from llmdbenchmark.analysis.benchmark_report import ...`` callers keep
working.
"""

from llmdbenchmark_schema.base import BenchmarkReport
from llmdbenchmark_schema.schema_v0_1 import BenchmarkReportV01
from llmdbenchmark_schema.schema_v0_2 import BenchmarkReportV02

from .core import (
    get_nested,
    import_benchmark_report,
    import_yaml,
    load_benchmark_report,
    make_json_schema,
    update_dict,
    yaml_str_to_benchmark_report,
)

__all__ = [
    "BenchmarkReport",
    "BenchmarkReportV01",
    "BenchmarkReportV02",
    "get_nested",
    "import_benchmark_report",
    "import_yaml",
    "load_benchmark_report",
    "make_json_schema",
    "update_dict",
    "yaml_str_to_benchmark_report",
]
