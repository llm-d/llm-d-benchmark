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
