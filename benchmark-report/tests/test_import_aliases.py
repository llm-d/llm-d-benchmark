"""The deprecated ``benchmark_report`` alias must expose the same module
and class objects as the canonical ``llmd_benchmark_report`` package, so
isinstance checks hold across mixed import styles."""

import benchmark_report
import benchmark_report.schema_v0_2  # noqa: F401  (import-statement form)
import llmd_benchmark_report
from benchmark_report.schema_v0_2 import BenchmarkReportV02 as AliasV02
from llmd_benchmark_report.schema_v0_2 import BenchmarkReportV02 as CanonicalV02


def test_submodule_identity() -> None:
    assert benchmark_report.schema_v0_2 is llmd_benchmark_report.schema_v0_2
    assert AliasV02 is CanonicalV02


def test_public_api_reexported() -> None:
    assert benchmark_report.__all__ == llmd_benchmark_report.__all__
    for name in llmd_benchmark_report.__all__:
        assert getattr(benchmark_report, name) is getattr(llmd_benchmark_report, name)


# schema_v0_2_1 and native_to_br0_2_1 are deprecated names for the v0.2
# modules. schema_v0_2_1.LoadMetadata must be schema_v0_2.LoadMetadata (same
# for the other extended models and every importer), BenchmarkReportV021 must
# be BenchmarkReportV02, and both modules report VERSION "0.2.1".
def test_v0_2_1_modules_alias_v0_2() -> None:
    from llmd_benchmark_report import (
        native_to_br0_2,
        native_to_br0_2_1,
        schema_v0_2,
        schema_v0_2_1,
    )

    assert schema_v0_2.BenchmarkReportV021 is schema_v0_2.BenchmarkReportV02
    assert schema_v0_2_1.VERSION == schema_v0_2.VERSION == "0.2.1"
    for name in (
        "BenchmarkReportV02",
        "BenchmarkReportV021",
        "LoadMetadata",
        "AggregateRequests",
        "AggregateThroughput",
        "TimeSeriesResourceMetrics",
        "Observability",
        "MultiModalRequests",
    ):
        assert getattr(schema_v0_2_1, name) is getattr(schema_v0_2, name)
    for name in (
        "import_guidellm",
        "import_guidellm_all",
        "import_inference_max",
        "import_inference_perf",
        "import_inference_perf_session",
        "import_vllm_benchmark",
    ):
        assert getattr(native_to_br0_2_1, name) is getattr(native_to_br0_2, name)


# Each module that moved under artifacts/ keeps its old path. Every public name in
# artifacts/benchmark_report/v0_2/schema.py is the same object at schema_v0_2,
# including RunTime, which moved to the shared header. Likewise for base,
# schema_v0_1 and schema_v0_2_components.
def test_moved_modules_keep_their_old_paths() -> None:
    import importlib

    moves = {
        "base": "artifacts.benchmark_report.base",
        "schema_v0_1": "artifacts.benchmark_report.v0_1.schema",
        "schema_v0_2": "artifacts.benchmark_report.v0_2.schema",
        "schema_v0_2_components": "artifacts.benchmark_report.v0_2.components",
    }
    for old, new in moves.items():
        old_mod = importlib.import_module(f"llmd_benchmark_report.{old}")
        new_mod = importlib.import_module(f"llmd_benchmark_report.{new}")
        public = [name for name in vars(new_mod) if not name.startswith("_")]
        assert public
        for name in public:
            assert getattr(old_mod, name) is getattr(new_mod, name), f"{old}.{name}"
    assert llmd_benchmark_report.schema_v0_2.RunTime is (
        importlib.import_module("llmd_benchmark_report.artifacts").RunTime
    )
